#!/usr/bin/env python3
"""Build an EvidenceRadar ChatGPT Work Pack deterministically.

The builder deliberately has a narrow allow-list. It packages the protocol,
configuration, taxonomy, templates, schemas, examples, setup/migration guides,
the active V3 renderer/projection library and validation tools; runtime history, the legacy
crawler, CI files and secret-bearing material stay out of the archive. The
resulting manifest records every included file's digest and the source Git
revision so a release can be audited after extraction.  The builder itself is
standard-library only; the included active runner uses the pinned packages in
``requirements.txt``.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC_PATH = ROOT / "release" / "work-pack-manifest.json"
WORK_ENTRYPOINT = "WORK_ENTRY.md"
SEED_STATE_PATH = "state/current/EvidenceRadar_State.json"
WORK_PACK_CAPABILITIES = (
    "EXECUTOR_HTTP_TELEMETRY_V1",
    "MASTER_CONTROL_V1",
    "TERMINAL_FOUR_ARTIFACT_DELIVERY_V1",
)
CANONICAL_ARTIFACTS = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)
DISABLED_ENTRYPOINTS = ("tools/run_github_radar.py",)
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)


class WorkPackError(RuntimeError):
    """Raised when a source tree cannot satisfy the Work Pack contract."""


@dataclass(frozen=True)
class BuildResult:
    """Paths and metadata produced by :func:`build_work_pack`."""

    archive_path: Path
    checksum_path: Path
    archive_sha256: str
    manifest: dict[str, Any]


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_pattern(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkPackError(f"{field} entries must be non-empty strings")
    if "\\" in value:
        raise WorkPackError(f"{field} entry uses a backslash: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise WorkPackError(f"unsafe {field} entry: {value!r}")
    return path.as_posix()


def _normalise_relative(path: Path, root: Path) -> str:
    """Return a safe POSIX path for an archive entry.

    Symlinks and paths that resolve outside *root* are rejected even when the
    lexical path appears to be inside the repository.
    """

    root_resolved = root.resolve()
    candidate = path.resolve(strict=False)
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkPackError(f"path escapes source root: {path}") from exc

    lexical = path.relative_to(root)
    if any(part in ("", ".", "..") for part in lexical.parts):
        raise WorkPackError(f"unsafe source path: {lexical}")
    # A symlink anywhere below the root could redirect an otherwise valid file.
    current = root
    for part in lexical.parts:
        current = current / part
        if current.is_symlink():
            raise WorkPackError(f"symlink is not allowed in Work Pack sources: {lexical}")
    if not path.is_file():
        raise WorkPackError(f"Work Pack source is not a regular file: {lexical}")
    return PurePosixPath(relative.as_posix()).as_posix()


def _pattern_matches(relative: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return relative == prefix or relative.startswith(prefix + "/")
    if pattern.startswith("**/"):
        # fnmatch does not treat **/ as matching a root-level filename.  The
        # basename check keeps exclusions such as **/.env effective at root.
        if fnmatch.fnmatchcase(relative, pattern[3:]):
            return True
    return fnmatch.fnmatchcase(relative, pattern)


def _is_excluded(relative: str, patterns: Iterable[str]) -> bool:
    return any(_pattern_matches(relative, pattern) for pattern in patterns)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkPackError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkPackError(f"manifest must be a JSON object: {path}")
    return value


def load_pack_spec(path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    """Load and minimally validate the checked-in static package allow-list."""

    spec = _load_json(path)
    for key in ("format", "manifest_version", "pack_version", "schema_version", "include", "exclude"):
        if key not in spec:
            raise WorkPackError(f"pack manifest missing {key!r}")
    if spec["format"] != "evidenceradar-work-pack-manifest":
        raise WorkPackError(f"unexpected pack manifest format: {spec['format']!r}")
    if not isinstance(spec["pack_version"], str) or not _VERSION_RE.fullmatch(spec["pack_version"]):
        raise WorkPackError(f"invalid pack version: {spec['pack_version']!r}")
    if not isinstance(spec["schema_version"], str) or not spec["schema_version"]:
        raise WorkPackError("schema_version must be a non-empty string")
    for field in ("include", "exclude"):
        values = spec[field]
        if not isinstance(values, list) or not values:
            raise WorkPackError(f"{field} must be a non-empty list")
        spec[field] = [_normalise_pattern(item, field=field) for item in values]
    required = spec.get("required", [])
    if not isinstance(required, list):
        raise WorkPackError("required must be a list when present")
    spec["required"] = [_normalise_pattern(item, field="required") for item in required]
    return spec


def _glob_candidates(root: Path, pattern: str) -> list[Path]:
    if pattern.endswith("/**"):
        # pathlib treats a trailing ** as the directory itself on some
        # supported Python versions.  Enumerate its files explicitly so the
        # archive includes nested templates/schemas/examples consistently.
        directory = root / pattern[:-3].rstrip("/")
        if not directory.exists():
            return []
        return sorted(
            (candidate for candidate in directory.rglob("*") if candidate.is_file()),
            key=lambda item: item.as_posix(),
        )
    try:
        candidates = list(root.glob(pattern))
    except (NotImplementedError, OSError) as exc:
        raise WorkPackError(f"invalid include pattern {pattern!r}: {exc}") from exc
    return sorted(candidates, key=lambda item: item.as_posix())


def collect_source_files(root: Path, spec: dict[str, Any]) -> list[tuple[str, Path]]:
    """Collect and validate all allow-listed source files in stable order."""

    root = root.resolve()
    include = spec.get("include")
    exclude = spec.get("exclude")
    if not isinstance(include, list) or not isinstance(exclude, list):
        raise WorkPackError("spec include/exclude lists are required")

    found: dict[str, Path] = {}
    for raw_pattern in include:
        pattern = _normalise_pattern(raw_pattern, field="include")
        candidates = _glob_candidates(root, pattern)
        # Exact required paths are checked below.  Globs may be empty so a
        # future optional extension (for example config/*.yaml) is harmless.
        for candidate in candidates:
            if not candidate.is_file():
                continue
            relative = _normalise_relative(candidate, root)
            if relative == "manifest.json":
                raise WorkPackError("source tree must not provide the generated manifest.json")
            if _is_excluded(relative, exclude):
                raise WorkPackError(f"allow-list intersects an excluded path: {relative}")
            found[relative] = candidate

    for required in spec.get("required", []):
        required_path = root / required
        if not required_path.is_file():
            raise WorkPackError(f"required Work Pack source is missing: {required}")
        relative = _normalise_relative(required_path, root)
        if relative not in found:
            raise WorkPackError(f"required source is not covered by include patterns: {required}")

    if not found:
        raise WorkPackError("pack manifest selected no source files")

    result: list[tuple[str, Path]] = []
    for relative in sorted(found):
        source = found[relative]
        data = source.read_bytes()
        for pattern in _SECRET_PATTERNS:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if pattern.search(text):
                raise WorkPackError(f"possible secret pattern in Work Pack source: {relative}")
        result.append((relative, source))
    return result


def _git_state(root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {
            "git_commit": "NO_GIT_COMMIT",
            "source_commit": "NO_GIT_COMMIT-dirty",
            "git_dirty": True,
            "git_state": "unavailable",
        }
    if not commit:
        return {
            "git_commit": "NO_GIT_COMMIT",
            "source_commit": "NO_GIT_COMMIT-dirty",
            "git_dirty": True,
            "git_state": "unavailable",
        }
    dirty = bool(status.strip())
    return {
        "git_commit": commit,
        "source_commit": f"{commit}-dirty" if dirty else commit,
        "git_dirty": dirty,
        "git_state": "dirty" if dirty else "clean",
    }


def _source_date_epoch(value: int | str | None, spec: dict[str, Any]) -> int:
    if value is None:
        value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        reproducibility = spec.get("reproducibility", {})
        value = reproducibility.get("default_source_date_epoch", 0)
    try:
        epoch = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkPackError(f"SOURCE_DATE_EPOCH must be an integer: {value!r}") from exc
    if epoch < 0:
        raise WorkPackError("SOURCE_DATE_EPOCH must be non-negative")
    try:
        timestamp = _datetime.datetime.fromtimestamp(epoch, tz=_datetime.timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise WorkPackError(f"SOURCE_DATE_EPOCH is outside the supported range: {epoch}") from exc
    if timestamp.year > 2107:
        raise WorkPackError("SOURCE_DATE_EPOCH must map to a ZIP-compatible year no later than 2107")
    return epoch


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    timestamp = _datetime.datetime.fromtimestamp(epoch, tz=_datetime.timezone.utc)
    if timestamp.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    # ZIP stores seconds at two-second precision.  Normalising here avoids a
    # platform-dependent truncation when an odd SOURCE_DATE_EPOCH is supplied.
    second = timestamp.second - (timestamp.second % 2)
    return (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, second)


def _zip_entry(name: str, data: bytes, timestamp: tuple[int, int, int, int, int, int]) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=timestamp)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0x800  # UTF-8 names, deterministic for non-ASCII paths.
    info.external_attr = 0o100644 << 16
    info.extra = b""
    info.comment = b""
    return info


def _validate_version(version: str) -> str:
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise WorkPackError(f"invalid pack version: {version!r}")
    return version


def build_work_pack(
    root: Path = ROOT,
    output_dir: Path | None = None,
    *,
    version: str | None = None,
    schema_version: str | None = None,
    source_date_epoch: int | str | None = None,
    spec_path: Path | None = None,
) -> BuildResult:
    """Build a deterministic Work Pack and return its archive metadata."""

    root = Path(root).resolve()
    spec = load_pack_spec(Path(spec_path or (root / "release" / "work-pack-manifest.json")))
    pack_version = _validate_version(version or str(spec["pack_version"]))
    selected_schema_version = str(schema_version or spec["schema_version"])
    if not selected_schema_version:
        raise WorkPackError("schema_version must be non-empty")
    files = collect_source_files(root, spec)
    epoch = _source_date_epoch(source_date_epoch, spec)
    git = _git_state(root)

    records: list[dict[str, Any]] = []
    payloads: list[tuple[str, bytes]] = []
    for relative, source in files:
        data = source.read_bytes()
        records.append({"path": relative, "sha256": _sha256(data), "size": len(data)})
        payloads.append((relative, data))

    seed_state = next(
        (dict(record) for record in records if record["path"] == SEED_STATE_PATH),
        None,
    )
    if seed_state is None:
        raise WorkPackError(f"Work Pack must include seed State: {SEED_STATE_PATH}")

    manifest: dict[str, Any] = {
        "format": "evidenceradar-work-pack",
        "manifest_version": str(spec["manifest_version"]),
        "pack_version": pack_version,
        "schema_version": selected_schema_version,
        "source_commit": git["source_commit"],
        "git_commit": git["git_commit"],
        "git_dirty": git["git_dirty"],
        "git_state": git["git_state"],
        "execution_lane": "chatgpt_work",
        "entrypoint": WORK_ENTRYPOINT,
        "terminal_delivery": True,
        "canonical_artifacts": list(CANONICAL_ARTIFACTS),
        "github_role": "source_and_package_storage_only",
        "post_download_github_access": False,
        "disabled_entrypoints": list(DISABLED_ENTRYPOINTS),
        "capabilities": list(WORK_PACK_CAPABILITIES),
        "seed_state": seed_state,
        "source_date_epoch": epoch,
        "archive_manifest_path": str(spec.get("archive_manifest_path", "manifest.json")),
        "files": records,
        "file_count": len(records),
        "schema_files": [item["path"] for item in records if item["path"].startswith("schemas/")],
        "excluded_patterns": list(spec["exclude"]),
        "reproducible": True,
    }
    manifest_data = _canonical_json(manifest)
    manifest_path = PurePosixPath(manifest["archive_manifest_path"])
    _normalise_pattern(manifest_path.as_posix(), field="archive_manifest_path")

    if output_dir is None:
        output_dir = root / "dist"
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"EvidenceRadar-WorkPack-v{pack_version}.zip"
    archive_path = output_dir / archive_name
    checksum_path = output_dir / f"{archive_name}.sha256"
    timestamp = _zip_datetime(epoch)

    archive_buffer = io.BytesIO()
    with ZipFile(archive_buffer, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, data in payloads:
            archive.writestr(_zip_entry(relative, data, timestamp), data)
        archive.writestr(_zip_entry(manifest_path.as_posix(), manifest_data, timestamp), manifest_data)
    archive_bytes = archive_buffer.getvalue()
    archive_path.write_bytes(archive_bytes)
    archive_sha256 = _sha256(archive_bytes)
    checksum_path.write_text(f"{archive_sha256}  {archive_name}\n", encoding="utf-8")
    return BuildResult(
        archive_path=archive_path,
        checksum_path=checksum_path,
        archive_sha256=archive_sha256,
        manifest=manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="EvidenceRadar repository root")
    parser.add_argument("--output-dir", type=Path, default=None, help="directory for ZIP and .sha256")
    parser.add_argument("--version", help="override the version in release/work-pack-manifest.json")
    parser.add_argument("--schema-version", help="override the package schema version")
    parser.add_argument("--source-date-epoch", help="fixed UTC timestamp used for archive entries")
    parser.add_argument("--spec", type=Path, default=None, help="alternate static package manifest")
    args = parser.parse_args(argv)
    try:
        result = build_work_pack(
            root=args.root,
            output_dir=args.output_dir,
            version=args.version,
            schema_version=args.schema_version,
            source_date_epoch=args.source_date_epoch,
            spec_path=args.spec,
        )
    except WorkPackError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "archive": str(result.archive_path),
                "checksum": str(result.checksum_path),
                "sha256": result.archive_sha256,
                "manifest": result.manifest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
