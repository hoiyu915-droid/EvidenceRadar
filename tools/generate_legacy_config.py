#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from radar_control import legacy_projection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    streams, scoring = legacy_projection(args.master, profile_id=args.profile)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "streams.yml").write_text(
        yaml.safe_dump(streams, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (args.out_dir / "scoring.yml").write_text(
        yaml.safe_dump(scoring, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
