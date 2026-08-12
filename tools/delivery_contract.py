#!/usr/bin/env python3
"""Shared delivery-contract constants and producer-version checks.

The canonical report may be written by two execution lanes, but a bundle must
not be promoted by an older producer after the repository contract changes.
This module keeps that check dependency-free so it also travels in the Work
Pack.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

BUNDLE_FILENAMES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)

PUBLICATION_CURRENT_ROOT = "artifacts/current"
PUBLICATION_RUNS_ROOT = "runs"
PUBLICATION_STATE_PATH = "state/current/EvidenceRadar_State.json"
PUBLICATION_HISTORY_PATH = "runs/pages-history.json"
SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,191}\Z")

CORE_CONTRACT_PATHS = (
    "EVIDENCE_RADAR_PROTOCOL.md",
    "requirements.txt",
    "requirements.lock",
    "config/deployment.yml",
    "config/radar_master.json",
    "config/output.yml",
    "config/scoring.yml",
    "config/streams.yml",
    "schemas/evidence-radar-evidence.schema.json",
    "schemas/evidence-radar-run.schema.json",
    "schemas/evidence-radar-state.schema.json",
    "docs/SEMANTIC_CONTRACT_V3.md",
)

GITHUB_TRANSLATION_CONTRACT_PATHS = (
    "schemas/evidence-radar-translation-request.schema.json",
    "schemas/evidence-radar-translation-response.schema.json",
)

GITHUB_PRODUCER_PATHS = CORE_CONTRACT_PATHS + GITHUB_TRANSLATION_CONTRACT_PATHS + (
    "tools/delivery_contract.py",
    "tools/featured_selection.py",
    "tools/network_safety.py",
    "tools/publisher_feed.py",
    "tools/radar_control.py",
    "tools/run_github_radar.py",
    "tools/strict_json.py",
    "tools/translation_handoff.py",
    "tools/validate_delivery_bundle.py",
)

WORK_PRODUCER_PATHS = CORE_CONTRACT_PATHS + (
    ".agents/skills/evidence-radar/SKILL.md",
    "WORK_ENTRY.md",
    "docs/MIGRATION_DUAL_LANE_1.0.md",
    "docs/WORK_SETUP.md",
    "docs/research_taxonomy.md",
    "templates/gpt-work-instructions.md",
    "tools/delivery_contract.py",
    "tools/featured_selection.py",
    "tools/merge_radar_state.py",
    "tools/network_safety.py",
    "tools/materialize_delivery_aliases.py",
    "tools/package_work_delivery.py",
    "tools/publisher_feed.py",
    "tools/publication_preflight.py",
    "tools/radar_control.py",
    "tools/render_report_from_artifacts.py",
    "tools/run_github_radar.py",
    "tools/run_work_radar.py",
    "tools/strict_json.py",
    "tools/validate_delivery_bundle.py",
    "tools/validate_gpt_work_artifacts.py",
    "tools/verify_work_pack.py",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def publication_stage_paths(run_id: str) -> tuple[str, ...]:
    """Return the only repository paths authorized for one publication.

    The path set is derived from the canonical delivery contract instead of
    copying artifact names into an automation prompt.  A malformed run ID is
    rejected before any path is constructed.
    """

    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError(f"unsafe EvidenceRadar run_id: {run_id!r}")
    current = tuple(f"{PUBLICATION_CURRENT_ROOT}/{name}" for name in BUNDLE_FILENAMES)
    immutable = tuple(
        f"{PUBLICATION_RUNS_ROOT}/{run_id}/{name}" for name in BUNDLE_FILENAMES
    )
    return current + (PUBLICATION_STATE_PATH,) + immutable + (PUBLICATION_HISTORY_PATH,)


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
