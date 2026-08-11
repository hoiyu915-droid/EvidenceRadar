#!/usr/bin/env python3
"""Build the immutable EvidenceRadar local Runtime release deterministically.

The Runtime package deliberately reuses the existing Work Pack allow-list for
portable protocol/config/schema/producer files, then adds only the small Runtime
contract surface. State, generated artifacts, history, Git metadata, CI files,
tests and release-build tooling are not packaged.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_work_pack import (
    WorkPackError,
    _canonical_json,
    _git_state,
    _normalise_relative,
    _sha256,
    _source_date_epoch,
    _zip_datetime,
    _zip_entry,
    collect_source_files,
    load_pack_spec,
)


VERSION_PATH = Path("runtime/VERSION")
DEFAULT_WORK_PACK_SPEC = Path("release/work-pack-manifest.json")
RUNTIME_MANIFEST_NAME = "RUNTIME_MANIFEST.json"
RUNTIME_EXTRA_PATHS = (
    "docs/RUNTIME_RELEASE.md",
    "runtime/README.md",
    "runtime/VERSION",
    "runtime/runtime-manifest.schema.json",
    "tools/run_local_runtime.py",
    "tools/verify_runtime_release.py",
)
REQUIRED_ENTRYPOINTS = (
    "tools/run_github_radar.py",
    "tools/validate_delivery_bundle.py",
    "tools/run_local_runtime.py",
    "tools/verify_runtime_release.py",
)
REQUIRED_RUNTIME_FILES = (
    "config/radar_master.json",
    "tools/featured_selection.py",
    "tools/publisher_feed.py",
    "tools/radar_control.py",
    *REQUIRED_ENTRYPOINTS,
)
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
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class RuntimeReleaseError(RuntimeError):
    """Raised when a source tree cannot satisfy the Runtime release contract."""


@dataclass(frozen=True)
class RuntimeBuildResult:
    archive_path: Path
    checksum_path: Path
    archive_sha256: str
    manifest: dict[str, Any]


def _read_runtime_version(root: Path) -> str:
    path = root / VERSION_PATH
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeReleaseError(f"cannot read Runtime version {path}: {exc}") from exc
    if not _VERSION_RE.fullmatch(version):
        raise RuntimeReleaseError(f"invalid runtime/VERSION value: {version!r}")
    return version


def _runtime_source_files(root: Path, spec_path: Path) -> tuple[list[tuple[str, Path]], dict[str, Any]]:
    try:
        spec = load_pack_spec(spec_path)
        base_files = collect_source_files(root, spec)
    except WorkPackError as exc:
        raise RuntimeReleaseError(str(exc)) from exc

    selected = {relative: source for relative, source in base_files}
    for raw_relative in RUNTIME_EXTRA_PATHS:
        source = root / raw_relative
        if not source.is_file():
            raise RuntimeReleaseError(f"required Runtime source is missing: {raw_relative}")
        try:
            relative = _normalise_relative(source, root)
        except WorkPackError as exc:
            raise RuntimeReleaseError(str(exc)) from exc
        selected[relative] = source

    for relative in selected:
        if relative == RUNTIME_MANIFEST_NAME:
            raise RuntimeReleaseError("source tree must not provide generated RUNTIME_MANIFEST.json")
        if relative.startswith(FORBIDDEN_PREFIXES):
            raise RuntimeReleaseError(f"forbidden Runtime package path selected: {relative}")

    missing = sorted(set(REQUIRED_RUNTIME_FILES) - set(selected))
    if missing:
        raise RuntimeReleaseError(
            "Runtime package omits required producer files: " + ", ".join(missing)
        )
    return sorted(selected.items()), spec


def build_runtime_release(
    root: Path = ROOT,
    output_dir: Path | None = None,
    *,
    source_date_epoch: int | str | None = None,
    work_pack_spec: Path | None = None,
) -> RuntimeBuildResult:
    """Build one clean, deterministic and versioned Runtime release archive."""

    root = Path(root).resolve()
    version = _read_runtime_version(root)
    spec_path = Path(work_pack_spec or (root / DEFAULT_WORK_PACK_SPEC)).resolve()
    files, work_pack_spec_value = _runtime_source_files(root, spec_path)
    git = _git_state(root)
    commit = str(git.get("git_commit") or "")
    if git.get("git_state") != "clean" or git.get("git_dirty") is not False:
        raise RuntimeReleaseError("Runtime release requires a clean Git checkout")
    if not _COMMIT_RE.fullmatch(commit):
        raise RuntimeReleaseError("Runtime release requires an exact 40-character Git commit")
    if git.get("source_commit") != commit:
        raise RuntimeReleaseError("Runtime source_commit must equal the clean Git commit")

    try:
        epoch = _source_date_epoch(source_date_epoch, work_pack_spec_value)
    except WorkPackError as exc:
        raise RuntimeReleaseError(str(exc)) from exc

    archive_name = f"EvidenceRadar-Runtime-v{version}.zip"
    checksum_name = f"{archive_name}.sha256"
    payloads: list[tuple[str, bytes]] = []
    records: list[dict[str, Any]] = []
    for relative, source in files:
        data = source.read_bytes()
        payloads.append((relative, data))
        records.append({"path": relative, "sha256": _sha256(data), "size": len(data)})

    manifest: dict[str, Any] = {
        "format": "evidenceradar-runtime-release",
        "manifest_version": "1",
        "runtime_version": version,
        "semantic_contract": "3",
        "python_version": "3.12",
        "source_commit": commit,
        "git_commit": commit,
        "git_dirty": False,
        "git_state": "clean",
        "immutable_source": True,
        "state_packaged": False,
        "artifacts_packaged": False,
        "execution_lane": "github_actions",
        "execution_host": "local_runtime",
        "archive_name": archive_name,
        "checksum_sidecar": checksum_name,
        "source_date_epoch": epoch,
        "work_pack_version": str(work_pack_spec_value.get("pack_version") or "unknown"),
        "file_count": len(records),
        "files": records,
        "required_entrypoints": list(REQUIRED_ENTRYPOINTS),
    }
    manifest_data = _canonical_json(manifest)

    output_dir = Path(output_dir or (root / "dist")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / archive_name
    checksum_path = output_dir / checksum_name
    timestamp = _zip_datetime(epoch)

    archive_buffer = io.BytesIO()
    with ZipFile(archive_buffer, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, data in payloads:
            archive.writestr(_zip_entry(relative, data, timestamp), data)
        archive.writestr(_zip_entry(RUNTIME_MANIFEST_NAME, manifest_data, timestamp), manifest_data)
    archive_bytes = archive_buffer.getvalue()
    archive_path.write_bytes(archive_bytes)
    archive_sha256 = _sha256(archive_bytes)
    checksum_path.write_text(
        f"{archive_sha256}  {archive_name}\n",
        encoding="utf-8",
    )
    return RuntimeBuildResult(
        archive_path=archive_path,
        checksum_path=checksum_path,
        archive_sha256=archive_sha256,
        manifest=manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="EvidenceRadar source checkout")
    parser.add_argument("--output-dir", type=Path, default=None, help="directory for ZIP and checksum")
    parser.add_argument("--source-date-epoch", help="fixed UTC timestamp for reproducible ZIP entries")
    parser.add_argument("--work-pack-spec", type=Path, default=None, help="alternate Work Pack allow-list")
    args = parser.parse_args(argv)
    try:
        result = build_runtime_release(
            root=args.root,
            output_dir=args.output_dir,
            source_date_epoch=args.source_date_epoch,
            work_pack_spec=args.work_pack_spec,
        )
    except RuntimeReleaseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "archive": str(result.archive_path),
                "checksum": str(result.checksum_path),
                "sha256": result.archive_sha256,
                "runtime_version": result.manifest["runtime_version"],
                "source_commit": result.manifest["source_commit"],
                "file_count": result.manifest["file_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
