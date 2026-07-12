#!/usr/bin/env python3
"""Canonical EvidenceRadar entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import quality, radar

# Generic age words such as "adult" and "adolescent" occur in animal papers.
# Only explicit human-participant signals may override an animal-species signal.
quality.HUMAN_SIGNALS = (
    "participant",
    "patient",
    "human",
    "people",
    "athlete",
    "volunteer",
)


def main() -> int:
    quality.install()
    return radar.main()


if __name__ == "__main__":
    raise SystemExit(main())
