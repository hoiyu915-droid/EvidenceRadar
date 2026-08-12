#!/usr/bin/env python3
"""Execute one terminal EvidenceRadar ChatGPT Work delivery.

ChatGPT Work performs the live searches and source reading with its native web
tools, then records those executed observations in one strict input ledger.
This deterministic executor binds the observations and Work-authored zh-TW
translations to candidate identities, advances State, renders and validates
the canonical four artifacts, and creates collision-safe delivery files.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.materialize_delivery_aliases import (
    DeliveryAliasError,
    materialize_aliases,
)
from tools.package_work_delivery import (
    WorkDeliveryError,
    package_work_delivery,
)
from tools.run_github_radar import (
    Candidate,
    DiscoveryResult,
    RadarRuntimeError,
    _candidate_from_payload,
    execute,
)
from tools.strict_json import dumps as strict_json_dumps
from tools.strict_json import load_path as strict_json_load_path
from tools.verify_work_pack import (
    WorkPackVerificationError,
    verify_extracted_root,
)

INPUT_TYPE = "EvidenceRadar_WorkInput"
INPUT_VERSION = "1.0"
INPUT_FIELDS = {
    "artifact_type",
    "schema_version",
    "run_id",
    "end_at",
    "profile_id",
    "raw_candidate_count",
    "queries",
    "source_access",
    "checked_sources",
    "searched_sources",
    "unavailable_sources",
    "priority_candidate_ids",
    "publisher_access",
    "publisher_warnings",
    "candidates",
}


class WorkExecutorError(RuntimeError):
    """Raised when Work observations cannot produce a terminal delivery."""


def _outside_pack(path: Path, root: Path, *, label: str) -> Path:
    resolved = Path(path).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise WorkExecutorError(f"{label} must be outside the verified Work Pack: {resolved}")


def _object_array(value: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WorkExecutorError(f"{field} must be an array of objects")
    return copy.deepcopy(value)


def _string_set(value: Any, *, field: str) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        raise WorkExecutorError(f"{field} must be a sorted unique string array")
    return set(value)


def _load_work_input(path: Path) -> dict[str, Any]:
    try:
        value = strict_json_load_path(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkExecutorError(f"cannot load strict Work input JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkExecutorError("Work input must be a JSON object")
    if value.get("artifact_type") != INPUT_TYPE or value.get("schema_version") != INPUT_VERSION:
        raise WorkExecutorError(
            f"Work input must declare {INPUT_TYPE} schema_version {INPUT_VERSION}"
        )
    unknown = sorted(set(value) - INPUT_FIELDS)
    missing = sorted(INPUT_FIELDS - {"run_id", "profile_id"} - set(value))
    if unknown or missing:
        raise WorkExecutorError(
            f"Work input fields are invalid; missing={missing!r} unknown={unknown!r}"
        )
    return value


def _candidate_inputs(
    value: Any,
) -> tuple[list[Candidate], dict[str, dict[str, str]]]:
    records = _object_array(value, field="candidates")
    candidates: list[Candidate] = []
    translations: dict[str, dict[str, str]] = {}
    for index, record in enumerate(records):
        if set(record) != {"work_id", "candidate", "translation"}:
            raise WorkExecutorError(
                f"candidates[{index}] must contain exactly work_id, candidate and translation"
            )
        work_id = record.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            raise WorkExecutorError(f"candidates[{index}].work_id must be non-empty")
        try:
            candidate = _candidate_from_payload(record.get("candidate"))
        except RadarRuntimeError as exc:
            raise WorkExecutorError(f"candidates[{index}]: {exc}") from exc
        if candidate.work_id != work_id:
            raise WorkExecutorError(
                f"candidates[{index}] work_id does not match its candidate identity"
            )
        translation = record.get("translation")
        if not isinstance(translation, dict):
            raise WorkExecutorError(f"candidates[{index}].translation must be an object")
        if work_id in translations:
            raise WorkExecutorError(f"duplicate candidate work_id: {work_id}")
        candidates.append(candidate)
        translations[work_id] = copy.deepcopy(translation)
    return candidates, translations


def _discovery_from_input(
    value: dict[str, Any],
) -> tuple[DiscoveryResult, dict[str, dict[str, str]], list[dict[str, Any]], list[str]]:
    candidates, translations = _candidate_inputs(value.get("candidates"))
    by_id = {candidate.work_id: candidate for candidate in candidates}
    priority_ids = value.get("priority_candidate_ids")
    if (
        not isinstance(priority_ids, list)
        or any(not isinstance(item, str) or not item for item in priority_ids)
        or priority_ids != list(dict.fromkeys(priority_ids))
    ):
        raise WorkExecutorError("priority_candidate_ids must be a unique string array")
    missing_priority = sorted(set(priority_ids) - set(by_id))
    if missing_priority:
        raise WorkExecutorError(
            "priority_candidate_ids contains unknown work IDs: "
            + ", ".join(missing_priority[:5])
        )
    raw_count = value.get("raw_candidate_count")
    if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < len(candidates):
        raise WorkExecutorError(
            "raw_candidate_count must be an integer at least as large as candidates"
        )
    discovery = DiscoveryResult(
        all_candidates=candidates,
        priority_candidates=[by_id[work_id] for work_id in priority_ids],
        raw_candidate_count=raw_count,
        queries=_object_array(value.get("queries"), field="queries"),
        source_access=_object_array(value.get("source_access"), field="source_access"),
        checked_sources=_string_set(value.get("checked_sources"), field="checked_sources"),
        searched_sources=_string_set(value.get("searched_sources"), field="searched_sources"),
        unavailable_sources=_string_set(
            value.get("unavailable_sources"), field="unavailable_sources"
        ),
    )
    publisher_access = _object_array(
        value.get("publisher_access"), field="publisher_access"
    )
    warnings = value.get("publisher_warnings")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise WorkExecutorError("publisher_warnings must be a string array")
    return discovery, translations, publisher_access, list(warnings)


def execute_work_input(
    *,
    root: Path,
    input_path: Path,
    run_dir: Path,
    delivery_dir: Path,
    run_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    verified = verify_extracted_root(root)
    input_path = _outside_pack(input_path, root, label="input")
    run_dir = _outside_pack(run_dir, root, label="run directory")
    delivery_dir = _outside_pack(delivery_dir, root, label="delivery directory")
    if run_dir.exists() or delivery_dir.exists():
        raise WorkExecutorError("run and delivery directories must both be fresh")
    value = _load_work_input(input_path)
    discovery, translations, publisher_access, publisher_warnings = _discovery_from_input(
        value
    )
    raw_end_at = value.get("end_at")
    try:
        end_at = datetime.fromisoformat(str(raw_end_at))
    except (TypeError, ValueError) as exc:
        raise WorkExecutorError("end_at must be an ISO 8601 date-time") from exc
    if end_at.tzinfo is None or end_at.utcoffset() is None:
        raise WorkExecutorError("end_at must include a timezone offset")
    selected_run_id = run_id or str(value.get("run_id") or "").strip() or None
    selected_profile = (
        profile_id or str(value.get("profile_id") or "").strip() or "owner_daily"
    )
    protocol_commit = str(verified.get("source_commit") or "")
    if not protocol_commit:
        raise WorkExecutorError("verified Work Pack is missing source_commit")

    run_dir.mkdir(parents=True)
    state_path = run_dir / "EvidenceRadar_State.json"
    seed_state = root / "state/current/EvidenceRadar_State.json"
    shutil.copyfile(seed_state, state_path)

    def discoverer(*_args: Any, **_kwargs: Any) -> DiscoveryResult:
        return discovery

    def publisher_probe(
        items: list[Candidate],
        _config: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[list[tuple[Candidate, dict[str, Any]]], list[dict[str, Any]], list[str]]:
        eligible = {candidate.work_id for candidate in items}
        observed = {
            str(record.get("work_id") or "")
            for record in publisher_access
            if record.get("work_id")
        }
        extra = sorted(observed - eligible)
        if extra:
            raise RadarRuntimeError(
                "publisher_access is not bound to a qualifying priority candidate: "
                + ", ".join(extra[:5])
            )
        successes = [
            (candidate, record)
            for candidate in items
            for record in publisher_access
            if record.get("work_id") == candidate.work_id
            and record.get("status") == "SUCCESS"
        ]
        return successes, copy.deepcopy(publisher_access), list(publisher_warnings)

    summary = execute(
        root=root,
        output_dir=run_dir,
        state_path=state_path,
        end_at=end_at,
        run_id=selected_run_id,
        execution_lane="chatgpt_work",
        protocol_commit=protocol_commit,
        profile_id=selected_profile,
        work_translation_overrides=translations,
        discoverer=discoverer,
        publisher_probe=publisher_probe,
    )
    delivery = package_work_delivery(
        run_dir,
        delivery_dir,
        run_id=str(summary["run_id"]),
        validation_root=root,
        input_manifest=root / "manifest.json",
        expected_lane="chatgpt_work",
    )
    aliases = materialize_aliases(run_dir, delivery_dir)
    return {
        **summary,
        "status": "COMPLETE",
        "canonical_artifacts": [str(run_dir / name) for name in (
            "EvidenceRadar_Report.html",
            "EvidenceRadar_State.json",
            "EvidenceRadar_Evidence.json",
            "EvidenceRadar_Run.json",
        )],
        "delivery_aliases": [str(path) for path in aliases],
        "delivery_archive": str(delivery.archive_path),
        "delivery_checksum": str(delivery.checksum_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    try:
        summary = execute_work_input(
            root=args.root,
            input_path=args.input,
            run_dir=args.run_dir,
            delivery_dir=args.delivery_dir,
            run_id=args.run_id,
            profile_id=args.profile,
        )
    except (
        DeliveryAliasError,
        OSError,
        RadarRuntimeError,
        ValueError,
        WorkDeliveryError,
        WorkExecutorError,
        WorkPackVerificationError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(strict_json_dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
