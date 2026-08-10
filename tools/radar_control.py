from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RadarControlError(RuntimeError):
    pass


ALLOWED_STAGES = {"discovery", "bounded_verification"}
ALLOWED_SOURCE_STATUS = {"active", "planned", "disabled"}
ALLOWED_DISCOVERY_TIERS = {"primary", "supplemental"}
IMPLEMENTED_ADAPTERS = {
    "pubmed",
    "europe_pmc",
    "openalex",
    "arxiv",
    "openreview",
    "acl_anthology",
    "pmlr",
    "rss_atom",
    "bounded_publisher",
}


@dataclass(frozen=True)
class MasterRuntime:
    profile_id: str
    streams: dict[str, Any]
    scoring: dict[str, Any]
    category_order: list[str]
    source_endpoints: dict[str, str]
    source_adapters: dict[str, str]
    category_labels_zh_tw: dict[str, str]
    taxonomy: dict[str, Any]


def load_master(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RadarControlError(f"cannot load master control {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RadarControlError("master control must be a JSON object")
    validate_master(value)
    return value


def _require_mapping(master: dict[str, Any], key: str) -> dict[str, Any]:
    value = master.get(key)
    if not isinstance(value, dict):
        raise RadarControlError(f"master.{key} must be an object")
    return value


def _resolve_source_groups(master: dict[str, Any], stream: dict[str, Any]) -> list[str]:
    groups = _require_mapping(master, "source_groups")
    resolved: list[str] = [str(item) for item in stream.get("sources", [])]
    for group_id in stream.get("source_groups", []):
        if group_id not in groups:
            raise RadarControlError(f"unknown source group: {group_id}")
        values = groups[group_id]
        if not isinstance(values, list):
            raise RadarControlError(f"source group {group_id} must be an array")
        resolved.extend(str(item) for item in values)
    return list(dict.fromkeys(resolved))


def validate_master(master: dict[str, Any]) -> None:
    if master.get("artifact_type") != "EvidenceRadar_MasterControl":
        raise RadarControlError("artifact_type must be EvidenceRadar_MasterControl")
    if str(master.get("schema_version") or "") != "1.0":
        raise RadarControlError("unsupported master schema_version")

    sources = _require_mapping(master, "sources")
    domains = _require_mapping(_require_mapping(master, "taxonomy"), "domains")
    categories = _require_mapping(master, "routing_categories")
    streams = _require_mapping(master, "streams")
    profiles = _require_mapping(master, "profiles")

    for source_id, config in sources.items():
        if not isinstance(config, dict):
            raise RadarControlError(f"source {source_id} must be an object")
        stage = str(config.get("stage") or "")
        if stage not in ALLOWED_STAGES:
            raise RadarControlError(f"source {source_id} has invalid stage {stage!r}")
        status = str(config.get("status") or "")
        if status not in ALLOWED_SOURCE_STATUS:
            raise RadarControlError(f"source {source_id} has invalid status {status!r}")
        adapter = str(config.get("adapter") or "")
        if stage == "discovery":
            tier = str(config.get("discovery_tier") or "primary")
            if tier not in ALLOWED_DISCOVERY_TIERS:
                raise RadarControlError(f"source {source_id} has invalid discovery_tier {tier!r}")
        if status == "active" and adapter not in IMPLEMENTED_ADAPTERS:
            raise RadarControlError(
                f"active source {source_id} uses an unimplemented adapter {adapter!r}"
            )
        for domain in config.get("domains", []):
            if domain != "*" and domain not in domains:
                raise RadarControlError(f"source {source_id} references unknown domain {domain}")
        if adapter == "rss_atom" and status == "active":
            feeds = config.get("feeds")
            if not isinstance(feeds, list) or not feeds:
                raise RadarControlError(f"active rss_atom source {source_id} needs feeds[]")

    for stream_id, stream in streams.items():
        if not isinstance(stream, dict):
            raise RadarControlError(f"stream {stream_id} must be an object")
        category = str(stream.get("category") or "")
        if category not in categories:
            raise RadarControlError(f"stream {stream_id} references unknown category {category}")
        for domain in stream.get("domains", []):
            if domain not in domains:
                raise RadarControlError(f"stream {stream_id} references unknown domain {domain}")
        queries = stream.get("queries")
        if not isinstance(queries, list) or not queries:
            raise RadarControlError(f"stream {stream_id} must define at least one query")
        for source_id in _resolve_source_groups(master, stream):
            if source_id not in sources:
                raise RadarControlError(f"stream {stream_id} references unknown source {source_id}")
            if sources[source_id].get("status") != "active" or not sources[source_id].get("enabled"):
                raise RadarControlError(
                    f"stream {stream_id} references source {source_id} that is not active+enabled"
                )

    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            raise RadarControlError(f"profile {profile_id} must be an object")
        active_streams = [str(item) for item in profile.get("streams", [])]
        unknown = sorted(set(active_streams) - set(streams))
        if unknown:
            raise RadarControlError(
                f"profile {profile_id} references unknown streams: {', '.join(unknown)}"
            )
        order = [str(item) for item in profile.get("category_order", [])]
        unknown_categories = sorted(set(order) - set(categories))
        if unknown_categories:
            raise RadarControlError(
                f"profile {profile_id} references unknown categories: {', '.join(unknown_categories)}"
            )

    default_profile = str(master.get("control_plane", {}).get("default_profile") or "")
    if default_profile not in profiles:
        raise RadarControlError(f"default profile does not exist: {default_profile}")


def compile_runtime(master: dict[str, Any], profile_id: str | None = None) -> MasterRuntime:
    validate_master(master)
    profiles = master["profiles"]
    if profile_id is None:
        profile_id = str(master["control_plane"]["default_profile"])
    if profile_id not in profiles:
        raise RadarControlError(f"unknown profile: {profile_id}")
    profile = profiles[profile_id]
    sources = master["sources"]
    selected_streams: dict[str, Any] = {}
    requested_source_ids: set[str] = set()

    for stream_id in profile["streams"]:
        source_stream = master["streams"][stream_id]
        resolved_sources = _resolve_source_groups(master, source_stream)
        requested_source_ids.update(resolved_sources)
        selected_streams[stream_id] = {
            "sources": resolved_sources,
            "relevance_terms": copy.deepcopy(source_stream.get("relevance_terms", [])),
            "queries": copy.deepcopy(source_stream.get("queries", [])),
        }

    source_catalog: dict[str, Any] = {}
    stage_by_source: dict[str, str] = {}
    verification_sources: list[str] = []
    for source_id in sorted(requested_source_ids):
        config = sources[source_id]
        source_catalog[source_id] = {
            "role": str(config.get("role") or ""),
            "stage": str(config["stage"]),
            "adapter": str(config["adapter"]),
            "kind": str(config.get("kind") or ""),
            **({"discovery_tier": str(config["discovery_tier"])} if config.get("discovery_tier") else {}),
            "check_summary_required": True,
            **({"preferred_domain": config["preferred_domains"][0]} if config.get("preferred_domains") else {}),
            **({"endpoint": config["endpoint"]} if config.get("endpoint") else {}),
            **({"feeds": copy.deepcopy(config["feeds"])} if config.get("feeds") else {}),
        }
        stage_by_source[source_id] = str(config["stage"])
        if config["stage"] == "bounded_verification":
            verification_sources.append(source_id)

    streams_runtime = {
        "execution": copy.deepcopy(master["execution"]),
        "window": copy.deepcopy(master["window"]),
        "source_check_contract": {
            **copy.deepcopy(master["source_check_contract"]),
            "stage_by_source": stage_by_source,
            "bounded_verification_sources": sorted(verification_sources),
        },
        "source_catalog": source_catalog,
        "candidate_guidance": copy.deepcopy(master["candidate_guidance"]),
        "streams": selected_streams,
        "control_plane": {
            "master_schema_version": master["schema_version"],
            "profile_id": profile_id,
        },
    }

    categories: dict[str, Any] = {}
    category_min: dict[str, int] = {}
    for stream_id in profile["streams"]:
        category_id = str(master["streams"][stream_id]["category"])
        categories.setdefault(category_id, {"streams": []})["streams"].append(stream_id)
        category_min[category_id] = int(master["routing_categories"][category_id]["min_relevance"])

    scoring_runtime = copy.deepcopy(master["scoring"])
    scoring_runtime["categories"] = categories
    scoring_runtime["category_min_relevance"] = category_min

    endpoints = {
        source_id: str(sources[source_id].get("endpoint") or "")
        for source_id in requested_source_ids
        if sources[source_id].get("endpoint")
    }
    adapters = {
        source_id: str(sources[source_id]["adapter"])
        for source_id in requested_source_ids
    }
    labels = {
        category_id: str(config.get("label_zh_tw") or category_id)
        for category_id, config in master["routing_categories"].items()
        if category_id in categories
    }
    return MasterRuntime(
        profile_id=profile_id,
        streams=streams_runtime,
        scoring=scoring_runtime,
        category_order=[str(item) for item in profile.get("category_order", []) if item in categories],
        source_endpoints=endpoints,
        source_adapters=adapters,
        category_labels_zh_tw=labels,
        taxonomy=copy.deepcopy(master["taxonomy"]),
    )


def load_master_runtime(path: Path, profile_id: str | None = None) -> MasterRuntime:
    return compile_runtime(load_master(path), profile_id=profile_id)


def legacy_projection(master: dict[str, Any], profile_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = compile_runtime(master, profile_id=profile_id)
    return runtime.streams, runtime.scoring
