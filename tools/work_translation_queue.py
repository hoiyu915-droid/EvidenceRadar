#!/usr/bin/env python3
"""Checkpointed ChatGPT Work translation queue for EvidenceRadar handoffs.

This module is deliberately outside the Radar producer fingerprint.  It does
not discover, rank, or publish candidates.  It only partitions one immutable
TranslationRequest, validates complete batch responses with the existing
handoff rules, maintains an atomic cumulative checkpoint, and builds the one
full TranslationResponse accepted by the request's exact producer commit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.strict_json import dumps as strict_json_dumps
from tools.strict_json import loads as strict_json_loads
from tools.translation_handoff import (
    TranslationHandoffError,
    _validate_translation_item,
    load_translation_request,
    validate_translation_request,
    validate_translation_response,
)

QUEUE_VERSION = "1.0"
PLAN_TYPE = "EvidenceRadar_TranslationBatchPlan"
BATCH_REQUEST_TYPE = "EvidenceRadar_TranslationBatchRequest"
BATCH_RESPONSE_TYPE = "EvidenceRadar_TranslationBatchResponse"
CHECKPOINT_TYPE = "EvidenceRadar_TranslationCheckpoint"
SUBMISSION_TYPE = "EvidenceRadar_TranslationSubmission"
RESPONSE_TYPE = "EvidenceRadar_TranslationResponse"
REQUEST_FILENAME = "EvidenceRadar_TranslationRequest.json"
RESPONSE_FILENAME = "EvidenceRadar_TranslationResponse.json"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WorkTranslationQueueError(RuntimeError):
    """Raised when queue state is stale, incomplete, or structurally invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    return strict_json_dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _bound_sha(value: Mapping[str, Any], field: str) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop(field, None)
    return sha256_json(payload)


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkTranslationQueueError(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkTranslationQueueError(f"{label} must be a JSON object")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any], *, replace: bool) -> None:
    path = Path(path)
    if path.exists() and not replace:
        raise WorkTranslationQueueError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = strict_json_dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            temporary_name = temporary.name
        Path(temporary_name).replace(path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkTranslationQueueError(f"{label} must be a positive integer")
    return value


def _required_string(value: Mapping[str, Any], field: str, *, label: str) -> str:
    observed = value.get(field)
    if not isinstance(observed, str) or not observed.strip():
        raise WorkTranslationQueueError(f"{label}.{field} must be a non-empty string")
    return observed


def _request_items(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = request.get("candidates")
    if not isinstance(items, list):
        raise WorkTranslationQueueError("translation request candidates must be an array")
    return [dict(item) for item in items]


def _request_ids(request: Mapping[str, Any]) -> list[str]:
    return [str(item["immutable_candidate_id"]) for item in _request_items(request)]


def _batch_id(request_sha256: str, batch_index: int, candidate_ids: list[str]) -> str:
    return sha256_json(
        {
            "request_sha256": request_sha256,
            "batch_index": batch_index,
            "candidate_ids": candidate_ids,
        }
    )


def build_batch_plan(
    request: Mapping[str, Any],
    *,
    max_items: int = 24,
    max_source_chars: int = 16_000,
) -> dict[str, Any]:
    """Return a deterministic greedy plan without altering request order."""

    try:
        validate_translation_request(request)
    except TranslationHandoffError as exc:
        raise WorkTranslationQueueError(str(exc)) from exc
    max_items = _positive_integer(max_items, label="max_items")
    max_source_chars = _positive_integer(max_source_chars, label="max_source_chars")
    request_sha = _required_string(request, "request_sha256", label="translation request")
    if not _SHA_RE.fullmatch(request_sha):
        raise WorkTranslationQueueError("translation request SHA-256 is malformed")

    groups: list[tuple[list[str], int]] = []
    current_ids: list[str] = []
    current_chars = 0
    for item in _request_items(request):
        candidate_id = str(item["immutable_candidate_id"])
        source_chars = len(str(item.get("title_en") or "")) + len(
            str(item.get("source_excerpt") or "")
        )
        source_chars = max(source_chars, 1)
        if current_ids and (
            len(current_ids) >= max_items
            or current_chars + source_chars > max_source_chars
        ):
            groups.append((current_ids, current_chars))
            current_ids = []
            current_chars = 0
        current_ids.append(candidate_id)
        current_chars += source_chars
    if current_ids:
        groups.append((current_ids, current_chars))

    batches: list[dict[str, Any]] = []
    for zero_index, (candidate_ids, source_chars) in enumerate(groups):
        batch_index = zero_index + 1
        batches.append(
            {
                "batch_index": batch_index,
                "batch_id": _batch_id(request_sha, batch_index, candidate_ids),
                "candidate_ids": candidate_ids,
                "source_chars": source_chars,
            }
        )
    plan: dict[str, Any] = {
        "schema_version": QUEUE_VERSION,
        "artifact_type": PLAN_TYPE,
        "request_sha256": request_sha,
        "max_items": max_items,
        "max_source_chars": max_source_chars,
        "candidate_count": len(_request_ids(request)),
        "batch_count": len(batches),
        "batches": batches,
    }
    plan["batch_plan_sha256"] = _bound_sha(plan, "batch_plan_sha256")
    return plan


def validate_batch_plan(
    request: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    required = {
        "schema_version",
        "artifact_type",
        "request_sha256",
        "max_items",
        "max_source_chars",
        "candidate_count",
        "batch_count",
        "batches",
        "batch_plan_sha256",
    }
    if set(plan) != required:
        raise WorkTranslationQueueError("batch plan fields are invalid")
    if plan.get("schema_version") != QUEUE_VERSION or plan.get("artifact_type") != PLAN_TYPE:
        raise WorkTranslationQueueError("batch plan version or artifact_type is invalid")
    expected = build_batch_plan(
        request,
        max_items=_positive_integer(plan.get("max_items"), label="batch plan max_items"),
        max_source_chars=_positive_integer(
            plan.get("max_source_chars"), label="batch plan max_source_chars"
        ),
    )
    if dict(plan) != expected:
        raise WorkTranslationQueueError("batch plan does not match the immutable request")
    return expected


def build_batch_request(
    request: Mapping[str, Any], plan: Mapping[str, Any], *, batch_index: int
) -> dict[str, Any]:
    validated_plan = validate_batch_plan(request, plan)
    batch_index = _positive_integer(batch_index, label="batch_index")
    try:
        batch = validated_plan["batches"][batch_index - 1]
    except IndexError as exc:
        raise WorkTranslationQueueError(f"batch_index is outside the plan: {batch_index}") from exc
    requested = {
        str(item["immutable_candidate_id"]): item for item in _request_items(request)
    }
    return {
        "schema_version": QUEUE_VERSION,
        "artifact_type": BATCH_REQUEST_TYPE,
        "request_sha256": request["request_sha256"],
        "batch_plan_sha256": validated_plan["batch_plan_sha256"],
        "batch_index": batch_index,
        "batch_count": validated_plan["batch_count"],
        "batch_id": batch["batch_id"],
        "instructions": list(request.get("instructions") or []),
        "candidates": [requested[candidate_id] for candidate_id in batch["candidate_ids"]],
    }


def validate_batch_response(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    response: Mapping[str, Any],
) -> list[dict[str, str]]:
    validated_plan = validate_batch_plan(request, plan)
    required = {
        "schema_version",
        "artifact_type",
        "request_sha256",
        "batch_plan_sha256",
        "batch_index",
        "batch_id",
        "items",
    }
    if set(response) != required:
        raise WorkTranslationQueueError("batch response fields are invalid")
    if response.get("schema_version") != QUEUE_VERSION or response.get("artifact_type") != BATCH_RESPONSE_TYPE:
        raise WorkTranslationQueueError("batch response version or artifact_type is invalid")
    if response.get("request_sha256") != request.get("request_sha256"):
        raise WorkTranslationQueueError("batch response is bound to another request")
    if response.get("batch_plan_sha256") != validated_plan.get("batch_plan_sha256"):
        raise WorkTranslationQueueError("batch response is bound to another plan")
    batch_index = _positive_integer(response.get("batch_index"), label="batch response index")
    try:
        batch = validated_plan["batches"][batch_index - 1]
    except IndexError as exc:
        raise WorkTranslationQueueError("batch response index is outside the plan") from exc
    if response.get("batch_id") != batch["batch_id"]:
        raise WorkTranslationQueueError("batch response batch_id mismatch")
    items = response.get("items")
    if not isinstance(items, list):
        raise WorkTranslationQueueError("batch response items must be an array")
    requested = {
        str(item["immutable_candidate_id"]): item for item in _request_items(request)
    }
    observed_ids: list[str] = []
    validated_items: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise WorkTranslationQueueError(f"batch response items[{index}] must be an object")
        candidate_id = str(item.get("immutable_candidate_id") or "")
        if candidate_id not in batch["candidate_ids"]:
            raise WorkTranslationQueueError(
                f"batch response contains an ID outside batch {batch_index}: {candidate_id or '<blank>'}"
            )
        try:
            candidate_id, title_zh, summary_zh = _validate_translation_item(
                requested[candidate_id], item, index=index
            )
        except TranslationHandoffError as exc:
            raise WorkTranslationQueueError(str(exc)) from exc
        observed_ids.append(candidate_id)
        validated_items.append(
            {
                "immutable_candidate_id": candidate_id,
                "title_zh_tw": title_zh,
                "summary_zh_tw": summary_zh,
            }
        )
    if observed_ids != batch["candidate_ids"]:
        raise WorkTranslationQueueError(
            "batch response must contain every planned candidate exactly once and in request order"
        )
    return validated_items


def _checkpoint_sha256(checkpoint: Mapping[str, Any]) -> str:
    return _bound_sha(checkpoint, "checkpoint_sha256")


def empty_checkpoint(request: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    validated_plan = validate_batch_plan(request, plan)
    checkpoint: dict[str, Any] = {
        "schema_version": QUEUE_VERSION,
        "artifact_type": CHECKPOINT_TYPE,
        "request_sha256": request["request_sha256"],
        "batch_plan_sha256": validated_plan["batch_plan_sha256"],
        "completed_batch_ids": [],
        "items": [],
    }
    checkpoint["checkpoint_sha256"] = _checkpoint_sha256(checkpoint)
    return checkpoint


def validate_checkpoint(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    validated_plan = validate_batch_plan(request, plan)
    required = {
        "schema_version",
        "artifact_type",
        "request_sha256",
        "batch_plan_sha256",
        "completed_batch_ids",
        "items",
        "checkpoint_sha256",
    }
    if set(checkpoint) != required:
        raise WorkTranslationQueueError("checkpoint fields are invalid")
    if checkpoint.get("schema_version") != QUEUE_VERSION or checkpoint.get("artifact_type") != CHECKPOINT_TYPE:
        raise WorkTranslationQueueError("checkpoint version or artifact_type is invalid")
    if checkpoint.get("request_sha256") != request.get("request_sha256"):
        raise WorkTranslationQueueError("checkpoint is bound to another request")
    if checkpoint.get("batch_plan_sha256") != validated_plan.get("batch_plan_sha256"):
        raise WorkTranslationQueueError("checkpoint is bound to another plan")
    if checkpoint.get("checkpoint_sha256") != _checkpoint_sha256(checkpoint):
        raise WorkTranslationQueueError("checkpoint SHA-256 mismatch")
    items = checkpoint.get("items")
    if not isinstance(items, list):
        raise WorkTranslationQueueError("checkpoint items must be an array")
    requested_items = {
        str(item["immutable_candidate_id"]): item for item in _request_items(request)
    }
    request_order = _request_ids(request)
    observed: dict[str, dict[str, str]] = {}
    observed_order: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise WorkTranslationQueueError(f"checkpoint items[{index}] must be an object")
        candidate_id = str(item.get("immutable_candidate_id") or "")
        if candidate_id not in requested_items:
            raise WorkTranslationQueueError(f"checkpoint contains unknown ID: {candidate_id or '<blank>'}")
        if candidate_id in observed:
            raise WorkTranslationQueueError(f"checkpoint contains duplicate ID: {candidate_id}")
        try:
            candidate_id, title_zh, summary_zh = _validate_translation_item(
                requested_items[candidate_id], item, index=index
            )
        except TranslationHandoffError as exc:
            raise WorkTranslationQueueError(str(exc)) from exc
        observed_order.append(candidate_id)
        observed[candidate_id] = {
            "immutable_candidate_id": candidate_id,
            "title_zh_tw": title_zh,
            "summary_zh_tw": summary_zh,
        }
    expected_order = [candidate_id for candidate_id in request_order if candidate_id in observed]
    if observed_order != expected_order:
        raise WorkTranslationQueueError("checkpoint items are not in immutable request order")

    completed_batch_ids: list[str] = []
    observed_ids = set(observed)
    for batch in validated_plan["batches"]:
        batch_ids = set(batch["candidate_ids"])
        overlap = batch_ids & observed_ids
        if overlap and overlap != batch_ids:
            raise WorkTranslationQueueError(
                f"checkpoint contains a partial batch: {batch['batch_id']}"
            )
        if overlap == batch_ids:
            completed_batch_ids.append(batch["batch_id"])
    if checkpoint.get("completed_batch_ids") != completed_batch_ids:
        raise WorkTranslationQueueError("checkpoint completed_batch_ids mismatch")
    return copy.deepcopy(dict(checkpoint))


def merge_batch_response(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    batch_response: Mapping[str, Any],
) -> dict[str, Any]:
    validated_checkpoint = validate_checkpoint(request, plan, checkpoint)
    new_items = validate_batch_response(request, plan, batch_response)
    existing = {
        str(item["immutable_candidate_id"]): dict(item)
        for item in validated_checkpoint["items"]
    }
    for item in new_items:
        candidate_id = item["immutable_candidate_id"]
        if candidate_id in existing and existing[candidate_id] != item:
            raise WorkTranslationQueueError(
                f"validated checkpoint item cannot be silently replaced: {candidate_id}"
            )
        existing[candidate_id] = item
    ordered_items = [existing[candidate_id] for candidate_id in _request_ids(request) if candidate_id in existing]
    completed_batch_ids: list[str] = []
    observed_ids = set(existing)
    for batch in validate_batch_plan(request, plan)["batches"]:
        if set(batch["candidate_ids"]).issubset(observed_ids):
            completed_batch_ids.append(batch["batch_id"])
    merged: dict[str, Any] = {
        "schema_version": QUEUE_VERSION,
        "artifact_type": CHECKPOINT_TYPE,
        "request_sha256": request["request_sha256"],
        "batch_plan_sha256": plan["batch_plan_sha256"],
        "completed_batch_ids": completed_batch_ids,
        "items": ordered_items,
    }
    merged["checkpoint_sha256"] = _checkpoint_sha256(merged)
    return validate_checkpoint(request, plan, merged)


def finalize_response(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    validated_checkpoint = validate_checkpoint(request, plan, checkpoint)
    if len(validated_checkpoint["items"]) != len(_request_ids(request)):
        raise WorkTranslationQueueError(
            f"checkpoint is incomplete: {len(validated_checkpoint['items'])}/"
            f"{len(_request_ids(request))} candidates"
        )
    response = {
        "schema_version": QUEUE_VERSION,
        "artifact_type": RESPONSE_TYPE,
        "request_sha256": request["request_sha256"],
        "items": validated_checkpoint["items"],
    }
    try:
        validate_translation_response(request, response)
    except TranslationHandoffError as exc:
        raise WorkTranslationQueueError(str(exc)) from exc
    return response


def _submission_sha256(submission: Mapping[str, Any]) -> str:
    return _bound_sha(submission, "submission_sha256")


def build_submission(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    repository: str,
    artifact_id: int,
    workflow_run_id: int,
    artifact_name: str,
    handoff_issue_number: int,
    created_at: str,
) -> dict[str, Any]:
    try:
        validate_translation_request(request)
    except TranslationHandoffError as exc:
        raise WorkTranslationQueueError(str(exc)) from exc
    try:
        validate_translation_response(request, response)
    except TranslationHandoffError as exc:
        raise WorkTranslationQueueError(str(exc)) from exc
    if not _REPOSITORY_RE.fullmatch(repository):
        raise WorkTranslationQueueError("repository must use owner/name form")
    _positive_integer(artifact_id, label="artifact_id")
    _positive_integer(workflow_run_id, label="workflow_run_id")
    _positive_integer(handoff_issue_number, label="handoff_issue_number")
    if not artifact_name.strip():
        raise WorkTranslationQueueError("artifact_name must be non-empty")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkTranslationQueueError("created_at must be an ISO-8601 timestamp") from exc
    if parsed_created_at.tzinfo is None:
        raise WorkTranslationQueueError("created_at must include a timezone")
    submission: dict[str, Any] = {
        "schema_version": QUEUE_VERSION,
        "artifact_type": SUBMISSION_TYPE,
        "created_at": created_at,
        "handoff_issue_number": handoff_issue_number,
        "request_artifact": {
            "repository": repository,
            "artifact_id": artifact_id,
            "workflow_run_id": workflow_run_id,
            "artifact_name": artifact_name,
            "request_sha256": request["request_sha256"],
        },
        "response": copy.deepcopy(dict(response)),
    }
    submission["submission_sha256"] = _submission_sha256(submission)
    return submission


def validate_submission(
    request: Mapping[str, Any], submission: Mapping[str, Any]
) -> dict[str, Any]:
    required = {
        "schema_version",
        "artifact_type",
        "created_at",
        "handoff_issue_number",
        "request_artifact",
        "response",
        "submission_sha256",
    }
    if set(submission) != required:
        raise WorkTranslationQueueError("translation submission fields are invalid")
    if submission.get("schema_version") != QUEUE_VERSION or submission.get("artifact_type") != SUBMISSION_TYPE:
        raise WorkTranslationQueueError("translation submission version or artifact_type is invalid")
    if submission.get("submission_sha256") != _submission_sha256(submission):
        raise WorkTranslationQueueError("translation submission SHA-256 mismatch")
    request_artifact = submission.get("request_artifact")
    if not isinstance(request_artifact, Mapping):
        raise WorkTranslationQueueError("translation submission request_artifact must be an object")
    artifact_required = {
        "repository",
        "artifact_id",
        "workflow_run_id",
        "artifact_name",
        "request_sha256",
    }
    if set(request_artifact) != artifact_required:
        raise WorkTranslationQueueError("translation submission request_artifact fields are invalid")
    repository = _required_string(request_artifact, "repository", label="request_artifact")
    if not _REPOSITORY_RE.fullmatch(repository):
        raise WorkTranslationQueueError("request_artifact.repository must use owner/name form")
    _positive_integer(request_artifact.get("artifact_id"), label="request_artifact.artifact_id")
    _positive_integer(
        request_artifact.get("workflow_run_id"), label="request_artifact.workflow_run_id"
    )
    _required_string(request_artifact, "artifact_name", label="request_artifact")
    if request_artifact.get("request_sha256") != request.get("request_sha256"):
        raise WorkTranslationQueueError("translation submission references another request")
    _positive_integer(submission.get("handoff_issue_number"), label="handoff_issue_number")
    created_at = _required_string(submission, "created_at", label="translation submission")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkTranslationQueueError(
            "translation submission created_at must be an ISO-8601 timestamp"
        ) from exc
    if parsed_created_at.tzinfo is None:
        raise WorkTranslationQueueError(
            "translation submission created_at must include a timezone"
        )
    response = submission.get("response")
    if not isinstance(response, Mapping):
        raise WorkTranslationQueueError("translation submission response must be an object")
    try:
        validate_translation_response(request, response)
    except TranslationHandoffError as exc:
        raise WorkTranslationQueueError(str(exc)) from exc
    return copy.deepcopy(dict(submission))


def extract_request_artifact(archive_path: Path, output_path: Path) -> None:
    """Extract exactly one regular request file from an Actions artifact ZIP."""

    try:
        with ZipFile(archive_path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1 or members[0].filename != REQUEST_FILENAME:
                raise WorkTranslationQueueError(
                    f"request artifact must contain exactly {REQUEST_FILENAME} at ZIP root"
                )
            member = members[0]
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode not in (0, 0o100000):
                raise WorkTranslationQueueError("request artifact member is not a regular file")
            payload = archive.read(member)
    except WorkTranslationQueueError:
        raise
    except (OSError, ValueError, BadZipFile) as exc:
        raise WorkTranslationQueueError(f"cannot read request artifact ZIP: {exc}") from exc
    output_path = Path(output_path)
    if output_path.exists():
        raise WorkTranslationQueueError(
            f"refusing to overwrite existing extracted request: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    load_translation_request(output_path)


def _print_summary(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="create a deterministic batch plan and requests")
    plan.add_argument("--request", type=Path, required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    plan.add_argument("--max-items", type=int, default=24)
    plan.add_argument("--max-source-chars", type=int, default=16_000)

    merge = subparsers.add_parser("merge-batch", help="validate one complete batch and checkpoint it")
    merge.add_argument("--request", type=Path, required=True)
    merge.add_argument("--plan", type=Path, required=True)
    merge.add_argument("--batch-response", type=Path, required=True)
    merge.add_argument("--checkpoint", type=Path, required=True)

    status = subparsers.add_parser("status", help="report completed and missing batches")
    status.add_argument("--request", type=Path, required=True)
    status.add_argument("--plan", type=Path, required=True)
    status.add_argument("--checkpoint", type=Path, required=True)

    finalize = subparsers.add_parser("finalize", help="build the full response from a complete checkpoint")
    finalize.add_argument("--request", type=Path, required=True)
    finalize.add_argument("--plan", type=Path, required=True)
    finalize.add_argument("--checkpoint", type=Path, required=True)
    finalize.add_argument("--response", type=Path, required=True)

    submission = subparsers.add_parser("build-submission", help="bind a complete response to its Actions artifact")
    submission.add_argument("--request", type=Path, required=True)
    submission.add_argument("--response", type=Path, required=True)
    submission.add_argument("--repository", required=True)
    submission.add_argument("--artifact-id", type=int, required=True)
    submission.add_argument("--workflow-run-id", type=int, required=True)
    submission.add_argument("--artifact-name", required=True)
    submission.add_argument("--handoff-issue-number", type=int, required=True)
    submission.add_argument("--created-at", required=True)
    submission.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate-submission", help="validate and extract a complete response")
    validate.add_argument("--request", type=Path, required=True)
    validate.add_argument("--submission", type=Path, required=True)
    validate.add_argument("--response-output", type=Path)

    extract = subparsers.add_parser("extract-request", help="safely extract an Actions request artifact")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "extract-request":
            extract_request_artifact(args.archive, args.output)
            request = load_translation_request(args.output)
            _print_summary(
                {
                    "request": str(args.output),
                    "request_sha256": request["request_sha256"],
                    "candidate_count": len(request["candidates"]),
                }
            )
            return 0

        request = load_translation_request(args.request)
        if args.command == "build-submission":
            response = _load_object(args.response, label="translation response")
            submission = build_submission(
                request,
                response,
                repository=args.repository,
                artifact_id=args.artifact_id,
                workflow_run_id=args.workflow_run_id,
                artifact_name=args.artifact_name,
                handoff_issue_number=args.handoff_issue_number,
                created_at=args.created_at,
            )
            _write_json_atomic(args.output, submission, replace=False)
            _print_summary(
                {
                    "submission": str(args.output),
                    "submission_sha256": submission["submission_sha256"],
                    "request_sha256": request["request_sha256"],
                    "candidate_count": len(response["items"]),
                }
            )
            return 0
        if args.command == "validate-submission":
            submission = validate_submission(
                request,
                _load_object(args.submission, label="translation submission"),
            )
            if args.response_output is not None:
                _write_json_atomic(
                    args.response_output,
                    submission["response"],
                    replace=False,
                )
            _print_summary(
                {
                    "submission_sha256": submission["submission_sha256"],
                    "request_sha256": request["request_sha256"],
                    "protocol_commit": request["protocol_commit"],
                    "run_id": request["run_id"],
                    "base_state_sha256": request["base_state_sha256"],
                    "handoff_issue_number": submission["handoff_issue_number"],
                    "artifact_id": submission["request_artifact"]["artifact_id"],
                    "artifact_name": submission["request_artifact"]["artifact_name"],
                    "workflow_run_id": submission["request_artifact"]["workflow_run_id"],
                    "repository": submission["request_artifact"]["repository"],
                    "candidate_count": len(submission["response"]["items"]),
                    **(
                        {"response": str(args.response_output)}
                        if args.response_output is not None
                        else {}
                    ),
                }
            )
            return 0
        if args.command == "plan":
            if args.output_dir.exists():
                raise WorkTranslationQueueError(
                    f"plan output directory already exists: {args.output_dir}"
                )
            plan = build_batch_plan(
                request,
                max_items=args.max_items,
                max_source_chars=args.max_source_chars,
            )
            (args.output_dir / "batches").mkdir(parents=True)
            _write_json_atomic(args.output_dir / "plan.json", plan, replace=False)
            for batch in plan["batches"]:
                batch_request = build_batch_request(
                    request, plan, batch_index=batch["batch_index"]
                )
                _write_json_atomic(
                    args.output_dir / "batches" / f"batch-{batch['batch_index']:04d}.json",
                    batch_request,
                    replace=False,
                )
            _write_json_atomic(
                args.output_dir / "checkpoint.json",
                empty_checkpoint(request, plan),
                replace=False,
            )
            _print_summary(
                {
                    "request_sha256": request["request_sha256"],
                    "batch_plan_sha256": plan["batch_plan_sha256"],
                    "candidate_count": plan["candidate_count"],
                    "batch_count": plan["batch_count"],
                    "output_dir": str(args.output_dir),
                }
            )
            return 0

        plan = validate_batch_plan(request, _load_object(args.plan, label="batch plan"))
        checkpoint = (
            validate_checkpoint(
                request,
                plan,
                _load_object(args.checkpoint, label="translation checkpoint"),
            )
            if args.checkpoint.exists()
            else empty_checkpoint(request, plan)
        )
        if args.command == "merge-batch":
            batch_response = _load_object(args.batch_response, label="batch response")
            merged = merge_batch_response(request, plan, checkpoint, batch_response)
            _write_json_atomic(args.checkpoint, merged, replace=True)
            _print_summary(
                {
                    "checkpoint": str(args.checkpoint),
                    "checkpoint_sha256": merged["checkpoint_sha256"],
                    "completed_batches": len(merged["completed_batch_ids"]),
                    "batch_count": plan["batch_count"],
                    "completed_candidates": len(merged["items"]),
                    "candidate_count": plan["candidate_count"],
                }
            )
            return 0
        if args.command == "status":
            completed = set(checkpoint["completed_batch_ids"])
            missing = [
                batch["batch_index"]
                for batch in plan["batches"]
                if batch["batch_id"] not in completed
            ]
            _print_summary(
                {
                    "request_sha256": request["request_sha256"],
                    "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                    "completed_batches": len(completed),
                    "batch_count": plan["batch_count"],
                    "completed_candidates": len(checkpoint["items"]),
                    "candidate_count": plan["candidate_count"],
                    "missing_batch_indexes": missing,
                }
            )
            return 0
        if args.command == "finalize":
            response = finalize_response(request, plan, checkpoint)
            _write_json_atomic(args.response, response, replace=False)
            _print_summary(
                {
                    "response": str(args.response),
                    "request_sha256": response["request_sha256"],
                    "candidate_count": len(response["items"]),
                }
            )
            return 0
        raise WorkTranslationQueueError(f"unsupported command: {args.command}")
    except (WorkTranslationQueueError, TranslationHandoffError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
