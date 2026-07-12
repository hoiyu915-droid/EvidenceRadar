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

_BASE_CLASSIFY = quality.classify_study
EXPLICIT_ANIMAL_TITLE = (
    "animal model",
    "animal models",
    "in mice",
    "in rats",
    "mouse model",
    "rat model",
    "murine model",
)


def classify_with_explicit_animal_gate(paper):
    title = paper.title.casefold()
    if any(signal in title for signal in EXPLICIT_ANIMAL_TITLE):
        types = {value.casefold() for value in paper.publication_types}
        if "review" in types or "review" in title or "meta-analysis" in title:
            return "Preclinical synthesis", "Preclinical/U", 45
        return "Preclinical study", "Preclinical/U", 40
    return _BASE_CLASSIFY(paper)


quality.classify_study = classify_with_explicit_animal_gate


def main() -> int:
    quality.install()
    return radar.main()


if __name__ == "__main__":
    raise SystemExit(main())
