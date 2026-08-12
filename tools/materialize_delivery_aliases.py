#!/usr/bin/env python3
"""Create timestamp-prefixed direct-delivery aliases for a validated Radar run.

Canonical bundle filenames remain unchanged for validators, replay and State
handoff. This tool copies the four canonical bytes to user-facing sibling files
named YYYYMMDD_HHMMSS__<canonical name>, using the Run timestamp in Asia/Tokyo.
Existing aliases are never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.strict_json import loads as strict_json_loads

CANONICAL_FILES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)
DELIVERY_TIMEZONE = "Asia/Tokyo"


class DeliveryAliasError(RuntimeError):
    pass


def delivery_prefix(run: dict[str, object]) -> str:
    raw = str(run.get("finished_at") or run.get("started_at") or "")
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DeliveryAliasError(f"Run timestamp is invalid: {raw!r}") from exc
    if timestamp.tzinfo is None:
        raise DeliveryAliasError("Run timestamp must include a timezone offset")
    return timestamp.astimezone(ZoneInfo(DELIVERY_TIMEZONE)).strftime("%Y%m%d_%H%M%S")


def alias_names(run: dict[str, object]) -> dict[str, str]:
    prefix = delivery_prefix(run)
    return {name: f"{prefix}__{name}" for name in CANONICAL_FILES}


def materialize_aliases(source_dir: Path, output_dir: Path) -> list[Path]:
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    sources = {name: source_dir / name for name in CANONICAL_FILES}
    for name, path in sources.items():
        if not path.is_file() or path.is_symlink():
            raise DeliveryAliasError(f"missing or non-regular canonical artifact: {name}")
    try:
        run = strict_json_loads(sources["EvidenceRadar_Run.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryAliasError(f"cannot read EvidenceRadar_Run.json: {exc}") from exc
    if not isinstance(run, dict):
        raise DeliveryAliasError("EvidenceRadar_Run.json must contain an object")

    names = alias_names(run)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / names[name] for name in CANONICAL_FILES]
    collisions = [path.name for path in targets if path.exists()]
    if collisions:
        raise DeliveryAliasError("refusing to overwrite delivery aliases: " + ", ".join(collisions))

    written: list[Path] = []
    try:
        for name, target in zip(CANONICAL_FILES, targets):
            payload = sources[name].read_bytes()
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=output_dir,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_name = temporary.name
                os.replace(temporary_name, target)
                temporary_name = None
                written.append(target)
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = materialize_aliases(args.source_dir, args.output_dir)
    except (DeliveryAliasError, OSError) as exc:
        print(f"FAIL: {exc}")
        return 2
    print(json.dumps({"delivery_files": [str(path) for path in paths]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
