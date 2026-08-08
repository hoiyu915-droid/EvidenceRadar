#!/usr/bin/env python3
"""Run the public GitHub Actions EvidenceRadar lane.

This lane performs automated discovery, event-window filtering, identity
deduplication, and bounded publisher-page access.  It deliberately does not
turn metadata or search snippets into scientific conclusions.  Runs therefore
remain auditable through the same four-artifact contract used by ChatGPT Work
and report source limitations explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_WORKS = "https://api.openalex.org/works"
USER_AGENT = "EvidenceRadar/1.0 (+https://github.com/hoiyu915-droid/EvidenceRadar)"
DEFAULT_TIMEZONE = "Asia/Tokyo"
ABSOLUTE_PUBLISHER_HARD_MAX = 15
ARTIFACT_NAMES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)
DEDUPE_PRIORITY = (
    "doi",
    "pmid",
    "pmcid",
    "arxiv_id",
    "anthology_id",
    "openalex_id",
    "normalized_title",
)
QUALIFYING_EVENTS = {
    "version_of_record_first_online",
    "first_formal_indexing",
    "formal_proceedings_release",
    "oa_fulltext_first_available",
    "author_accepted_manuscript_first_available",
    "embargo_lifted",
    "preprint_to_peer_reviewed_upgrade",
    "formal_version_verified",
}


class RadarRuntimeError(RuntimeError):
    """Raised when a run cannot produce a structurally valid bundle."""


@dataclass
class Candidate:
    title: str
    stream: str
    category: str
    source: str
    publication_date: str
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    arxiv_id: str = ""
    anthology_id: str = ""
    openalex_id: str = ""
    landing_url: str = ""
    open_access: bool | None = None
    is_preprint: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    score: int = 0

    @property
    def normalized_title(self) -> str:
        value = unicodedata.normalize("NFKD", self.title).casefold()
        value = re.sub(r"[^\w\s]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @property
    def identifiers(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("doi", normalize_doi(self.doi)),
                ("pmid", self.pmid.strip()),
                ("pmcid", self.pmcid.strip()),
                ("arxiv_id", self.arxiv_id.strip()),
                ("anthology_id", self.anthology_id.strip()),
                ("openalex_id", self.openalex_id.strip()),
            )
            if value
        }

    @property
    def work_id(self) -> str:
        identifiers = self.identifiers
        for key in DEDUPE_PRIORITY:
            if key == "normalized_title":
                break
            if identifiers.get(key):
                return f"{key}:{identifiers[key]}"
        digest = hashlib.sha256(self.normalized_title.encode("utf-8")).hexdigest()
        return f"title:{digest}"

    def publisher_url(self) -> str:
        if self.landing_url.startswith(("http://", "https://")):
            return self.landing_url
        if self.doi:
            return f"https://doi.org/{quote(normalize_doi(self.doi), safe='/')}"
        if self.pmcid:
            return f"https://pmc.ncbi.nlm.nih.gov/articles/{self.pmcid}/"
        if self.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
        if self.openalex_id.startswith(("http://", "https://")):
            return self.openalex_id
        return ""


def normalize_doi(value: str | None) -> str:
    doi = re.sub(r"\s+", " ", value or "").strip().casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(" .")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RadarRuntimeError(f"cannot load configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RadarRuntimeError(f"configuration must be a mapping: {path}")
    return payload


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _safe_date(year: str, month: str, day: str) -> str:
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    try:
        month_value = month.strip().casefold()[:3]
        month_number = months.get(month_value, int(month) if month.isdigit() else 1)
        day_number = int(day) if day.isdigit() else 1
        return date(int(year), month_number, day_number).isoformat()
    except (TypeError, ValueError):
        return f"{year}-01-01" if year else ""


def _pubmed_publication_date(article: ET.Element) -> str:
    article_date = article.find("ArticleDate")
    if article_date is None:
        article_date = article.find(".//Article/ArticleDate")
    if article_date is not None:
        value = _safe_date(
            _text(article_date.find("Year")),
            _text(article_date.find("Month")) or "1",
            _text(article_date.find("Day")) or "1",
        )
        if value:
            return value
    pub_date = article.find(".//JournalIssue/PubDate")
    if pub_date is not None:
        value = _safe_date(
            _text(pub_date.find("Year")),
            _text(pub_date.find("Month")) or "1",
            _text(pub_date.find("Day")) or "1",
        )
        if value:
            return value
        match = re.search(r"(?:19|20)\d{2}", _text(pub_date.find("MedlineDate")))
        if match:
            return f"{match.group(0)}-01-01"
    return ""


def redact_error(value: object) -> str:
    text = str(value)
    for name in ("OPENALEX_API_KEY", "NCBI_API_KEY", "NCBI_EMAIL"):
        secret = os.getenv(name, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)(api_key|email)=([^&\s]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )


def _request(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 40,
    attempts: int = 3,
) -> requests.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RadarRuntimeError(f"request failed: {url}: {redact_error(last_error)}")


def fetch_pubmed(
    session: requests.Session,
    query: str,
    stream: str,
    category: str,
    start_date: date,
    end_date: date,
    max_results: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Candidate]:
    common: dict[str, str] = {"tool": "EvidenceRadar"}
    if os.getenv("NCBI_EMAIL", "").strip():
        common["email"] = os.environ["NCBI_EMAIL"].strip()
    if os.getenv("NCBI_API_KEY", "").strip():
        common["api_key"] = os.environ["NCBI_API_KEY"].strip()
    request_interval = 0.11 if "api_key" in common else 0.34

    def pubmed_request(url: str, *, params: dict[str, Any]) -> requests.Response:
        response = _request(session, url, params=params)
        # NCBI E-utilities allows 3 requests/s without an API key and 10/s
        # with a default key. Sleeping after every call also spaces the first
        # request made by the next stream/query.
        sleep(request_interval)
        return response

    ids: list[str] = []
    for date_type in ("pdat", "edat"):
        payload = pubmed_request(
            f"{PUBMED_BASE}/esearch.fcgi",
            params={
                **common,
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": max_results,
                "sort": "pub date",
                "datetype": date_type,
                "mindate": start_date.strftime("%Y/%m/%d"),
                "maxdate": end_date.strftime("%Y/%m/%d"),
            },
        ).json()
        if not isinstance(payload, dict):
            raise RadarRuntimeError("PubMed ESearch returned a non-object payload")
        result = payload.get("esearchresult")
        if not isinstance(result, dict) or not isinstance(result.get("idlist", []), list):
            raise RadarRuntimeError("PubMed ESearch returned an invalid result list")
        ids.extend(str(item) for item in result.get("idlist", []) if str(item).strip())
    ids = list(dict.fromkeys(ids))[:max_results]
    if not ids:
        return []
    response = pubmed_request(
        f"{PUBMED_BASE}/efetch.fcgi",
        params={**common, "db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
    )
    root = ET.fromstring(response.text)
    candidates: list[Candidate] = []
    for item in root.findall(".//PubmedArticle"):
        citation = item.find("MedlineCitation")
        article = item.find("MedlineCitation/Article")
        if citation is None or article is None:
            continue
        title = _text(article.find("ArticleTitle"))
        if not title:
            continue
        identifiers: dict[str, str] = {}
        for node in item.findall("PubmedData/ArticleIdList/ArticleId"):
            kind = (node.attrib.get("IdType") or "").casefold()
            if kind:
                identifiers[kind] = _text(node)
        pmid = _text(citation.find("PMID"))
        publication_date = _pubmed_publication_date(article)
        events: list[dict[str, Any]] = []
        article_date = article.find("ArticleDate")
        if article_date is not None:
            occurred_at = _safe_date(
                _text(article_date.find("Year")),
                _text(article_date.find("Month")) or "1",
                _text(article_date.find("Day")) or "1",
            )
            if occurred_at:
                events.append(
                    event_record(
                        "version_of_record_first_online",
                        occurred_at,
                        "PubMed",
                        "Article/ArticleDate",
                        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "date",
                        "publisher_supplied_citation",
                    )
                )
        for history_date in item.findall("PubmedData/History/PubMedPubDate"):
            status = (history_date.attrib.get("PubStatus") or "").casefold()
            if status not in {"pubmed", "entrez"}:
                continue
            occurred_at = _safe_date(
                _text(history_date.find("Year")),
                _text(history_date.find("Month")) or "1",
                _text(history_date.find("Day")) or "1",
            )
            precision = "date"
            if occurred_at:
                events.append(
                    event_record(
                        "first_formal_indexing",
                        occurred_at,
                        "PubMed",
                        f"PubMedData/History[{status}]",
                        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        precision,
                        "provider_metadata",
                    )
                )
        if not events and publication_date:
            events.append(
                event_record(
                    "formal_version_verified",
                    publication_date,
                    "PubMed",
                    "publication_date",
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "date",
                    "provider_metadata",
                )
            )
        authors: list[str] = []
        for author in article.findall("AuthorList/Author"):
            name = _text(author.find("CollectiveName"))
            if not name:
                name = " ".join(
                    part
                    for part in (_text(author.find("LastName")), _text(author.find("Initials")))
                    if part
                )
            if name:
                authors.append(name)
        abstract = " ".join(
            _text(node) for node in article.findall("Abstract/AbstractText") if _text(node)
        )
        doi = normalize_doi(identifiers.get("doi", ""))
        candidates.append(
            Candidate(
                title=title,
                stream=stream,
                category=category,
                source="PubMed",
                publication_date=publication_date,
                authors=authors,
                venue=_text(article.find("Journal/Title")),
                abstract=abstract,
                doi=doi,
                pmid=pmid,
                pmcid=identifiers.get("pmc", ""),
                landing_url=f"https://doi.org/{quote(doi, safe='/')}" if doi else "",
                events=events,
            )
        )
    return candidates


def _openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions = sorted(
        (position, word)
        for word, offsets in index.items()
        for position in offsets
    )
    return " ".join(word for _, word in positions)


def _terminal_id(value: str | None) -> str:
    return (value or "").strip().rstrip("/").rsplit("/", 1)[-1]


def fetch_openalex(
    session: requests.Session,
    query: str,
    stream: str,
    category: str,
    start_date: date,
    end_date: date,
    max_results: int,
) -> list[Candidate]:
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        raise RadarRuntimeError(
            "OPENALEX_API_KEY is required for the OpenAlex discovery adapter"
        )
    params: dict[str, Any] = {
        "search": query,
        "filter": (
            f"from_publication_date:{start_date.isoformat()},"
            f"to_publication_date:{end_date.isoformat()}"
        ),
        "sort": "publication_date:desc",
        "per-page": min(max_results, 100),
        "select": (
            "id,doi,title,display_name,publication_date,type,authorships,"
            "primary_location,best_oa_location,open_access,abstract_inverted_index,ids"
        ),
        "api_key": api_key,
    }
    payload = _request(session, OPENALEX_WORKS, params=params).json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results", []), list):
        raise RadarRuntimeError("OpenAlex returned an invalid works payload")
    candidates: list[Candidate] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        title = re.sub(r"\s+", " ", item.get("display_name") or item.get("title") or "").strip()
        if not title:
            continue
        location = item.get("primary_location") or {}
        best_oa = item.get("best_oa_location") or {}
        source = location.get("source") or {}
        work_type = str(item.get("type") or "")
        publication_date = str(item.get("publication_date") or "")
        landing_url = str(
            location.get("landing_page_url")
            or best_oa.get("landing_page_url")
            or item.get("doi")
            or item.get("id")
            or ""
        )
        events = []
        if publication_date:
            events.append(
                event_record(
                    "formal_version_verified",
                    publication_date,
                    "OpenAlex",
                    "publication_date",
                    landing_url,
                    "date",
                    "provider_metadata",
                )
            )
        ids = item.get("ids") or {}
        candidates.append(
            Candidate(
                title=title,
                stream=stream,
                category=category,
                source="OpenAlex",
                publication_date=publication_date,
                authors=[
                    str(authorship.get("author", {}).get("display_name") or "").strip()
                    for authorship in item.get("authorships", [])
                    if str(authorship.get("author", {}).get("display_name") or "").strip()
                ],
                venue=str(source.get("display_name") or work_type),
                abstract=_openalex_abstract(item.get("abstract_inverted_index")),
                doi=normalize_doi(item.get("doi")),
                pmid=_terminal_id(ids.get("pmid")),
                pmcid=_terminal_id(ids.get("pmcid")),
                openalex_id=str(item.get("id") or ""),
                landing_url=landing_url,
                open_access=(item.get("open_access") or {}).get("is_oa"),
                is_preprint=work_type.casefold() in {"preprint", "posted-content"},
                events=events,
            )
        )
    return candidates


def event_record(
    event_type: str,
    occurred_at: str,
    source: str,
    source_field: str,
    source_url: str,
    precision: str,
    confidence: str,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "source": source,
        "source_field": source_field,
        "source_url": source_url,
        "precision": precision,
        "confidence": confidence,
    }


def parse_event_time(value: str, timezone: ZoneInfo) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value[:10])
        except ValueError:
            return None
        return datetime.combine(parsed_date, datetime_time.min, timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def event_in_window(
    event: dict[str, Any],
    start: datetime,
    end: datetime,
    timezone: ZoneInfo,
) -> bool:
    if event.get("event_type") not in QUALIFYING_EVENTS:
        return False
    occurred = parse_event_time(str(event.get("occurred_at") or ""), timezone)
    if occurred is None:
        return False
    if event.get("precision") == "date":
        return start.date() < occurred.date() <= end.date()
    return start <= occurred <= end


def category_lookup(scoring: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for category, config in scoring.get("categories", {}).items():
        for stream in config.get("streams", []):
            lookup[str(stream)] = str(category)
    return lookup


def score_candidate(candidate: Candidate, relevance_terms: Iterable[str]) -> int:
    title = candidate.title.casefold()
    text = f"{candidate.title} {candidate.abstract}".casefold()
    matches = sum(1 for term in relevance_terms if str(term).casefold() in text)
    title_matches = sum(1 for term in relevance_terms if str(term).casefold() in title)
    identity_bonus = 8 if candidate.doi or candidate.pmid else 0
    oa_bonus = 4 if candidate.open_access else 0
    return min(100, 40 + matches * 6 + title_matches * 3 + identity_bonus + oa_bonus)


def candidate_is_eligible(candidate: Candidate, scoring: dict[str, Any]) -> bool:
    title = candidate.title.casefold()
    excluded_title_terms = (
        "protocol",
        "editorial",
        "letter to the editor",
        "retracted",
        "withdrawn",
    )
    if any(term in title for term in excluded_title_terms):
        return False
    minimum = int(scoring.get("category_min_relevance", {}).get(candidate.category, 0))
    return candidate.score >= minimum


def deduplicate(candidates: Iterable[Candidate]) -> list[Candidate]:
    selected: dict[str, Candidate] = {}
    for candidate in candidates:
        key = candidate.work_id
        current = selected.get(key)
        if current is None or candidate.score > current.score:
            if current is not None:
                candidate.events = merge_event_lists(current.events, candidate.events)
            selected[key] = candidate
        elif current is not None:
            current.events = merge_event_lists(current.events, candidate.events)
    return sorted(selected.values(), key=lambda item: (-item.score, item.work_id))


def merge_event_lists(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for group in groups:
        for event in group:
            key = "|".join(
                str(event.get(field) or "").casefold()
                for field in ("event_type", "occurred_at", "source", "source_field")
            )
            events[key] = dict(event)
    return [events[key] for key in sorted(events)]


def discover_candidates(
    streams: dict[str, Any],
    scoring: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    session: requests.Session,
) -> tuple[list[Candidate], list[dict[str, Any]], list[dict[str, Any]], set[str], set[str]]:
    categories = category_lookup(scoring)
    guidance = streams.get("candidate_guidance", {})
    max_results = int(guidance.get("suggested_max_per_query", 40))
    candidates: list[Candidate] = []
    queries: list[dict[str, Any]] = []
    source_access: list[dict[str, Any]] = []
    searched_sources: set[str] = set()
    unavailable_sources: set[str] = set()
    query_index = 0
    for stream, config in streams.get("streams", {}).items():
        sources = [str(item) for item in config.get("sources", [])]
        discovery_source = "pubmed" if "pubmed" in sources else "openalex" if "openalex" in sources else ""
        unavailable_sources.update(source for source in sources if source not in {"pubmed", "openalex", "publisher"})
        category = categories.get(str(stream), str(stream))
        for query in config.get("queries", []):
            query_index += 1
            query_id = f"query-{query_index:03d}"
            searched_at = datetime.now(start.tzinfo).isoformat()
            status = "SUCCESS"
            error = ""
            found: list[Candidate] = []
            if not discovery_source:
                status = "NOT_ATTEMPTED"
                error = "No automated discovery adapter for configured sources."
            else:
                try:
                    fetcher = fetch_pubmed if discovery_source == "pubmed" else fetch_openalex
                    found = fetcher(
                        session,
                        str(query),
                        str(stream),
                        category,
                        start.date(),
                        end.date(),
                        max_results,
                    )
                    searched_sources.add(discovery_source)
                except (
                    RadarRuntimeError,
                    requests.RequestException,
                    ValueError,
                    TypeError,
                    AttributeError,
                    KeyError,
                    ET.ParseError,
                ) as exc:
                    status = "FAILED"
                    error = redact_error(exc)
                    unavailable_sources.add(discovery_source)
            for candidate in found:
                candidate.score = score_candidate(candidate, config.get("relevance_terms", []))
            candidates.extend(found)
            queries.append(
                {
                    "query_id": query_id,
                    "category": category,
                    "query": str(query),
                    "searched_at": searched_at,
                    "source_ids": [discovery_source or "unavailable"],
                    "status": status,
                    "result_count": len(found),
                    **({"notes": [error]} if error else {}),
                }
            )
            source_access.append(
                {
                    "source_id": f"{query_id}-{discovery_source or 'unavailable'}",
                    "url": PUBMED_BASE if discovery_source == "pubmed" else OPENALEX_WORKS,
                    "accessed_at": searched_at,
                    "status": status,
                    "result_count": len(found),
                    **({"error": error} if error else {}),
                }
            )
    unique = [
        candidate
        for candidate in deduplicate(candidates)
        if candidate_is_eligible(candidate, scoring)
    ]
    return unique, queries, source_access, searched_sources, unavailable_sources


def qualifying_event(
    candidate: Candidate,
    start: datetime,
    end: datetime,
    timezone: ZoneInfo,
) -> dict[str, Any] | None:
    matches = [
        event for event in candidate.events
        if event_in_window(event, start, end, timezone)
    ]
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda event: (
            str(event.get("occurred_at") or ""),
            str(event.get("event_type") or ""),
        ),
        reverse=True,
    )[0]


def probe_publisher_pages(
    candidates: list[Candidate],
    config: dict[str, Any],
    *,
    session: requests.Session,
    accessed_at: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[tuple[Candidate, dict[str, Any]]], list[dict[str, Any]], list[str]]:
    target_min = int(config.get("target_min_per_run", 10))
    hard_max = int(config.get("hard_max_per_run", 15))
    per_domain_max = int(config.get("per_domain_hard_max", 2))
    timeout = int(config.get("timeout_seconds", 20))
    delay = float(config.get("request_delay_seconds", 1.0))
    stop_statuses = {int(value) for value in config.get("stop_domain_on_http_status", [401, 403, 429])}
    if not (0 <= target_min <= hard_max):
        raise RadarRuntimeError("publisher output requires 0 <= target_min_per_run <= hard_max_per_run")
    if hard_max > ABSOLUTE_PUBLISHER_HARD_MAX:
        raise RadarRuntimeError(
            f"publisher output hard_max_per_run cannot exceed {ABSOLUTE_PUBLISHER_HARD_MAX}"
        )
    if per_domain_max < 1:
        raise RadarRuntimeError("publisher output requires per_domain_hard_max >= 1")
    if timeout < 1:
        raise RadarRuntimeError("publisher output requires timeout_seconds >= 1")
    if delay < 0:
        raise RadarRuntimeError("publisher output requires request_delay_seconds >= 0")
    successes: list[tuple[Candidate, dict[str, Any]]] = []
    access_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocked_domains: set[str] = set()
    domain_counts: dict[str, int] = {}
    attempts = 0
    for candidate in candidates:
        if attempts >= hard_max:
            break
        url = candidate.publisher_url()
        if not url:
            continue
        initial_domain = urlparse(url).netloc.casefold()
        if initial_domain in blocked_domains or domain_counts.get(initial_domain, 0) >= per_domain_max:
            continue
        attempts += 1
        status = "FAILED"
        error = ""
        result_url = url
        http_status: int | None = None
        current_url = url
        try:
            visited: set[str] = set()
            for _redirect in range(7):
                if current_url in visited:
                    error = "publisher redirect loop detected"
                    break
                visited.add(current_url)
                domain = urlparse(current_url).netloc.casefold() or initial_domain
                is_doi_resolver = domain in {"doi.org", "dx.doi.org"}
                if not is_doi_resolver and (
                    domain in blocked_domains
                    or domain_counts.get(domain, 0) >= per_domain_max
                ):
                    error = f"publisher domain budget reached before access: {domain}"
                    break
                response = session.get(
                    current_url,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )
                http_status = response.status_code
                response_url = str(response.url or current_url)
                if not is_doi_resolver:
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
                location = str(getattr(response, "headers", {}).get("Location") or "")
                response.close()
                if http_status in {301, 302, 303, 307, 308} and location:
                    next_url = urljoin(response_url, location)
                    if urlparse(next_url).scheme not in {"http", "https"}:
                        result_url = response_url
                        error = "publisher returned a non-HTTP redirect"
                        break
                    current_url = next_url
                    result_url = next_url
                    continue
                result_url = response_url
                if http_status in stop_statuses:
                    blocked_domains.add(domain)
                    error = f"publisher blocked automated access with HTTP {http_status}"
                elif 200 <= http_status < 400:
                    status = "SUCCESS"
                else:
                    error = f"publisher returned HTTP {http_status}"
                break
            else:
                error = "publisher redirect limit exceeded"
        except requests.RequestException as exc:
            domain = urlparse(current_url).netloc.casefold() or initial_domain
            if domain not in {"doi.org", "dx.doi.org"}:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            result_url = current_url
            error = redact_error(exc)
        access_record = {
            "source_id": f"publisher-{attempts:03d}",
            "url": result_url,
            "accessed_at": accessed_at.isoformat(),
            "status": status,
            "result_count": 1 if status == "SUCCESS" else 0,
            **({"error": error} if error else {}),
        }
        access_records.append(access_record)
        if status == "SUCCESS":
            successes.append((candidate, access_record))
        if delay > 0 and attempts < hard_max:
            sleep(delay)
    if len(successes) < target_min:
        warnings.append(
            f"Publisher output target was {target_min}-{hard_max}; "
            f"only {len(successes)} source pages were accessible."
        )
    if blocked_domains:
        warnings.append(
            "Publisher access stopped for blocked domains: "
            + ", ".join(sorted(blocked_domains))
        )
    return successes, access_records, warnings


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def state_sha256(state: dict[str, Any]) -> str:
    canonical = json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(canonical)


def load_prior_state(
    path: Path,
    *,
    schema_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if not path.is_file():
        return None, sha256_bytes(b"")
    try:
        state = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return None, sha256_bytes(b"")
    if not isinstance(state, dict) or state.get("artifact_type") != "EvidenceRadar_State":
        return None, sha256_bytes(b"")
    if schema_path is not None:
        try:
            sys.path.insert(0, str(schema_path.parent.parent))
            from tools.validate_gpt_work_artifacts import load_json, validate_document

            if validate_document(state, load_json(schema_path)):
                return None, sha256_bytes(b"")
        except (OSError, json.JSONDecodeError):
            return None, sha256_bytes(b"")
    return state, state_sha256(state)


def _event_id(work_id: str, event: dict[str, Any]) -> str:
    payload = "|".join(
        [
            work_id,
            str(event.get("event_type") or ""),
            str(event.get("occurred_at") or ""),
            str(event.get("source") or ""),
            str(event.get("source_field") or ""),
        ]
    )
    return "event:" + sha256_bytes(payload.encode("utf-8"))


def build_state(
    prior: dict[str, Any] | None,
    selected: list[tuple[Candidate, dict[str, Any], dict[str, Any]]],
    *,
    generated_at: datetime,
    run_id: str,
    execution_lane: str,
    protocol_commit: str,
    base_state_sha256: str,
) -> dict[str, Any]:
    prior_works = {
        str(item.get("work_id")): dict(item)
        for item in (prior or {}).get("works", [])
        if isinstance(item, dict) and item.get("work_id")
    }
    prior_events = {
        str(item.get("event_id")): dict(item)
        for item in (prior or {}).get("notified_events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    now = generated_at.isoformat()
    for candidate, event, access in selected:
        work = prior_works.get(candidate.work_id)
        event_id = _event_id(candidate.work_id, event)
        if work is None:
            work = {
                "work_id": candidate.work_id,
                "title": candidate.title,
                "normalized_title": candidate.normalized_title,
                "identifiers": candidate.identifiers,
                "first_seen_at": now,
                "last_seen_at": now,
                "seen_count": 1,
                "notified_event_ids": [],
                "category": candidate.category,
                "streams": [candidate.stream],
                "source_urls": [access["url"]],
                "open_access": bool(candidate.open_access),
                "is_preprint": candidate.is_preprint,
            }
        else:
            work["last_seen_at"] = now
            work["seen_count"] = int(work.get("seen_count", 0)) + 1
            work["identifiers"] = {**work.get("identifiers", {}), **candidate.identifiers}
            work["streams"] = sorted(set(work.get("streams", [])) | {candidate.stream})
            work["source_urls"] = sorted(set(work.get("source_urls", [])) | {access["url"]})
        notified_ids = set(work.get("notified_event_ids", []))
        if event_id not in prior_events:
            prior_events[event_id] = {
                "event_id": event_id,
                "work_id": candidate.work_id,
                "event_type": event["event_type"],
                "occurred_at": event["occurred_at"],
                "notified_at": now,
                "source": event["source"],
                "source_field": event["source_field"],
                "source_url": event.get("source_url") or access["url"],
                "precision": event["precision"],
                "confidence": event["confidence"],
            }
        notified_ids.add(event_id)
        work["notified_event_ids"] = sorted(notified_ids)
        prior_works[candidate.work_id] = work
    history_status = (
        str(prior.get("history_status"))
        if prior and prior.get("history_status") in {"COMPLETE", "STATE_HISTORY_INCOMPLETE"}
        else "STATE_HISTORY_INCOMPLETE"
    )
    parent_ids = [str(prior.get("last_run_id"))] if prior and prior.get("last_run_id") else []
    return {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_State",
        "generated_at": now,
        "timezone": str(generated_at.tzinfo),
        "history_status": history_status,
        "history_note": (
            "Canonical prior state was loaded."
            if prior
            else "No valid canonical prior State artifact was available at run start."
        ),
        "last_run_id": run_id,
        "dedupe_priority": list(DEDUPE_PRIORITY),
        "works": [prior_works[key] for key in sorted(prior_works)],
        "notified_events": [prior_events[key] for key in sorted(prior_events)],
        "execution_lane": execution_lane,
        "protocol_commit": protocol_commit,
        "base_state_sha256": base_state_sha256,
        "parent_run_ids": parent_ids,
        "notes": [
            "Automated GitHub lane records only event candidates whose source page was accessible."
        ],
    }


def build_evidence(
    selected: list[tuple[Candidate, dict[str, Any], dict[str, Any]]],
    *,
    generated_at: datetime,
    run_id: str,
    requested_sources: set[str],
    searched_sources: set[str],
    unavailable_sources: set[str],
    coverage_status: str,
) -> dict[str, Any]:
    sources = []
    works = []
    for index, (candidate, _event, access) in enumerate(selected, start=1):
        source_id = f"publisher-source-{index:03d}"
        sources.append(
            {
                "source_id": source_id,
                "url": access["url"],
                "source_type": "publisher",
                "accessed_at": access["accessed_at"],
                "access_status": "METADATA",
                "title": candidate.title,
                **({"publisher": candidate.venue} if candidate.venue else {}),
                "locator": "Publisher landing page; automated access check only",
                "notes": [
                    "No substantive claim was promoted without ChatGPT Work or human source review."
                ],
            }
        )
        works.append(
            {
                "work_id": candidate.work_id,
                "title": candidate.title,
                "category": candidate.category,
                "stream": candidate.stream,
                "identifiers": candidate.identifiers,
                "limitations": [
                    "Automated lane verified page access and event metadata, not the paper's substantive claims."
                ],
            }
        )
    return {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_Evidence",
        "generated_at": generated_at.isoformat(),
        "run_id": run_id,
        "coverage_status": coverage_status,
        "coverage": {
            "requested_sources": sorted(requested_sources),
            "searched_sources": sorted(searched_sources),
            "unavailable_sources": sorted(unavailable_sources),
            "notes": [
                "GitHub Actions is an automated discovery/source-access lane; claim verification remains a ChatGPT Work or human-review task."
            ],
        },
        "sources": sources,
        "works": works,
        "claims": [],
        "notes": [
            "An empty claims ledger is intentional: metadata and snippets are not scientific conclusions."
        ],
    }


def render_report(
    selected: list[tuple[Candidate, dict[str, Any], dict[str, Any]]],
    *,
    generated_at: datetime,
    start: datetime,
    end: datetime,
    run_status: str,
    coverage_status: str,
    warnings: list[dict[str, str]],
    publisher_min: int,
    publisher_max: int,
) -> str:
    cards: list[str] = []
    for candidate, event, access in selected:
        authors = ", ".join(candidate.authors[:8])
        card = (
            "<article class=\"card\">"
            f"<h3>{html.escape(candidate.title)}</h3>"
            f"<p><strong>{html.escape(candidate.category)}</strong>"
            f" · {html.escape(candidate.venue or candidate.source)}</p>"
        )
        if authors:
            card += f"<p>{html.escape(authors)}</p>"
        card += (
            f"<p>Event: <code>{html.escape(event['event_type'])}</code>"
            f" · {html.escape(str(event['occurred_at']))}</p>"
            f"<p><a href=\"{html.escape(access['url'], quote=True)}\">Source page</a></p>"
            "<p class=\"caveat\">Automated access/event audit only; no outcome claim was promoted.</p>"
            "</article>"
        )
        cards.append(card)
    warning_items = "".join(
        f"<li><code>{html.escape(item['code'])}</code>: {html.escape(item['message'])}</li>"
        for item in warnings
    ) or "<li>None</li>"
    card_html = "".join(cards) or "<p>No new source-accessible qualifying events were recorded.</p>"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EvidenceRadar automated run</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:980px;margin:0 auto;padding:32px;color:#17202a;background:#f5f7fa}}
.banner,.card{{background:#fff;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:14px 0}}
.status{{font-weight:700}} code{{background:#eef2f6;padding:2px 5px;border-radius:4px}} a{{color:#075985}} .caveat{{color:#7c2d12}}
</style>
</head>
<body>
<h1>EvidenceRadar｜GitHub automated lane</h1>
<section class="banner">
<p>Generated: {html.escape(generated_at.isoformat())}</p>
<p>Window: {html.escape(start.isoformat())} → {html.escape(end.isoformat())}</p>
<p class="status">Run status: <code>{html.escape(run_status)}</code></p>
<p>Coverage: <code>{html.escape(coverage_status)}</code></p>
<p>Publisher source-page output target: {publisher_min}–{publisher_max}; actual: {len(selected)}</p>
</section>
<h2>Daily audit candidates</h2>
{card_html}
<h2>Warnings and gaps</h2>
<ul>{warning_items}</ul>
<p>This report is research triage, not individual medical advice. The automated lane does not convert metadata or snippets into scientific conclusions.</p>
</body>
</html>
"""


