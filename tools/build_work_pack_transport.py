#!/usr/bin/env python3
"""Build the minimal byte-transport payload for a released EvidenceRadar Work Pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile

CANONICAL_ARCHIVE = "EvidenceRadar-WorkPack-current.zip"
CANONICAL_CHECKSUM = f"{CANONICAL_ARCHIVE}.sha256"
CANONICAL_PROVENANCE = "EvidenceRadar-WorkPack-current.sigstore.json"
TRANSPORT_MANIFEST = "TRANSPORT_MANIFEST.json"
SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CHECKSUM_RE = re.compile(r"([0-9a-f]{64})\s+[ *](.+)")


class WorkPackTransportError(RuntimeError):
    """Raised when a transport payload cannot be built safely."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(path: Path, expected_name: str) -> Path:
    if path.name != expected_name:
        raise WorkPackTransportError(
            f"expected {expected_name}, got input path {path.name}"
        )
    if not path.is_file():
        raise WorkPackTransportError(f"missing transport input: {path}")
    return path


def _verify_checksum(archive: Path, checksum: Path) -> str:
    line = checksum.read_text(encoding="utf-8").strip()
    match = CHECKSUM_RE.fullmatch(line)
    if match is None:
        raise WorkPackTransportError("checksum sidecar must contain one sha256sum line")
    expected_digest, named_file = match.groups()
    if Path(named_file).name != archive.name:
        raise WorkPackTransportError(
            f"checksum sidecar names {named_file}, expected {archive.name}"
        )
    actual_digest = sha256_path(archive)
    if actual_digest != expected_digest:
        raise WorkPackTransportError(
            f"Work Pack checksum mismatch: expected {expected_digest}, got {actual_digest}"
        )
    return actual_digest


def _load_inner_manifest(archive: Path) -> dict[str, object]:
    try:
        with ZipFile(archive) as value:
            bad_member = value.testzip()
            if bad_member is not None:
                raise WorkPackTransportError(
                    f"Work Pack ZIP CRC failure at {bad_member}"
                )
            try:
                raw = value.read("manifest.json")
            except KeyError as exc:
                raise WorkPackTransportError(
                    "Work Pack ZIP is missing manifest.json"
                ) from exc
    except BadZipFile as exc:
        raise WorkPackTransportError("Work Pack input is not a valid ZIP archive") from exc

    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkPackTransportError("Work Pack manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise WorkPackTransportError("Work Pack manifest.json must be an object")
    return manifest


def build_transport(
    archive: Path,
    checksum: Path,
    provenance: Path,
    *,
    source_commit: str,
    version: str,
    output_dir: Path,
) -> dict[str, object]:
    """Validate the canonical release trio and stage one unambiguous transport set."""

    archive = _require_file(Path(archive), CANONICAL_ARCHIVE)
    checksum = _require_file(Path(checksum), CANONICAL_CHECKSUM)
    provenance = _require_file(Path(provenance), CANONICAL_PROVENANCE)

    if SOURCE_COMMIT_RE.fullmatch(source_commit) is None:
        raise WorkPackTransportError("source_commit must be a lowercase 40-hex commit SHA")
    if not version or version.strip() != version or any(ch.isspace() for ch in version):
        raise WorkPackTransportError("version must be a non-empty whitespace-free string")

    archive_digest = _verify_checksum(archive, checksum)
    inner_manifest = _load_inner_manifest(archive)
    if inner_manifest.get("format") != "evidenceradar-work-pack":
        raise WorkPackTransportError("inner archive is not an EvidenceRadar Work Pack")
    if inner_manifest.get("source_commit") != source_commit:
        raise WorkPackTransportError(
            "inner Work Pack source_commit does not match transport source_commit"
        )
    if inner_manifest.get("git_commit") != source_commit:
        raise WorkPackTransportError(
            "inner Work Pack git_commit does not match transport source_commit"
        )
    if inner_manifest.get("pack_version") != version:
        raise WorkPackTransportError(
            "inner Work Pack pack_version does not match transport version"
        )

    try:
        provenance_value = json.loads(provenance.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkPackTransportError("Sigstore provenance bundle is not valid JSON") from exc
    if not isinstance(provenance_value, dict):
        raise WorkPackTransportError("Sigstore provenance bundle must be a JSON object")

    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise WorkPackTransportError(f"transport output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = (archive, checksum, provenance)
    for source in inputs:
        shutil.copyfile(source, output_dir / source.name)

    members = [
        {
            "path": source.name,
            "sha256": sha256_path(output_dir / source.name),
            "size": (output_dir / source.name).stat().st_size,
        }
        for source in inputs
    ]
    manifest: dict[str, object] = {
        "artifact_type": "EvidenceRadar_WorkPackTransport",
        "schema_version": "1.0",
        "transport_role": "byte_transport_only",
        "source_commit": source_commit,
        "work_pack_version": version,
        "authoritative_release_tag": f"work-pack-{source_commit}",
        "canonical_member": CANONICAL_ARCHIVE,
        "checksum_member": CANONICAL_CHECKSUM,
        "provenance_member": CANONICAL_PROVENANCE,
        "work_pack_sha256": archive_digest,
        "members": members,
    }
    (output_dir / TRANSPORT_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_transport(
            args.archive,
            args.checksum,
            args.provenance,
            source_commit=args.source_commit,
            version=args.version,
            output_dir=args.output_dir,
        )
    except WorkPackTransportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
