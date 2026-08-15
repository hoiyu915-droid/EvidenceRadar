#!/usr/bin/env python3
"""Compatibility entry point for the archived GitHub/local runner.

The full historical implementation is preserved in ``run_github_radar_core``.
This shim only teaches its source-coverage accounting that the new
``publisher_listing`` adapter is a recognized discovery capability.  Imports
resolve to the core module itself so existing monkey-patches and test hooks keep
the same global-function behavior as before this refactor.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Runtime/Work Pack verification requires the extracted package to remain
# byte-identical even when this compatibility entrypoint is invoked directly.
sys.dont_write_bytecode = True

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import run_github_radar_core as _core  # noqa: E402

_core.DISCOVERY_ADAPTER_KEYS.add("publisher_listing")

if __name__ == "__main__":
    raise SystemExit(_core.main())

# The import surface is deliberately the implementation module, not a copy of
# its globals. unittest.mock.patch("tools.run_github_radar.<name>") therefore
# patches the same globals used by discover()/run(), exactly as it did when the
# implementation lived in this file.
sys.modules[__name__] = _core