def _protocol_commit(root: Path, supplied: str | None) -> str:
    if supplied:
        return supplied
    if os.getenv("GITHUB_SHA", "").strip():
        return os.environ["GITHUB_SHA"].strip()
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip() or "unknown"


def validate_documents(root: Path, documents: dict[str, dict[str, Any]]) -> None:
    sys.path.insert(0, str(root))
    from tools.validate_gpt_work_artifacts import load_json, validate_document

    schema_names = {
        "EvidenceRadar_State.json": "evidence-radar-state.schema.json",
        "EvidenceRadar_Evidence.json": "evidence-radar-evidence.schema.json",
        "EvidenceRadar_Run.json": "evidence-radar-run.schema.json",
    }
    errors: list[str] = []
    for artifact_name, schema_name in schema_names.items():
        schema = load_json(root / "schemas" / schema_name)
        for error in validate_document(documents[artifact_name], schema):
            errors.append(f"{artifact_name}: {error}")
    if errors:
        raise RadarRuntimeError("artifact validation failed:\n" + "\n".join(errors))


def write_bundle(output_dir: Path, report_html: str, documents: dict[str, dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, str] = {
        "EvidenceRadar_Report.html": report_html,
        **{
            name: json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            for name, document in documents.items()
        },
    }
    for name, payload in payloads.items():
        temporary = output_dir / f".{name}.tmp"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, output_dir / name)


