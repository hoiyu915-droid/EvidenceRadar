from __future__ import annotations

import html
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from tools.network_safety import bounded_response_bytes, bounded_response_text

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
PRISM_1 = "{http://prismstandard.org/namespaces/1.2/basic/}"
PRISM_2 = "{http://prismstandard.org/namespaces/basic/2.0/}"
QUERY_STOPWORDS = {"and", "or", "not", "the", "a", "an", "of", "to", "for", "in", "on"}
CROSSREF_JOURNAL_WORKS = "https://api.crossref.org/journals/{issn}/works"
CROSSREF_ROWS = 1000
CROSSREF_MAX_PAGES = 25


class PublisherFeedError(RuntimeError):
    """Bounded publisher-inventory failure with auditable progress metadata."""

    def __init__(
        self,
        message: str,
        *,
        inventory_url: str = "",
        pages_requested: int = 0,
        pages_received: int = 0,
        partial_records: list[dict[str, Any]] | None = None,
        unusable_record_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.inventory_url = inventory_url
        self.pages_requested = pages_requested
        self.pages_received = pages_received
        self.partial_records = list(partial_records or [])
        self.unusable_record_count = unusable_record_count


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
    # Quoted terms are an explicit OR-list.  Once a query opts into exact
    # phrases, do not broaden it again through the unquoted token fallback;
    # that fallback turned owner-scoped Nature queries into matches for any
    # incidental word such as "model", "training", or "human".
    if quoted:
        return any(_contains_term(haystack, part) for part in quoted)
    tokens = [
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.casefold())
        if token not in QUERY_STOPWORDS
    ]
    return not tokens or any(_contains_term(haystack, token) for token in tokens)


def _contains_term(haystack: str, raw_term: str) -> bool:
    """Match a token/phrase on alphanumeric boundaries, with optional suffix wildcard."""

    term = re.sub(r"\s+", " ", str(raw_term or "").casefold()).strip()
    if not term:
        return False
    wildcard = term.endswith("*")
    term = term.rstrip("*")
    pieces = [re.escape(piece) for piece in term.split() if piece]
    if not pieces:
        return False
    body = r"\s+".join(pieces)
    suffix = "" if wildcard else r"(?![a-z0-9])"
    return re.search(rf"(?<![a-z0-9]){body}{suffix}", haystack) is not None


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


