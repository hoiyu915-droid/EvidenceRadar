from __future__ import annotations

import copy
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
        raise PublisherFeedError(
            "publisher listing inventory configuration must be an object"
        )
    return inventory


def _publisher_listing_shards(source_config: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = _publisher_listing_inventory_config(source_config)
    raw_shards = inventory.get("shards", [])
    if raw_shards in (None, []):
        return []
    if not isinstance(raw_shards, list):
        raise PublisherFeedError("publisher listing inventory shards must be an array")
    shards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_shards):
        if not isinstance(raw, dict):
            raise PublisherFeedError(
                f"publisher listing shard {index} must be an object"
            )
        shard_id = str(raw.get("shard_id") or "").strip()
        journal_slug = str(raw.get("journal_slug") or "").strip().casefold()
        journal_title = str(raw.get("journal_title") or "").strip()
        endpoint = str(raw.get("endpoint") or "").strip()
        if not shard_id or shard_id in seen:
            raise PublisherFeedError(
                f"publisher listing shard {index} has duplicate/missing shard_id"
            )
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", journal_slug):
            raise PublisherFeedError(
                f"publisher listing shard {shard_id} has invalid journal_slug"
            )
        expected_prefix = f"https://www.cambridge.org/core/journals/{journal_slug}/listing"
        if not endpoint.startswith(expected_prefix):
            raise PublisherFeedError(
                f"publisher listing shard {shard_id} endpoint is not bound to its journal"
            )
        if not journal_title:
            raise PublisherFeedError(
                f"publisher listing shard {shard_id} needs journal_title"
            )
        seen.add(shard_id)
        shards.append(copy.deepcopy(raw))
    return shards


def _container_identity(record: dict[str, Any], inventory: dict[str, Any]) -> str:
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
    shard: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    inventory = _publisher_listing_inventory_config(source_config)
    enriched: list[dict[str, Any]] = []
    containers: set[str] = set()
    article_oa_guarantee = inventory.get("article_oa_guarantee") is True
    for raw in records:
        record = dict(raw)
        container_id = ""
        if shard is not None:
            journal_slug = str(shard.get("journal_slug") or "").strip().casefold()
            prefix = str(inventory.get("container_id_prefix") or "").strip()
            container_id = f"{prefix}:{journal_slug}" if prefix else journal_slug
            record["journal_slug"] = journal_slug
            record["venue"] = str(shard.get("journal_title") or "").strip()
            record["inventory_shard_id"] = str(shard.get("shard_id") or "")
        if not container_id:
            container_id = _container_identity(record, inventory)
        if container_id:
            record["container_id"] = container_id
            containers.add(container_id)
            if not str(record.get("venue") or "").strip():
                record["venue"] = container_id
        if article_oa_guarantee:
            record["open_access"] = True
            record["oa_basis"] = "publisher_oa_article_inventory"
        enriched.append(record)

    observation = cache.get(f"source_observation:{source_id}")
    if isinstance(observation, dict):
        errors = [str(value) for value in observation.get("errors", []) if str(value)]
        page_bound_reached = any(
            "did not close the requested window within" in value for value in errors
        )
        hard_failure = any("HARD_FAILURE:" in value for value in errors)
        observation.update(
            {
                "inventory_scope": str(
                    inventory.get("scope") or "publisher_oa_articles"
                ),
                "coverage_unit": str(inventory.get("coverage_unit") or "article"),
                "journal_level_coverage": inventory.get("journal_level_coverage")
                is True,
                "window_closed": not page_bound_reached and not hard_failure,
                "page_bound_reached": page_bound_reached,
                "observed_container_count": len(containers),
            }
        )
    return enriched


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("doi") or "").casefold(),
        str(record.get("landing_url") or ""),
        str(record.get("title") or "").casefold(),
    )


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("publication_date") or ""),
        str(record.get("doi") or "").casefold(),
        str(record.get("landing_url") or ""),
    )


