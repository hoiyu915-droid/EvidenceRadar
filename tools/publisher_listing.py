from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from tools.network_safety import bounded_response_text

QUERY_STOPWORDS = {"and", "or", "not", "the", "a", "an", "of", "to", "for", "in", "on"}
ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*?\bhref\s*=\s*(?P<quote>[\"'])(?P<href>.*?)(?P=quote)[^>]*)>"
    r"(?P<body>.*?)</a\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
DATE_TOKEN_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b"),
    re.compile(r"\b[A-Za-z]+\s+\d{1,2},\s+\d{4}\b"),
)
DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", flags=re.IGNORECASE)


class PublisherListingError(RuntimeError):
    """Fail-closed publisher-listing error with auditable partial progress."""

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


def _strip_markup(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))),
    ).strip()


def _contains_term(haystack: str, raw_term: str) -> bool:
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


def _matches_query(text: str, query: str) -> bool:
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query or query == "*":
        return True
    haystack = _strip_markup(text).casefold()
    quoted = [part.casefold().strip() for part in re.findall(r'"([^\"]{2,})"', query)]
    if quoted:
        return any(_contains_term(haystack, part) for part in quoted)
    tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.casefold())
        if token not in QUERY_STOPWORDS
    ]
    return not tokens or any(_contains_term(haystack, token) for token in tokens)


def _with_page(endpoint: str, parameter: str, page_number: int) -> str:
    parsed = urlsplit(endpoint)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != parameter]
    pairs.append((parameter, str(page_number)))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment)
    )


def _parse_date_token(
    text: str,
    *,
    authoritative_label: str,
    date_formats: list[str],
) -> tuple[str, str]:
    folded = text.casefold()
    index = folded.find(authoritative_label.casefold())
    if index < 0:
        return "", ""
    tail = text[index + len(authoritative_label) : index + len(authoritative_label) + 160]
    for pattern in DATE_TOKEN_PATTERNS:
        match = pattern.search(tail)
        if not match:
            continue
        token = match.group(0)
        for date_format in date_formats:
            try:
                return datetime.strptime(token, date_format).date().isoformat(), token
            except ValueError:
                continue
    return "", ""


def _normalize_doi(value: str) -> str:
    match = DOI_RE.search(html.unescape(str(value or "")))
    return match.group(0).rstrip(" .,;").casefold() if match else ""


