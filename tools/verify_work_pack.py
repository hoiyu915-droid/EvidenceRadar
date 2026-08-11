#!/usr/bin/env python3
"""Verify an EvidenceRadar ChatGPT Work Pack archive or extracted tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile, ZipInfo


MANIFEST_NAME = "manifest.json"
REQUIRED_CAPABILITIES = {
    "EXECUTOR_HTTP_TELEMETRY_V1",
    "MASTER_CONTROL_V1",
    "TERMINAL_FOUR_ARTIFACT_DELIVERY_V1",
}
CANONICAL_ARTIFACTS = [
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
]
REQUIRED_PATHS = {
    "WORK_ENTRY.md",
    "EVIDENCE_RADAR_PROTOCOL.md",
    "config/radar_master.json",
    "docs/SEMANTIC_CONTRACT_V3.md",
    "docs/WORK_SETUP.md",
    "docs/research_taxonomy.md",
    "schemas/evidence-radar-evidence.schema.json",
    "schemas/evidence-radar-run.schema.json",
    "schemas/evidence-radar-state.schema.json",
    "state/current/EvidenceRadar_State.json",
    "templates/gpt-work-instructions.md",
    "tools/delivery_contract.py",
    "tools/featured_selection.py",
    "tools/materialize_delivery_aliases.py",
    "tools/merge_radar_state.py",
    "tools/package_work_delivery.py",
    "tools/publisher_feed.py",
    "tools/radar_control.py",
    "tools/render_report_from_artifacts.py",
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
    "tools/validate_gpt_work_artifacts.py",
    "tools/verify_work_pack.py",
}
FORBIDDEN_PATHS = {
    "schemas/evidence-radar-translation-checkpoint.schema.json",
    "schemas/evidence-radar-translation-request.schema.json",
    "schemas/evidence-radar-translation-response.schema.json",
    "schemas/evidence-radar-translation-submission.schema.json",
    "templates/work-stage-b-automation.md",
    "tools/run_local_runtime.py",
    "tools/translation_handoff.py",
}
FORBIDDEN_PREFIXES = (".git/", ".github/", "artifacts/", "daily/", "legacy/", "runs/")
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class WorkPackVerificationError(RuntimeError):
    """Raised when a Work Pack cannot satisfy the portable execution contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WorkPackVerificationError(f"unsafe Work Pack path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise WorkPackVerificationError(f"unsafe Work Pack path: {value!r}")
    return posix.as_posix()


def _load_manifest(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkPackVerificationError(f"invalid Work Pack manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkPackVerificationError("Work Pack manifest must be a JSON object")
    return value


def _validated_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("format") != "evidenceradar-work-pack":
        raise WorkPackVerificationError("unexpected Work Pack manifest format")
    if manifest.get("execution_lane") != "chatgpt_work":
        raise WorkPackVerificationError("Work Pack execution_lane must be chatgpt_work")
    if manifest.get("entrypoint") != "WORK_ENTRY.md":
        raise WorkPackVerificationError("Work Pack entrypoint must be WORK_ENTRY.md")
    if manifest.get("terminal_delivery") is not True:
        raise WorkPackVerificationError("Work Pack must require terminal delivery")
    if manifest.get("canonical_artifacts") != CANONICAL_ARTIFACTS:
        raise WorkPackVerificationError("Work Pack canonical artifact contract is invalid")
    if manifest.get("github_role") != "source_and_package_storage_only":
        raise WorkPackVerificationError("Work Pack GitHub role must be source/package storage only")
    if manifest.get("post_download_github_access") is not False:
        raise WorkPackVerificationError("Work Pack must forbid post-download GitHub access")
    if manifest.get("disabled_entrypoints") != ["tools/run_github_radar.py"]:
        raise WorkPackVerificationError("Work Pack must disable the GitHub runner CLI")
    if manifest.get("git_dirty") is not False:
        raise WorkPackVerificationError("released Work Pack source must be clean")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise WorkPackVerificationError("Work Pack source_commit must be a full clean commit")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not REQUIRED_CAPABILITIES.issubset(capabilities):
        raise WorkPackVerificationError("Work Pack omits required execution capabilities")

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise WorkPackVerificationError("Work Pack manifest files must be a non-empty array")
    if manifest.get("file_count") != len(records):
        raise WorkPackVerificationError("Work Pack file_count does not match files")

    validated: list[dict[str, Any]] = []
    paths: list[str] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise WorkPackVerificationError("Work Pack file record must be an object")
        path = _safe_relative(raw.get("path"))
        digest = raw.get("sha256")
        size = raw.get("size")
        if not isinstance(digest, str) or not _SHA_RE.fullmatch(digest):
            raise WorkPackVerificationError(f"invalid Work Pack digest for {path}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_MEMBER_BYTES:
            raise WorkPackVerificationError(f"invalid Work Pack size for {path}")
        paths.append(path)
        validated.append({"path": path, "sha256": digest, "size": size})
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise WorkPackVerificationError("Work Pack file paths must be sorted and unique")
    path_set = set(paths)
    missing = sorted(REQUIRED_PATHS - path_set)
    if missing:
        raise WorkPackVerificationError("Work Pack omits required files: " + ", ".join(missing))
    forbidden = sorted(FORBIDDEN_PATHS & path_set)
    if forbidden:
        raise WorkPackVerificationError("Work Pack includes control-plane files: " + ", ".join(forbidden))
    bad_prefixes = sorted(path for path in path_set if path.startswith(FORBIDDEN_PREFIXES))
    if bad_prefixes:
        raise WorkPackVerificationError("Work Pack includes forbidden paths: " + ", ".join(bad_prefixes))

    seed = manifest.get("seed_state")
    if not isinstance(seed, Mapping):
        raise WorkPackVerificationError("Work Pack seed_state record is required")
    seed_path = seed.get("path")
    seed_record = next((item for item in validated if item["path"] == seed_path), None)
    if seed_path != "state/current/EvidenceRadar_State.json" or seed_record is None:
        raise WorkPackVerificationError("Work Pack seed_state path is invalid")
    if seed.get("sha256") != seed_record["sha256"] or seed.get("size") != seed_record["size"]:
        raise WorkPackVerificationError("Work Pack seed_state does not match its file record")
    return validated


def _verify_payloads(manifest: Mapping[str, Any], payloads: Mapping[str, bytes]) -> dict[str, Any]:
    records = _validated_records(manifest)
    declared = {item["path"] for item in records}
    if set(payloads) != declared:
        missing = sorted(declared - set(payloads))
        extra = sorted(set(payloads) - declared)
        raise WorkPackVerificationError(
            f"Work Pack payload set mismatch; missing={missing!r} extra={extra!r}"
        )
    total = 0
    for item in records:
        payload = payloads[item["path"]]
        total += len(payload)
        if len(payload) != item["size"] or _sha256(payload) != item["sha256"]:
            raise WorkPackVerificationError(f"Work Pack file does not match manifest: {item['path']}")
    if total > MAX_TOTAL_BYTES:
        raise WorkPackVerificationError("Work Pack expanded payload exceeds size limit")
    try:
        state = json.loads(payloads["state/current/EvidenceRadar_State.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkPackVerificationError(f"embedded State is invalid JSON: {exc}") from exc
    if not isinstance(state, Mapping) or state.get("artifact_type") != "EvidenceRadar_State":
        raise WorkPackVerificationError("embedded State is not an EvidenceRadar State artifact")
    return dict(manifest)


def _checksum_digest(checksum: Path, archive_name: str) -> str:
    try:
        fields = checksum.read_text(encoding="utf-8").strip().split()
    except OSError as exc:
        raise WorkPackVerificationError(f"cannot read checksum sidecar: {exc}") from exc
    if len(fields) != 2 or fields[1].lstrip("*") != archive_name or not _SHA_RE.fullmatch(fields[0]):
        raise WorkPackVerificationError("checksum sidecar must bind the selected archive filename")
    return fields[0]


def _zip_member_errors(info: ZipInfo) -> None:
    name = _safe_relative(info.filename)
    if name.endswith("/") or info.is_dir():
        raise WorkPackVerificationError(f"Work Pack archive contains a directory entry: {name}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise WorkPackVerificationError(f"Work Pack archive contains a symlink: {name}")
    if info.file_size > MAX_MEMBER_BYTES:
        raise WorkPackVerificationError(f"Work Pack archive member is too large: {name}")
    if info.file_size and info.compress_size == 0:
        raise WorkPackVerificationError(f"Work Pack archive member has invalid compression size: {name}")
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise WorkPackVerificationError(f"Work Pack archive member exceeds compression ratio: {name}")


def verify_archive(archive_path: Path, checksum_path: Path) -> dict[str, Any]:
    archive_path = Path(archive_path).resolve()
    checksum_path = Path(checksum_path).resolve()
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise WorkPackVerificationError("Work Pack archive exceeds size limit")
    payload = archive_path.read_bytes()
    expected = _checksum_digest(checksum_path, archive_path.name)
    if _sha256(payload) != expected:
        raise WorkPackVerificationError("Work Pack archive does not match checksum sidecar")
    try:
        with ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len({name.casefold() for name in names}) != len(names):
                raise WorkPackVerificationError("Work Pack archive paths must be unique by case")
            if not names or names[-1] != MANIFEST_NAME or names.count(MANIFEST_NAME) != 1:
                raise WorkPackVerificationError("Work Pack manifest must be the final unique archive member")
            for info in infos:
                _zip_member_errors(info)
            if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
                raise WorkPackVerificationError("Work Pack archive expansion exceeds size limit")
            manifest = _load_manifest(archive.read(MANIFEST_NAME))
            payloads = {name: archive.read(name) for name in names if name != MANIFEST_NAME}
    except (BadZipFile, KeyError, OSError) as exc:
        raise WorkPackVerificationError(f"cannot verify Work Pack archive: {exc}") from exc
    result = _verify_payloads(manifest, payloads)
    result["archive_sha256"] = expected
    return result


def verify_extracted_root(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = root / MANIFEST_NAME
    manifest = _load_manifest(manifest_path.read_bytes())
    records = _validated_records(manifest)
    expected_paths = {item["path"] for item in records} | {MANIFEST_NAME}
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WorkPackVerificationError(
                f"extracted Work Pack contains a symlink: {path.relative_to(root).as_posix()}"
            )
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise WorkPackVerificationError(
            "extracted Work Pack payload set mismatch; "
            f"missing={sorted(expected_paths - actual_paths)!r} "
            f"extra={sorted(actual_paths - expected_paths)!r}"
        )
    payloads: dict[str, bytes] = {}
    for item in records:
        relative = Path(item["path"])
        path = root / relative
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise WorkPackVerificationError(f"extracted Work Pack path uses symlink: {item['path']}")
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise WorkPackVerificationError(f"invalid extracted Work Pack path: {item['path']}") from exc
        if not path.is_file():
            raise WorkPackVerificationError(f"extracted Work Pack file is missing: {item['path']}")
        payloads[item["path"]] = path.read_bytes()
    return _verify_payloads(manifest, payloads)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--root", type=Path, help="extracted Work Pack root")
    mode.add_argument("--archive", type=Path, help="Work Pack ZIP")
    parser.add_argument("--checksum", type=Path, help="checksum sidecar required with --archive")
    args = parser.parse_args(argv)
    try:
        if args.archive is not None:
            if args.checksum is None:
                raise WorkPackVerificationError("--archive requires --checksum")
            result = verify_archive(args.archive, args.checksum)
        else:
            if args.checksum is not None:
                raise WorkPackVerificationError("--checksum is only valid with --archive")
            result = verify_extracted_root(args.root)
    except (OSError, WorkPackVerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "pack_version": result.get("pack_version"),
                "source_commit": result.get("source_commit"),
                "execution_lane": result.get("execution_lane"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
