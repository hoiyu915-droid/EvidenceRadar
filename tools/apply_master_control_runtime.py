#!/usr/bin/env python3
"""Fail-closed validator for the checked-in master-controlled Radar runner.

Master routing, profile limits and Stage A/B profile binding are part of the
versioned producer. Formal workflows must never rewrite that producer. The
historical filename remains as a compatibility entrypoint for ``--check``.
"""
from __future__ import annotations

import argparse
from pathlib import Path


class RuntimePatchError(RuntimeError):
    pass


INTEGRATED_RUNNER_MARKERS = (
    "from tools.radar_control import (",
    "load_master_runtime,",
    "project_runtime_limits,",
    "from tools.publisher_feed import PublisherFeedError, fetch_feed_records",
    '"rss_atom": fetch_rss_atom',
    "def _apply_master_runtime_limits(",
    'parser.add_argument(\n        "--profile"',
    "featured_policy_note(featured_policy)",
    "translation request profile mismatch",
    "authoritative master control is missing: config/radar_master.json",
)
COMPATIBILITY_CORE_MARKER = "run_github_radar_core"


def validate_runner_source(source: str) -> None:
    missing = [marker for marker in INTEGRATED_RUNNER_MARKERS if marker not in source]
    if missing:
        raise RuntimePatchError(
            "runner is not the complete checked-in master-control producer; "
            "missing marker(s): " + ", ".join(missing)
        )


def patch_runner_source(source: str) -> str:
    """Compatibility API: validate and return the already-integrated source."""

    validate_runner_source(source)
    return source


def _implementation_path(path: Path) -> Path:
    """Resolve the implementation behind the checked-in compatibility wrapper."""

    path = Path(path)
    source = path.read_text(encoding="utf-8")
    if COMPATIBILITY_CORE_MARKER not in source:
        return path
    core = path.with_name("run_github_radar_core.py")
    if not core.is_file():
        raise RuntimePatchError(
            "runner compatibility entrypoint is missing run_github_radar_core.py"
        )
    return core


def patch_runner(path: Path, *, check_only: bool = False) -> bool:
    """Validate a runner without changing bytes; always returns ``False``."""

    if not check_only:
        raise RuntimePatchError(
            "write mode was retired; master-control must be committed in the producer"
        )
    implementation = _implementation_path(Path(path))
    validate_runner_source(implementation.read_text(encoding="utf-8"))
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.check:
        print("ERROR: write mode was retired; pass --check")
        return 2
    try:
        patch_runner(args.runner, check_only=True)
    except (OSError, RuntimePatchError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