def _fetch_sharded_publisher_listing_records(
    session: Any,
    *,
    source_id: str,
    source_config: dict[str, Any],
    query: str,
    start_date: date,
    end_date: date,
    max_results: int,
    cache: dict[str, Any],
    user_agent: str,
    timeout: int,
) -> list[dict[str, Any]]:
    shards = _publisher_listing_shards(source_config)
    inventory = _publisher_listing_inventory_config(source_config)
    selected: list[dict[str, Any]] = []
    observations: list[tuple[dict[str, Any], dict[str, Any]]] = []
    hard_failures: list[str] = []

    for shard in shards:
        shard_id = str(shard["shard_id"])
        shard_cache_key = f"publisher_listing_shard_cache:{source_id}:{shard_id}"
        shard_cache = cache.setdefault(shard_cache_key, {})
        if not isinstance(shard_cache, dict):
            raise PublisherFeedError(
                f"publisher listing shard cache is corrupt for {shard_id}"
            )
        shard_config = copy.deepcopy(source_config)
        shard_config["endpoint"] = str(shard["endpoint"])
        try:
            rows = fetch_publisher_listing_records(
                session,
                source_id=source_id,
                source_config=shard_config,
                query=query,
                start_date=start_date,
                end_date=end_date,
                max_results=max_results,
                cache=shard_cache,
                user_agent=user_agent,
                timeout=timeout,
            )
        except PublisherListingError as exc:
            message = f"[shard:{shard_id}] HARD_FAILURE: {exc}"
            hard_failures.append(message)
            rows = [dict(record) for record in exc.partial_records]
            shard_cache[f"source_observation:{source_id}"] = {
                "retrieval_complete": False,
                "retrieval_backend": "publisher_listing",
                "feed_entry_count": len(exc.partial_records),
                "registry_record_count": 0,
                "unusable_record_count": exc.unusable_record_count,
                "window_record_count": 0,
                "inventory_url": str(shard.get("endpoint") or exc.inventory_url),
                "inventory_pages_requested": exc.pages_requested,
                "inventory_pages_received": exc.pages_received,
                "errors": [message],
                "window_closed": False,
                "page_bound_reached": False,
            }
        rows = _enrich_publisher_listing_records(
            rows,
            source_config=shard_config,
            cache=shard_cache,
            source_id=source_id,
            shard=shard,
        )
        selected.extend(rows)
        observation = shard_cache.get(f"source_observation:{source_id}", {})
        if not isinstance(observation, dict):
            raise PublisherFeedError(
                f"publisher listing shard {shard_id} produced no observation"
            )
        observations.append((shard, observation))

    all_errors: list[str] = []
    for shard, observation in observations:
        shard_id = str(shard["shard_id"])
        all_errors.extend(
            f"[shard:{shard_id}] {value}"
            for value in observation.get("errors", [])
            if str(value)
        )
    page_bound_reached = any(
        observation.get("page_bound_reached") is True
        for _shard, observation in observations
    )
    window_closed = bool(observations) and all(
        observation.get("window_closed") is True
        for _shard, observation in observations
    )
    retrieval_complete = bool(observations) and all(
        observation.get("retrieval_complete") is True
        for _shard, observation in observations
    )
    cache[f"source_observation:{source_id}"] = {
        "retrieval_complete": retrieval_complete,
        "retrieval_backend": "publisher_listing_shards",
        "feed_entry_count": sum(
            int(observation.get("feed_entry_count") or 0)
            for _shard, observation in observations
        ),
        "registry_record_count": 0,
        "unusable_record_count": sum(
            int(observation.get("unusable_record_count") or 0)
            for _shard, observation in observations
        ),
        "window_record_count": sum(
            int(observation.get("window_record_count") or 0)
            for _shard, observation in observations
        ),
        "inventory_url": str(
            inventory.get("family_url") or source_config.get("endpoint") or ""
        ),
        "inventory_pages_requested": sum(
            int(observation.get("inventory_pages_requested") or 0)
            for _shard, observation in observations
        ),
        "inventory_pages_received": sum(
            int(observation.get("inventory_pages_received") or 0)
            for _shard, observation in observations
        ),
        "errors": all_errors,
        "inventory_scope": str(inventory.get("scope") or "curated_journal_articles"),
        "coverage_unit": str(inventory.get("coverage_unit") or "article"),
        "journal_level_coverage": inventory.get("journal_level_coverage") is True,
        "window_closed": window_closed,
        "page_bound_reached": page_bound_reached,
        "observed_container_count": len(
            {
                str(record.get("container_id") or "")
                for record in selected
                if str(record.get("container_id") or "")
            }
        ),
        "selected_journal_count": len(shards),
        "selection_id": str(inventory.get("selection_id") or ""),
    }

    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in selected:
        key = _record_key(record)
        if not any(key):
            continue
        deduplicated.setdefault(key, record)
    ordered = sorted(deduplicated.values(), key=_record_sort_key, reverse=True)
    if hard_failures and not ordered:
        observation = cache[f"source_observation:{source_id}"]
        raise PublisherFeedError(
            "; ".join(hard_failures),
            inventory_url=str(
                inventory.get("family_url") or source_config.get("endpoint") or ""
            ),
            pages_requested=int(observation["inventory_pages_requested"]),
            pages_received=int(observation["inventory_pages_received"]),
            partial_records=[],
            unusable_record_count=int(observation["unusable_record_count"]),
        )
    return ordered[:max_results]


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
    established path. Sources declaring ``publisher_listing_v1`` use the generic
    first-party HTML listing adapter. Curated publishers may provide deterministic
    journal shards; every shard is crawled once per run, closed independently,
    and then reused across downstream topic queries through the shared source cache.
    """

    adapter_config = source_config.get("adapter_config")
    if (
        isinstance(adapter_config, dict)
        and adapter_config.get("template") == "publisher_listing_v1"
    ):
        local_cache = cache if cache is not None else {}
        shards = _publisher_listing_shards(source_config)
        if shards:
            return _fetch_sharded_publisher_listing_records(
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
