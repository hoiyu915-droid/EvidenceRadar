#!/usr/bin/env python3
"""Package one ChatGPT Work run as an immutable, uniquely named delivery.

The package contains the canonical four EvidenceRadar artifacts at its archive
root, a manifest with per-file SHA-256/size records, and a sidecar checksum for
the archive itself.  A run-id is part of every output name so a Work client
cannot accidentally re-use a previous attachment with the same canonical file
name.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

# Delivery runs against a verified, read-only Work Pack.  Local helper imports
# must not mutate that package with bytecode cache files.
sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_delivery_bundle import validate_delivery_bundle
from tools.validate_gpt_work_artifacts import validate_files


CANONICAL_FILES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)
_RUN_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+\-]*$")


class WorkDeliveryError(RuntimeError):
    """Raised when a Work delivery cannot be made unambiguously."""


@dataclass(frozen=True)
class DeliveryResult:
    """Paths and integrity metadata for a packaged Work run."""

    bundle_dir: Path
    manifest_path: Path
    archive_path: Path
    checksum_path: Path
    archive_sha256: str
    manifest: dict[str, Any]


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkDeliveryError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkDeliveryError(f"artifact must contain a JSON object: {path}")
    return value


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not value or not _RUN_ID_RE.fullmatch(value):
        raise WorkDeliveryError(
            "run_id must be a non-empty filename-safe value containing only "
            "letters, numbers, '.', '_', '+', or '-'"
        )
    return value


def _source_date_epoch(value: int | str | None) -> int:
    if value is None:
        value = os.environ.get("SOURCE_DATE_EPOCH", "0")
    try:
        epoch = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkDeliveryError(f"SOURCE_DATE_EPOCH must be an integer: {value!r}") from exc
    if epoch < 0:
        raise WorkDeliveryError("SOURCE_DATE_EPOCH must be non-negative")
    try:
        timestamp = _datetime.datetime.fromtimestamp(epoch, tz=_datetime.timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise WorkDeliveryError(f"SOURCE_DATE_EPOCH is outside the supported range: {epoch}") from exc
    if timestamp.year > 2107:
        raise WorkDeliveryError("SOURCE_DATE_EPOCH must map to a ZIP-compatible year no later than 2107")
    return epoch


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    timestamp = _datetime.datetime.fromtimestamp(epoch, tz=_datetime.timezone.utc)
    if timestamp.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    second = timestamp.second - (timestamp.second % 2)
    return (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, second)


def _zip_entry(name: str, data: bytes, timestamp: tuple[int, int, int, int, int, int]) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=timestamp)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0x800
    info.external_attr = 0o100644 << 16
    info.extra = b""
    info.comment = b""
    return info


def package_work_delivery(
    source_dir: Path,
    output_dir: Path,
    *,
    run_id: str | None = None,
    source_date_epoch: int | str | None = None,
    validation_root: Path | None = None,
    input_manifest: Path | None = None,
    expected_lane: str | None = "chatgpt_work",
    require_current_producer: bool = False,
) -> DeliveryResult:
    """Copy and package exactly one run's canonical four artifacts.

    Existing bundle names are rejected instead of overwritten.  This is an
    intentional fail-closed guard against serving stale bytes after a Work
    attachment is corrected or re-generated.
    """

    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if not source_dir.is_dir():
        raise WorkDeliveryError(f"source directory does not exist: {source_dir}")

    source_paths: dict[str, Path] = {}
    payloads: dict[str, bytes] = {}
    for name in CANONICAL_FILES:
        path = source_dir / name
        if not path.is_file() or path.is_symlink():
            raise WorkDeliveryError(f"missing or non-regular canonical artifact: {path}")
        source_paths[name] = path
        payloads[name] = path.read_bytes()

    run_artifact = _load_json(source_paths["EvidenceRadar_Run.json"])
    artifact_run_id = _safe_run_id(run_artifact.get("run_id"))
    selected_run_id = _safe_run_id(run_id) if run_id is not None else artifact_run_id
    if selected_run_id != artifact_run_id:
        raise WorkDeliveryError(
            f"requested run_id {selected_run_id!r} does not match Run artifact {artifact_run_id!r}"
        )

    validation_root = Path(validation_root or ROOT).resolve()
    schema_errors = validate_files(
        [source_paths["EvidenceRadar_State.json"], source_paths["EvidenceRadar_Evidence.json"], source_paths["EvidenceRadar_Run.json"]],
        schema_dir=validation_root / "schemas",
    )
    if schema_errors:
        raise WorkDeliveryError("artifact schema validation failed: " + " | ".join(schema_errors))
    protocol_commit = run_artifact.get("protocol_commit")
    if not isinstance(protocol_commit, str) or not protocol_commit:
        raise WorkDeliveryError("Run artifact must declare a non-empty protocol_commit")
    delivery_errors, _validated_run = validate_delivery_bundle(
        validation_root,
        source_dir,
        expected_lane=expected_lane,
        expected_protocol_commit=protocol_commit,
        manifest=Path(input_manifest).resolve() if input_manifest is not None else None,
        require_current_producer=require_current_producer,
        require_semantic_contract_v3=True,
    )
    if delivery_errors:
        raise WorkDeliveryError("delivery validation failed: " + " | ".join(delivery_errors))

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"EvidenceRadar-WorkRun-{selected_run_id}"
    bundle_dir = output_dir / bundle_name
    archive_path = output_dir / f"{bundle_name}.zip"
    checksum_path = output_dir / f"{bundle_name}.zip.sha256"
    if bundle_dir.exists() or archive_path.exists() or checksum_path.exists():
        raise WorkDeliveryError(
            f"delivery target already exists; use a new run_id/output directory: {bundle_name}"
        )
    bundle_dir.mkdir()

    records: list[dict[str, Any]] = []
    for name in CANONICAL_FILES:
        data = payloads[name]
        (bundle_dir / name).write_bytes(data)
        records.append({"path": name, "sha256": _sha256(data), "size": len(data)})

    manifest: dict[str, Any] = {
        "format": "evidenceradar-work-delivery",
        "manifest_version": "1",
        "run_id": selected_run_id,
        "execution_lane": run_artifact.get("execution_lane"),
        "protocol_commit": run_artifact.get("protocol_commit"),
        "canonical_files": list(CANONICAL_FILES),
        "files": records,
        "file_count": len(records),
        "archive_name": archive_path.name,
        "integrity": {
            "archive_sha256_sidecar": checksum_path.name,
            "manifest_file": "manifest.json",
        },
    }
    manifest_data = _canonical_json(manifest)
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_bytes(manifest_data)

    epoch = _source_date_epoch(source_date_epoch)
    timestamp = _zip_datetime(epoch)
    archive_buffer = io.BytesIO()
    with ZipFile(archive_buffer, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in (*CANONICAL_FILES, "manifest.json"):
            data = (bundle_dir / name).read_bytes()
            archive.writestr(_zip_entry(name, data, timestamp), data)
    archive_bytes = archive_buffer.getvalue()
    archive_path.write_bytes(archive_bytes)
    archive_sha256 = _sha256(archive_bytes)
    checksum_path.write_text(f"{archive_sha256}  {archive_path.name}\n", encoding="utf-8")

    return DeliveryResult(
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        archive_path=archive_path,
        checksum_path=checksum_path,
        archive_sha256=archive_sha256,
        manifest=manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("."), help="directory containing the canonical four artifacts")
    parser.add_argument("--output-dir", type=Path, required=True, help="fresh directory for the run bundle, ZIP and checksum")
    parser.add_argument("--run-id", help="must equal EvidenceRadar_Run.json.run_id")
    parser.add_argument("--source-date-epoch", help="fixed UTC timestamp for deterministic ZIP metadata")
    parser.add_argument(
        "--validation-root",
        type=Path,
        default=None,
        help="repository or extracted Work Pack root containing schemas (default: this repository root)",
    )
    parser.add_argument(
        "--expected-lane",
        choices=("chatgpt_work", "github_actions"),
        default="chatgpt_work",
        help="execution lane required by the four-file validator",
    )
    parser.add_argument(
        "--input-manifest",
        type=Path,
        help="extracted Work Pack manifest used to bind delivery validation",
    )
    parser.add_argument(
        "--require-current-producer",
        action="store_true",
        help="also require the protocol commit to be the current clean checkout commit",
    )
    args = parser.parse_args(argv)
    try:
        result = package_work_delivery(
            args.source_dir,
            args.output_dir,
            run_id=args.run_id,
            source_date_epoch=args.source_date_epoch,
            validation_root=args.validation_root,
            input_manifest=args.input_manifest,
            expected_lane=args.expected_lane,
            require_current_producer=args.require_current_producer,
        )
    except WorkDeliveryError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "bundle_dir": str(result.bundle_dir),
                "manifest": str(result.manifest_path),
                "archive": str(result.archive_path),
                "checksum": str(result.checksum_path),
                "sha256": result.archive_sha256,
                "run_id": result.manifest["run_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
