from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from tools import radar_control_core as _core

# Re-export the established public/private surface so existing callers and
# tests continue to import this module unchanged.  The wrapper only supplies
# versioned template defaults before the core validates/compiles master data.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

RadarControlError = _core.RadarControlError
MasterRuntime = _core.MasterRuntime

PUBLISHER_LISTING_V1_EXTRACT_DEFAULTS = {
    "article_href_contains": "/article/",
    "date_formats": ["%Y-%m-%d", "%d %B %Y", "%B %d, %Y"],
    "minimum_title_chars": 12,
}
PUBLISHER_LISTING_V1_INVENTORY_DEFAULTS = {
    "scope": "publisher_oa_articles",
    "coverage_unit": "article",
    "journal_level_coverage": False,
    "shard_strategy": "catalog_or_subject_optional",
}
CAMBRIDGE_SELECTION_PATH = Path(__file__).resolve().parents[1] / "config" / "cambridge_journal_selection.json"

CAMBRIDGE_CONTAINER_DEFAULTS = {
    "container_path_regex": r"/core/journals/(?P<container>[^/]+)/article/",
    "container_id_prefix": "cambridge-core",
}


def _load_cambridge_selection() -> dict[str, Any]:
    try:
        selection = _core._load_mapping(CAMBRIDGE_SELECTION_PATH, json_only=True)
    except (OSError, RadarControlError) as exc:
        raise RadarControlError(
            f"cannot load Cambridge journal selection {CAMBRIDGE_SELECTION_PATH}: {exc}"
        ) from exc
    if selection.get("artifact_type") != "EvidenceRadar_CambridgeJournalSelection":
        raise RadarControlError("Cambridge journal selection has invalid artifact_type")
    if str(selection.get("schema_version") or "") != "1.0":
        raise RadarControlError("unsupported Cambridge journal selection schema_version")
    if str(selection.get("source_id") or "") != "cambridge_core_oa":
        raise RadarControlError("Cambridge journal selection must bind cambridge_core_oa")
    family_url = str(selection.get("family_url") or "").strip()
    endpoint_template = str(selection.get("endpoint_template") or "").strip()
    journals = selection.get("journals")
    max_pages = selection.get("max_pages_per_shard")
    if not family_url.startswith("https://www.cambridge.org/core/journals"):
        raise RadarControlError("Cambridge family_url must be a Cambridge Core journals URL")
    if "{slug}" not in endpoint_template or not endpoint_template.startswith(
        "https://www.cambridge.org/core/journals/"
    ):
        raise RadarControlError("Cambridge endpoint_template must contain {slug}")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
        raise RadarControlError("Cambridge max_pages_per_shard must be a positive integer")
    if not isinstance(journals, list) or not journals:
        raise RadarControlError("Cambridge journal selection requires journals[]")
    seen: set[str] = set()
    normalized_journals: list[dict[str, Any]] = []
    for index, raw in enumerate(journals):
        if not isinstance(raw, dict):
            raise RadarControlError(f"Cambridge journals[{index}] must be an object")
        slug = str(raw.get("slug") or "").strip().casefold()
        title = str(raw.get("title") or "").strip()
        if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise RadarControlError(f"Cambridge journals[{index}] has invalid slug")
        if slug in seen:
            raise RadarControlError(f"duplicate Cambridge journal slug: {slug}")
        if not title:
            raise RadarControlError(f"Cambridge journals[{index}] needs title")
        seen.add(slug)
        normalized_journals.append(
            {
                "shard_id": f"journal:{slug}",
                "journal_slug": slug,
                "journal_title": title,
                "endpoint": endpoint_template.format(slug=slug),
                "priority": str(raw.get("priority") or "supporting"),
                "topic_lines": [
                    str(value) for value in raw.get("topic_lines", []) if str(value)
                ],
            }
        )
    normalized = copy.deepcopy(selection)
    normalized["journals"] = normalized_journals
    return normalized


