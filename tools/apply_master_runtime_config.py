#!/usr/bin/env python3
"""Project profile-specific master limits into ephemeral legacy runtime configs.

The repository keeps output.yml and deployment.yml as compatibility inputs.
This tool applies authoritative limits from radar_master.json to a disposable
runtime checkout before EvidenceRadar executes. It never changes source/query
routing; that remains the job of radar_control + the runner bridge.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import yaml

from tools.radar_control import RadarControlError, load_master_runtime


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


def effective_configs(root: Path, profile_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(root).resolve()
    runtime = load_master_runtime(root / "config" / "radar_master.json", profile_id=profile_id)
    output = copy.deepcopy(_load_yaml(root / "config" / "output.yml"))
    deployment = copy.deepcopy(_load_yaml(root / "config" / "deployment.yml"))

    selection = output.setdefault("selection", {})
    featured = selection.setdefault("featured", {})
    featured["target_min"] = int(runtime.limits["selection"]["featured_target_per_category"])
    featured["hard_max"] = int(runtime.limits["selection"]["featured_hard_max_per_category"])

    publisher = deployment.setdefault("publisher_output", {})
    publisher["target_min_per_run"] = int(runtime.limits["verification"]["publisher_target_per_run"])
    publisher["hard_max_per_run"] = int(runtime.limits["verification"]["publisher_hard_max_per_run"])
    publisher["per_domain_hard_max"] = int(runtime.limits["verification"]["publisher_per_domain_hard_max"])

    summary = {
        "profile_id": runtime.profile_id,
        "limits": copy.deepcopy(runtime.limits),
        "featured": copy.deepcopy(featured),
        "publisher_output": {
            key: publisher[key]
            for key in ("target_min_per_run", "hard_max_per_run", "per_domain_hard_max")
        },
    }
    return output, deployment, summary


def _atomic_yaml(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.master-runtime.tmp")
    temporary.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    temporary.replace(path)


def apply_runtime_config(root: Path, profile_id: str | None = None, *, check_only: bool = False) -> dict[str, Any]:
    output, deployment, summary = effective_configs(root, profile_id=profile_id)
    if not check_only:
        root = Path(root).resolve()
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