def _normalize_doi(value: Any) -> str:
    """Return the canonical DOI identity used across feed and registry records."""

    text = html.unescape(str(value or "")).strip()
    text = re.sub(r"^doi\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^https?://(?:dx\.)?doi\.org/",
        "",
        text,
        flags=re.IGNORECASE,
    )
    match = re.search(
        r"10\.\d{4,9}/[-._;()/:A-Z0-9]+",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(0).rstrip(" .,;").casefold() if match else ""


def _doi(parent: ET.Element) -> str:
    # JAMA Network feeds currently declare ``xmlns:prism="prism"`` instead
    # of either standard PRISM URI.  ElementTree therefore exposes
    # ``{prism}doi``.  The local-name fallback keeps that first-party DOI
    # available for canonical RSS/Crossref merging without trusting arbitrary
    # nested markup.
    # Prefer every explicitly DOI-shaped field before generic identifiers.  A
    # feed may include a non-DOI dc:identifier ahead of JAMA's relative-URI
    # prism:doi; combining them in one first-match lookup would hide the DOI.
    for value in (
        _first_text(parent, [f"{PRISM_2}doi", f"{PRISM_1}doi"]),
        _first_local(parent, ["doi"]),
        _first_text(parent, [f"{DC}identifier"]),
        _first_local(parent, ["identifier"]),
    ):
        doi = _normalize_doi(value)
        if doi:
            return doi
    return ""


def parse_feed(
    text: str,
    *,
    feed_url: str,
    source_id: str,
    observation: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise PublisherFeedError(f"invalid XML from {source_id}: {exc}") from exc

    records: list[dict[str, Any]] = []
    root_name = root.tag.rsplit("}", 1)[-1].casefold()
    if root_name == "feed":
        entries = root.findall(f"{ATOM}entry")
        if observation is not None:
            observation["raw_entry_count"] = len(entries)
        for entry in entries:
            title = _text(entry.find(f"{ATOM}title"))
            if not title:
                if observation is not None:
                    observation["normalization_failures"] = (
                        observation.get("normalization_failures", 0) + 1
                    )
                continue
            link = ""
            for node in entry.findall(f"{ATOM}link"):
                if (node.attrib.get("rel") or "alternate") == "alternate" and node.attrib.get("href"):
                    link = str(node.attrib["href"])
                    break
            published_raw = _text(entry.find(f"{ATOM}published"))
            updated_raw = _text(entry.find(f"{ATOM}updated"))
            records.append({
                "title": title,
                "landing_url": urljoin(feed_url, link),
                "publication_date": _normalized_date(published_raw),
                "published_raw": published_raw,
                "updated_raw": updated_raw,
                "summary": _strip_markup(_first_text(entry, [f"{ATOM}summary", f"{ATOM}content"])),
                "authors": [_text(node.find(f"{ATOM}name")) for node in entry.findall(f"{ATOM}author") if _text(node.find(f"{ATOM}name"))],
                "venue": "",
                "doi": _doi(entry),
                "source_field": "atom:published" if published_raw else "",
                "feed_url": feed_url,
                "source_id": source_id,
            })
        return records

    # RSS 2.0 and RDF/RSS 1.0 both expose item nodes with local name 'item'.
    items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].casefold() == "item"]
    if not items:
        raise PublisherFeedError(f"feed {source_id} contains no recognizable entries")
    if observation is not None:
        observation["raw_entry_count"] = len(items)
    for item in items:
        title = _first_text(item, [f"{DC}title"]) or _first_local(item, ["title"])
        if not title:
            if observation is not None:
                observation["normalization_failures"] = (
                    observation.get("normalization_failures", 0) + 1
                )
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


def _crossref_date(item: dict[str, Any]) -> tuple[str, str]:
    for field in ("published-online", "published", "published-print", "issued"):
        block = item.get(field)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        values = parts[0]
        try:
            year = int(values[0])
            month = int(values[1]) if len(values) > 1 else 1
            day = int(values[2]) if len(values) > 2 else 1
            return date(year, month, day).isoformat(), f"crossref:{field}"
        except (TypeError, ValueError, IndexError):
            continue
    return "", ""


def _crossref_record(item: Any, *, source_id: str, issn: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    title_values = item.get("title")
    title = (
        _strip_markup(str(title_values[0]))
        if isinstance(title_values, list) and title_values
        else ""
    )
    if not title:
        return None
    publication_date, source_field = _crossref_date(item)
    doi = _normalize_doi(item.get("DOI"))
    landing_url = str(item.get("URL") or "").strip()
    if not landing_url and doi:
        landing_url = f"https://doi.org/{quote(doi, safe='/')}"
    authors: list[str] = []
    for author in item.get("author", []):
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in (str(author.get("given") or "").strip(), str(author.get("family") or "").strip())
            if part
        )
        if name:
            authors.append(name)
    container = item.get("container-title")
    venue = str(container[0]).strip() if isinstance(container, list) and container else ""
    return {
        "title": title,
        "landing_url": landing_url,
        "publication_date": publication_date,
        "published_raw": publication_date,
        "summary": _strip_markup(str(item.get("abstract") or "")),
        "authors": authors,
        "venue": venue,
        "doi": doi,
        "source_field": source_field,
        "feed_url": CROSSREF_JOURNAL_WORKS.format(issn=quote(issn, safe="")),
        "source_id": source_id,
        # Crossref is a publisher-deposited registry, not the publisher's
        # first-party feed.  Keep this distinction schema-compatible all the
        # way into notified-event provenance.
        "event_confidence": "publisher_supplied_citation",
    }


def _record_title_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _strip_markup(str(value or "")).casefold())