def _apply_template_defaults(master: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic defaults for known adapter templates."""

    normalized = copy.deepcopy(master)
    sources = normalized.get("sources")
    if not isinstance(sources, dict):
        return normalized
    for source_id, config in sources.items():
        if not isinstance(config, dict):
            continue
        if str(config.get("adapter") or "") != "publisher_listing":
            continue
        adapter_config = config.get("adapter_config")
        if not isinstance(adapter_config, dict):
            continue
        if str(adapter_config.get("template") or "") != "publisher_listing_v1":
            continue

        extract = adapter_config.get("extract")
        if extract is None:
            adapter_config["extract"] = copy.deepcopy(
                PUBLISHER_LISTING_V1_EXTRACT_DEFAULTS
            )
        elif isinstance(extract, dict):
            for key, value in PUBLISHER_LISTING_V1_EXTRACT_DEFAULTS.items():
                extract.setdefault(key, copy.deepcopy(value))
        # Let the core validator report malformed non-mapping extract values.

        inventory = adapter_config.get("inventory")
        if inventory is None:
            inventory = copy.deepcopy(PUBLISHER_LISTING_V1_INVENTORY_DEFAULTS)
            adapter_config["inventory"] = inventory
        elif isinstance(inventory, dict):
            for key, value in PUBLISHER_LISTING_V1_INVENTORY_DEFAULTS.items():
                inventory.setdefault(key, copy.deepcopy(value))
        # Keep malformed inventory values visible to callers; do not silently
        # replace them.  Generic v1 consumers can fail closed when using them.

        endpoint = str(config.get("endpoint") or "")
        if isinstance(inventory, dict) and "cambridge.org/" in endpoint.casefold():
            for key, value in CAMBRIDGE_CONTAINER_DEFAULTS.items():
                inventory.setdefault(key, copy.deepcopy(value))

        if source_id == "cambridge_core_oa" and isinstance(inventory, dict):
            selection = _load_cambridge_selection()
            shards = copy.deepcopy(selection["journals"])
            inventory.update(
                {
                    "scope": "curated_journal_articles",
                    "coverage_unit": "article",
                    "journal_level_coverage": False,
                    "shard_strategy": "curated_journal_allowlist",
                    "article_oa_guarantee": False,
                    "selection_id": str(selection.get("selection_id") or ""),
                    "family_url": str(selection.get("family_url") or ""),
                    "selected_journal_count": len(shards),
                    "shards": shards,
                }
            )
            pagination = adapter_config.get("pagination")
            if isinstance(pagination, dict):
                pagination["max_pages"] = int(selection["max_pages_per_shard"])
            configured_oa_mode = str(config.get("oa_mode") or "")
            if configured_oa_mode:
                config.setdefault("configured_oa_mode", configured_oa_mode)
            config["oa_mode"] = "verify_per_work"
        elif str(config.get("oa_mode") or "").casefold() == "fully_oa":
            # A publisher-wide OA-article listing proves article-level OA.  It
            # does not prove that every parent journal is fully OA.
            config["configured_oa_mode"] = "fully_oa"
            config["oa_mode"] = "publisher_oa_articles"
    return normalized


def _restore_semantic_adapter(runtime: MasterRuntime) -> MasterRuntime:
    """Expose semantic source identity while retaining the internal dispatch path."""

    source_catalog = runtime.streams.get("source_catalog", {})
    if not isinstance(source_catalog, dict):
        return runtime
    for config in source_catalog.values():
        if not isinstance(config, dict):
            continue
        configured_adapter = str(config.get("configured_adapter") or "")
        if configured_adapter == "publisher_listing":
            dispatch_adapter = str(config.get("adapter") or "").strip()
            if dispatch_adapter:
                config["dispatch_adapter"] = dispatch_adapter
            config["adapter"] = configured_adapter
    return runtime


def validate_master(master: dict[str, Any]) -> None:
    _core.validate_master(_apply_template_defaults(master))


def load_master(path: Path) -> dict[str, Any]:
    value = _core._load_mapping(path, json_only=True)
    normalized = _apply_template_defaults(value)
    _core.validate_master(normalized)
    return normalized


def compile_runtime(
    master: dict[str, Any],
    *,
    legacy_streams: dict[str, Any],
    legacy_scoring: dict[str, Any],
    profile_id: str | None = None,
) -> MasterRuntime:
    runtime = _core.compile_runtime(
        _apply_template_defaults(master),
        legacy_streams=legacy_streams,
        legacy_scoring=legacy_scoring,
        profile_id=profile_id,
    )
    return _restore_semantic_adapter(runtime)


def load_master_runtime(path: Path, profile_id: str | None = None) -> MasterRuntime:
    master = load_master(path)
    streams_path, scoring_path = _core._legacy_paths(path, master)
    return compile_runtime(
        master,
        legacy_streams=_core._load_mapping(streams_path),
        legacy_scoring=_core._load_mapping(scoring_path),
        profile_id=profile_id,
    )


def legacy_projection(
    master_path: Path, profile_id: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_master_runtime(master_path, profile_id=profile_id)
    return runtime.streams, runtime.scoring
