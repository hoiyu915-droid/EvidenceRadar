#!/usr/bin/env python3
"""Shared delivery-contract constants and producer-version checks.

The canonical report may be written by two execution lanes, but a bundle must
not be promoted by an older producer after the repository contract changes.
This module keeps that check dependency-free so it also travels in the Work
Pack.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


BUNDLE_FILENAMES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)

SHARED_CONTRACT_PATHS = (
    "EVIDENCE_RADAR_PROTOCOL.md",
    "requirements.txt",
    "config/deployment.yml",
    "config/output.yml",
    "config/scoring.yml",
    "config/streams.yml",
    "schemas/evidence-radar-evidence.schema.json",
    "schemas/evidence-radar-run.schema.json",
    "schemas/evidence-radar-state.schema.json",
    "docs/SEMANTIC_CONTRACT_V3.md",
)

GITHUB_PRODUCER_PATHS = SHARED_CONTRACT_PATHS + (
    "tools/delivery_contract.py",
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
)

WORK_PRODUCER_PATHS = SHARED_CONTRACT_PATHS + (
    "docs/MIGRATION_DUAL_LANE_1.0.md",
    "docs/WORK_SETUP.md",
    "docs/research_taxonomy.md",
    "templates/gpt-work-instructions.md",
    "tools/delivery_contract.py",
    "tools/merge_radar_state.py",
    "tools/package_work_delivery.py",
    "tools/render_report_from_artifacts.py",
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
    "tools/validate_gpt_work_artifacts.py",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def producer_paths(execution_lane: str) -> tuple[str, ...]:
    if execution_lane == "github_actions":
        return GITHUB_PRODUCER_PATHS
    if execution_lane == "chatgpt_work":
        return WORK_PRODUCER_PATHS
    return ()


def current_producer_errors(
    root: Path,
    *,
    execution_lane: str,
    protocol_commit: str,
) -> list[str]:
    """Return drift errors between *protocol_commit* and the current checkout.

    Artifact-only commits are allowed: the recorded producer commit may be an
    ancestor of HEAD as long as every lane-relevant producer/contract file is
    byte-identical.  A stale Work Pack or runner therefore fails closed while
    ordinary artifact commits remain valid.
    """

    root = Path(root).resolve()
    errors: list[str] = []
    paths = producer_paths(execution_lane)
    if not paths:
        return [f"unsupported execution_lane for producer check: {execution_lane!r}"]
    if not protocol_commit or protocol_commit.endswith("-dirty"):
        return ["public delivery requires a clean protocol_commit"]
    try:
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{protocol_commit}^{{commit}}"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return [f"protocol_commit is not available in repository history: {protocol_commit}"]

    for relative in paths:
        current_path = root / relative
        if not current_path.is_file():
            errors.append(f"current producer path is missing: {relative}")
            continue
        try:
            recorded = subprocess.run(
                ["git", "show", f"{resolved}:{relative}"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            errors.append(
                f"producer path is absent at protocol_commit {protocol_commit}: {relative}"
            )
            continue
        current = current_path.read_bytes()
        if current != recorded:
            errors.append(
                "producer drift: "
                f"{relative} differs between protocol_commit {protocol_commit} and current checkout"
            )
    return errors