def write_state_atomic(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = state_path.parent / f".{state_path.name}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, state_path)


def execute(
    *,
    root: Path,
    output_dir: Path,
    state_path: Path,
    runs_dir: Path | None = None,
    end_at: datetime,
    run_id: str,
    execution_lane: str,
    publisher_target_min: int | None = None,
    publisher_hard_max: int | None = None,
    protocol_commit: str | None = None,
    session: requests.Session | None = None,
    discoverer: Callable[..., tuple[list[Candidate], list[dict[str, Any]], list[dict[str, Any]], set[str], set[str]]] = discover_candidates,
    publisher_probe: Callable[..., tuple[list[tuple[Candidate, dict[str, Any]]], list[dict[str, Any]], list[str]]] = probe_publisher_pages,
) -> dict[str, Any]:
    streams = load_yaml(root / "config" / "streams.yml")
    scoring = load_yaml(root / "config" / "scoring.yml")
    output = load_yaml(root / "config" / "output.yml")
    deployment = load_yaml(root / "config" / "deployment.yml")
    timezone_name = str(output.get("window", {}).get("timezone", DEFAULT_TIMEZONE))
    timezone = ZoneInfo(timezone_name)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone)
    else:
        end_at = end_at.astimezone(timezone)
    window_hours = int(output.get("window", {}).get("rolling_hours", 72))
    start = end_at - timedelta(hours=window_hours)
    publisher_config = dict(deployment.get("publisher_output", {}))
    if publisher_target_min is not None:
        publisher_config["target_min_per_run"] = publisher_target_min
    if publisher_hard_max is not None:
        publisher_config["hard_max_per_run"] = publisher_hard_max
    publisher_min = int(publisher_config.get("target_min_per_run", 10))
    publisher_max = int(publisher_config.get("hard_max_per_run", 15))
    session = session or requests.Session()
    discovered, queries, source_access, searched_sources, unavailable_sources = discoverer(
        streams, scoring, start, end_at, session=session
    )
    requested_sources = {
        str(source)
        for stream in streams.get("streams", {}).values()
        for source in stream.get("sources", [])
    }
    prior_state, base_hash = load_prior_state(
        state_path,
        schema_path=root / "schemas" / "evidence-radar-state.schema.json",
    )
    history_status = (
        str(prior_state.get("history_status"))
        if prior_state and prior_state.get("history_status") in {"COMPLETE", "STATE_HISTORY_INCOMPLETE"}
        else "STATE_HISTORY_INCOMPLETE"
    )
    notified_event_ids = {
        str(item.get("event_id"))
        for item in (prior_state or {}).get("notified_events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    event_candidates: list[tuple[Candidate, dict[str, Any]]] = []
    for candidate in discovered:
        event = qualifying_event(candidate, start, end_at, timezone)
        if event is not None and _event_id(candidate.work_id, event) not in notified_event_ids:
            event_candidates.append((candidate, event))
    event_candidates.sort(key=lambda item: (-item[0].score, item[0].work_id))
    probe_input = [candidate for candidate, _event in event_candidates]
    successes, publisher_access, publisher_warnings = publisher_probe(
        probe_input,
        publisher_config,
        session=session,
        accessed_at=end_at,
    )
    event_by_work = {candidate.work_id: event for candidate, event in event_candidates}
    selected = [
        (candidate, event_by_work[candidate.work_id], access)
        for candidate, access in successes
        if candidate.work_id in event_by_work
    ]
    source_access.extend(publisher_access)
    if publisher_access:
        searched_sources.add("publisher")
    publisher_failures = [item for item in publisher_access if item["status"] != "SUCCESS"]
    if publisher_failures:
        unavailable_sources.add("publisher")
    adapter_sources = {"pubmed", "openalex"}
    unsupported = requested_sources - adapter_sources - {"publisher"}
    failed_adapters = (requested_sources & adapter_sources) - searched_sources
    unavailable_sources.update(unsupported)
    unavailable_sources.update(failed_adapters)
    if publisher_failures:
        coverage_status = "SOURCE_ACCESS_GAP"
    elif unavailable_sources:
        coverage_status = "PARTIAL_SOURCE_COVERAGE"
    else:
        coverage_status = "COMPLETE"
    if history_status == "STATE_HISTORY_INCOMPLETE":
        run_status = "STATE_HISTORY_INCOMPLETE"
    elif coverage_status == "SOURCE_ACCESS_GAP":
        run_status = "SOURCE_ACCESS_GAP"
    elif coverage_status == "PARTIAL_SOURCE_COVERAGE":
        run_status = "PARTIAL_SOURCE_COVERAGE"
    elif not selected:
        run_status = "NO_QUALIFYING_ITEMS"
    else:
        run_status = "PARTIAL_SOURCE_COVERAGE"
    warnings: list[dict[str, str]] = []
    if history_status == "STATE_HISTORY_INCOMPLETE":
        warnings.append(
            {
                "code": "STATE_HISTORY_INCOMPLETE",
                "message": "No complete canonical prior State was available; cross-run history is incomplete.",
                "severity": "WARNING",
            }
        )
    for message in publisher_warnings:
        warnings.append(
            {
                "code": "PUBLISHER_OUTPUT_BELOW_TARGET",
                "message": message,
                "severity": "WARNING",
            }
        )
    if unsupported:
        warnings.append(
            {
                "code": "AUTOMATED_SOURCE_ADAPTER_GAP",
                "message": "No GitHub adapter ran for: " + ", ".join(sorted(unsupported)),
                "severity": "WARNING",
            }
        )
    if failed_adapters:
        warnings.append(
            {
                "code": "SOURCE_ADAPTER_FAILED",
                "message": "Configured GitHub adapter did not complete for: "
                + ", ".join(sorted(failed_adapters)),
                "severity": "WARNING",
            }
        )
    if selected:
        warnings.append(
            {
                "code": "AUTOMATED_CLAIM_REVIEW_REQUIRED",
                "message": "Source pages and publication events were recorded, but substantive claims require ChatGPT Work or human review.",
                "severity": "INFO",
            }
        )
    commit = _protocol_commit(root, protocol_commit)
    finished_at = max(datetime.now(timezone), end_at)
    state = build_state(
        prior_state,
        selected,
        generated_at=finished_at,
        run_id=run_id,
        execution_lane=execution_lane,
        protocol_commit=commit,
        base_state_sha256=base_hash,
    )
    evidence = build_evidence(
        selected,
        generated_at=finished_at,
        run_id=run_id,
        requested_sources=requested_sources,
        searched_sources=searched_sources,
        unavailable_sources=unavailable_sources,
        coverage_status=coverage_status,
    )
    run = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_Run",
        "run_id": run_id,
        "started_at": end_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "mode": "daily",
        "window": {"start": start.isoformat(), "end": end_at.isoformat(), "hours": window_hours},
        "run_status": run_status,
        "history_status": history_status,
        "coverage_status": coverage_status,
        "queries": queries,
        "source_access": source_access,
        "counts": {
            "queries": len(queries),
            "raw_candidates": len(discovered),
            "deduplicated_candidates": len(discovered),
            "verified_works": 0,
            "claims": 0,
            "notified_events": len(selected),
        },
        "artifacts": {
            "report_html": ARTIFACT_NAMES[0],
            "state_json": ARTIFACT_NAMES[1],
            "evidence_json": ARTIFACT_NAMES[2],
            "run_json": ARTIFACT_NAMES[3],
        },
        "warnings": warnings,
        "execution_lane": execution_lane,
        "protocol_commit": commit,
        "base_state_sha256": base_hash,
        "parent_run_ids": (
            [str(prior_state.get("last_run_id"))]
            if prior_state and prior_state.get("last_run_id")
            else []
        ),
        "notes": [
            "GitHub Actions performs automated discovery and source-access auditing; it does not promote unreviewed scientific claims."
        ],
    }
    documents = {
        "EvidenceRadar_State.json": state,
        "EvidenceRadar_Evidence.json": evidence,
        "EvidenceRadar_Run.json": run,
    }
    validate_documents(root, documents)
    report = render_report(
        selected,
        generated_at=finished_at,
        start=start,
        end=end_at,
        run_status=run_status,
        coverage_status=coverage_status,
        warnings=warnings,
        publisher_min=publisher_min,
        publisher_max=publisher_max,
    )
    write_bundle(output_dir, report, documents)
    immutable_output: Path | None = None
    if runs_dir is not None:
        safe_run_id = re.sub(r"[^A-Za-z0-9._+-]", "-", run_id).strip(".-")
        if not safe_run_id:
            raise RadarRuntimeError("run_id does not contain a safe path component")
        immutable_output = runs_dir / safe_run_id
        if immutable_output.exists():
            raise RadarRuntimeError(f"immutable run directory already exists: {immutable_output}")
        write_bundle(immutable_output, report, documents)
    # Advance canonical state last, after every other artifact validates and
    # all requested bundles have been written successfully.
    if state_path.resolve() != (output_dir / "EvidenceRadar_State.json").resolve():
        write_state_atomic(state_path, state)
    return {
        "run_id": run_id,
        "run_status": run_status,
        "coverage_status": coverage_status,
        "output_dir": str(output_dir),
        **({"immutable_output_dir": str(immutable_output)} if immutable_output else {}),
        "publisher_output": len(selected),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path)
    parser.add_argument("--end-at", type=datetime.fromisoformat)
    parser.add_argument("--run-id")
    parser.add_argument("--execution-lane", choices=("github_actions",), default="github_actions")
    parser.add_argument("--publisher-target-min", type=int)
    parser.add_argument("--publisher-hard-max", type=int)
    parser.add_argument("--protocol-commit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    timezone_name = str(load_yaml(root / "config" / "output.yml").get("window", {}).get("timezone", DEFAULT_TIMEZONE))
    timezone = ZoneInfo(timezone_name)
    end_at = args.end_at or datetime.now(timezone)
    run_id = args.run_id or (
        "github-actions-" + end_at.astimezone(timezone).strftime("%Y%m%dT%H%M%S%z")
    )
    try:
        summary = execute(
            root=root,
            output_dir=args.output_dir.resolve(),
            state_path=args.state.resolve(),
            runs_dir=args.runs_dir.resolve() if args.runs_dir else None,
            end_at=end_at,
            run_id=run_id,
            execution_lane=args.execution_lane,
            publisher_target_min=args.publisher_target_min,
            publisher_hard_max=args.publisher_hard_max,
            protocol_commit=args.protocol_commit,
        )
    except (RadarRuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