def _record_aliases(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Return strong provider-record identities.

    Titles are deliberately excluded.  Journal feeds commonly reuse headings
    such as ``Editorial`` or ``Introduction`` and a title collision must not
    collapse two real publications before the runtime's candidate deduper can
    inspect richer metadata.
    """

    aliases: list[tuple[str, str]] = []
    doi = _normalize_doi(record.get("doi"))
    landing_url = str(record.get("landing_url") or "").casefold().rstrip("/")
    if doi:
        aliases.append(("doi", doi))
    if landing_url:
        aliases.append(("url", landing_url))
    return aliases


_GENERIC_RECORD_TITLES = {
    "abstract",
    "announcement",
    "contents",
    "correction",
    "editorial",
    "erratum",
    "foreword",
    "introduction",
    "news",
    "preface",
}


def _record_title_is_informative(record: dict[str, Any]) -> bool:
    raw_title = _strip_markup(str(record.get("title") or "")).strip()
    words = re.findall(r"[a-z0-9]+", raw_title.casefold())
    return (
        _record_title_key(raw_title) not in _GENERIC_RECORD_TITLES
        and len(words) >= 3
        and len(_record_title_key(raw_title)) >= 12
    )


def _records_share_supported_title_identity(
    current: dict[str, Any], incoming: dict[str, Any]
) -> bool:
    """Permit title bridging only with date and bibliographic corroboration."""

    if (
        not _record_title_is_informative(current)
        or not _record_title_is_informative(incoming)
        or _record_title_key(current.get("title"))
        != _record_title_key(incoming.get("title"))
    ):
        return False
    current_date = str(current.get("publication_date") or "")[:10]
    incoming_date = str(incoming.get("publication_date") or "")[:10]
    if not current_date or current_date != incoming_date:
        return False
    current_authors = {
        _record_title_key(value) for value in current.get("authors", []) if value
    }
    incoming_authors = {
        _record_title_key(value) for value in incoming.get("authors", []) if value
    }
    author_match = bool(current_authors & incoming_authors)
    current_venue = _record_title_key(current.get("venue"))
    incoming_venue = _record_title_key(incoming.get("venue"))
    venue_match = bool(current_venue and current_venue == incoming_venue)
    return author_match or venue_match


def _merge_feed_and_registry_records(
    feed_records: list[dict[str, Any]],
    registry_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge one provider inventory while preserving first-party RSS values."""

    merged: list[dict[str, Any]] = []
    alias_index: dict[tuple[str, str], int] = {}
    title_index: dict[str, list[int]] = {}
    for raw_record in [*feed_records, *registry_records]:
        record = dict(raw_record)
        if "doi" in record:
            record["doi"] = _normalize_doi(record.get("doi"))
        aliases = _record_aliases(record)
        matched_indexes = {
            alias_index[alias] for alias in aliases if alias in alias_index
        }
        target_index = min(matched_indexes) if matched_indexes else None
        if target_index is None:
            title_key = _record_title_key(record.get("title"))
            target_index = next(
                (
                    index
                    for index in title_index.get(title_key, [])
                    if _records_share_supported_title_identity(merged[index], record)
                ),
                None,
            )
        if target_index is not None:
            current = merged[target_index]
            current_doi = _normalize_doi(current.get("doi"))
            incoming_doi = _normalize_doi(record.get("doi"))
            # A generic/shared title is not sufficient to merge conflicting
            # strong identifiers.
            if current_doi and incoming_doi and current_doi != incoming_doi:
                target_index = None
        if target_index is None:
            target_index = len(merged)
            merged.append(record)
            for alias in aliases:
                alias_index.setdefault(alias, target_index)
            title_key = _record_title_key(record.get("title"))
            if title_key:
                title_index.setdefault(title_key, []).append(target_index)
            continue
        current = merged[target_index]
        for field, value in record.items():
            if not current.get(field) and value:
                current[field] = value
        for alias in _record_aliases(current):
            alias_index.setdefault(alias, target_index)
    return merged


def _fetch_crossref_inventory(
    session: Any,
    *,
    source_id: str,
    issn: str,
    start_date: date,
    end_date: date,
    user_agent: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], str, int, int, int]:
    endpoint = CROSSREF_JOURNAL_WORKS.format(issn=quote(issn, safe=""))
    cursor = "*"
    records: list[dict[str, Any]] = []
    inventory_url = endpoint
    seen_cursors: set[str] = set()
    total_results: int | None = None
    retrieved_items = 0
    pages_requested = 0
    pages_received = 0
    normalization_failures = 0

    def failure(message: str) -> PublisherFeedError:
        return PublisherFeedError(
            message,
            inventory_url=inventory_url,
            pages_requested=pages_requested,
            pages_received=pages_received,
            partial_records=records,
            unusable_record_count=normalization_failures,
        )

    for _page in range(CROSSREF_MAX_PAGES):
        pages_requested += 1
        response_counted = False
        try:
            response = session.get(
                endpoint,
                params={
                    "filter": (
                        f"from-online-pub-date:{start_date.isoformat()},"
                        f"until-online-pub-date:{end_date.isoformat()}"
                    ),
                    "rows": CROSSREF_ROWS,
                    "cursor": cursor,
                },
                headers={"User-Agent": user_agent, "Accept": "application/json"},
                timeout=timeout,
                stream=True,
            )
            inventory_url = str(getattr(response, "url", "") or endpoint)
            pages_received += 1
            response_counted = True
            response.raise_for_status()
            bounded_response_bytes(response)
            payload = response.json()
        except Exception as exc:
            if not response_counted:
                failed_response = getattr(exc, "response", None)
                if failed_response is not None:
                    pages_received += 1
            raise failure(
                f"Crossref journal inventory failed for {source_id}: {exc}"
            ) from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            raise failure(
                f"Crossref journal inventory returned an invalid payload for {source_id}"
            )
        items = message["items"]
        retrieved_items += len(items)
        if total_results is None:
            try:
                total_results = int(message.get("total-results"))
            except (TypeError, ValueError):
                total_results = None
        for item in items:
            record = _crossref_record(item, source_id=source_id, issn=issn)
            if record is not None:
                records.append(record)
            else:
                normalization_failures += 1
        # Crossref's total counts raw registry items.  Some malformed items
        # may be unusable as candidates, so pagination must not depend on the
        # smaller number of successfully normalized records.
        if total_results is not None:
            if retrieved_items >= total_results:
                return (
                    records,
                    inventory_url,
                    pages_requested,
                    pages_received,
                    normalization_failures,
                )
            if not items:
                raise failure(
                    "Crossref journal inventory ended before total-results "
                    f"for {source_id}: received {retrieved_items} of {total_results}"
                )
        elif not items or len(items) < CROSSREF_ROWS:
            return (
                records,
                inventory_url,
                pages_requested,
                pages_received,
                normalization_failures,
            )
        next_cursor = str(message.get("next-cursor") or "").strip()
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            raise failure(
                f"Crossref journal inventory pagination stalled for {source_id}"
            )
        seen_cursors.add(cursor)
        cursor = next_cursor
    raise failure(
        f"Crossref journal inventory exceeded {CROSSREF_MAX_PAGES} pages for {source_id}"
    )


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
    crossref_issn = str(source_config.get("crossref_issn") or "").strip()
    if not feeds and not crossref_issn:
        raise PublisherFeedError(f"source {source_id} has no feed URLs or Crossref ISSN")
    cache = cache if cache is not None else {}
    cache_key = f"publisher_feed:{source_id}"
    if cache_key not in cache:
        feed_records: list[dict[str, Any]] = []
        registry_records: list[dict[str, Any]] = []
        errors: list[str] = []
        inventory_url = feeds[0] if feeds else CROSSREF_JOURNAL_WORKS.format(
            issn=quote(crossref_issn, safe="")
        )
        pages_requested = 0
        pages_received = 0
        normalization_failures = 0

        for feed_url in feeds:
            pages_requested += 1
            response_counted = False
            try:
                response = session.get(
                    feed_url,
                    headers={"User-Agent": user_agent, "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"},
                    timeout=timeout,
                    stream=True,
                )
                pages_received += 1
                response_counted = True
                response.raise_for_status()
                feed_observation: dict[str, int] = {}
                parsed = parse_feed(
                    bounded_response_text(response),
                    feed_url=feed_url,
                    source_id=source_id,
                    observation=feed_observation,
                )
            except Exception as exc:  # requests is intentionally not a dependency of this parser module.
                if not response_counted:
                    failed_response = getattr(exc, "response", None)
                    if failed_response is not None:
                        pages_received += 1
                errors.append(f"feed request failed for {source_id}: {exc}")
                continue
            normalization_failures += feed_observation.get(
                "normalization_failures", 0
            )
            for record in parsed:
                item = dict(record)
                item.setdefault("event_confidence", "publisher_verified")
                feed_records.append(item)

        registry_error: PublisherFeedError | None = None
        if crossref_issn:
            try:
                (
                    registry_records,
                    inventory_url,
                    registry_pages_requested,
                    registry_pages_received,
                    registry_normalization_failures,
                ) = _fetch_crossref_inventory(
                    session,
                    source_id=source_id,
                    issn=crossref_issn,
                    start_date=start_date,
                    end_date=end_date,
                    user_agent=user_agent,
                    timeout=timeout,
                )
            except PublisherFeedError as exc:
                registry_error = exc
                registry_records = list(exc.partial_records)
                registry_pages_requested = exc.pages_requested
                registry_pages_received = exc.pages_received
                registry_normalization_failures = exc.unusable_record_count
                inventory_url = exc.inventory_url or inventory_url
                errors.append(str(exc))
            pages_requested += registry_pages_requested
            pages_received += registry_pages_received
            normalization_failures += registry_normalization_failures

        all_records = _merge_feed_and_registry_records(
            feed_records, registry_records
        )
        unusable_record_count = normalization_failures + sum(
            1
            for record in all_records
            if not str(record.get("publication_date") or "")
        )
        if unusable_record_count:
            errors.append(
                f"{source_id} returned {unusable_record_count} record(s) without "
                "a trustworthy publication date"
            )
        retrieval_backend = (
            "rss_atom+crossref_journal_window"
            if feeds and crossref_issn
            else "crossref_journal_window"
            if crossref_issn
            else "rss_atom"
        )
        cache[cache_key] = all_records
        cache[f"source_observation:{source_id}"] = {
            "retrieval_complete": not errors,
            "retrieval_backend": retrieval_backend,
            "feed_entry_count": len(feed_records),
            "registry_record_count": len(registry_records),
            "unusable_record_count": unusable_record_count,
            "window_record_count": sum(
                1
                for record in all_records
                if str(record.get("publication_date") or "")
                and _in_window(
                    str(record.get("publication_date") or ""),
                    start_date,
                    end_date,
                )
            ),
            "inventory_url": inventory_url,
            "inventory_pages_requested": pages_requested,
            "inventory_pages_received": pages_received,
            "errors": errors,
        }
        # A Crossref-only source cannot safely consume a known-partial cursor
        # inventory.  Hybrid sources may retain first-party feed observations,
        # while the caller marks the provider FAILED from retrieval_complete.
        if registry_error is not None and not feeds:
            raise registry_error
        if errors and not all_records:
            raise PublisherFeedError(
                "; ".join(errors),
                inventory_url=inventory_url,
                pages_requested=pages_requested,
                pages_received=pages_received,
            )

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    def selection_key(record: dict[str, Any]) -> tuple[Any, ...]:
        try:
            publication_rank = -date.fromisoformat(
                str(record.get("publication_date") or "")[:10]
            ).toordinal()
        except ValueError:
            publication_rank = 0
        return (
            publication_rank,
            str(record.get("doi") or "").casefold(),
            str(record.get("title") or "").casefold(),
            str(record.get("landing_url") or ""),
        )

    for record in sorted(cache[cache_key], key=selection_key):
        publication_date = str(record.get("publication_date") or "")
        if not publication_date or not _in_window(publication_date, start_date, end_date):
            continue
        if not _matches_query(f"{record.get('title','')} {record.get('summary','')}", query):
            continue
        aliases = _record_aliases(record)
        identity = aliases[0] if aliases else ("record", str(len(selected)))
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(dict(record))
        if len(selected) >= max_results:
            break
    return selected