def parse_publisher_listing(
    text: str,
    *,
    listing_url: str,
    source_id: str,
    adapter_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Parse one first-party publisher listing page using the v1 template.

    The v1 contract intentionally uses only stable semantic anchors configured
    by the source: an article-link path fragment and the publisher's explicit
    online-publication label.  Issue/volume/print dates are never consulted.
    """

    freshness = adapter_config.get("freshness")
    extract = adapter_config.get("extract")
    verification = adapter_config.get("verification")
    if not isinstance(freshness, dict) or not isinstance(extract, dict):
        raise PublisherListingError(
            f"publisher listing {source_id} is missing freshness/extract configuration",
            inventory_url=listing_url,
        )
    if not isinstance(verification, dict):
        verification = {}

    authoritative_label = str(freshness.get("authoritative_label") or "").strip()
    href_contains = str(extract.get("article_href_contains") or "").strip()
    date_formats = [
        str(value)
        for value in extract.get("date_formats", DEFAULT_DATE_FORMATS)
        if str(value)
    ]
    minimum_title_chars = int(extract.get("minimum_title_chars") or 12)
    accepted_marker = str(verification.get("accepted_manuscript_marker") or "").strip()
    if not authoritative_label or not href_contains or not date_formats:
        raise PublisherListingError(
            f"publisher listing {source_id} has incomplete extraction configuration",
            inventory_url=listing_url,
        )

    anchors: list[tuple[re.Match[str], str, str]] = []
    for match in ANCHOR_RE.finditer(text):
        href = html.unescape(match.group("href")).strip()
        title = _strip_markup(match.group("body"))
        if href_contains not in href or len(title) < minimum_title_chars:
            continue
        if len(re.findall(r"[\w'-]+", title)) < 3:
            continue
        anchors.append((match, href, title))

    if not anchors:
        raise PublisherListingError(
            f"publisher listing {source_id} contains no configured article links",
            inventory_url=listing_url,
        )

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    unusable = 0
    seen_urls: set[str] = set()
    for index, (match, href, title) in enumerate(anchors):
        next_start = anchors[index + 1][0].start() if index + 1 < len(anchors) else len(text)
        block_text = _strip_markup(text[match.start() : next_start])
        landing_url = urljoin(listing_url, href)
        if landing_url in seen_urls:
            continue
        seen_urls.add(landing_url)

        if accepted_marker and accepted_marker.casefold() in block_text.casefold():
            unusable += 1
            errors.append(
                f"{source_id} listing exposed an Accepted Manuscript that requires "
                "article-page version verification before event promotion"
            )
            continue

        publication_date, published_raw = _parse_date_token(
            block_text,
            authoritative_label=authoritative_label,
            date_formats=date_formats,
        )
        if not publication_date:
            unusable += 1
            errors.append(
                f"{source_id} listing record lacks the configured published-online date: {title[:120]}"
            )
            continue
        records.append(
            {
                "title": title,
                "landing_url": landing_url,
                "publication_date": publication_date,
                "published_raw": published_raw,
                "summary": block_text[:4000],
                "authors": [],
                "venue": "",
                "doi": _normalize_doi(block_text),
                "source_field": "publisher_listing:published_online",
                "feed_url": listing_url,
                "source_id": source_id,
                "event_confidence": "publisher_verified",
            }
        )
    return records, errors, unusable


def _selection_key(record: dict[str, Any]) -> tuple[Any, ...]:
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


def fetch_publisher_listing_records(
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
    adapter_config = source_config.get("adapter_config")
    if not isinstance(adapter_config, dict) or adapter_config.get("template") != "publisher_listing_v1":
        raise PublisherListingError(
            f"source {source_id} is not configured for publisher_listing_v1"
        )
    endpoint = str(source_config.get("endpoint") or "").strip()
    pagination = adapter_config.get("pagination")
    freshness = adapter_config.get("freshness")
    if not endpoint.startswith("https://") or not isinstance(pagination, dict) or not isinstance(freshness, dict):
        raise PublisherListingError(
            f"source {source_id} has an invalid publisher listing endpoint/configuration",
            inventory_url=endpoint,
        )

    parameter = str(pagination.get("parameter") or "").strip()
    start_page = int(pagination.get("start_page") or 1)
    max_pages = int(pagination.get("max_pages") or 1)
    descending = str(freshness.get("order") or "").casefold() == "desc"
    stop_when_old = freshness.get("stop_when_older_than_window") is True
    cache = cache if cache is not None else {}
    cache_key = f"publisher_feed:{source_id}"

    if cache_key not in cache:
        all_records: list[dict[str, Any]] = []
        errors: list[str] = []
        pages_requested = 0
        pages_received = 0
        unusable_record_count = 0
        inventory_url = endpoint
        previous_oldest: date | None = None
        closed_window = False
        seen_urls: set[str] = set()

        def failure(message: str) -> PublisherListingError:
            return PublisherListingError(
                message,
                inventory_url=inventory_url,
                pages_requested=pages_requested,
                pages_received=pages_received,
                partial_records=all_records,
                unusable_record_count=unusable_record_count,
            )

        for page_number in range(start_page, start_page + max_pages):
            page_url = _with_page(endpoint, parameter, page_number)
            pages_requested += 1
            response_counted = False
            try:
                response = session.get(
                    page_url,
                    headers={
                        "User-Agent": user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                    timeout=timeout,
                    stream=True,
                )
                inventory_url = str(getattr(response, "url", "") or page_url)
                pages_received += 1
                response_counted = True
                response.raise_for_status()
                page_records, page_errors, page_unusable = parse_publisher_listing(
                    bounded_response_text(response),
                    listing_url=inventory_url,
                    source_id=source_id,
                    adapter_config=adapter_config,
                )
            except Exception as exc:
                if not response_counted:
                    failed_response = getattr(exc, "response", None)
                    if failed_response is not None:
                        pages_received += 1
                if isinstance(exc, PublisherListingError):
                    raise failure(str(exc)) from exc
                raise failure(
                    f"publisher listing request failed for {source_id}: {exc}"
                ) from exc

            errors.extend(page_errors)
            unusable_record_count += page_unusable
            unique_page_records: list[dict[str, Any]] = []
            for record in page_records:
                landing_url = str(record.get("landing_url") or "")
                if not landing_url or landing_url in seen_urls:
                    continue
                seen_urls.add(landing_url)
                unique_page_records.append(record)
                all_records.append(record)

            page_dates = [
                date.fromisoformat(str(record["publication_date"])[:10])
                for record in unique_page_records
                if record.get("publication_date")
            ]
            page_order_valid = bool(page_dates)
            if page_dates and descending and page_dates != sorted(page_dates, reverse=True):
                page_order_valid = False
                errors.append(
                    f"{source_id} publisher listing is not descending by published-online date"
                )
            if previous_oldest is not None and page_dates and max(page_dates) > previous_oldest:
                page_order_valid = False
                errors.append(
                    f"{source_id} publisher listing pagination is not monotonic by published-online date"
                )
            if page_dates:
                previous_oldest = min(page_dates)

            if (
                stop_when_old
                and descending
                and page_order_valid
                and page_dates
                and min(page_dates) < start_date
            ):
                closed_window = True
                break

        if stop_when_old and not closed_window:
            errors.append(
                f"{source_id} publisher listing did not close the requested window within {max_pages} page(s)"
            )

        cache[cache_key] = all_records
        cache[f"source_observation:{source_id}"] = {
            "retrieval_complete": not errors,
            "retrieval_backend": "publisher_listing",
            "feed_entry_count": len(all_records),
            "registry_record_count": 0,
            "unusable_record_count": unusable_record_count,
            "window_record_count": sum(
                1
                for record in all_records
                if record.get("publication_date")
                and start_date
                <= date.fromisoformat(str(record["publication_date"])[:10])
                <= end_date
            ),
            "inventory_url": endpoint,
            "inventory_pages_requested": pages_requested,
            "inventory_pages_received": pages_received,
            "errors": errors,
        }
        if errors and not all_records:
            raise failure("; ".join(errors))

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(cache[cache_key], key=_selection_key):
        publication_date = str(record.get("publication_date") or "")
        try:
            observed = date.fromisoformat(publication_date[:10])
        except ValueError:
            continue
        if not (start_date <= observed <= end_date):
            continue
        if not _matches_query(
            f"{record.get('title', '')} {record.get('summary', '')}", query
        ):
            continue
        identity = str(record.get("doi") or record.get("landing_url") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        selected.append(dict(record))
        if len(selected) >= max_results:
            break
    return selected
