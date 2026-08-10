from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin


ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
PRISM_1 = "{http://prismstandard.org/namespaces/1.2/basic/}"
PRISM_2 = "{http://prismstandard.org/namespaces/basic/2.0/}"
QUERY_STOPWORDS = {"and", "or", "not", "the", "a", "an", "of", "to", "for", "in", "on"}


class PublisherFeedError(RuntimeError):
    pass


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape("".join(node.itertext()))).strip()


def _strip_markup(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _normalized_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _in_window(value: str, start_date: date, end_date: date) -> bool:
    try:
        observed = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return start_date <= observed <= end_date


def _matches_query(text: str, query: str) -> bool:
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query or query == "*":
        return True
    haystack = _strip_markup(text).casefold()
    quoted = [part.casefold().strip() for part in re.findall(r'"([^\"]{2,})"', query)]
    if quoted and any(part in haystack for part in quoted):
        return True
    tokens = [
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.casefold())
        if token not in QUERY_STOPWORDS
    ]
    return not tokens or any(token.rstrip("*") in haystack for token in tokens)


def _first_text(parent: ET.Element, paths: list[str]) -> str:
    for path in paths:
        value = _text(parent.find(path))
        if value:
            return value
    return ""


def _first_local(parent: ET.Element, names: list[str]) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(parent):
        local = child.tag.rsplit("}", 1)[-1].casefold()
        if local in wanted:
            value = _text(child)
            if value:
                return value
    return ""


def _doi(parent: ET.Element) -> str:
    value = _first_text(parent, [f"{PRISM_2}doi", f"{PRISM_1}doi", f"{DC}identifier"])
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value, flags=re.IGNORECASE)
    return match.group(0).rstrip(" .") if match else ""


def parse_feed(text: str, *, feed_url: str, source_id: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PublisherFeedError(f"invalid XML from {source_id}: {exc}") from exc

    records: list[dict[str, Any]] = []
    root_name = root.tag.rsplit("}", 1)[-1].casefold()
    if root_name == "feed":
        for entry in root.findall(f"{ATOM}entry"):
            title = _text(entry.find(f"{ATOM}title"))
            if not title:
                continue
            link = ""
            for node in entry.findall(f"{ATOM}link"):
                if (node.attrib.get("rel") or "alternate") == "alternate" and node.attrib.get("href"):
                    link = str(node.attrib["href"])
                    break
            published_raw = _first_text(entry, [f"{ATOM}published", f"{ATOM}updated"])
            records.append({
                "title": title,
                "landing_url": urljoin(feed_url, link),
                "publication_date": _normalized_date(published_raw),
                "published_raw": published_raw,
                "summary": _strip_markup(_first_text(entry, [f"{ATOM}summary", f"{ATOM}content"])),
                "authors": [_text(node.find(f"{ATOM}name")) for node in entry.findall(f"{ATOM}author") if _text(node.find(f"{ATOM}name"))],
                "venue": "",
                "doi": _doi(entry),
                "source_field": "atom:published" if entry.find(f"{ATOM}published") is not None else "atom:updated",
                "feed_url": feed_url,
                "source_id": source_id,
            })
        return records

    # RSS 2.0 and RDF/RSS 1.0 both expose item nodes with local name 'item'.
    items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].casefold() == "item"]
    if not items:
        raise PublisherFeedError(f"feed {source_id} contains no recognizable entries")
    for item in items:
        title = _first_text(item, [f"{DC}title"]) or _first_local(item, ["title"])
        if not title:
            continue
        link = _first_local(item, ["link"]) or _first_text(item, [f"{DC}identifier"])
        published_raw = _first_text(item, [f"{DC}date", f"{PRISM_2}publicationDate", f"{PRISM_1}publicationDate"]) or _first_local(item, ["pubDate", "date", "publicationDate"])
        records.append({
            "title": title,
            "landing_url": urljoin(feed_url, link),
            "publication_date": _normalized_date(published_raw),
            "published_raw": published_raw,
            "summary": _strip_markup(_first_text(item, [f"{DC}description"]) or _first_local(item, ["description"])),
            "authors": [value for value in [_first_text(item, [f"{DC}creator"]) or _first_local(item, ["author", "creator"])] if value],
            "venue": _first_text(item, [f"{PRISM_2}publicationName", f"{PRISM_1}publicationName"]),
            "doi": _doi(item),
            "source_field": "dc:date" if item.find(f"{DC}date") is not None else "rss:pubDate",
            "feed_url": feed_url,
            "source_id": source_id,
        })
    return records


def fetch_feed_records(
    session: Any,
    *,
    source_id: str,
    source_config: dict[str, Any],
    query: str,
    start_date: date,
    end_date: date,
    max_results: int,
    cache: dict[str, Any] | None = None,
    user_agent: str = "EvidenceRadar/1.0",
    timeout: int = 30,
) -> list[dict[str, Any]]:
    feeds = [str(value) for value in source_config.get("feeds", []) if str(value).startswith(("http://", "https://"))]
    if not feeds:
        raise PublisherFeedError(f"source {source_id} has no feed URLs")
    cache = cache if cache is not None else {}
    cache_key = f"publisher_feed:{source_id}"
    if cache_key not in cache:
        all_records: list[dict[str, Any]] = []
        for feed_url in feeds:
            try:
                response = session.get(
                    feed_url,
                    headers={"User-Agent": user_agent, "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"},
                    timeout=timeout,
                )
                response.raise_for_status()
            except Exception as exc:  # requests is intentionally not a dependency of this parser module.
                raise PublisherFeedError(f"feed request failed for {source_id}: {exc}") from exc
            all_records.extend(parse_feed(response.text, feed_url=feed_url, source_id=source_id))
        cache[cache_key] = all_records

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in cache[cache_key]:
        publication_date = str(record.get("publication_date") or "")
        if not publication_date or not _in_window(publication_date, start_date, end_date):
            continue
        if not _matches_query(f"{record.get('title','')} {record.get('summary','')}", query):
            continue
        identity = (str(record.get("doi") or "").casefold(), str(record.get("title") or "").casefold())
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(dict(record))
        if len(selected) >= max_results:
            break
    return selected
