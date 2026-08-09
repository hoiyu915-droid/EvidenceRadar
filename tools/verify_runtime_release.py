#!/usr/bin/env python3
"""Verify an EvidenceRadar immutable Runtime ZIP or extracted Runtime tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST_NAME = "RUNTIME_MANIFEST.json"
REQUIRED_FORMAT = "evidenceradar-runtime-release"
REQUIRED_ENTRYPOINTS = {
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
    "tools/run_local_runtime.py",
    "tools/verify_runtime_release.py",
}
FORBIDDEN_PREFIXES = (
    ".git/",
    ".github/",
    "artifacts/",
    "daily/",
    "dist/",
    "legacy/",
    "runs/",
    "state/",
    "tests/",
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")


class RuntimeVerificationError(RuntimeError):
    """Raised when Runtime package verification fails."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeVerificationError(f"unsafe Runtime path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or "\\" in value
    ):
        raise RuntimeVerificationError(f"unsafe Runtime path: {value!r}")
    normalized = posix.as_posix()
    if normalized.startswith(FORBIDDEN_PREFIXES):
        raise RuntimeVerificationError(f"forbidden Runtime path: {normalized}")
    return normalized


def _load_manifest_bytes(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeVerificationError(f"invalid {RUNTIME_MANIFEST_NAME}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeVerificationError(f"{RUNTIME_MANIFEST_NAME} must be a JSON object")
    return value


def _validated_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("format") != REQUIRED_FORMAT:
        raise RuntimeVerificationError(f"Runtime manifest format must be {REQUIRED_FORMAT}")
    if manifest.get("manifest_version") != "1":
        raise RuntimeVerificationError("unsupported Runtime manifest version")
    version = manifest.get("runtime_version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise RuntimeVerificationError("Runtime manifest has invalid runtime_version")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise RuntimeVerificationError("Runtime manifest source_commit must be a full Git SHA")
    if manifest.get("git_commit") != commit:
        raise RuntimeVerificationError("Runtime manifest git_commit must equal source_commit")
    if manifest.get("git_dirty") is not False or manifest.get("git_state") != "clean":
        raise RuntimeVerificationError("Runtime release must come from a clean Git checkout")
    if manifest.get("immutable_source") is not True:
        raise RuntimeVerificationError("Runtime manifest must declare immutable_source=true")
    if manifest.get("state_packaged") is not False or manifest.get("artifacts_packaged") is not False:
        raise RuntimeVerificationError("Runtime release must keep State and artifacts external")
    if manifest.get("execution_lane") != "github_actions":
        raise RuntimeVerificationError("Runtime canonical execution_lane must remain github_actions")
    if manifest.get("execution_host") != "local_runtime":
        raise RuntimeVerificationError("Runtime execution_host must be local_runtime")
    if manifest.get("semantic_contract") != "3":
        raise RuntimeVerificationError("Runtime release requires semantic contract 3")
    if manifest.get("python_version") != "3.12":
        raise RuntimeVerificationError("Runtime release requires Python 3.12")

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise RuntimeVerificationError("Runtime manifest.files must be a non-empty array")
    if manifest.get("file_count") != len(records):
        raise RuntimeVerificationError("Runtime manifest.file_count must equal len(files)")

    normalized: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, item in enumerate(records):
        if not isinstance(item, Mapping):
            raise RuntimeVerificationError(f"Runtime manifest.files[{index}] must be an object")
        path = _safe_relative_path(str(item.get("path") or ""))
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise RuntimeVerificationError(f"Runtime manifest file has invalid sha256: {path}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RuntimeVerificationError(f"Runtime manifest file has invalid size: {path}")
        paths.append(path)
        normalized.append({"path": path, "sha256": digest, "size": size})
    if paths != sorted(paths):
        raise RuntimeVerificationError("Runtime manifest files must use deterministic path order")
    if len(paths) != len(set(paths)):
        raise RuntimeVerificationError("Runtime manifest contains duplicate file paths")
    missing = sorted(REQUIRED_ENTRYPOINTS - set(paths))
    if missing:
        raise RuntimeVerificationError(
            "Runtime manifest omits required entrypoints: " + ", ".join(missing)
        )
    declared_entrypoints = manifest.get("required_entrypoints")
    if not isinstance(declared_entrypoints, list) or set(declared_entrypoints) != REQUIRED_ENTRYPOINTS:
        raise RuntimeVerificationError("Runtime manifest.required_entrypoints is incomplete")
    return normalized


def _verify_version_file(read_bytes, manifest: Mapping[str, Any]) -> None:
    try:
        version = read_bytes("runtime/VERSION").decode("utf-8").strip()
    except (KeyError, OSError, UnicodeDecodeError) as exc:
        raise RuntimeVerificationError(f"cannot read runtime/VERSION: {exc}") from exc
    if version != manifest.get("runtime_version"):
        raise RuntimeVerificationError("runtime/VERSION disagrees with Runtime manifest")


def _verify_checksum(archive_path: Path, checksum_path: Path) -> str:
    try:
        text = checksum_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeVerificationError(f"cannot read checksum sidecar: {exc}") from exc
    parts = text.split()
    if len(parts) != 2 or not _HASH_RE.fullmatch(parts[0]):
        raise RuntimeVerificationError("checksum sidecar must contain '<sha256>  <archive-name>'")
    if parts[1] != archive_path.name:
        raise RuntimeVerificationError("checksum sidecar archive name does not match selected ZIP")
    observed = _sha256(archive_path.read_bytes())
    if observed != parts[0]:
        raise RuntimeVerificationError("Runtime archive SHA-256 does not match checksum sidecar")
    return observed


def verify_archive(archive_path: Path, checksum_path: Path | None = None) -> dict[str, Any]:
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file():
        raise RuntimeVerificationError(f"Runtime archive does not exist: {archive_path}")
    archive_hash = _verify_checksum(archive_path, Path(checksum_path).resolve()) if checksum_path else _sha256(archive_path.read_bytes())
    try:
        with ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RuntimeVerificationError("Runtime ZIP contains duplicate entry names")
            for info in infos:
                name = _safe_relative_path(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise RuntimeVerificationError(f"Runtime ZIP contains a symlink: {name}")
                if info.is_dir():
                    raise RuntimeVerificationError(f"Runtime ZIP contains an unexpected directory entry: {name}")
            if RUNTIME_MANIFEST_NAME not in names:
                raise RuntimeVerificationError(f"Runtime ZIP is missing {RUNTIME_MANIFEST_NAME}")
            manifest = _load_manifest_bytes(archive.read(RUNTIME_MANIFEST_NAME))
            records = _validated_records(manifest)
            expected_names = [item["path"] for item in records] + [RUNTIME_MANIFEST_NAME]
            if names != expected_names:
                raise RuntimeVerificationError("Runtime ZIP entries must exactly match manifest order and contents")
            for item in records:
                payload = archive.read(item["path"])
                if len(payload) != item["size"] or _sha256(payload) != item["sha256"]:
                    raise RuntimeVerificationError(f"Runtime ZIP file does not match manifest: {item['path']}")
            _verify_version_file(archive.read, manifest)
    except BadZipFile as exc:
        raise RuntimeVerificationError(f"invalid Runtime ZIP: {exc}") from exc
    if manifest.get("archive_name") != archive_path.name:
        raise RuntimeVerificationError("Runtime manifest archive_name disagrees with selected ZIP")
    if manifest.get("checksum_sidecar") != archive_path.name + ".sha256":
        raise RuntimeVerificationError("Runtime manifest checksum_sidecar is inconsistent")
    return {"manifest": manifest, "archive_sha256": archive_hash}


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeVerificationError(f"extracted Runtime contains a symlink: {path.relative_to(root)}")


def verify_extracted_root(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = root / RUNTIME_MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeVerificationError(f"extracted Runtime is missing {RUNTIME_MANIFEST_NAME}")
    _reject_symlinks(root)
    for prefix in FORBIDDEN_PREFIXES:
        head = prefix.rstrip("/")
        if (root / head).exists():
            raise RuntimeVerificationError(f"extracted Runtime contains forbidden path: {head}")
    manifest = _load_manifest_bytes(manifest_path.read_bytes())
    records = _validated_records(manifest)
    for item in records:
        path = root / item["path"]
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RuntimeVerificationError(f"Runtime file is missing or escapes root: {item['path']}") from exc
        if not path.is_file():
            raise RuntimeVerificationError(f"Runtime file is missing: {item['path']}")
        payload = path.read_bytes()
        if len(payload) != item["size"] or _sha256(payload) != item["sha256"]:
            raise RuntimeVerificationError(f"extracted Runtime file does not match manifest: {item['path']}")
    _verify_version_file(lambda name: (root / name).read_bytes(), manifest)
    return {"manifest": manifest, "manifest_sha256": _sha256(manifest_path.read_bytes())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--archive", type=Path, help="Runtime ZIP to verify")
    target.add_argument("--root", type=Path, help="extracted Runtime directory to verify")
    parser.add_argument("--checksum", type=Path, help="matching .zip.sha256 sidecar")
    args = parser.parse_args(argv)
    if args.root is not None and args.checksum is not None:
        parser.error("--checksum is valid only with --archive")
    try:
        if args.archive is not None:
            result = verify_archive(args.archive, args.checksum)
            manifest = result["manifest"]
            print(
                "PASS: EvidenceRadar Runtime archive "
                f"version={manifest['runtime_version']} source_commit={manifest['source_commit']} "
                f"sha256={result['archive_sha256']}"
            )
        else:
            result = verify_extracted_root(args.root)
            manifest = result["manifest"]
            print(
                "PASS: EvidenceRadar extracted Runtime "
                f"version={manifest['runtime_version']} source_commit={manifest['source_commit']}"
            )
    except RuntimeVerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
