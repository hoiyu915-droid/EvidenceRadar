#!/usr/bin/env python3
"""Canonical EvidenceRadar entrypoint."""

from __future__ import annotations

try:
    from . import quality
    from . import radar
except ImportError:  # Executed as: python src/run.py
    import quality  # type: ignore
    import radar  # type: ignore


def main() -> int:
    quality.install()
    return radar.main()


if __name__ == "__main__":
    raise SystemExit(main())
