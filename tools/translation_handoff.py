#!/usr/bin/env python3
"""Fail-closed ordinary-chatbot translation handoff contract.

The request is the immutable boundary between discovery and publication.  Its
SHA-256 covers every field except ``request_sha256`` itself, including the
frozen resume context.  A response may supply translations only; it cannot
change candidate identity, ranking, event evidence, or source observations.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


REQUEST_ARTIFACT_TYPE = "EvidenceRadar_TranslationRequest"
RESPONSE_ARTIFACT_TYPE = "EvidenceRadar_TranslationResponse"
HANDOFF_VERSION = "1.0"


class TranslationHandoffError(RuntimeError):
    """Raised when a request or response violates the handoff contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def request_sha256(request: Mapping[str, Any]) -> str:
    payload = dict(request)
    payload.pop("request_sha256", None)
    return sha256_json(payload)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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


def write_translation_request(path: Path, request: Mapping[str, Any]) -> None:
    if Path(path).exists():
        raise TranslationHandoffError(
            f"translation request already exists and will not be overwritten: {path}"
        )
    validate_translation_request(request)
    _write_json_atomic(path, request)


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationHandoffError(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise TranslationHandoffError(f"{label} must be a JSON object")
    return value


def load_translation_request(path: Path) -> dict[str, Any]:
    request = load_json_object(path, label="translation request")
    validate_translation_request(request)
    return request


def _required_string(value: Mapping[str, Any], field: str, *, label: str) -> str:
    observed = value.get(field)
    if not isinstance(observed, str) or not observed.strip():
        raise TranslationHandoffError(f"{label}.{field} must be a non-empty string")
    return observed


def validate_translation_request(request: Mapping[str, Any]) -> None:
    if request.get("schema_version") != HANDOFF_VERSION:
        raise TranslationHandoffError("translation request schema_version must be 1.0")
    if request.get("artifact_type") != REQUEST_ARTIFACT_TYPE:
        raise TranslationHandoffError("translation request artifact_type is invalid")
    for field in (
        "run_id",
        "created_at",
        "execution_lane",
        "protocol_commit",
        "base_state_sha256",
        "request_sha256",
    ):
        _required_string(request, field, label="translation request")
    if not re.fullmatch(r"[0-9a-f]{64}", str(request["request_sha256"])):
        raise TranslationHandoffError("translation request SHA-256 is malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", str(request["base_state_sha256"])):
        raise TranslationHandoffError("translation request base State SHA-256 is malformed")
    if request_sha256(request) != request["request_sha256"]:
        raise TranslationHandoffError("translation request SHA-256 mismatch")
    candidates = request.get("candidates")
    if not isinstance(candidates, list):
        raise TranslationHandoffError("translation request candidates must be an array")
    observed_ids: list[str] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            raise TranslationHandoffError(
                f"translation request candidates[{index}] must be an object"
            )
        candidate_id = _required_string(
            item, "immutable_candidate_id", label=f"translation request candidates[{index}]"
        )
        _required_string(item, "title_en", label=f"translation request candidates[{index}]")
        if not isinstance(item.get("source_excerpt"), str):
            raise TranslationHandoffError(
                f"translation request candidates[{index}].source_excerpt must be a string"
            )
        if not isinstance(item.get("metadata"), Mapping):
            raise TranslationHandoffError(
                f"translation request candidates[{index}].metadata must be an object"
            )
        observed_ids.append(candidate_id)
    if len(observed_ids) != len(set(observed_ids)):
        raise TranslationHandoffError("translation request contains duplicate candidate IDs")
    if not isinstance(request.get("resume_context"), Mapping):
        raise TranslationHandoffError("translation request resume_context must be an object")


_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_NUMERIC_RE = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*%?")
_ABBREVIATION_RE = re.compile(r"\b(?:[A-Z]{2,}[A-Z0-9-]*|[A-Z]+\d+[A-Z0-9-]*)\b")
_FILLER = (
    "題名所示",
    "相關議題",
    "仍須自行查看原始來源",
    "仍須回到原始來源",
    "仍待來源審查",
    "待繁中題名翻譯",
    "無法提供摘要",
)
_RESULT_CLAIM_RE = re.compile(
    r"(?:結果顯示|研究發現|顯著(?:增加|降低|改善|相關|差異)|"
    r"證實|證明|導致|造成|可降低|可提高|改善了|增加了|降低了)"
)
_SOURCE_RESULT_RE = re.compile(
    r"\b(?:result|found|showed|demonstrated|associated|increased|decreased|"
    r"improved|significant|conclusion)\b",
    re.IGNORECASE,
)


def _numeric_tokens(value: str) -> set[str]:
    return set(_NUMERIC_RE.findall(value))


def _abbreviation_tokens(value: str) -> set[str]:
    return set(_ABBREVIATION_RE.findall(value))


def _validate_translation_item(
    request_item: Mapping[str, Any], response_item: Mapping[str, Any], *, index: int
) -> tuple[str, str, str]:
    candidate_id = str(response_item.get("immutable_candidate_id") or "").strip()
    if not candidate_id:
        raise TranslationHandoffError(
            f"translation response items[{index}].immutable_candidate_id is required"
        )
    if set(response_item) != {"immutable_candidate_id", "title_zh_tw", "summary_zh_tw"}:
        raise TranslationHandoffError(
            f"translation response items[{index}] may contain only immutable_candidate_id, "
            "title_zh_tw, and summary_zh_tw"
        )
    title_zh = str(response_item.get("title_zh_tw") or "").strip()
    summary_zh = str(response_item.get("summary_zh_tw") or "").strip()
    title_en = str(request_item["title_en"])
    excerpt = str(request_item.get("source_excerpt") or "")
    if not title_zh or not _HAN_RE.search(title_zh):
        raise TranslationHandoffError(f"{candidate_id}: title_zh_tw must contain Traditional Chinese")
    if any(token in title_zh for token in _FILLER):
        raise TranslationHandoffError(f"{candidate_id}: title_zh_tw contains prohibited filler")
    missing_numbers = _numeric_tokens(title_en) - _numeric_tokens(title_zh)
    if missing_numbers:
        raise TranslationHandoffError(
            f"{candidate_id}: title_zh_tw omitted number/year token(s): {sorted(missing_numbers)}"
        )
    missing_abbreviations = _abbreviation_tokens(title_en) - _abbreviation_tokens(title_zh)
    if missing_abbreviations:
        raise TranslationHandoffError(
            f"{candidate_id}: title_zh_tw omitted abbreviation(s): {sorted(missing_abbreviations)}"
        )
    if excerpt:
        if not summary_zh or not _HAN_RE.search(summary_zh):
            raise TranslationHandoffError(
                f"{candidate_id}: summary_zh_tw is required when source_excerpt is available"
            )
        if any(token in summary_zh for token in _FILLER):
            raise TranslationHandoffError(f"{candidate_id}: summary_zh_tw contains prohibited filler")
        invented_numbers = _numeric_tokens(summary_zh) - _numeric_tokens(excerpt)
        if invented_numbers:
            raise TranslationHandoffError(
                f"{candidate_id}: summary_zh_tw introduced unsupported number(s): {sorted(invented_numbers)}"
            )
        if _RESULT_CLAIM_RE.search(summary_zh) and not _SOURCE_RESULT_RE.search(excerpt):
            raise TranslationHandoffError(
                f"{candidate_id}: summary_zh_tw asserts results absent from the source excerpt"
            )
    elif summary_zh:
        raise TranslationHandoffError(
            f"{candidate_id}: summary_zh_tw must be empty when no source excerpt is available"
        )
    return candidate_id, title_zh, summary_zh


def validate_translation_response(
    request: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    validate_translation_request(request)
    if response.get("schema_version") != HANDOFF_VERSION:
        raise TranslationHandoffError("translation response schema_version must be 1.0")
    if response.get("artifact_type") != RESPONSE_ARTIFACT_TYPE:
        raise TranslationHandoffError("translation response artifact_type is invalid")
    if response.get("request_sha256") != request.get("request_sha256"):
        raise TranslationHandoffError("translation response is stale or bound to another request")
    if set(response) != {"schema_version", "artifact_type", "request_sha256", "items"}:
        raise TranslationHandoffError(
            "translation response may contain only schema_version, artifact_type, request_sha256, and items"
        )
    items = response.get("items")
    if not isinstance(items, list):
        raise TranslationHandoffError("translation response items must be an array")
    requested = {
        str(item["immutable_candidate_id"]): item for item in request.get("candidates", [])
    }
    observed_ids: list[str] = []
    validated: dict[str, dict[str, str]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TranslationHandoffError(f"translation response items[{index}] must be an object")
        candidate_id = str(item.get("immutable_candidate_id") or "").strip()
        if candidate_id not in requested:
            raise TranslationHandoffError(
                f"translation response contains unknown candidate ID: {candidate_id or '<blank>'}"
            )
        candidate_id, title_zh, summary_zh = _validate_translation_item(
            requested[candidate_id], item, index=index
        )
        observed_ids.append(candidate_id)
        validated[candidate_id] = {
            "title_zh_tw": title_zh,
            "summary_zh_tw": summary_zh,
        }
    if len(observed_ids) != len(set(observed_ids)):
        raise TranslationHandoffError("translation response contains duplicate candidate IDs")
    missing = sorted(set(requested) - set(observed_ids))
    if missing:
        raise TranslationHandoffError(
            f"translation response is missing {len(missing)} candidate ID(s): {', '.join(missing[:5])}"
        )
    return validated


def load_and_validate_translation_response(
    request: Mapping[str, Any], response_path: Path
) -> dict[str, dict[str, str]]:
    response = load_json_object(response_path, label="translation response")
    return validate_translation_response(request, response)
