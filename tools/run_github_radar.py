#!/usr/bin/env python3
"""Compatibility entry point for the archived GitHub/local runner.

The full historical implementation is preserved in ``run_github_radar_core``.
This shim keeps semantic source identity separate from the stable internal
publisher-inventory dispatch path, so publisher-scale HTML listings can reuse
one inventory across topic streams without being relabelled as RSS in audit
artifacts.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PACKAGED_PRODUCER_CAPABILITY_MARKERS = (
    "load_master_runtime",
    "RADAR_STREAMS_JSON:",
    "RADAR_SOURCES_JSON:",
    "EXECUTOR_HTTP_TELEMETRY_V1",
    "http_requests_attempted",
    "inventory_pages_requested",
    "unusable_record_count",
    "publisher_inventory_scope",
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import run_github_radar_core as _core  # noqa: E402


def _is_publisher_listing_source(config: dict[str, Any]) -> bool:
    configured = str(
        config.get("configured_adapter") or config.get("adapter") or ""
    ).strip()
    if configured == "publisher_listing":
        return True
    adapter_config = config.get("adapter_config")
    return (
        isinstance(adapter_config, dict)
        and adapter_config.get("template") == "publisher_listing_v1"
    )


def _publisher_inventory_config(config: dict[str, Any]) -> dict[str, Any]:
    adapter_config = config.get("adapter_config")
    if not isinstance(adapter_config, dict):
        return {}
    inventory = adapter_config.get("inventory")
    return inventory if isinstance(inventory, dict) else {}


def _install_publisher_listing_compatibility() -> None:
    if getattr(_core, "_publisher_listing_compatibility_installed", False):
        return

    original_fetch_rss_atom = _core.fetch_rss_atom
    original_discover_candidates = _core.discover_candidates

    def fetch_publisher_inventory(
        session: Any,
        query: str,
        stream: str,
        category: str,
        start_date: Any,
        end_date: Any,
        max_results: int,
        *,
        source_id: str,
        source_config: dict[str, Any],
        cache: dict[str, Any],
    ) -> list[Any]:
        candidates = original_fetch_rss_atom(
            session,
            query,
            stream,
            category,
            start_date,
            end_date,
            max_results,
            source_id=source_id,
            source_config=source_config,
            cache=cache,
        )
        if not _is_publisher_listing_source(source_config):
            return candidates

        inventory = _publisher_inventory_config(source_config)
        article_oa_guarantee = inventory.get("article_oa_guarantee") is True
        for candidate in candidates:
            candidate.oa_evidence = [
                evidence
                for evidence in candidate.oa_evidence
                if not (
                    str(evidence.get("evidence_type") or "")
                    == "source_catalog_oa_mode"
                    and str(evidence.get("value") or "") == "fully_oa"
                )
            ]
            if not article_oa_guarantee:
                candidate.open_access = None
                candidate.oa_evidence = [
                    evidence
                    for evidence in candidate.oa_evidence
                    if str(evidence.get("evidence_type") or "")
                    != "publisher_listing_oa_inventory"
                ]
                continue
            candidate.open_access = True
            if not any(
                str(evidence.get("evidence_type") or "")
                == "publisher_listing_oa_inventory"
                for evidence in candidate.oa_evidence
            ):
                candidate.oa_evidence.append(
                    {
                        "source": source_id,
                        "evidence_type": "publisher_listing_oa_inventory",
                        "value": "article_open_access",
                        "url": candidate.landing_url
                        or str(source_config.get("endpoint") or ""),
                    }
                )
        return candidates

    def discover_with_semantic_publisher_sources(
        streams: dict[str, Any],
        scoring: dict[str, Any],
        start: Any,
        end: Any,
        *,
        session: Any,
    ) -> Any:
        source_catalog = streams.get("source_catalog", {})
        publisher_sources = {
            str(source_id): config
            for source_id, config in source_catalog.items()
            if isinstance(config, dict) and _is_publisher_listing_source(config)
        }
        if not publisher_sources:
            return original_discover_candidates(
                streams, scoring, start, end, session=session
            )

        dispatch_streams = copy.deepcopy(streams)
        dispatch_catalog = dispatch_streams.get("source_catalog", {})
        for source_id in publisher_sources:
            config = dispatch_catalog.get(source_id)
            if not isinstance(config, dict):
                continue
            config["adapter"] = str(config.get("dispatch_adapter") or "rss_atom")

        result = original_discover_candidates(
            dispatch_streams, scoring, start, end, session=session
        )
        for access in result.source_access:
            source_id = str(access.get("provider") or "")
            config = publisher_sources.get(source_id)
            if config is None:
                continue
            inventory = _publisher_inventory_config(config)
            combined_errors = [
                str(value)
                for value in access.get("observation_errors", [])
                if str(value)
            ]
            if str(access.get("error") or ""):
                combined_errors.append(str(access["error"]))
            page_bound_reached = any(
                "did not close the requested window within" in value
                for value in combined_errors
            )
            hard_failure = any("HARD_FAILURE:" in value for value in combined_errors)
            status = str(access.get("status") or "")
            pages_received = int(access.get("inventory_pages_received") or 0)
            access.update(
                {
                    "publisher_inventory_scope": str(
                        inventory.get("scope") or "publisher_oa_articles"
                    ),
                    "coverage_unit": str(
                        inventory.get("coverage_unit") or "article"
                    ),
                    "journal_level_coverage": inventory.get(
                        "journal_level_coverage"
                    )
                    is True,
                    "selection_id": str(inventory.get("selection_id") or ""),
                    "selected_journal_count": int(
                        inventory.get("selected_journal_count") or 0
                    ),
                    "shard_strategy": str(inventory.get("shard_strategy") or ""),
                    "page_bound_reached": page_bound_reached,
                    "window_closed": (
                        not page_bound_reached
                        and not hard_failure
                        and pages_received > 0
                        and status in {"SUCCESS", "NO_RESULTS", "PARTIAL"}
                    ),
                }
            )
        return result

    _core.fetch_rss_atom = fetch_publisher_inventory
    _core.discover_candidates = discover_with_semantic_publisher_sources
    _core.DISCOVERY_ADAPTER_KEYS.add("publisher_listing")
    _core._publisher_listing_compatibility_installed = True


_install_publisher_listing_compatibility()

if __name__ == "__main__":
    raise SystemExit(_core.main())

sys.modules[__name__] = _core
