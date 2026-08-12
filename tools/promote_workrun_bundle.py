#!/usr/bin/env python3
"""Offline canonicalization of a validated WorkRun delivery bundle.

This tool deliberately does not discover, fetch, publish, or modify canonical
artifacts.  It validates a historical WorkRun ZIP with its matching Runtime
ZIP, merges the delivered State with the current canonical State, renders the
report with the target producer, and atomically writes a new four-file staging
directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tools.delivery_contract import (
        BUNDLE_FILENAMES,
        current_producer_errors,
        producer_paths,
    )
    from tools.merge_radar_state import merge_states, state_sha256
    from tools.render_report_from_artifacts import (
        render_report_from_documents,
        validate_documents,
    )
    from tools.strict_json import loads as strict_json_loads
    from tools.validate_delivery_bundle import (
        validate_delivery_bundle,
        validate_delivery_payload,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from delivery_contract import (  # type: ignore
        BUNDLE_FILENAMES,
        current_producer_errors,
        producer_paths,
    )
    from merge_radar_state import merge_states, state_sha256  # type: ignore
    from render_report_from_artifacts import (  # type: ignore
        render_report_from_documents,
        validate_documents,
    )
    from strict_json import loads as strict_json_loads  # type: ignore
    from validate_delivery_bundle import (  # type: ignore
        validate_delivery_bundle,
        validate_delivery_payload,
    )


CANONICAL_FILES = tuple(BUNDLE_FILENAMES)
REPORT_FILE, STATE_FILE, EVIDENCE_FILE, RUN_FILE = CANONICAL_FILES
WORKRUN_MANIFEST = "manifest.json"
WORKRUN_FORMAT = "evidenceradar-work-delivery"
WORKRUN_FORMAT_VERSION = "1"
RUNTIME_MANIFEST = "RUNTIME_MANIFEST.json"
RUNTIME_FORMAT = "evidenceradar-runtime-release"
RUNTIME_FORMAT_VERSION = "1"
RUNTIME_REQUIRED_ENTRYPOINTS = {
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
    "tools/run_local_runtime.py",
    "tools/verify_runtime_release.py",
}
RUNTIME_REQUIRED_FILES = RUNTIME_REQUIRED_ENTRYPOINTS | {
    "config/radar_master.json",
    "tools/featured_selection.py",
    "tools/publisher_feed.py",
    "tools/radar_control.py",
}
OFFLINE_CANONICALIZER_PATHS = (
    "tools/merge_radar_state.py",
    "tools/promote_workrun_bundle.py",
    "tools/render_report_from_artifacts.py",
)
MASTER_CONTROL_FIELDS = (
    "profile_id",
    "resolved_stream_ids",
    "resolved_source_ids",
    "master_control_sha256",
    "runtime_request_sha256",
)
RETRIEVAL_LEDGER_FIELDS = (
    "window",
    "queries",
    "retrieval_attempts",
    "search_expansions",
    "source_access",
)
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PromotionError(RuntimeError):
    """Raised when an offline promotion precondition is not satisfied."""


@dataclass(frozen=True)
class VerifiedWorkRun:
    manifest: dict[str, Any]
    payloads: dict[str, bytes]
    archive_sha256: str
    documents: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class PromotionResult:
    output_dir: Path
    source_run_id: str
    source_protocol_commit: str
    target_protocol_commit: str
    workrun_archive_sha256: str
    runtime_archive_sha256: str
    retrieval_ledger_sha256: str
    report_sha256: str


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _load_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{label} must contain a JSON object")
    return value


def _load_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PromotionError(f"cannot read {label} at {path}: {exc}") from exc
    return _load_json_bytes(payload, label=label)


def _validate_archive_member(name: str) -> None:
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        not name
        or name.endswith("/")
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or "\\" in name
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise PromotionError(f"unsafe archive member: {name!r}")


def _verify_checksum_sidecar(archive: Path, checksum: Path | None) -> str:
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if checksum is None:
        checksum = Path(str(archive) + ".sha256")
    if not checksum.is_file():
        raise PromotionError(
            f"archive checksum sidecar is required and was not found: {checksum}"
        )
    try:
        fields = checksum.read_text(encoding="utf-8").strip().split()
    except OSError as exc:
        raise PromotionError(f"cannot read checksum sidecar {checksum}: {exc}") from exc
    if len(fields) != 2 or not HEX64_RE.fullmatch(fields[0].lower()):
        raise PromotionError(f"invalid checksum sidecar format: {checksum}")
    expected_name = fields[1].lstrip("*")
    if expected_name != archive.name:
        raise PromotionError(
            f"checksum sidecar names {expected_name!r}, expected {archive.name!r}"
        )
    if fields[0].lower() != actual:
        raise PromotionError(
            f"archive checksum mismatch for {archive.name}: "
            f"expected {fields[0].lower()}, got {actual}"
        )
    return actual


def verify_workrun_archive(
    archive: Path,
    *,
    checksum: Path | None = None,
) -> VerifiedWorkRun:
    """Validate the WorkRun ZIP container, manifest, hashes, and four documents."""

    archive = archive.resolve()
    if not archive.is_file():
        raise PromotionError(f"WorkRun archive does not exist: {archive}")
    archive_sha256 = _verify_checksum_sidecar(archive, checksum)
    expected_names = (*CANONICAL_FILES, WORKRUN_MANIFEST)

    try:
        with zipfile.ZipFile(archive, "r") as bundle_zip:
            infos = bundle_zip.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PromotionError("WorkRun archive contains duplicate members")
            for info in infos:
                _validate_archive_member(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PromotionError(
                        f"WorkRun archive contains a symlink: {info.filename}"
                    )
            if tuple(names) != expected_names:
                raise PromotionError(
                    "WorkRun archive must contain exactly, in order: "
                    + ", ".join(expected_names)
                )
            raw = {name: bundle_zip.read(name) for name in names}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError(f"invalid WorkRun archive {archive}: {exc}") from exc

    manifest = _load_json_bytes(raw[WORKRUN_MANIFEST], label="WorkRun manifest")
    if manifest.get("format") != WORKRUN_FORMAT:
        raise PromotionError("unsupported WorkRun manifest format")
    if str(manifest.get("manifest_version")) != WORKRUN_FORMAT_VERSION:
        raise PromotionError("unsupported WorkRun manifest_version")
    if manifest.get("archive_name") != archive.name:
        raise PromotionError("WorkRun manifest archive_name does not match the ZIP")
    if manifest.get("canonical_files") != list(CANONICAL_FILES):
        raise PromotionError("WorkRun manifest canonical_files is not canonical")
    if manifest.get("file_count") != len(CANONICAL_FILES):
        raise PromotionError("WorkRun manifest file_count is not four")

    records = manifest.get("files")
    if not isinstance(records, list) or [item.get("path") for item in records if isinstance(item, dict)] != list(CANONICAL_FILES):
        raise PromotionError("WorkRun manifest file records are incomplete or unordered")
    for record in records:
        if not isinstance(record, dict):
            raise PromotionError("WorkRun manifest file record must be an object")
        name = record["path"]
        payload = raw[name]
        digest = hashlib.sha256(payload).hexdigest()
        if record.get("size") != len(payload):
            raise PromotionError(f"WorkRun size mismatch for {name}")
        if record.get("sha256") != digest:
            raise PromotionError(f"WorkRun SHA-256 mismatch for {name}")

    payloads = {name: raw[name] for name in CANONICAL_FILES}
    documents = {
        name: _load_json_bytes(payloads[name], label=name)
        for name in CANONICAL_FILES
        if name.endswith(".json")
    }
    run = documents[RUN_FILE]
    state = documents[STATE_FILE]
    evidence = documents[EVIDENCE_FILE]
    run_id = run.get("run_id")
    lane = run.get("execution_lane")
    protocol = run.get("protocol_commit")
    if not isinstance(run_id, str) or not run_id:
        raise PromotionError("source Run.json has no run_id")
    if state.get("last_run_id") != run_id or evidence.get("run_id") != run_id:
        raise PromotionError("source four-file bundle has inconsistent run IDs")
    if manifest.get("run_id") != run_id:
        raise PromotionError("WorkRun manifest run_id does not match Run.json")
    if manifest.get("execution_lane") != lane:
        raise PromotionError("WorkRun manifest execution_lane does not match Run.json")
    if manifest.get("protocol_commit") != protocol:
        raise PromotionError("WorkRun manifest protocol_commit does not match Run.json")
    report_digest = hashlib.sha256(payloads[REPORT_FILE]).hexdigest()
    if run.get("report_sha256") != report_digest:
        raise PromotionError("source Run.json report_sha256 does not match report bytes")

    return VerifiedWorkRun(
        manifest=manifest,
        payloads=payloads,
        archive_sha256=archive_sha256,
        documents=documents,
    )


def verify_runtime_archive(
    archive: Path,
    *,
    checksum: Path | None = None,
) -> dict[str, Any]:
    """Verify immutable Runtime container invariants without current-version policy.

    Historical Runtime releases must be checked against the contract they
    shipped with.  This structural pass therefore validates paths, hashes,
    clean provenance, required verification entrypoints, and ZIP/manifest
    parity while leaving version-specific policy to the extracted Runtime's
    own verifier.
    """

    archive = archive.resolve()
    if not archive.is_file():
        raise PromotionError(f"Runtime archive does not exist: {archive}")
    archive_sha256 = _verify_checksum_sidecar(archive, checksum)
    try:
        with zipfile.ZipFile(archive, "r") as runtime_zip:
            infos = runtime_zip.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PromotionError("Runtime archive contains duplicate members")
            for info in infos:
                _validate_archive_member(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PromotionError(
                        f"Runtime archive contains a symlink: {info.filename}"
                    )
            if RUNTIME_MANIFEST not in names:
                raise PromotionError(f"Runtime archive has no {RUNTIME_MANIFEST}")
            manifest = _load_json_bytes(
                runtime_zip.read(RUNTIME_MANIFEST),
                label="Runtime manifest",
            )
            if manifest.get("format") != RUNTIME_FORMAT:
                raise PromotionError("unsupported Runtime manifest format")
            if str(manifest.get("manifest_version")) != RUNTIME_FORMAT_VERSION:
                raise PromotionError("unsupported Runtime manifest_version")
            source_commit = manifest.get("source_commit")
            if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
                raise PromotionError("Runtime source_commit must be a full Git SHA")
            if manifest.get("git_commit") != source_commit:
                raise PromotionError("Runtime git_commit must equal source_commit")
            if manifest.get("git_dirty") is not False or manifest.get("git_state") != "clean":
                raise PromotionError("Runtime must declare a clean Git source")
            if manifest.get("immutable_source") is not True:
                raise PromotionError("Runtime must declare immutable_source=true")
            if (
                manifest.get("state_packaged") is not False
                or manifest.get("artifacts_packaged") is not False
            ):
                raise PromotionError("Runtime must keep State and artifacts external")
            if manifest.get("execution_lane") not in {"chatgpt_work", "github_actions"}:
                raise PromotionError("Runtime has an unsupported execution_lane")
            if manifest.get("semantic_contract") != "3":
                raise PromotionError("Runtime does not declare semantic contract 3")

            records = manifest.get("files")
            if not isinstance(records, list) or not records:
                raise PromotionError("Runtime manifest files must be a non-empty array")
            if manifest.get("file_count") != len(records):
                raise PromotionError("Runtime manifest file_count is inconsistent")
            paths: list[str] = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    raise PromotionError(
                        f"Runtime manifest file record {index} must be an object"
                    )
                path = record.get("path")
                digest = record.get("sha256")
                size = record.get("size")
                if not isinstance(path, str):
                    raise PromotionError("Runtime manifest file path must be a string")
                _validate_archive_member(path)
                if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest):
                    raise PromotionError(f"Runtime manifest has invalid SHA-256 for {path}")
                if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                    raise PromotionError(f"Runtime manifest has invalid size for {path}")
                paths.append(path)
            if paths != sorted(paths) or len(paths) != len(set(paths)):
                raise PromotionError(
                    "Runtime manifest file paths must be sorted and unique"
                )
            required = manifest.get("required_entrypoints")
            if not isinstance(required, list) or not all(
                isinstance(item, str) for item in required
            ):
                raise PromotionError("Runtime required_entrypoints must be an array")
            if not RUNTIME_REQUIRED_ENTRYPOINTS.issubset(set(required)):
                raise PromotionError("Runtime omits required verification entrypoints")
            if not set(required).issubset(set(paths)):
                raise PromotionError("Runtime required_entrypoints are absent from files")
            missing_runtime_files = sorted(RUNTIME_REQUIRED_FILES - set(paths))
            if missing_runtime_files:
                raise PromotionError(
                    "Runtime omits required executable files: "
                    + ", ".join(missing_runtime_files)
                )
            if names != [*paths, RUNTIME_MANIFEST]:
                raise PromotionError(
                    "Runtime ZIP entries do not exactly match manifest order"
                )
            for record in records:
                payload = runtime_zip.read(record["path"])
                if len(payload) != record["size"]:
                    raise PromotionError(
                        f"Runtime size mismatch for {record['path']}"
                    )
                if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                    raise PromotionError(
                        f"Runtime SHA-256 mismatch for {record['path']}"
                    )
            try:
                version = runtime_zip.read("runtime/VERSION").decode("utf-8").strip()
            except (KeyError, UnicodeDecodeError) as exc:
                raise PromotionError("Runtime has no valid runtime/VERSION") from exc
            if version != manifest.get("runtime_version"):
                raise PromotionError("Runtime version file disagrees with manifest")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError(f"invalid Runtime archive {archive}: {exc}") from exc

    if manifest.get("archive_name") != archive.name:
        raise PromotionError("Runtime manifest archive_name does not match the ZIP")
    if manifest.get("checksum_sidecar") != archive.name + ".sha256":
        raise PromotionError("Runtime manifest checksum_sidecar is inconsistent")
    return {"manifest": manifest, "archive_sha256": archive_sha256}


def _git_output(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise PromotionError(detail)
    return proc.stdout.strip()


def _validate_commit_exists(root: Path, commit: str, *, label: str) -> None:
    if not COMMIT_RE.fullmatch(commit):
        raise PromotionError(f"{label} commit must be a lowercase 40-hex SHA")
    try:
        resolved = _git_output(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    except PromotionError as exc:
        raise PromotionError(f"{label} commit does not exist: {commit}") from exc
    if resolved != commit:
        raise PromotionError(
            f"{label} commit resolved to {resolved}, expected exact {commit}"
        )


def _validate_target_producer(root: Path, lane: str, commit: str) -> None:
    _validate_commit_exists(root, commit, label="target producer")
    target_paths = tuple(dict.fromkeys((*producer_paths(lane), *OFFLINE_CANONICALIZER_PATHS)))
    for path in target_paths:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}:{path}"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode:
            raise PromotionError(
                f"target producer commit does not contain required producer path: {path}"
            )
    drift = current_producer_errors(
        root,
        execution_lane=lane,
        protocol_commit=commit,
    )
    if drift:
        raise PromotionError(
            "working producer does not match target producer commit: " + "; ".join(drift)
        )
    for path in OFFLINE_CANONICALIZER_PATHS:
        current_path = root / path
        if not current_path.is_file() or current_path.is_symlink():
            raise PromotionError(f"working offline producer path is missing or unsafe: {path}")
        proc = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode or proc.stdout != current_path.read_bytes():
            raise PromotionError(
                f"working offline producer does not match target producer commit: {path}"
            )


def _validate_runtime_against_source_commit(
    root: Path,
    manifest: Mapping[str, Any],
    source_commit: str,
) -> None:
    """Bind every Runtime payload record to bytes in its declared Git commit."""

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise PromotionError("Runtime manifest has no files to bind to source commit")
    for record in records:
        if not isinstance(record, dict):
            raise PromotionError("Runtime manifest contains an invalid file record")
        path = record.get("path")
        digest = record.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise PromotionError("Runtime manifest contains an incomplete file record")
        proc = subprocess.run(
            ["git", "show", f"{source_commit}:{path}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise PromotionError(
                f"source producer commit does not contain Runtime file: {path}"
            )
        observed = hashlib.sha256(proc.stdout).hexdigest()
        if observed != digest:
            raise PromotionError(
                f"Runtime file differs from source producer commit: {path}"
            )


def _extract_verified_runtime(
    archive: Path,
    manifest: Mapping[str, Any],
    destination: Path,
) -> None:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise PromotionError("Runtime manifest has no verified file list")
    allowed = [record.get("path") for record in records if isinstance(record, dict)]
    if len(allowed) != len(records) or not all(isinstance(item, str) for item in allowed):
        raise PromotionError("Runtime manifest file list is malformed")
    with zipfile.ZipFile(archive, "r") as runtime_zip:
        for relative in allowed:
            assert isinstance(relative, str)
            _validate_archive_member(relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(runtime_zip.read(relative))


def _validate_source_bundle_with_runtime(
    *,
    runtime_archive: Path,
    runtime_manifest: Mapping[str, Any],
    payloads: Mapping[str, bytes],
    execution_lane: str,
    source_protocol_commit: str,
) -> None:
    """Run the source Runtime's own validator with network sockets disabled."""

    with tempfile.TemporaryDirectory(prefix="evidenceradar-offline-source-") as raw_tmp:
        temp_root = Path(raw_tmp)
        runtime_root = temp_root / "runtime"
        source_bundle = temp_root / "source-bundle"
        guard_root = temp_root / "offline-guard"
        runtime_root.mkdir()
        source_bundle.mkdir()
        guard_root.mkdir()
        _extract_verified_runtime(runtime_archive, runtime_manifest, runtime_root)
        for name, payload in payloads.items():
            (source_bundle / name).write_bytes(payload)

        validator = runtime_root / "tools" / "validate_delivery_bundle.py"
        runtime_verifier = runtime_root / "tools" / "verify_runtime_release.py"
        if not validator.is_file():
            raise PromotionError("source Runtime has no delivery validator")
        if not runtime_verifier.is_file():
            raise PromotionError("source Runtime has no self-verifier")
        (guard_root / "sitecustomize.py").write_text(
            "import socket\n"
            "def _offline(*args, **kwargs):\n"
            "    raise RuntimeError('network disabled during offline promotion')\n"
            "socket.create_connection = _offline\n"
            "socket.socket.connect = _offline\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["EVIDENCERADAR_OFFLINE_PROMOTION"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        python_path = [str(guard_root), str(runtime_root)]
        if env.get("PYTHONPATH"):
            python_path.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_path)
        commands = (
            (
                "source Runtime failed its own archive verification",
                [
                    sys.executable,
                    str(runtime_verifier),
                    "--archive",
                    str(runtime_archive),
                ],
            ),
            (
                "source four-file bundle failed validation with its Runtime",
                [
                    sys.executable,
                    str(validator),
                    "--root",
                    str(runtime_root),
                    "--bundle",
                    str(source_bundle),
                    "--expected-lane",
                    execution_lane,
                    "--expected-protocol-commit",
                    source_protocol_commit,
                    "--require-semantic-contract-v3",
                ],
            ),
        )
        for failure, command in commands:
            proc = subprocess.run(
                command,
                cwd=runtime_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if not proc.returncode:
                continue
            detail = proc.stdout.strip()
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise PromotionError(failure + (f":\n{detail}" if detail else ""))


class _OfflineNetworkGuard:
    def __enter__(self) -> "_OfflineNetworkGuard":
        self._create_connection = socket.create_connection
        self._socket_connect = socket.socket.connect

        def denied(*_args: Any, **_kwargs: Any) -> None:
            raise PromotionError("network access is disabled during offline promotion")

        socket.create_connection = denied  # type: ignore[assignment]
        socket.socket.connect = denied  # type: ignore[method-assign]
        return self

    def __exit__(self, *_exc: object) -> None:
        socket.create_connection = self._create_connection  # type: ignore[assignment]
        socket.socket.connect = self._socket_connect  # type: ignore[method-assign]


def _retrieval_ledger(run: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in RETRIEVAL_LEDGER_FIELDS if field not in run]
    if missing:
        raise PromotionError(
            "source Run.json is missing retrieval ledger fields: " + ", ".join(missing)
        )
    return {field: copy.deepcopy(run[field]) for field in RETRIEVAL_LEDGER_FIELDS}


def _source_projection_union(
    base_evidence: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    merged_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    projections: dict[str, dict[str, Any]] = {}
    for evidence in (base_evidence, source_evidence):
        items = evidence.get("sources", [])
        if not isinstance(items, list):
            raise PromotionError("Evidence.json sources must be an array")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
                raise PromotionError("Evidence.json contains an invalid source projection")
            projections[item["source_id"]] = copy.deepcopy(item)

    registry = merged_state.get("source_registry", [])
    if not isinstance(registry, list):
        raise PromotionError("merged State source_registry must be an array")
    registry_by_id: dict[str, dict[str, Any]] = {}
    for item in registry:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            raise PromotionError("merged State contains an invalid source registry item")
        registry_by_id[item["source_id"]] = item
    if set(projections) != set(registry_by_id):
        missing = sorted(set(registry_by_id) - set(projections))
        extra = sorted(set(projections) - set(registry_by_id))
        raise PromotionError(
            "cannot project merged source registry into Evidence.json "
            f"(missing={missing}, extra={extra})"
        )
    for source_id, projection in projections.items():
        canonical_url = registry_by_id[source_id].get("canonical_url")
        if isinstance(canonical_url, str) and canonical_url:
            projection["url"] = canonical_url
    return [projections[source_id] for source_id in sorted(projections)]


def _master_bindings(document: Mapping[str, Any]) -> dict[str, Any]:
    present = {field: document[field] for field in MASTER_CONTROL_FIELDS if field in document}
    if present and len(present) != len(MASTER_CONTROL_FIELDS):
        missing = [field for field in MASTER_CONTROL_FIELDS if field not in present]
        raise PromotionError(
            "source State has partial master-control bindings: " + ", ".join(missing)
        )
    return copy.deepcopy(present)


def _append_notes(run: dict[str, Any], notes: Sequence[str]) -> None:
    current = run.get("notes", [])
    if not isinstance(current, list) or not all(isinstance(item, str) for item in current):
        raise PromotionError("source Run.json notes must be an array of strings")
    for note in notes:
        if note not in current:
            current.append(note)
    run["notes"] = current


def _canonicalize_documents(
    *,
    base_documents: Mapping[str, dict[str, Any]],
    source_documents: Mapping[str, dict[str, Any]],
    target_protocol_commit: str,
    workrun_archive_sha256: str,
    runtime_archive_sha256: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    base_state = copy.deepcopy(base_documents[STATE_FILE])
    base_evidence = copy.deepcopy(base_documents[EVIDENCE_FILE])
    source_run = copy.deepcopy(source_documents[RUN_FILE])
    source_state = copy.deepcopy(source_documents[STATE_FILE])
    source_evidence = copy.deepcopy(source_documents[EVIDENCE_FILE])

    run_id = source_run.get("run_id")
    lane = source_run.get("execution_lane")
    source_protocol = source_run.get("protocol_commit")
    if not isinstance(run_id, str) or not run_id:
        raise PromotionError("source Run.json has no run_id")
    if not isinstance(lane, str) or not lane:
        raise PromotionError("source Run.json has no execution_lane")
    if not isinstance(source_protocol, str) or not COMMIT_RE.fullmatch(source_protocol):
        raise PromotionError("source Run.json has an invalid protocol_commit")

    ledger = _retrieval_ledger(source_run)
    ledger_bytes = _canonical_json_bytes(ledger)
    ledger_sha256 = hashlib.sha256(ledger_bytes).hexdigest()
    bindings = _master_bindings(source_state)
    base_sha256 = state_sha256(base_state)
    parent_run_id = base_state.get("last_run_id")
    parent_run_ids = (
        [parent_run_id]
        if isinstance(parent_run_id, str) and parent_run_id and parent_run_id != run_id
        else []
    )

    merged_state = merge_states(
        base_state,
        source_state,
        execution_lane=lane,
        protocol_commit=target_protocol_commit,
    )
    merged_state["last_run_id"] = run_id
    merged_state["execution_lane"] = lane
    merged_state["protocol_commit"] = target_protocol_commit
    merged_state["base_state_sha256"] = base_sha256
    merged_state["parent_run_ids"] = parent_run_ids
    if "generated_at" in source_state:
        merged_state["generated_at"] = source_state["generated_at"]
    for field in MASTER_CONTROL_FIELDS:
        if field in bindings:
            merged_state[field] = bindings[field]
        else:
            merged_state.pop(field, None)

    for field in MASTER_CONTROL_FIELDS:
        if field in bindings:
            source_run[field] = copy.deepcopy(bindings[field])
            source_evidence[field] = copy.deepcopy(bindings[field])
        else:
            source_run.pop(field, None)
            source_evidence.pop(field, None)
    source_run["protocol_commit"] = target_protocol_commit
    source_run["base_state_sha256"] = base_sha256
    source_run["parent_run_ids"] = parent_run_ids
    source_evidence["source_registry"] = copy.deepcopy(merged_state["source_registry"])
    source_evidence["source_observations"] = copy.deepcopy(
        merged_state["source_observations"]
    )
    source_evidence["sources"] = _source_projection_union(
        base_evidence,
        source_evidence,
        merged_state,
    )
    _append_notes(
        source_run,
        (
            "OFFLINE_CANONICALIZATION_V1",
            f"SOURCE_PROTOCOL_COMMIT:{source_protocol}",
            f"SOURCE_WORKRUN_SHA256:{workrun_archive_sha256}",
            f"SOURCE_RUNTIME_SHA256:{runtime_archive_sha256}",
            f"RETRIEVAL_LEDGER_SHA256:{ledger_sha256}",
            "DISCOVERY_REUSED_NO_NETWORK",
        ),
    )
    if _canonical_json_bytes(_retrieval_ledger(source_run)) != ledger_bytes:
        raise PromotionError("retrieval ledger changed during canonicalization")

    documents = {
        RUN_FILE: source_run,
        STATE_FILE: merged_state,
        EVIDENCE_FILE: source_evidence,
    }
    report = render_report_from_documents(source_run, source_evidence)
    source_run["report_sha256"] = hashlib.sha256(report.encode("utf-8")).hexdigest()
    documents[REPORT_FILE] = {"_raw_html": report}
    return documents, ledger_sha256


def _write_staging_bundle(
    *,
    root: Path,
    output_dir: Path,
    documents: Mapping[str, dict[str, Any]],
    expected_lane: str,
    expected_protocol_commit: str,
) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PromotionError(f"staging output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent)
    )
    try:
        for name in CANONICAL_FILES:
            value = documents[name]
            if name.endswith(".html"):
                payload = value["_raw_html"]
                if not isinstance(payload, str):
                    raise PromotionError("rendered report payload is not text")
                payload = payload.encode("utf-8")
            else:
                payload = _json_bytes(value)
            (temp_dir / name).write_bytes(payload)
        errors, _ = validate_delivery_bundle(
            root,
            temp_dir,
            canonical_state=temp_dir / STATE_FILE,
            expected_lane=expected_lane,
            expected_protocol_commit=expected_protocol_commit,
            require_semantic_contract_v3=True,
            require_current_producer=False,
        )
        if errors:
            raise PromotionError(
                "staged canonical bundle failed validation:\n- " + "\n- ".join(errors)
            )
        os.replace(temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _validate_staging_target(
    *,
    root: Path,
    output_dir: Path,
    canonical_bundle: Path,
    canonical_state: Path,
) -> Path:
    resolved = output_dir.resolve()
    if resolved == canonical_state.resolve():
        raise PromotionError("staging output must not replace canonical State")
    protected = {
        canonical_bundle.resolve(),
        (root / "artifacts" / "current").resolve(),
        (root / "state" / "current").resolve(),
    }
    for directory in protected:
        if resolved == directory or directory in resolved.parents:
            raise PromotionError(
                f"staging output must not be inside a canonical location: {directory}"
            )
    return resolved


def promote_workrun_bundle(
    *,
    root: Path,
    workrun_archive: Path,
    runtime_archive: Path,
    canonical_bundle: Path,
    canonical_state: Path,
    target_producer_commit: str,
    output_dir: Path,
    workrun_checksum: Path | None = None,
    runtime_checksum: Path | None = None,
) -> PromotionResult:
    """Validate, canonicalize, and stage one offline WorkRun delivery."""

    root = root.resolve()
    canonical_bundle = canonical_bundle.resolve()
    canonical_state = canonical_state.resolve()
    runtime_archive = runtime_archive.resolve()
    output_dir = _validate_staging_target(
        root=root,
        output_dir=output_dir,
        canonical_bundle=canonical_bundle,
        canonical_state=canonical_state,
    )
    workrun = verify_workrun_archive(workrun_archive, checksum=workrun_checksum)
    source_run = workrun.documents[RUN_FILE]
    lane = source_run.get("execution_lane")
    source_protocol = source_run.get("protocol_commit")
    source_run_id = source_run.get("run_id")
    if not isinstance(lane, str) or not lane:
        raise PromotionError("source Run.json has no execution_lane")
    if not isinstance(source_protocol, str) or not COMMIT_RE.fullmatch(source_protocol):
        raise PromotionError("source Run.json has an invalid protocol_commit")
    if not isinstance(source_run_id, str) or not source_run_id:
        raise PromotionError("source Run.json has no run_id")

    _validate_target_producer(root, lane, target_producer_commit)
    try:
        runtime_result = verify_runtime_archive(
            runtime_archive,
            checksum=runtime_checksum,
        )
    except Exception as exc:
        raise PromotionError(f"Runtime archive verification failed: {exc}") from exc
    runtime_manifest = runtime_result.get("manifest")
    runtime_archive_sha256 = runtime_result.get("archive_sha256")
    if not isinstance(runtime_manifest, dict) or not isinstance(runtime_archive_sha256, str):
        raise PromotionError("Runtime verifier returned an incomplete result")
    if runtime_manifest.get("source_commit") != source_protocol:
        raise PromotionError(
            "source Runtime producer commit does not match the WorkRun bundle"
        )
    _validate_commit_exists(root, source_protocol, label="source producer")
    _validate_runtime_against_source_commit(
        root,
        runtime_manifest,
        source_protocol,
    )

    canonical_errors, _ = validate_delivery_bundle(
        root,
        canonical_bundle,
        canonical_state=canonical_state,
        require_semantic_contract_v3=True,
        require_current_producer=False,
    )
    if canonical_errors:
        raise PromotionError(
            "current canonical bundle/State failed validation:\n- "
            + "\n- ".join(canonical_errors)
        )
    bundle_state = _load_json_file(
        canonical_bundle / STATE_FILE, label=f"canonical bundle {STATE_FILE}"
    )
    state_document = _load_json_file(canonical_state, label="canonical State")
    if _canonical_json_bytes(bundle_state) != _canonical_json_bytes(state_document):
        raise PromotionError("canonical bundle State.json differs from canonical State")
    base_documents = {
        RUN_FILE: _load_json_file(
            canonical_bundle / RUN_FILE, label=f"canonical {RUN_FILE}"
        ),
        STATE_FILE: bundle_state,
        EVIDENCE_FILE: _load_json_file(
            canonical_bundle / EVIDENCE_FILE, label=f"canonical {EVIDENCE_FILE}"
        ),
    }

    _validate_source_bundle_with_runtime(
        runtime_archive=runtime_archive,
        runtime_manifest=runtime_manifest,
        payloads=workrun.payloads,
        execution_lane=lane,
        source_protocol_commit=source_protocol,
    )
    with _OfflineNetworkGuard():
        documents, ledger_sha256 = _canonicalize_documents(
            base_documents=base_documents,
            source_documents=workrun.documents,
            target_protocol_commit=target_producer_commit,
            workrun_archive_sha256=workrun.archive_sha256,
            runtime_archive_sha256=runtime_archive_sha256,
        )
        validation_documents = {
            name: value
            for name, value in documents.items()
            if name.endswith(".json")
        }
        try:
            validate_documents(root, validation_documents)
        except Exception as exc:
            raise PromotionError(f"canonicalized schemas failed validation: {exc}") from exc
        errors = validate_delivery_payload(
            root,
            run=documents[RUN_FILE],
            state=documents[STATE_FILE],
            evidence=documents[EVIDENCE_FILE],
            report_html=documents[REPORT_FILE]["_raw_html"],
            expected_lane=lane,
            expected_protocol_commit=target_producer_commit,
            require_semantic_contract_v3=True,
        )
        if errors:
            raise PromotionError(
                "canonicalized in-memory bundle failed validation:\n- "
                + "\n- ".join(errors)
            )
        _write_staging_bundle(
            root=root,
            output_dir=output_dir,
            documents=documents,
            expected_lane=lane,
            expected_protocol_commit=target_producer_commit,
        )

    return PromotionResult(
        output_dir=output_dir.resolve(),
        source_run_id=source_run_id,
        source_protocol_commit=source_protocol,
        target_protocol_commit=target_producer_commit,
        workrun_archive_sha256=workrun.archive_sha256,
        runtime_archive_sha256=runtime_archive_sha256,
        retrieval_ledger_sha256=ledger_sha256,
        report_sha256=documents[RUN_FILE]["report_sha256"],
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workrun-archive", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--canonical-bundle", type=Path, required=True)
    parser.add_argument("--canonical-state", type=Path, required=True)
    parser.add_argument("--target-producer-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workrun-checksum", type=Path)
    parser.add_argument("--runtime-checksum", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = promote_workrun_bundle(
            root=args.root,
            workrun_archive=args.workrun_archive,
            runtime_archive=args.runtime_archive,
            canonical_bundle=args.canonical_bundle,
            canonical_state=args.canonical_state,
            target_producer_commit=args.target_producer_commit,
            output_dir=args.output_dir,
            workrun_checksum=args.workrun_checksum,
            runtime_checksum=args.runtime_checksum,
        )
    except PromotionError as exc:
        print(f"offline promotion failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "source_run_id": result.source_run_id,
                "source_protocol_commit": result.source_protocol_commit,
                "target_protocol_commit": result.target_protocol_commit,
                "workrun_archive_sha256": result.workrun_archive_sha256,
                "runtime_archive_sha256": result.runtime_archive_sha256,
                "retrieval_ledger_sha256": result.retrieval_ledger_sha256,
                "report_sha256": result.report_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
