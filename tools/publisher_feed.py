from __future__ import annotations

from datetime import date
from typing import Any

from tools import publisher_feed_core as _feed_core
from tools.publisher_listing import (
    PublisherListingError,
    fetch_publisher_listing_records,
)

# Keep the established feed/Crossref parser surface stable for callers and
# tests while this module becomes the common publisher-inventory dispatcher.
for _name in dir(_feed_core):
    if not _name.startswith("__") and _name != "fetch_feed_records":
        globals()[_name] = getattr(_feed_core, _name)

PublisherFeedError = _feed_core.PublisherFeedError


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
    """Fetch one configured first-party publisher inventory.

    RSS/Atom plus optional Crossref journal-window enrichment remains the
    established path.  Sources declaring ``publisher_listing_v1`` instead use
    the generic first-party HTML listing adapter and retain the same cache,
    telemetry and candidate-record contract expected by the canonical runner.
    """

    adapter_config = source_config.get("adapter_config")
    if (
        isinstance(adapter_config, dict)
        and adapter_config.get("template") == "publisher_listing_v1"
    ):
        try:
            return fetch_publisher_listing_records(
                session,
                source_id=source_id,
                source_config=source_config,
                query=query,
                start_date=start_date,
                end_date=end_date,
                max_results=max_results,
                cache=cache,
                user_agent=user_agent,
                timeout=timeout,
            )
        except PublisherListingError as exc:
            raise PublisherFeedError(
                str(exc),
                inventory_url=exc.inventory_url,
                pages_requested=exc.pages_requested,
                pages_received=exc.pages_received,
                partial_records=exc.partial_records,
                unusable_record_count=exc.unusable_record_count,
            ) from exc

    return _feed_core.fetch_feed_records(
        session,
        source_id=source_id,
        source_config=source_config,
        query=query,
        start_date=start_date,
        end_date=end_date,
        max_results=max_results,
        cache=cache,
        user_agent=user_agent,
        timeout=timeout,
    )
