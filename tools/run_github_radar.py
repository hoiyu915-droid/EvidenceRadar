#!/usr/bin/env python3
"""Compatibility entry point for the archived GitHub/local runner.

The full historical implementation is preserved in ``run_github_radar_core``.
This thin shim only teaches its source-coverage accounting that the new
``publisher_listing`` adapter is a recognized discovery capability.  Active
EvidenceRadar execution remains the ChatGPT Work lane; the archived runner
continues to fail closed if no executor actually services a configured source.
"""

from __future__ import annotations

from tools import run_github_radar_core as _core

_core.DISCOVERY_ADAPTER_KEYS.add("publisher_listing")

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


if __name__ == "__main__":
    raise SystemExit(_core.main())
