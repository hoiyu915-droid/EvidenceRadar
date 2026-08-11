#!/usr/bin/env python3
"""Project master/profile limits into ephemeral legacy runtime configs.

The repository keeps streams.yml, output.yml and deployment.yml as compatibility
inputs. This tool overlays the authoritative limits from radar_master.json in a
disposable runtime checkout before EvidenceRadar executes. Source/query routing
is still compiled by radar_control + the runner bridge.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# This file is both an importable module and a documented direct CLI. When
# executed as ``python tools/apply_master_runtime_config.py``, Python places the
# tools directory rather than the repository root on sys.path. Add the root
# explicitly only for that direct-script mode so ``tools.radar_control`` keeps
# one canonical import path in both execution surfaces.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.radar_control import RadarControlError, load_master


class RuntimeConfigError(RuntimeError):
    pass


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeConfigError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeConfigError(f"{path} must contain a mapping")
    return value


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_mapping(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeConfigError(f"{label} must be a positive integer")
    return value


def _resolved_limits(master: dict[str, Any], profile_id: str | None) -> tuple[str, dict[str, Any]]:
    profiles = master.get("profiles")
    control = master.get("control_plane")
    base = master.get("limits")
    if not isinstance(profiles, dict) or not isinstance(control, dict) or not isinstance(base, dict):
        raise RuntimeConfigError("radar_master.json requires profiles, control_plane and limits objects")
    selected = profile_id or str(control.get("default_profile") or "")
    profile = profiles.get(selected)
    if not isinstance(profile, dict):
        raise RuntimeConfigError(f"unknown profile: {selected}")
    override = profile.get("limits", {})
    if not isinstance(override, dict):
        raise RuntimeConfigError(f"profile {selected} limits must be an object")
    limits = _merge_mapping(base, override)

    discovery = limits.get("discovery")
    ranking_pool = limits.get("ranking_pool")
    selection = limits.get("selection")
    verification = limits.get("verification")
    if not all(isinstance(value, dict) for value in (discovery, ranking_pool, selection, verification)):
        raise RuntimeConfigError(
            "limits requires discovery, ranking_pool, selection and verification objects"
        )
    _positive_int(discovery.get("max_per_query"), "limits.discovery.max_per_query")
    for field in ("max_per_source", "max_per_category", "global_candidate_hard_max"):
        if discovery.get(field) is not None:
            raise RuntimeConfigError(
                f"limits.discovery.{field} must remain null so the complete deduplicated ledger is never globally truncated"
            )
    ranking_max = ranking_pool.get("max_per_category")
    if ranking_max is not None:
        _positive_int(ranking_max, "limits.ranking_pool.max_per_category")
    featured_target = _positive_int(
        selection.get("featured_target_per_category"),
        "limits.selection.featured_target_per_category",
    )
    featured_hard = _positive_int(
        selection.get("featured_hard_max_per_category"),
        "limits.selection.featured_hard_max_per_category",
    )
    if featured_target > featured_hard:
        raise RuntimeConfigError("featured target cannot exceed featured hard max")
    per_category = selection.get("per_category", {})
    if not isinstance(per_category, dict):
        raise RuntimeConfigError("limits.selection.per_category must be an object")
    for category_id, values in per_category.items():
        if not isinstance(values, dict):
            raise RuntimeConfigError(f"limits.selection.per_category.{category_id} must be an object")
        category_target = _positive_int(
            values.get("target", featured_target),
            f"limits.selection.per_category.{category_id}.target",
        )
        category_hard = _positive_int(
            values.get("hard_max", featured_hard),
            f"limits.selection.per_category.{category_id}.hard_max",
        )
        if category_target > category_hard:
            raise RuntimeConfigError(
                f"limits.selection.per_category.{category_id} target cannot exceed hard max"
            )
    final_digest = selection.get("final_digest", {})
    if not isinstance(final_digest, dict):
        raise RuntimeConfigError("limits.selection.final_digest must be an object")
    final_target = final_digest.get("target")
    final_hard = final_digest.get("hard_max")
    if final_target is not None:
        final_target = _positive_int(final_target, "limits.selection.final_digest.target")
    if final_hard is not None:
        final_hard = _positive_int(final_hard, "limits.selection.final_digest.hard_max")
    if final_target is not None and final_hard is not None and final_target > final_hard:
        raise RuntimeConfigError("final digest target cannot exceed final digest hard max")
    publisher_target = _positive_int(
        verification.get("publisher_target_per_run"),
        "limits.verification.publisher_target_per_run",
    )
    publisher_hard = _positive_int(
        verification.get("publisher_hard_max_per_run"),
        "limits.verification.publisher_hard_max_per_run",
    )
    _positive_int(
        verification.get("publisher_per_domain_hard_max"),
        "limits.verification.publisher_per_domain_hard_max",
    )
    if publisher_target > publisher_hard:
        raise RuntimeConfigError("publisher target cannot exceed publisher hard max")
    return selected, limits


def effective_configs(
    root: Path, profile_id: str | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(root).resolve()
    master = load_master(root / "config" / "radar_master.json")
    selected_profile, limits = _resolved_limits(master, profile_id)
    streams = copy.deepcopy(_load_yaml(root / "config" / "streams.yml"))
    output = copy.deepcopy(_load_yaml(root / "config" / "output.yml"))
    deployment = copy.deepcopy(_load_yaml(root / "config" / "deployment.yml"))

    # Discovery remains globally uncapped: the old hard_max_per_category key is
    # removed rather than repurposed. Ranking and digest limits live later.
    streams["candidate_guidance"] = {
        "suggested_max_per_query": int(limits["discovery"]["max_per_query"]),
        "max_per_source": None,
        "max_per_category": None,
        "global_candidate_hard_max": None,
    }

    selection_runtime = output.setdefault("selection", {})
    ranking_runtime = selection_runtime.setdefault("ranking_pool", {})
    ranking_runtime["max_per_category"] = limits["ranking_pool"].get("max_per_category")
    featured = selection_runtime.setdefault("featured", {})
    featured["target_min"] = int(limits["selection"]["featured_target_per_category"])
    featured["hard_max"] = int(limits["selection"]["featured_hard_max_per_category"])
    featured["per_category"] = copy.deepcopy(limits["selection"].get("per_category", {}))
    featured["final_digest"] = copy.deepcopy(
        limits["selection"].get("final_digest", {"target": None, "hard_max": None})
    )

    publisher = deployment.setdefault("publisher_output", {})
    publisher["target_min_per_run"] = int(limits["verification"]["publisher_target_per_run"])
    publisher["hard_max_per_run"] = int(limits["verification"]["publisher_hard_max_per_run"])
    publisher["per_domain_hard_max"] = int(limits["verification"]["publisher_per_domain_hard_max"])

    summary = {
        "profile_id": selected_profile,
        "limits": copy.deepcopy(limits),
        "candidate_guidance": copy.deepcopy(streams["candidate_guidance"]),
        "ranking_pool": copy.deepcopy(ranking_runtime),
        "featured": copy.deepcopy(featured),
        "publisher_output": {
            key: publisher[key]
            for key in ("target_min_per_run", "hard_max_per_run", "per_domain_hard_max")
        },
    }
    return streams, output, deployment, summary


def _atomic_yaml(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.master-runtime.tmp")
    temporary.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    temporary.replace(path)


def apply_runtime_config(
    root: Path, profile_id: str | None = None, *, check_only: bool = False
) -> dict[str, Any]:
    streams, output, deployment, summary = effective_configs(root, profile_id=profile_id)
    if not check_only:
        root = Path(root).resolve()
        _atomic_yaml(root / "config" / "streams.yml", streams)
        _atomic_yaml(root / "config" / "output.yml", output)
        _atomic_yaml(root / "config" / "deployment.yml", deployment)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--profile")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = apply_runtime_config(args.root, profile_id=args.profile, check_only=args.check)
    except (RadarControlError, RuntimeConfigError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
