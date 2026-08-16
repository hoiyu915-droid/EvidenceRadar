from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

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


def _publisher_listing_inventory_config(source_config: dict[str, Any]) -> dict[str, Any]:
    adapter_config = source_config.get("adapter_config")
    if not isinstance(adapter_config, dict):
        return {}
    inventory = adapter_config.get("inventory", {})
    if not isinstance(inventory, dict):
        raise PublisherFeedError("publisher listing inventory configuration must be an object")
    return inventory


def _container_identity(
    record: dict[str, Any], inventory: dict[str, Any]
) -> str:
    pattern_text = str(inventory.get("container_path_regex") or "").strip()
    if not pattern_text:
        return ""
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        raise PublisherFeedError(
            f"invalid publisher listing container_path_regex: {exc}"
        ) from exc
    landing_url = str(record.get("landing_url") or "")
    match = pattern.search(urlsplit(landing_url).path)
    if match is None:
        return ""
    group = match.groupdict().get("container")
    if group is None and match.groups():
        group = match.group(1)
    container = str(group or "").strip().casefold()
    if not container:
        return ""
    prefix = str(inventory.get("container_id_prefix") or "").strip()
    return f"{prefix}:{container}" if prefix else container


def _enrich_publisher_listing_records(
    records: list[dict[str, Any]],
    *,
    source_config: dict[str, Any],
    cache: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    inventory = _publisher_listing_inventory_config(source_config)
    enriched: list[dict[str, Any]] = []
    containers: set[str] = set()
    for raw in records:
        record = dict(raw)
        container_id = _container_identity(record, inventory)
        if container_id:
            record["container_id"] = container_id
            containers.add(container_id)
            # Preserve stable first-party container identity even when the
            # aggregate listing does not expose a separate human-readable
            # journal label in the article block.
            if not str(record.get("venue") or "").strip():
                record["venue"] = container_id
        record["open_access"] = True
        record["oa_basis"] = "publisher_oa_article_inventory"
        enriched.append(record)

    observation = cache.get(f"source_observation:{source_id}")
    if isinstance(observation, dict):
        errors = [str(value) for value in observation.get("errors", []) if str(value)]
        page_bound_reached = any(
            "did not close the requested window within" in value for value in errors
        )
        observation.update(
            {
                "inventory_scope": str(
                    inventory.get("scope") or "publisher_oa_articles"
                ),
                "coverage_unit": str(inventory.get("coverage_unit") or "article"),
                "journal_level_coverage": inventory.get("journal_level_coverage") is True,
                "window_closed": not page_bound_reached,
                "page_bound_reached": page_bound_reached,
                "observed_container_count": len(containers),
            }
        )
    return enriched


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
    the generic first-party HTML listing adapter and retain the same source-level
    cache so one publisher inventory can serve several downstream topic streams.
    """

    adapter_config = source_config.get("adapter_config")
    if (
        isinstance(adapter_config, dict)
        and adapter_config.get("template") == "publisher_listing_v1"
    ):
        local_cache = cache if cache is not None else {}
        try:
            records = fetch_publisher_listing_records(
                session,
                source_id=source_id,
                source_config=source_config,
                query=query,
                start_date=start_date,
                end_date=end_date,
                max_results=max_results,
                cache=local_cache,
                user_agent=user_agent,
                timeout=timeout,
            )
            return _enrich_publisher_listing_records(
                records,
                source_config=source_config,
                cache=local_cache,
                source_id=source_id,
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
