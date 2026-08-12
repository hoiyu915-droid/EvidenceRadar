#!/usr/bin/env python3
"""Fail-closed preflight for an EvidenceRadar publication stage plan."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_contract import BUNDLE_FILENAMES, publication_stage_paths
from tools.strict_json import loads as strict_json_loads


class PublicationPreflightError(RuntimeError):
    pass


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def validate_publication_preflight(
    *,
    run_id: str,
    manifest: Path | None = None,
    source_dir: Path | None = None,
    staged_paths: list[str] | None = None,
) -> dict[str, object]:
    try:
        authorized = publication_stage_paths(run_id)
    except ValueError as exc:
        raise PublicationPreflightError(str(exc)) from exc

    if manifest is not None:
        manifest = manifest.resolve()
        if not _regular_file(manifest):
            raise PublicationPreflightError(f"missing or non-regular manifest: {manifest}")
        try:
            document = strict_json_loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationPreflightError(f"cannot read manifest: {exc}") from exc
        canonical = document.get("canonical_files") if isinstance(document, dict) else None
        if canonical != list(BUNDLE_FILENAMES):
            raise PublicationPreflightError(
                "manifest canonical_files drift: "
                f"expected {list(BUNDLE_FILENAMES)!r}, got {canonical!r}"
            )
        manifest_run_id = document.get("run_id")
        if manifest_run_id != run_id:
            raise PublicationPreflightError(
                f"manifest run_id mismatch: expected {run_id!r}, got {manifest_run_id!r}"
            )

    if source_dir is not None:
        source_dir = source_dir.resolve()
        if not source_dir.is_dir() or source_dir.is_symlink():
            raise PublicationPreflightError(
                f"missing, symlinked, or non-directory source: {source_dir}"
            )
        for name in BUNDLE_FILENAMES:
            path = source_dir / name
            if not _regular_file(path):
                raise PublicationPreflightError(
                    f"missing or non-regular canonical artifact: {name}"
                )

    if staged_paths is not None:
        normalized = tuple(sorted(os.fspath(Path(path)) for path in staged_paths))
        expected = tuple(sorted(authorized))
        if normalized != expected:
            missing = sorted(set(expected) - set(normalized))
            unexpected = sorted(set(normalized) - set(expected))
            raise PublicationPreflightError(
                f"stage plan drift: missing={missing!r}; unexpected={unexpected!r}"
            )

    return {
        "status": "READY",
        "run_id": run_id,
        "canonical_files": list(BUNDLE_FILENAMES),
        "authorized_stage_paths": list(authorized),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--staged-path", action="append", dest="staged_paths")
    args = parser.parse_args(argv)
    try:
        result = validate_publication_preflight(
            run_id=args.run_id,
            manifest=args.manifest,
            source_dir=args.source_dir,
            staged_paths=args.staged_paths,
        )
    except PublicationPreflightError as exc:
        print(f"CONTRACT_PREFLIGHT_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
