#!/usr/bin/env python3
"""EvidenceRadar v0.4 runtime-safe precision installer."""

from __future__ import annotations

try:
    from . import radar_v3 as precision
except ImportError:  # Executed as: python src/radar_v4.py
    import radar_v3 as precision  # type: ignore


# Capture the unpatched v0.2 gate before v0.3 installs runtime overrides.
_BASE_HARD_EXCLUSION = precision.strict.hard_exclusion_reason


def hard_exclusion_v4(paper):
    base = _BASE_HARD_EXCLUSION(paper)
    if base and base != "stream relevance gate failed":
        return base
    if not precision.title_primary_relevance(paper):
        return "title-primary stream relevance gate failed"
    return None


def install() -> None:
    # Replace v0.3's dynamic reference before installing it into the core module.
    precision.hard_exclusion_v3 = hard_exclusion_v4
    precision.install_precision_layer()
    precision.strict.hard_exclusion_reason = hard_exclusion_v4
    precision.core.select_candidate_pool = precision.candidate_pool_v3
    precision.core.feature_section = precision.feature_section_v3


def main() -> int:
    install()
    return precision.core.main()


if __name__ == "__main__":
    raise SystemExit(main())
