#!/usr/bin/env python3
"""EvidenceRadar v0.1.

Fetch recent records from PubMed and OpenAlex, enrich shortlisted biomedical
records with Europe PMC, rank them, and write a Markdown daily radar.

This is a discovery/triage pipeline. Scores are not substitutes for full-text
critical appraisal.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time as time_module
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import yaml
from defusedxml import ElementTree as ET

from . import events

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPENALEX_WORKS = "https://api.openalex.org/works"
USER_AGENT = "EvidenceRadar/0.1 (+https://github.com/hoiyu915-droid/EvidenceRadar)"
TIMEZONE = "Asia/Tokyo"
RUN_CONTEXT: dict[str, Any] = {}

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass
class Paper:
    title: str
    abstract: str
    authors: list[str]
    journal_or_venue: str
    publication_date: str
    stream: str
    source: str
    study_design: str = "Other"
    evidence_tier: str = "Other/U"
    evidence_score: int = 40
    relevance_score: int = 0
    interest_score: int = 0
    practical_score: int = 0
    total_score: float = 0.0
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    openalex_id: str = ""
    open_access: bool | None = None
    publication_types: list[str] = field(default_factory=list)
    secondary_streams: list[str] = field(default_factory=list)
    one_line_reason: str = ""
    main_caveat: str = ""
    is_preprint: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    qualifying_events: list[dict[str, Any]] = field(default_factory=list)
    fulltext_urls: list[str] = field(default_factory=list)
    repository_versions: list[str] = field(default_factory=list)

    def identity_key(self) -> str:
        if self.doi:
            return f"doi:{normalize_doi(self.doi)}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id.rsplit('/', 1)[-1]}"
        normalized_title = normalize_title(self.title)
        # Preserve legacy IDs; this digest labels normalized titles and is not
        # used as a security primitive.
        return "title:" + hashlib.sha1(
            normalized_title.encode("utf-8"), usedforsecurity=False
        ).hexdigest()

    def all_streams(self) -> list[str]:
        return [self.stream, *[s for s in self.secondary_streams if s != self.stream]]


class RadarError(RuntimeError):
    """Expected operational failure with a readable message."""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RadarError(f"Config must be a mapping: {path}")
    return data


def compact_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_doi(value: str | None) -> str:
    doi = compact_whitespace(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(" .")


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def text_from_element(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return compact_whitespace("".join(element.itertext()))


def parse_pubmed_date(article: ET.Element) -> str:
    article_date = article.find(".//Article/ArticleDate")
    if article_date is not None:
        year = text_from_element(article_date.find("Year"))
        month = text_from_element(article_date.find("Month")) or "1"
        day = text_from_element(article_date.find("Day")) or "1"
        if year:
            return safe_iso_date(year, month, day)

    pub_date = article.find(".//JournalIssue/PubDate")
    if pub_date is not None:
        year = text_from_element(pub_date.find("Year"))
        month = text_from_element(pub_date.find("Month")) or "1"
        day = text_from_element(pub_date.find("Day")) or "1"
        if year:
            return safe_iso_date(year, month, day)
        medline = text_from_element(pub_date.find("MedlineDate"))
        match = re.search(r"(19|20)\d{2}", medline)
        if match:
            return f"{match.group(0)}-01-01"
    return ""


def safe_iso_date(year: str, month: str, day: str) -> str:
    try:
        year_i = int(year)
        month_value = month.strip().lower()[:3]
        month_i = MONTHS.get(month_value, int(month) if month.isdigit() else 1)
        day_i = int(day) if day.isdigit() else 1
        return date(year_i, month_i, day_i).isoformat()
    except (TypeError, ValueError):
        return f"{year}-01-01" if year else ""


def request(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 40,
    attempts: int = 3,
) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.9, */*;q=0.8"}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time_module.sleep(2 ** (attempt - 1))
    raise RadarError(f"Request failed after {attempts} attempts: {url}: {last_error}")


def pubmed_common_params() -> dict[str, str]:
    params = {"tool": "EvidenceRadar"}
    email = os.getenv("NCBI_EMAIL", "").strip()
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_pubmed(
    query: str,
    stream: str,
    start_date: date,
    end_date: date,
    max_results: int,
) -> list[Paper]:
    ids: list[str] = []
    # pdat catches new publications; edat/mdat catch newly indexed records and
    # old records whose PMC/full-text state changed inside the same window.
    for date_type in ("pdat", "edat", "mdat"):
        search_params: dict[str, Any] = {
            **pubmed_common_params(),
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "pub date",
            "datetype": date_type,
            "mindate": start_date.strftime("%Y/%m/%d"),
            "maxdate": end_date.strftime("%Y/%m/%d"),
        }
        search = request(f"{PUBMED_BASE}/esearch.fcgi", params=search_params).json()
        ids.extend(search.get("esearchresult", {}).get("idlist", []))
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []

    fetch_params: dict[str, Any] = {
        **pubmed_common_params(),
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
    }
    xml_text = request(f"{PUBMED_BASE}/efetch.fcgi", params=fetch_params).text
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []

    for item in root.findall(".//PubmedArticle"):
        article = item.find("MedlineCitation/Article")
        citation = item.find("MedlineCitation")
        if article is None or citation is None:
            continue

        title = text_from_element(article.find("ArticleTitle"))
        if not title:
            continue
        abstract_parts = [text_from_element(node) for node in article.findall("Abstract/AbstractText")]
        abstract = compact_whitespace(" ".join(part for part in abstract_parts if part))
        authors: list[str] = []
        for author in article.findall("AuthorList/Author"):
            collective = text_from_element(author.find("CollectiveName"))
            if collective:
                authors.append(collective)
                continue
            last = text_from_element(author.find("LastName"))
            initials = text_from_element(author.find("Initials"))
            name = compact_whitespace(f"{last} {initials}")
            if name:
                authors.append(name)

        journal = text_from_element(article.find("Journal/Title"))
        pmid = text_from_element(citation.find("PMID"))
        publication_types = [
            text_from_element(node) for node in article.findall("PublicationTypeList/PublicationType")
        ]
        identifiers: dict[str, str] = {}
        for node in item.findall("PubmedData/ArticleIdList/ArticleId"):
            id_type = (node.attrib.get("IdType") or "").lower()
            value = text_from_element(node)
            if id_type and value:
                identifiers[id_type] = value

        paper = Paper(
            title=title,
            abstract=abstract,
            authors=authors,
            journal_or_venue=journal,
            publication_date=parse_pubmed_date(item),
            stream=stream,
            source="PubMed",
            doi=normalize_doi(identifiers.get("doi", "")),
            pmid=pmid,
            pmcid=identifiers.get("pmc", ""),
            publication_types=publication_types,
        )
        article_date = item.find(".//Article/ArticleDate")
        if article_date is not None:
            value = safe_iso_date(
                text_from_element(article_date.find("Year")),
                text_from_element(article_date.find("Month")) or "1",
                text_from_element(article_date.find("Day")) or "1",
            )
            events.add_event(
                paper, "version_of_record_first_online", value,
                source="PubMed", source_field="Article/ArticleDate",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                confidence="publisher_supplied_citation",
            )
        for history_date in item.findall("PubmedData/History/PubMedPubDate"):
            status = (history_date.attrib.get("PubStatus") or "").casefold()
            value = safe_iso_date(
                text_from_element(history_date.find("Year")),
                text_from_element(history_date.find("Month")) or "1",
                text_from_element(history_date.find("Day")) or "1",
            )
            hour = text_from_element(history_date.find("Hour"))
            minute = text_from_element(history_date.find("Minute"))
            precision = "date"
            if value and hour.isdigit():
                value = f"{value}T{int(hour):02d}:{int(minute) if minute.isdigit() else 0:02d}:00+09:00"
                precision = "timestamp"
            if status in {"pubmed", "entrez"}:
                events.add_event(
                    paper, "first_formal_indexing", value,
                    source="PubMed", source_field=f"PubMedData/History[{status}]",
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                    precision=precision, confidence="registry_timestamp",
                )
            elif status in {"pmc-release", "pmcr"} and paper.pmcid:
                paper.open_access = True
                events.add_event(
                    paper, "oa_fulltext_first_available", value,
                    source="PubMed/PMC", source_field=f"PubMedData/History[{status}]",
                    url=f"https://pmc.ncbi.nlm.nih.gov/articles/{paper.pmcid}/",
                    precision=precision, confidence="repository_release_timestamp",
                )
        events.ensure_provider_event(paper)
        papers.append(paper)
    return papers


def reconstruct_openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        for index in indexes:
            positions.append((index, word))
    positions.sort(key=lambda pair: pair[0])
    return compact_whitespace(" ".join(word for _, word in positions))


def openalex_params(query: str, start_date: date, end_date: date, max_results: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "search": query,
        "filter": f"from_publication_date:{start_date.isoformat()},to_publication_date:{end_date.isoformat()}",
        "sort": "publication_date:desc",
        "per-page": min(max_results, 100),
        "select": (
            "id,doi,title,display_name,publication_date,type,authorships,"
            "primary_location,best_oa_location,open_access,abstract_inverted_index,ids"
        ),
    }
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_openalex(
    query: str,
    stream: str,
    start_date: date,
    end_date: date,
    max_results: int,
) -> list[Paper]:
    publication_params = openalex_params(query, start_date, end_date, max_results)
    payloads = [request(OPENALEX_WORKS, params=publication_params).json()]
    updated_params = dict(publication_params)
    updated_params["filter"] = (
        f"from_updated_date:{start_date.isoformat()},to_updated_date:{end_date.isoformat()}"
    )
    updated_params["sort"] = "updated_date:desc"
    try:
        payloads.append(request(OPENALEX_WORKS, params=updated_params, attempts=1).json())
    except (RadarError, ValueError):
        # Updated-date filtering is an over-fetch surface. Publication discovery
        # remains useful if an OpenAlex plan or transient limit rejects it.
        pass
    papers: list[Paper] = []
    seen_results: set[str] = set()
    for result in [item for payload in payloads for item in payload.get("results", [])]:
        result_key = compact_whitespace(result.get("id")) or normalize_doi(result.get("doi"))
        if result_key and result_key in seen_results:
            continue
        if result_key:
            seen_results.add(result_key)
        title = compact_whitespace(result.get("display_name") or result.get("title"))
        if not title:
            continue
        authors = [
            compact_whitespace(authorship.get("author", {}).get("display_name"))
            for authorship in result.get("authorships", [])
        ]
        authors = [author for author in authors if author]
        location = result.get("primary_location") or {}
        source = location.get("source") or {}
        ids = result.get("ids") or {}
        work_type = compact_whitespace(result.get("type"))
        openalex_id = compact_whitespace(result.get("id"))
        paper = Paper(
            title=title,
            abstract=reconstruct_openalex_abstract(result.get("abstract_inverted_index")),
            authors=authors,
            journal_or_venue=compact_whitespace(source.get("display_name")) or work_type,
            publication_date=compact_whitespace(result.get("publication_date")),
            stream=stream,
            source="OpenAlex",
            doi=normalize_doi(result.get("doi")),
            pmid=extract_terminal_id(ids.get("pmid", "")),
            pmcid=extract_terminal_id(ids.get("pmcid", "")),
            openalex_id=openalex_id,
            open_access=(result.get("open_access") or {}).get("is_oa"),
            publication_types=[work_type] if work_type else [],
            is_preprint=work_type.lower() in {"preprint", "posted-content"},
        )
        best_oa = result.get("best_oa_location") or {}
        version = compact_whitespace(best_oa.get("version"))
        if version:
            paper.repository_versions.append(version)
        for candidate in (best_oa.get("pdf_url"), best_oa.get("landing_page_url")):
            candidate = compact_whitespace(candidate)
            if candidate and candidate not in paper.fulltext_urls:
                paper.fulltext_urls.append(candidate)
        events.ensure_provider_event(paper)
        papers.append(paper)
    return papers


def extract_terminal_id(value: str | None) -> str:
    value = compact_whitespace(value)
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


def enrich_europe_pmc(paper: Paper) -> None:
    if not paper.pmid:
        return
    params = {
        "query": f"EXT_ID:{paper.pmid} AND SRC:MED",
        "format": "json",
        "pageSize": 1,
        "resultType": "core",
    }
    try:
        payload = request(EUROPE_PMC_SEARCH, params=params, attempts=2).json()
    except (RadarError, ValueError):
        return
    results = payload.get("resultList", {}).get("result", [])
    if not results:
        return
    result = results[0]
    paper.doi = paper.doi or normalize_doi(result.get("doi"))
    paper.pmcid = paper.pmcid or compact_whitespace(result.get("pmcid"))
    if result.get("isOpenAccess") in {"Y", "N"}:
        paper.open_access = result.get("isOpenAccess") == "Y"


def classify_study(paper: Paper) -> tuple[str, str, int]:
    types = " | ".join(paper.publication_types).lower()
    text = f"{paper.title} {paper.abstract}".lower()

    rules: list[tuple[bool, str, str, int]] = [
        ("meta-analysis" in types or "meta-analysis" in text or "meta analysis" in text, "Meta-analysis", "Meta/A", 95),
        ("systematic review" in types or "systematic review" in text, "Systematic review", "SR/A", 90),
        ("practice guideline" in types or "guideline" in types or "clinical guideline" in text, "Guideline", "Guideline/A", 92),
        ("consensus" in text or "consensus development conference" in types, "Consensus statement", "Stmt/B+", 78),
        ("randomized controlled trial" in types or re.search(r"\brandomi[sz]ed\b", text) is not None, "Randomized controlled trial", "RCT/A", 88),
        ("field experiment" in text or "field study" in text, "Field experiment", "Field/A", 82),
        ("longitudinal" in text or "prospective study" in types, "Longitudinal study", "Long/A", 78),
        ("cohort" in text, "Cohort study", "Cohort/B+", 72),
        ("crossover" in text or "cross-over" in text, "Crossover study", "Mechanistic/B+", 70),
        ("mechanistic" in text or "mechanism" in paper.title.lower(), "Mechanistic study", "Mechanistic/B+", 68),
        ("qualitative" in text or "thematic analysis" in text or "interview study" in text, "Qualitative study", "Qual/A", 65),
        ("cross-sectional" in text or "cross sectional" in text, "Cross-sectional study", "Cross/B", 52),
        ("survey" in text or "questionnaire" in text, "Survey", "Survey/B", 48),
        ("review" in types or paper.publication_types == ["review"], "Narrative review", "Rev/B", 60),
        (paper.is_preprint, "Preprint", "Preprint/U", 35),
    ]
    for matched, design, tier, score in rules:
        if matched:
            if paper.is_preprint and not tier.endswith("/U"):
                return design, f"{tier.split('/')[0]}/U", max(score - 20, 30)
            return design, tier, score
    return "Other", "Other/U", 40 if not paper.is_preprint else 30


def score_relevance(paper: Paper, relevance_terms: Iterable[str]) -> int:
    title = paper.title.lower()
    text = f"{paper.title} {paper.abstract}".lower()
    matched = {term.lower() for term in relevance_terms if term.lower() in text}
    title_matches = sum(1 for term in matched if term in title)
    base = 48 if matched else 42
    return min(100, base + len(matched) * 7 + title_matches * 4)


def extract_sample_size(text: str) -> int | None:
    candidates = re.findall(r"(?:\bn\s*=\s*|sample(?: of| size)?\s+)([0-9][0-9,]{1,8})", text, flags=re.I)
    sizes: list[int] = []
    for value in candidates:
        try:
            sizes.append(int(value.replace(",", "")))
        except ValueError:
            continue
    return max(sizes) if sizes else None


def score_interest(paper: Paper) -> int:
    text = f"{paper.title} {paper.abstract}".lower()
    score = 43
    signals = {
        "longitudinal": 10,
        "field experiment": 15,
        "real-world": 8,
        "real world": 8,
        "randomized": 7,
        "randomised": 7,
        "mechanistic": 8,
        "paradox": 12,
        "dose-response": 7,
        "digital trace": 10,
        "conversation logs": 12,
        "chat logs": 12,
        "ecological momentary": 10,
    }
    for signal, points in signals.items():
        if signal in text:
            score += points
    if paper.stream == "llm_social":
        for signal in ("companion", "attachment", "intimacy", "loneliness", "self-disclosure", "dependency"):
            if signal in text:
                score += 5
    sample_size = extract_sample_size(text)
    if sample_size is not None:
        if sample_size >= 10000:
            score += 15
        elif sample_size >= 1000:
            score += 10
        elif sample_size >= 200:
            score += 5
    if paper.evidence_score >= 85:
        score += 5
    return min(100, score)


def score_practical(paper: Paper) -> int:
    text = f"{paper.title} {paper.abstract}".lower()
    score = 40
    if paper.study_design in {"Guideline", "Consensus statement", "Meta-analysis", "Systematic review"}:
        score += 25
    if paper.study_design in {"Randomized controlled trial", "Field experiment", "Longitudinal study"}:
        score += 18
    for signal in ("performance", "mortality", "injury", "recovery", "clinical", "health", "training", "nutrition"):
        if signal in text:
            score += 4
    return min(100, score)


def build_reason(paper: Paper) -> str:
    reasons = [paper.study_design]
    if paper.open_access is True:
        reasons.append("可取得 OA 全文")
    if paper.evidence_score >= 85:
        reasons.append("證據設計優先")
    if paper.interest_score >= 85:
        reasons.append("新穎／反直覺訊號高")
    if paper.stream == "llm_social":
        reasons.append("命中 LLM 伴侶或社會影響主題")
    elif paper.relevance_score >= 75:
        reasons.append("與核心運動／體適能研究線高度相關")
    return "；".join(dict.fromkeys(reasons))


def build_caveat(paper: Paper) -> str:
    if paper.is_preprint:
        return "Preprint，尚未通過同儕審查；結論只作訊號追蹤。"
    if paper.stream == "llm_social" and paper.study_design in {"Survey", "Cross-sectional study", "Qualitative study"}:
        return "須優先檢查自我選擇、平台特異性、自陳偏差與因果方向。"
    caveats = {
        "Meta-analysis": "尚未核對納入研究品質、異質性、發表偏差與模型選擇。",
        "Systematic review": "尚未核對搜尋完整性、風險偏差評估與是否能支持因果結論。",
        "Randomized controlled trial": "尚未核對隨機化、盲法、流失、預註冊與主要終點切換。",
        "Cohort study": "殘餘混雜、反向因果與暴露測量誤差仍需全文審核。",
        "Longitudinal study": "時間序列較強，但仍須檢查流失偏差、基線差異與未測混雜。",
        "Cross-sectional study": "無法確立時間順序，方向性與選擇偏差是主要限制。",
        "Survey": "抽樣框、自陳測量與非回應偏差可能主導結果。",
        "Qualitative study": "可提供機制與經驗結構，但不可直接外推盛行率或效果量。",
    }
    return caveats.get(paper.study_design, "自動初篩結果；尚未完成全文、校正／撤稿與引用鏈核實。")


def apply_scores(paper: Paper, relevance_terms: Iterable[str], weights: dict[str, float]) -> None:
    design, tier, evidence = classify_study(paper)
    paper.study_design = design
    paper.evidence_tier = tier
    paper.evidence_score = evidence
    paper.relevance_score = score_relevance(paper, relevance_terms)
    paper.interest_score = score_interest(paper)
    paper.practical_score = score_practical(paper)
    paper.total_score = round(
        evidence * float(weights.get("evidence_quality", 0.4))
        + paper.relevance_score * float(weights.get("personal_relevance", 0.3))
        + paper.interest_score * float(weights.get("novelty_interest", 0.2))
        + paper.practical_score * float(weights.get("practical_impact", 0.1)),
        1,
    )
    paper.one_line_reason = build_reason(paper)
    paper.main_caveat = build_caveat(paper)


def deduplicate(papers: Iterable[Paper]) -> list[Paper]:
    deduped: dict[str, Paper] = {}
    title_keys: dict[str, str] = {}
    for paper in papers:
        key = paper.identity_key()
        title_key = normalize_title(paper.title)
        existing_key = title_keys.get(title_key, key)
        existing = deduped.get(existing_key)
        if existing is None:
            deduped[key] = paper
            title_keys[title_key] = key
            continue

        winner, loser = (paper, existing) if paper.total_score > existing.total_score else (existing, paper)
        merged_streams = list(dict.fromkeys(existing.all_streams() + paper.all_streams()))
        winner.stream = merged_streams[0]
        winner.secondary_streams = merged_streams[1:]
        winner.doi = winner.doi or loser.doi
        winner.pmid = winner.pmid or loser.pmid
        winner.pmcid = winner.pmcid or loser.pmcid
        winner.openalex_id = winner.openalex_id or loser.openalex_id
        if winner.open_access is None:
            winner.open_access = loser.open_access
        events.merge_paper_events(winner, loser)
        winner.repository_versions = list(
            dict.fromkeys([*winner.repository_versions, *loser.repository_versions])
        )
        deduped.pop(existing_key, None)
        deduped[key] = winner
        title_keys[title_key] = key
    return list(deduped.values())


def select_candidate_pool(papers: list[Paper], scoring: dict[str, Any]) -> list[Paper]:
    selection = scoring["selection"]
    min_score = float(selection.get("candidate_min_score", 45))
    hard_max = int(selection.get("candidate_hard_max", 30))
    stream_limits = {key: int(value) for key, value in scoring.get("stream_limits", {}).items()}
    counts: dict[str, int] = {}
    selected: list[Paper] = []

    for paper in sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True):
        if paper.total_score < min_score:
            continue
        stream_limit = stream_limits.get(paper.stream, hard_max)
        if counts.get(paper.stream, 0) >= stream_limit:
            continue
        selected.append(paper)
        counts[paper.stream] = counts.get(paper.stream, 0) + 1
        if len(selected) >= hard_max:
            break
    return selected


def feature_section(paper: Paper, scoring: dict[str, Any]) -> str | None:
    rules = scoring.get("rules", {})
    if paper.evidence_score >= int(rules.get("anchor_min_evidence", 75)):
        return "anchor"
    if (
        paper.evidence_score >= int(rules.get("strong_min_evidence", 55))
        and paper.relevance_score >= int(rules.get("strong_min_relevance", 60))
    ):
        return "strong_watch"
    if paper.interest_score >= int(rules.get("weird_min_interest", 85)):
        return "weird_but_important"
    return None


def select_featured(candidate_pool: list[Paper], scoring: dict[str, Any]) -> dict[str, list[Paper]]:
    selection = scoring["selection"]
    hard_max = int(selection.get("featured_hard_max", 8))
    caps = selection.get("section_caps", {})
    stream_cap = max(1, hard_max // 2)
    sections: dict[str, list[Paper]] = {"anchor": [], "strong_watch": [], "weird_but_important": []}
    stream_counts: dict[str, int] = {}
    total = 0

    for paper in candidate_pool:
        if total >= hard_max:
            break
        section = feature_section(paper, scoring)
        if section is None:
            continue
        if len(sections[section]) >= int(caps.get(section, hard_max)):
            continue
        if stream_counts.get(paper.stream, 0) >= stream_cap:
            continue
        sections[section].append(paper)
        stream_counts[paper.stream] = stream_counts.get(paper.stream, 0) + 1
        total += 1
    return sections


def primary_url(paper: Paper) -> str:
    if paper.doi:
        return f"https://doi.org/{quote(normalize_doi(paper.doi), safe='/()')}"
    if paper.pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/"
    if paper.openalex_id:
        return paper.openalex_id
    return ""


def truncate(value: str, limit: int = 440) -> str:
    value = compact_whitespace(value)
    if len(value) <= limit:
        return value
    shortened = value[:limit].rsplit(" ", 1)[0]
    return shortened + "…"


def ids_line(paper: Paper) -> str:
    values = []
    if paper.doi:
        values.append(f"DOI `{normalize_doi(paper.doi)}`")
    if paper.pmid:
        values.append(f"PMID `{paper.pmid}`")
    if paper.pmcid:
        values.append(f"PMCID `{paper.pmcid}`")
    if paper.openalex_id:
        values.append(f"OpenAlex `{paper.openalex_id.rsplit('/', 1)[-1]}`")
    return " · ".join(values) if values else "—"


def event_line(paper: Paper) -> str:
    event = events.display_event(paper)
    if not event:
        return "—"
    evidence = f"{event.get('source', '—')} / {event.get('source_field', '—')}"
    if event.get("url"):
        evidence = f"[{evidence}]({event['url']})"
    return (
        f"{event.get('label')} · `{event.get('occurred_at')}` · {evidence} · "
        f"`{event.get('precision')}` / `{event.get('confidence')}`"
    )


def render_featured_item(paper: Paper, rank: int) -> str:
    url = primary_url(paper)
    title = f"[{paper.title}]({url})" if url else paper.title
    oa = "OA" if paper.open_access is True else "非 OA／未確認"
    streams = ", ".join(paper.all_streams())
    authors = ", ".join(paper.authors[:6]) + (" et al." if len(paper.authors) > 6 else "")
    abstract_signal = truncate(paper.abstract) if paper.abstract else "無摘要；需直接開原文判讀。"
    return "\n".join(
        [
            f"#### {rank}. {title}",
            "",
            f"- **Tags:** `[{paper.evidence_tier}]` `[{streams}]` `[{paper.study_design}]` `{oa}`",
            f"- **Source:** {paper.journal_or_venue or '—'} · {paper.publication_date or '日期未確認'}",
            f"- **Trigger event:** {event_line(paper)}",
            f"- **Authors:** {authors or '—'}",
            f"- **Why flagged:** {paper.one_line_reason}",
            f"- **Abstract signal:** {abstract_signal}",
            f"- **Main caveat:** {paper.main_caveat}",
            (
                f"- **Scores:** total `{paper.total_score:.1f}` · evidence `{paper.evidence_score}` · "
                f"relevance `{paper.relevance_score}` · interest `{paper.interest_score}` · practical `{paper.practical_score}`"
            ),
            f"- **IDs:** {ids_line(paper)}",
        ]
    )


def render_candidate_item(paper: Paper, rank: int) -> str:
    url = primary_url(paper)
    title = f"[{paper.title}]({url})" if url else paper.title
    oa = "OA" if paper.open_access is True else "OA?"
    streams = ", ".join(paper.all_streams())
    return "\n".join(
        [
            f"{rank}. **{title}**",
            f"   - `[{paper.evidence_tier}]` `[{streams}]` `[{paper.study_design}]` `{oa}` · score `{paper.total_score:.1f}`",
            f"   - {paper.journal_or_venue or '—'} · {paper.publication_date or '日期未確認'}",
            f"   - Event: {event_line(paper)}",
            f"   - {paper.one_line_reason}",
            f"   - {ids_line(paper)}",
        ]
    )


def render_markdown(
    generated_at: datetime,
    featured: dict[str, list[Paper]],
    candidate_pool: list[Paper],
    retrieved_count: int,
    deduplicated_count: int,
    excluded_count: int,
    warnings: list[str],
) -> str:
    featured_ids = {paper.identity_key() for papers in featured.values() for paper in papers}
    remaining_candidates = [paper for paper in candidate_pool if paper.identity_key() not in featured_ids]
    featured_count = len(featured_ids)
    lines = [
        f"# Evidence Radar — {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Generated: `{generated_at.isoformat()}`",
        f"- Timezone: `{TIMEZONE}`",
        f"- Featured: `{featured_count}`",
        f"- Candidate Pool: `{len(candidate_pool)}`（含 Featured；下方列出其餘 `{len(remaining_candidates)}` 篇）",
        "- Status: `AUTO-TRIAGE` — 尚未完成全文與引用核實",
        "",
        "## Featured",
        "",
        "> 每日精選最多 8 篇。證據強度與有趣程度分開評分；不得為湊數降低門檻。",
        "",
    ]

    section_titles = {
        "anchor": "### Anchor Evidence",
        "strong_watch": "### Strong Watch",
        "weird_but_important": "### Weird but Important",
    }
    rank = 1
    for section_name in ("anchor", "strong_watch", "weird_but_important"):
        lines.extend([section_titles[section_name], ""])
        papers = featured[section_name]
        if not papers:
            lines.extend(["_本次沒有符合門檻的研究。_", ""])
            continue
        for paper in papers:
            lines.extend([render_featured_item(paper, rank), ""])
            rank += 1

    lines.extend(
        [
            "## Candidate Pool",
            "",
            "> 高召回率候選池，總數最多 30 篇。以下不重複列出 Featured；入池不代表通過完整證據審核。",
            "",
        ]
    )
    if remaining_candidates:
        for index, paper in enumerate(remaining_candidates, 1):
            lines.extend([render_candidate_item(paper, index), ""])
    else:
        lines.extend(["_沒有額外候選。_", ""])

    lines.extend(
        [
            "## Run Notes",
            "",
            f"- Retrieved: `{retrieved_count}`",
            f"- Deduplicated: `{deduplicated_count}`",
            f"- Excluded before Candidate Pool: `{excluded_count}`",
            f"- Warnings: {'；'.join(warnings) if warnings else 'None'}",
            "",
            "## Interpretation Guardrail",
            "",
            "> 本檔是發現與分流層，不是最終證據審核。Meta／RCT 等標籤來自 metadata、publication type 與摘要規則判定；正式引用前仍須完成 DOI/PMID 存在性、全文、校正／撤稿、方法與斷言核對。",
            "",
        ]
    )
    return "\n".join(lines)


def write_raw_snapshot(path: Path, papers: list[Paper]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump([asdict(paper) for paper in papers], handle, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Markdown and HTML evidence radars.")
    parser.add_argument("--streams", type=Path, default=Path("config/streams.yml"))
    parser.add_argument("--scoring", type=Path, default=Path("config/scoring.yml"))
    parser.add_argument("--output-dir", type=Path, default=Path("daily"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--window-hours", type=int, default=72)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-at", type=datetime.fromisoformat, default=None, help="ISO-8601 timestamp")
    parser.add_argument("--no-raw", action="store_true", help="Do not write JSON raw snapshot.")
    return parser.parse_args()


def fetch_query_groups(
    tasks: list[tuple[int, str, str, str]],
    start_date: date,
    end_date: date,
    per_query: int,
) -> tuple[dict[int, list[Paper]], list[tuple[int, str]]]:
    """Fetch independent queries concurrently without changing result order.

    PubMed stays serial to respect its unauthenticated rate limit. OpenAlex
    queries use bounded concurrency because they are independent and dominate
    the daily scan's wall-clock time.
    """
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for index, source, stream, query in tasks:
        grouped.setdefault(source, []).append((index, stream, query))

    def fetch_group(
        source: str, source_tasks: list[tuple[int, str, str]]
    ) -> tuple[dict[int, list[Paper]], list[tuple[int, str]]]:
        fetcher = fetch_pubmed if source == "pubmed" else fetch_openalex
        max_workers = 1 if source == "pubmed" else min(4, len(source_tasks))
        results: dict[int, list[Paper]] = {}
        failures: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetcher, query, stream, start_date, end_date, per_query): (
                    index,
                    stream,
                )
                for index, stream, query in source_tasks
            }
            for future in as_completed(futures):
                index, stream = futures[future]
                try:
                    results[index] = future.result()
                except (RadarError, ValueError, ET.ParseError) as exc:
                    failures.append((index, f"{source}/{stream}: {exc}"))
        return results, failures

    results: dict[int, list[Paper]] = {}
    failures: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(grouped))) as executor:
        futures = {
            executor.submit(fetch_group, source, source_tasks): source
            for source, source_tasks in grouped.items()
        }
        for future in as_completed(futures):
            group_results, group_failures = future.result()
            results.update(group_results)
            failures.extend(group_failures)
    return results, sorted(failures)


def main() -> int:
    args = parse_args()
    generated_at = args.end_at or datetime.now(ZoneInfo(TIMEZONE))
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=ZoneInfo(TIMEZONE))
    else:
        generated_at = generated_at.astimezone(ZoneInfo(TIMEZONE))
    if args.end_date and not args.end_at:
        generated_at = datetime.combine(args.end_date, time.max, ZoneInfo(TIMEZONE))
    end_date = args.end_date or generated_at.date()

    streams_config = load_yaml(args.streams)
    scoring_config = load_yaml(args.scoring)
    window_hours = int(args.window_hours)
    if window_hours <= 0:
        raise RadarError("--window-hours must be positive")
    start_date, end_date, cutoff = events.date_search_bounds(generated_at, window_hours)
    if args.lookback_days is not None:
        start_date = end_date - timedelta(days=max(args.lookback_days - 1, 0))
    per_query = int(streams_config.get("per_query", 60))
    stream_defs = streams_config.get("streams", {})
    weights = scoring_config.get("weights", {})

    papers: list[Paper] = []
    warnings: list[str] = []
    relevance_lookup: dict[str, list[str]] = {}

    query_tasks: list[tuple[int, str, str, str]] = []
    for stream, config in stream_defs.items():
        relevance_lookup[stream] = list(config.get("relevance_terms", []))
        for source in config.get("sources", []):
            for query in config.get("queries", []):
                if source in {"pubmed", "openalex"}:
                    query_tasks.append((len(query_tasks), source, stream, query))
                else:
                    warnings.append(f"Unknown source `{source}` for `{stream}`")

    fetched, fetch_warnings = fetch_query_groups(
        query_tasks, start_date, end_date, per_query
    )
    for index in range(len(query_tasks)):
        papers.extend(fetched.get(index, []))
    warnings.extend(message for _, message in fetch_warnings)

    retrieved_count = len(papers)
    for paper in papers:
        events.ensure_provider_event(paper)
        apply_scores(paper, relevance_lookup.get(paper.stream, []), weights)

    unique_papers = deduplicate(papers)
    deduplicated_count = len(unique_papers)
    event_papers = events.filter_window(unique_papers, cutoff, generated_at)
    event_qualified_count = len(event_papers)
    RUN_CONTEXT.clear()
    RUN_CONTEXT.update(
        {
            "window_hours": window_hours,
            "cutoff": cutoff.isoformat(),
            "end_at": generated_at.isoformat(),
            "retrieved_count": retrieved_count,
            "new_after_history_dedup": deduplicated_count,
            "event_qualified_count": event_qualified_count,
        }
    )
    candidate_pool = select_candidate_pool(event_papers, scoring_config)

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(candidate_pool)))) as executor:
        list(executor.map(enrich_europe_pmc, candidate_pool))
    for paper in candidate_pool:
        paper.one_line_reason = build_reason(paper)

    featured = select_featured(candidate_pool, scoring_config)
    excluded_count = max(deduplicated_count - len(candidate_pool), 0)
    markdown = render_markdown(
        generated_at,
        featured,
        candidate_pool,
        retrieved_count,
        deduplicated_count,
        excluded_count,
        warnings,
    )
    window_note = "\n".join(
        [
            "## Verified Event Window",
            "",
            f"- Rolling window: `{window_hours}` hours",
            f"- Cutoff: `{cutoff.isoformat()}`",
            f"- Event-qualified new works: `{event_qualified_count}`",
            "- Date-only evidence on the cutoff calendar day is excluded as boundary-ambiguous.",
            "",
        ]
    )
    markdown += "\n" + window_note

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = generated_at.strftime("%Y%m%d %H%M.Rader")
    output_path = args.output_dir / f"{stem}.md"
    output_path.write_text(markdown, encoding="utf-8")
    from . import categories as category_module
    from .html_report import render_html

    html_path = args.output_dir / f"{stem}.html"
    html_document = render_html(
        generated_at,
        featured,
        candidate_pool,
        category_order=category_module.CATEGORY_ORDER,
        category_titles=category_module.CATEGORY_TITLES,
        window_hours=window_hours,
        cutoff=cutoff,
        retrieved_count=retrieved_count,
        event_qualified_count=event_qualified_count,
        warnings=warnings,
    )
    html_path.write_text(html_document, encoding="utf-8")

    # Stable, space-free alias used for the user-facing rendered preview.
    # The timestamped HTML remains the immutable archival report.
    preview_path = args.output_dir / "EvidenceRadar_latest.html"
    preview_path.write_text(html_document, encoding="utf-8")

    if not args.no_raw:
        write_raw_snapshot(args.raw_dir / f"{stem}.json", unique_papers)

    print(output_path)
    print(html_path)
    print(preview_path)
    if warnings:
        print("Warnings:", *warnings, sep="\n- ", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
