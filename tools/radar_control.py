from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class RadarControlError(RuntimeError):
    pass


ALLOWED_STAGES = {"discovery", "bounded_verification"}
ALLOWED_SOURCE_STATUS = {"active", "planned", "disabled"}
ALLOWED_DISCOVERY_TIERS = {"primary", "supplemental"}
IMPLEMENTED_ADAPTERS = {
    "pubmed", "europe_pmc", "openalex", "arxiv", "openreview",
    "acl_anthology", "pmlr", "rss_atom", "bounded_publisher",
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
    limits: dict[str, Any]


def _load_mapping(path: Path, *, json_only: bool = False) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if json_only else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RadarControlError(f"cannot load control input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RadarControlError(f"control input must be an object/mapping: {path}")
    return value


def load_master(path: Path) -> dict[str, Any]:
    value = _load_mapping(path, json_only=True)
    validate_master(value)
    return value


def _require_mapping(master: dict[str, Any], key: str) -> dict[str, Any]:
    value = master.get(key)
    if not isinstance(value, dict):
        raise RadarControlError(f"master.{key} must be an object")
    return value


def _resolve_source_groups(master: dict[str, Any], stream: dict[str, Any]) -> list[str]:
    groups = _require_mapping(master, "source_groups")
    resolved = [str(item) for item in stream.get("sources", [])]
    for group_id in stream.get("source_groups", []):
        if group_id not in groups:
            raise RadarControlError(f"unknown source group: {group_id}")
        values = groups[group_id]
        if not isinstance(values, list):
            raise RadarControlError(f"source group {group_id} must be an array")
        resolved.extend(str(item) for item in values)
    return list(dict.fromkeys(resolved))


def _positive_int(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RadarControlError(f"{path} must be a positive integer")
    return value


def _validate_limit_block(limits: dict[str, Any], *, path: str = "master.limits") -> None:
    discovery = limits.get("discovery")
    ranking_pool = limits.get("ranking_pool")
    selection = limits.get("selection")
    verification = limits.get("verification")
    if discovery is not None:
        if not isinstance(discovery, dict):
            raise RadarControlError(f"{path}.discovery must be an object")
        if "max_per_query" in discovery:
            _positive_int(discovery["max_per_query"], path=f"{path}.discovery.max_per_query")
        # Discovery-ledger caps stay explicit nulls: they must never truncate the
        # complete deduplicated candidate ledger.
        for field in ("max_per_source", "max_per_category", "global_candidate_hard_max"):
            if field in discovery and discovery[field] is not None:
                raise RadarControlError(
                    f"{path}.discovery.{field} is reserved for ledger-safe semantics; use null"
                )
    if ranking_pool is not None:
        if not isinstance(ranking_pool, dict):
            raise RadarControlError(f"{path}.ranking_pool must be an object")
        if ranking_pool.get("max_per_category") is not None:
            _positive_int(
                ranking_pool["max_per_category"],
                path=f"{path}.ranking_pool.max_per_category",
            )
    if selection is not None:
        if not isinstance(selection, dict):
            raise RadarControlError(f"{path}.selection must be an object")
        target = selection.get("featured_target_per_category")
        hard = selection.get("featured_hard_max_per_category")
        if target is not None:
            target = _positive_int(target, path=f"{path}.selection.featured_target_per_category")
        if hard is not None:
            hard = _positive_int(hard, path=f"{path}.selection.featured_hard_max_per_category")
        if target is not None and hard is not None and target > hard:
            raise RadarControlError(f"{path}.selection featured target cannot exceed hard max")
        per_category = selection.get("per_category", {})
        if not isinstance(per_category, dict):
            raise RadarControlError(f"{path}.selection.per_category must be an object")
        for category_id, values in per_category.items():
            if not isinstance(values, dict):
                raise RadarControlError(f"{path}.selection.per_category.{category_id} must be an object")
            category_target = _positive_int(
                values.get("target", target),
                path=f"{path}.selection.per_category.{category_id}.target",
            )
            category_hard = _positive_int(
                values.get("hard_max", hard),
                path=f"{path}.selection.per_category.{category_id}.hard_max",
            )
            if category_target > category_hard:
                raise RadarControlError(
                    f"{path}.selection.per_category.{category_id} target cannot exceed hard max"
                )
        final_digest = selection.get("final_digest", {})
        if not isinstance(final_digest, dict):
            raise RadarControlError(f"{path}.selection.final_digest must be an object")
        total_target = final_digest.get("target")
        total_hard = final_digest.get("hard_max")
        if total_target is not None:
            total_target = _positive_int(total_target, path=f"{path}.selection.final_digest.target")
        if total_hard is not None:
            total_hard = _positive_int(total_hard, path=f"{path}.selection.final_digest.hard_max")
        if total_target is not None and total_hard is not None and total_target > total_hard:
            raise RadarControlError(f"{path}.selection final digest target cannot exceed hard max")
    if verification is not None:
        if not isinstance(verification, dict):
            raise RadarControlError(f"{path}.verification must be an object")
        target = verification.get("publisher_target_per_run")
        hard = verification.get("publisher_hard_max_per_run")
        per_domain = verification.get("publisher_per_domain_hard_max")
        if target is not None:
            target = _positive_int(target, path=f"{path}.verification.publisher_target_per_run")
        if hard is not None:
            hard = _positive_int(hard, path=f"{path}.verification.publisher_hard_max_per_run")
        if per_domain is not None:
            _positive_int(per_domain, path=f"{path}.verification.publisher_per_domain_hard_max")
        if target is not None and hard is not None and target > hard:
            raise RadarControlError(f"{path}.verification publisher target cannot exceed hard max")


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_mapping(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _resolved_limits(master: dict[str, Any], profile: dict[str, Any], profile_id: str) -> dict[str, Any]:
    base = _require_mapping(master, "limits")
    override = profile.get("limits", {})
    if not isinstance(override, dict):
        raise RadarControlError(f"profile {profile_id} limits must be an object")
    merged = _merge_mapping(base, override)
    _validate_limit_block(merged, path=f"resolved limits for profile {profile_id}")
    required = {
        "discovery": ("max_per_query", "global_candidate_hard_max"),
        "ranking_pool": ("max_per_category",),
        "selection": (
            "featured_target_per_category",
            "featured_hard_max_per_category",
            "per_category",
            "final_digest",
        ),
        "verification": (
            "publisher_target_per_run",
            "publisher_hard_max_per_run",
            "publisher_per_domain_hard_max",
        ),
    }
    for section, fields in required.items():
        section_value = merged.get(section)
        if not isinstance(section_value, dict):
            raise RadarControlError(f"resolved limits for profile {profile_id}.{section} is missing")
        for field in fields:
            if field not in section_value:
                raise RadarControlError(
                    f"resolved limits for profile {profile_id}.{section}.{field} is missing"
                )
    return merged


def project_runtime_limits(
    output: dict[str, Any],
    deployment: dict[str, Any],
    limits: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return config copies with one resolved master limit set projected.

    Both the canonical runner and the legacy inspection CLI use this pure
    function so profile limits cannot drift between an in-memory execution and
    the compatibility projection.  Caller-owned mappings are never mutated.
    """

    _validate_limit_block(limits, path="resolved runtime limits")
    ranking_limits = limits.get("ranking_pool")
    selection_limits = limits.get("selection")
    verification_limits = limits.get("verification")
    if not all(
        isinstance(value, dict)
        for value in (ranking_limits, selection_limits, verification_limits)
    ):
        raise RadarControlError(
            "resolved runtime limits require ranking_pool, selection and verification objects"
        )

    projected_output = copy.deepcopy(output)
    projected_deployment = copy.deepcopy(deployment)
    selection_runtime = projected_output.setdefault("selection", {})
    if not isinstance(selection_runtime, dict):
        raise RadarControlError("output.selection must be a mapping")
    ranking_runtime = selection_runtime.setdefault("ranking_pool", {})
    featured_runtime = selection_runtime.setdefault("featured", {})
    if not isinstance(ranking_runtime, dict) or not isinstance(featured_runtime, dict):
        raise RadarControlError(
            "output selection ranking_pool/featured must be mappings"
        )
    try:
        ranking_runtime["max_per_category"] = ranking_limits.get("max_per_category")
        featured_runtime["target_min"] = int(
            selection_limits["featured_target_per_category"]
        )
        featured_runtime["hard_max"] = int(
            selection_limits["featured_hard_max_per_category"]
        )
        featured_runtime["per_category"] = copy.deepcopy(
            selection_limits.get("per_category", {})
        )
        featured_runtime["final_digest"] = copy.deepcopy(
            selection_limits.get(
                "final_digest", {"target": None, "hard_max": None}
            )
        )

        publisher_runtime = projected_deployment.setdefault("publisher_output", {})
        if not isinstance(publisher_runtime, dict):
            raise RadarControlError("deployment.publisher_output must be a mapping")
        publisher_runtime["target_min_per_run"] = int(
            verification_limits["publisher_target_per_run"]
        )
        publisher_runtime["hard_max_per_run"] = int(
            verification_limits["publisher_hard_max_per_run"]
        )
        publisher_runtime["per_domain_hard_max"] = int(
            verification_limits["publisher_per_domain_hard_max"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RadarControlError("resolved runtime limits are incomplete") from exc
    return projected_output, projected_deployment


def validate_master(master: dict[str, Any]) -> None:
    if master.get("artifact_type") != "EvidenceRadar_MasterControl":
        raise RadarControlError("artifact_type must be EvidenceRadar_MasterControl")
    if str(master.get("schema_version") or "") != "1.0":
        raise RadarControlError("unsupported master schema_version")
    sources = _require_mapping(master, "sources")
    domains = _require_mapping(_require_mapping(master, "taxonomy"), "domains")
    categories = _require_mapping(master, "routing_categories")
    streams = _require_mapping(master, "stream_routing")
    profiles = _require_mapping(master, "profiles")
    limits = _require_mapping(master, "limits")
    _validate_limit_block(limits)

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
            raise RadarControlError(f"active source {source_id} uses an unimplemented adapter {adapter!r}")
        for domain in config.get("domains", []):
            if domain != "*" and domain not in domains:
                raise RadarControlError(f"source {source_id} references unknown domain {domain}")
        if adapter == "rss_atom" and status == "active":
            feeds = config.get("feeds")
            if not isinstance(feeds, list) or not feeds:
                raise RadarControlError(f"active rss_atom source {source_id} needs feeds[]")

    # Source groups are a catalog convenience. They may exist before any stream
    # selects them, but every member must at least exist in the source catalog.
    for group_id, values in _require_mapping(master, "source_groups").items():
        if not isinstance(values, list):
            raise RadarControlError(f"source group {group_id} must be an array")
        unknown_sources = sorted(set(map(str, values)) - set(sources))
        if unknown_sources:
            raise RadarControlError(
                f"source group {group_id} references unknown sources: {', '.join(unknown_sources)}"
            )

    for stream_id, stream in streams.items():
        if not isinstance(stream, dict):
            raise RadarControlError(f"stream_routing.{stream_id} must be an object")
        category = str(stream.get("category") or "")
        if category not in categories:
            raise RadarControlError(f"stream {stream_id} references unknown category {category}")
        for domain in stream.get("domains", []):
            if domain not in domains:
                raise RadarControlError(f"stream {stream_id} references unknown domain {domain}")
        for source_id in _resolve_source_groups(master, stream):
            if source_id not in sources:
                raise RadarControlError(f"stream {stream_id} references unknown source {source_id}")
            if sources[source_id].get("status") != "active" or not sources[source_id].get("enabled"):
                raise RadarControlError(f"stream {stream_id} references source {source_id} that is not active+enabled")

    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            raise RadarControlError(f"profile {profile_id} must be an object")
        unknown = sorted(set(map(str, profile.get("streams", []))) - set(streams))
        if unknown:
            raise RadarControlError(f"profile {profile_id} references unknown streams: {', '.join(unknown)}")
        unknown_categories = sorted(set(map(str, profile.get("category_order", []))) - set(categories))
        if unknown_categories:
            raise RadarControlError(f"profile {profile_id} references unknown categories: {', '.join(unknown_categories)}")
        if "limits" in profile:
            if not isinstance(profile["limits"], dict):
                raise RadarControlError(f"profile {profile_id} limits must be an object")
            _validate_limit_block(profile["limits"], path=f"profile {profile_id}.limits")
        resolved_profile_limits = _resolved_limits(master, profile, profile_id)
        unknown_limit_categories = sorted(
            set(map(str, resolved_profile_limits["selection"].get("per_category", {}))) - set(categories)
        )
        if unknown_limit_categories:
            raise RadarControlError(
                f"profile {profile_id} selection limits reference unknown categories: "
                + ", ".join(unknown_limit_categories)
            )

    control = _require_mapping(master, "control_plane")
    authority = set(map(str, control.get("authoritative_for", [])))
    if "limits" not in authority:
        raise RadarControlError("control_plane.authoritative_for must include limits")
    for field in ("default_profile", "production_profile"):
        profile_id = str(control.get(field) or "")
        if profile_id not in profiles:
            raise RadarControlError(f"{field} does not exist: {profile_id}")


def _legacy_paths(master_path: Path, master: dict[str, Any]) -> tuple[Path, Path]:
    root = master_path.parent.parent
    control = master["control_plane"]
    return (
        root / str(control.get("legacy_query_catalog") or "config/streams.yml"),
        root / str(control.get("legacy_scoring_policy") or "config/scoring.yml"),
    )


def compile_runtime(
    master: dict[str, Any],
    *,
    legacy_streams: dict[str, Any],
    legacy_scoring: dict[str, Any],
    profile_id: str | None = None,
) -> MasterRuntime:
    validate_master(master)
    profiles = master["profiles"]
    profile_id = profile_id or str(master["control_plane"]["default_profile"])
    if profile_id not in profiles:
        raise RadarControlError(f"unknown profile: {profile_id}")
    profile = profiles[profile_id]
    limits = _resolved_limits(master, profile, profile_id)
    sources = master["sources"]
    routing = master["stream_routing"]
    legacy_catalog = legacy_streams.get("streams", {})
    if not isinstance(legacy_catalog, dict):
        raise RadarControlError("legacy streams.yml has no streams mapping")

    selected_streams: dict[str, Any] = {}
    requested_source_ids: set[str] = set()
    for stream_id in profile["streams"]:
        route = routing[stream_id]
        resolved_sources = _resolve_source_groups(master, route)
        requested_source_ids.update(resolved_sources)
        legacy = legacy_catalog.get(stream_id, {})
        queries = copy.deepcopy(route.get("queries", legacy.get("queries", [])))
        relevance_terms = copy.deepcopy(route.get("relevance_terms", legacy.get("relevance_terms", [])))
        if not isinstance(queries, list) or not queries:
            raise RadarControlError(f"stream {stream_id} has no query definition in master or legacy catalog")
        selected_streams[stream_id] = {
            "sources": resolved_sources,
            "relevance_terms": relevance_terms if isinstance(relevance_terms, list) else [],
            "queries": queries,
        }

    source_catalog: dict[str, Any] = {}
    stage_by_source: dict[str, str] = {}
    verification_sources: list[str] = []
    passthrough_fields = (
        "oa_mode",
        "quality_tier",
        "journal",
        "publisher",
        "activation_blocker",
        "crossref_issn",
    )
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
            **({field: copy.deepcopy(config[field]) for field in passthrough_fields if field in config}),
        }
        stage_by_source[source_id] = str(config["stage"])
        if config["stage"] == "bounded_verification":
            verification_sources.append(source_id)

    # Master limits replace the legacy candidate_guidance block. Discovery
    # remains globally uncapped so every deduplicated candidate survives into
    # the ledger; ranking/featured limits are a later presentation concern.
    candidate_guidance = {
        "suggested_max_per_query": int(limits["discovery"]["max_per_query"]),
        "max_per_source": limits["discovery"].get("max_per_source"),
        "max_per_category": limits["discovery"].get("max_per_category"),
        "global_candidate_hard_max": limits["discovery"].get("global_candidate_hard_max"),
    }
    streams_runtime = {
        "execution": copy.deepcopy(legacy_streams.get("execution", {})),
        "window": copy.deepcopy(legacy_streams.get("window", {})),
        "source_check_contract": {
            **copy.deepcopy(legacy_streams.get("source_check_contract", {})),
            "stage_by_source": stage_by_source,
            "bounded_verification_sources": sorted(verification_sources),
        },
        "source_catalog": source_catalog,
        "candidate_guidance": candidate_guidance,
        "streams": selected_streams,
        "control_plane": {
            "master_schema_version": master["schema_version"],
            "profile_id": profile_id,
            "limits_authoritative": True,
        },
    }

    scoring_runtime = copy.deepcopy(legacy_scoring)
    categories: dict[str, Any] = {}
    category_min: dict[str, int] = {}
    for stream_id in profile["streams"]:
        category_id = str(routing[stream_id]["category"])
        categories.setdefault(category_id, {"streams": []})["streams"].append(stream_id)
        category_min[category_id] = int(master["routing_categories"][category_id]["min_relevance"])
    scoring_runtime["categories"] = categories
    scoring_runtime["category_min_relevance"] = category_min

    endpoints = {source_id: str(sources[source_id].get("endpoint") or "") for source_id in requested_source_ids if sources[source_id].get("endpoint")}
    adapters = {source_id: str(sources[source_id]["adapter"]) for source_id in requested_source_ids}
    labels = {category_id: str(config.get("label_zh_tw") or category_id) for category_id, config in master["routing_categories"].items() if category_id in categories}
    return MasterRuntime(
        profile_id=profile_id,
        streams=streams_runtime,
        scoring=scoring_runtime,
        category_order=[str(item) for item in profile.get("category_order", []) if item in categories],
        source_endpoints=endpoints,
        source_adapters=adapters,
        category_labels_zh_tw=labels,
        taxonomy=copy.deepcopy(master["taxonomy"]),
        limits=copy.deepcopy(limits),
    )


def load_master_runtime(path: Path, profile_id: str | None = None) -> MasterRuntime:
    master = load_master(path)
    streams_path, scoring_path = _legacy_paths(path, master)
    return compile_runtime(
        master,
        legacy_streams=_load_mapping(streams_path),
        legacy_scoring=_load_mapping(scoring_path),
        profile_id=profile_id,
    )


def legacy_projection(master_path: Path, profile_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_master_runtime(master_path, profile_id=profile_id)
    return runtime.streams, runtime.scoring
