#!/usr/bin/env python3
"""Canonical EvidenceRadar entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import quality, radar


def main() -> int:
    quality.install()
    return radar.main()


if __name__ == "__main__":
    raise SystemExit(main())
