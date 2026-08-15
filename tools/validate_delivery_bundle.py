#!/usr/bin/env python3
"""Compatibility entry point for the canonical delivery validator.

The full validator is preserved in ``validate_delivery_bundle_core``.  This
shim only resolves the archived runner compatibility entrypoint to its checked-
in implementation when producer capabilities are inferred from source bytes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import validate_delivery_bundle_core as _core  # noqa: E402

_ORIGINAL_DECLARED_PRODUCER_RUNNER = _core._declared_producer_runner


def _runner_core_bytes(root: Path, run: Mapping[str, Any]) -> bytes | None:
    protocol_commit = str(run.get("protocol_commit") or "")
    root = Path(root).resolve()
    if protocol_commit and not protocol_commit.endswith("-dirty"):
        try:
            git_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            if Path(git_root).resolve() == root:
                return subprocess.run(
                    [
                        "git",
                        "show",
                        f"{protocol_commit}^{{commit}}:tools/run_github_radar_core.py",
                    ],
                    cwd=root,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ).stdout
        except (OSError, subprocess.CalledProcessError):
            pass
    try:
        return (root / "tools" / "run_github_radar_core.py").read_bytes()
    except OSError:
        return None


def _declared_producer_runner(
    root: Path, run: Mapping[str, Any]
) -> bytes | None:
    runner = _ORIGINAL_DECLARED_PRODUCER_RUNNER(root, run)
    if runner is None or b"run_github_radar_core" not in runner:
        return runner
    core = _runner_core_bytes(root, run)
    if core is None:
        return runner
    # Keep wrapper and implementation as one independently bound capability
    # surface. Existing marker checks can then remain unchanged.
    return runner + b"\n" + core


_core._declared_producer_runner = _declared_producer_runner

if __name__ == "__main__":
    raise SystemExit(_core.main())

sys.modules[__name__] = _core
