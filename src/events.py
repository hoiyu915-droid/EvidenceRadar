"""Evidence-backed publication/full-text events for the rolling radar window."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TIMEZONE = "Asia/Tokyo"

EVENT_LABELS = {
    "version_of_record_first_online": "Version of record first online",
    "first_formal_indexing": "First formal indexing",
    "formal_proceedings_release": "Formal proceedings release",
    "oa_fulltext_first_available": "OA full text first available",
    "author_accepted_manuscript_first_available": "Author accepted manuscript first available",
    "embargo_lifted": "Embargo lifted",
    "preprint_to_peer_reviewed_upgrade": "Preprint to peer-reviewed upgrade",
    "formal_version_verified": "Formal version verified",
}

FORMAL_EVENTS = {
    "version_of_record_first_online",
    "first_formal_indexing",
    "formal_proceedings_release",
    "preprint_to_peer_reviewed_upgrade",
    "formal_version_verified",
}

FULLTEXT_EVENTS = {
    "oa_fulltext_first_available",
    "author_accepted_manuscript_first_available",
    "embargo_lifted",
}


def add_event(
    paper: Any,
    event_type: str,
    occurred_at: str,
    *,
    source: str,
    source_field: str,
    url: str = "",
    precision: str = "date",
    confidence: str = "source_metadata",
) -> None:
    """Attach a normalized event without duplicating equivalent evidence."""
    if event_type not in EVENT_LABELS or not occurred_at:
        return
    event = {
        "type": event_type,
        "label": EVENT_LABELS[event_type],
        "occurred_at": occurred_at,
        "source": source,
        "source_field": source_field,
        "url": url,
        "precision": precision,
        "confidence": confidence,
    }
    fingerprint = event_fingerprint(event)
    if fingerprint not in {event_fingerprint(item) for item in paper.events}:
        paper.events.append(event)
    if url and url not in paper.fulltext_urls:
        paper.fulltext_urls.append(url)


def event_fingerprint(event: dict[str, Any]) -> str:
    return "|".join(
        str(event.get(field) or "").casefold()
        for field in ("type", "occurred_at", "source", "source_field")
    )


def ensure_provider_event(paper: Any) -> None:
    """Use a provider publication date only as formal-version evidence, never freshness."""
    if paper.is_preprint or not paper.publication_date:
        return
    if any(event.get("type") in FORMAL_EVENTS for event in paper.events):
        return
    types = " ".join(paper.publication_types).casefold()
    event_type = (
        "formal_proceedings_release"
        if "proceedings" in types
        else "formal_version_verified"
    )
    add_event(
        paper,
        event_type,
        paper.publication_date,
        source=paper.source,
        source_field="publication_date",
        confidence="provider_metadata",
    )


def _parse_event_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value[:10])
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, ZoneInfo(TIMEZONE))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def event_in_window(event: dict[str, Any], cutoff: datetime, end_at: datetime) -> bool:
    occurred = _parse_event_at(str(event.get("occurred_at") or ""))
    if occurred is None:
        return False
    if event.get("precision") == "date":
        # Date-only evidence cannot resolve the cutoff day. Excluding that
        # boundary prevents a nominal "72h" report from silently becoming 96h.
        if occurred.date() == cutoff.astimezone(ZoneInfo(TIMEZONE)).date():
            return False
        return cutoff.date() < occurred.date() <= end_at.date()
    return cutoff <= occurred <= end_at


def qualifying_events(paper: Any, cutoff: datetime, end_at: datetime) -> list[dict[str, Any]]:
    return [event for event in paper.events if event_in_window(event, cutoff, end_at)]


def filter_window(
    papers: Iterable[Any], cutoff: datetime, end_at: datetime
) -> list[Any]:
    selected: list[Any] = []
    for paper in papers:
        paper.qualifying_events = qualifying_events(paper, cutoff, end_at)
        if paper.qualifying_events:
            selected.append(paper)
    return selected


def merge_paper_events(winner: Any, loser: Any) -> None:
    known = {event_fingerprint(event) for event in winner.events}
    for event in loser.events:
        if event_fingerprint(event) not in known:
            winner.events.append(event)
    winner.fulltext_urls = list(dict.fromkeys([*winner.fulltext_urls, *loser.fulltext_urls]))


def display_event(paper: Any) -> dict[str, Any] | None:
    events = paper.qualifying_events or paper.events
    if not events:
        return None
    priority = {
        "preprint_to_peer_reviewed_upgrade": 0,
        "embargo_lifted": 1,
        "oa_fulltext_first_available": 2,
        "author_accepted_manuscript_first_available": 3,
        "version_of_record_first_online": 4,
        "formal_proceedings_release": 5,
        "formal_version_verified": 6,
        "first_formal_indexing": 7,
    }
    return sorted(
        events,
        key=lambda item: (priority.get(str(item.get("type")), 99), str(item.get("occurred_at", ""))),
    )[0]


def date_search_bounds(end_at: datetime, window_hours: int) -> tuple[date, date, datetime]:
    cutoff = end_at - timedelta(hours=window_hours)
    # The cutoff calendar day is over-fetched so precise timestamps can still
    # qualify. Date-only boundary evidence is rejected by event_in_window().
    return cutoff.date(), end_at.date(), cutoff
