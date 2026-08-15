from __future__ import annotations

import copy
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


def _apply_template_defaults(master: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic defaults for known adapter templates."""

    normalized = copy.deepcopy(master)
    sources = normalized.get("sources")
    if not isinstance(sources, dict):
        return normalized
    for config in sources.values():
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
            continue
        if not isinstance(extract, dict):
            # Let the core validator report a structural error rather than
            # silently replacing malformed caller configuration.
            continue
        for key, value in PUBLISHER_LISTING_V1_EXTRACT_DEFAULTS.items():
            extract.setdefault(key, copy.deepcopy(value))
    return normalized


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
    return _core.compile_runtime(
        _apply_template_defaults(master),
        legacy_streams=legacy_streams,
        legacy_scoring=legacy_scoring,
        profile_id=profile_id,
    )


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
