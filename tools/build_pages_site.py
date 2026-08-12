#!/usr/bin/env python3
"""Build a validated static GitHub Pages site and immutable run archive."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_contract import BUNDLE_FILENAMES
from tools.promote_workrun_bundle import PromotionError, verify_workrun_archive
from tools.strict_json import loads as strict_json_loads
from tools.validate_delivery_bundle import validate_delivery_bundle


class PagesBuildError(RuntimeError):
    pass


REPORT_FILE, STATE_FILE, EVIDENCE_FILE, RUN_FILE = BUNDLE_FILENAMES
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PAGES_HISTORY_FORMAT = "evidenceradar-pages-history"
MAX_HISTORY_FILE_BYTES = 128 * 1024 * 1024
MAX_HISTORY_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
MAX_HISTORY_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CHECKSUM_SIDECAR_BYTES = 1024
HISTORICAL_RENDER_DRIFT_ERROR = (
    "V3 Report HTML is not the canonical byte-identical projection of Run + Evidence"
)


@dataclass(frozen=True)
class ValidatedRunBundle:
    run_id: str
    protocol_commit: str
    payloads: dict[str, bytes]
    source: str


def github_pages_base_url(repository: str) -> str:
    parts = repository.strip().split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise PagesBuildError("repository must use owner/name form")
    owner, name = parts
    if name.casefold() == f"{owner}.github.io".casefold():
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{name}"


def _url(base: str, relative: str = "") -> str:
    base = base.rstrip("/")
    return f"{base}/{relative.lstrip('/')}" if relative else f"{base}/"


def _safe_run_id(value: Any, *, source: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 180
        or not RUN_ID_RE.fullmatch(value)
    ):
        raise PagesBuildError(
            f"validated bundle from {source} has an unsafe run_id: {value!r}"
        )
    return value


def _read_four_files(directory: Path, *, source: str) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise PagesBuildError(f"historical bundle is not a regular directory: {source}")
    entries = list(directory.iterdir())
    names = {entry.name for entry in entries}
    if names != set(BUNDLE_FILENAMES):
        raise PagesBuildError(
            f"historical directory must contain exactly the canonical four files: {source}"
        )
    payloads: dict[str, bytes] = {}
    resolved = directory.resolve()
    total_size = 0
    for name in BUNDLE_FILENAMES:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise PagesBuildError(f"historical artifact is missing or unsafe: {path}")
        try:
            path.resolve(strict=True).relative_to(resolved)
        except (OSError, ValueError) as exc:
            raise PagesBuildError(f"historical artifact escapes its bundle: {path}") from exc
        size = path.stat().st_size
        total_size += size
        if size < 1 or size > MAX_HISTORY_FILE_BYTES:
            raise PagesBuildError(f"historical artifact has an unsafe size: {path}")
        if total_size > MAX_HISTORY_BUNDLE_BYTES:
            raise PagesBuildError(f"historical bundle exceeds the size limit: {directory}")
        payloads[name] = path.read_bytes()
    return payloads


def _git(
    root: Path,
    args: Sequence[str],
    *,
    text: bool,
) -> subprocess.CompletedProcess[Any]:
    environment = os.environ.copy()
    environment["GIT_NO_LAZY_FETCH"] = "1"
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        env=environment,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _resolve_history_baseline(root: Path, value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise PagesBuildError(
            "Pages history baseline must be a full lowercase Git commit SHA"
        )
    commit = _git(
        root,
        ["rev-parse", "--verify", f"{value}^{{commit}}"],
        text=True,
    )
    resolved = str(commit.stdout).strip()
    if commit.returncode or resolved != value:
        raise PagesBuildError(f"Pages history baseline commit is unavailable: {value}")
    head = _git(root, ["rev-parse", "--verify", "HEAD^{commit}"], text=True)
    head_commit = str(head.stdout).strip()
    if head.returncode or re.fullmatch(r"[0-9a-f]{40}", head_commit) is None:
        raise PagesBuildError("cannot resolve the current Git commit")
    if resolved == head_commit:
        raise PagesBuildError("Pages history baseline must precede the current commit")
    ancestor = _git(
        root,
        ["merge-base", "--is-ancestor", resolved, head_commit],
        text=True,
    )
    if ancestor.returncode:
        raise PagesBuildError(
            "Pages history baseline is not an ancestor of the current commit: "
            f"{resolved}"
        )
    return resolved


def _previous_history_manifest(
    root: Path,
    manifest_path: Path,
    baseline_commit: str,
) -> Any | None:
    try:
        relative_manifest = manifest_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PagesBuildError(
            "cannot compare an external Pages history manifest to a Git revision"
        ) from exc
    tree_entry = _git(
        root,
        ["ls-tree", "--name-only", baseline_commit, "--", relative_manifest],
        text=True,
    )
    if tree_entry.returncode:
        raise PagesBuildError(
            f"cannot inspect Pages history baseline {baseline_commit}"
        )
    if str(tree_entry.stdout).strip() != relative_manifest:
        return None
    previous = _git(
        root,
        ["show", f"{baseline_commit}:{relative_manifest}"],
        text=True,
    )
    if previous.returncode:
        raise PagesBuildError(
            "cannot read the previous Pages history manifest from baseline "
            f"{baseline_commit}"
        )
    try:
        return strict_json_loads(str(previous.stdout))
    except json.JSONDecodeError as exc:
        raise PagesBuildError(
            "previous Pages history manifest is not valid JSON"
        ) from exc


def _first_parent_history_manifests(
    root: Path,
    manifest_path: Path,
) -> list[tuple[str, Any]]:
    """Load every committed mainline version of the immutable inventory."""

    try:
        relative_manifest = manifest_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PagesBuildError(
            "cannot inspect Git history for an external Pages history manifest"
        ) from exc
    revisions = _git(
        root,
        ["rev-list", "--first-parent", "HEAD", "--", relative_manifest],
        text=True,
    )
    if revisions.returncode:
        raise PagesBuildError("cannot inspect Pages manifest first-parent history")
    commits = [value for value in str(revisions.stdout).splitlines() if value]
    if len(commits) > 4096:
        raise PagesBuildError("Pages manifest history exceeds the audit limit")
    values: list[tuple[str, Any]] = []
    for commit in commits:
        previous = _previous_history_manifest(root, manifest_path, commit)
        if previous is not None:
            values.append((commit, previous))
    return values


def _history_file_records(
    value: Any,
    *,
    source: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(BUNDLE_FILENAMES):
        raise PagesBuildError(f"history manifest has incomplete file records: {source}")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise PagesBuildError(
                f"history manifest file record {index} is malformed: {source}"
            )
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        if path != BUNDLE_FILENAMES[index]:
            raise PagesBuildError(
                f"history manifest file records are not canonical and ordered: {source}"
            )
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise PagesBuildError(f"history manifest has invalid SHA-256: {source}")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
            or size > MAX_HISTORY_FILE_BYTES
        ):
            raise PagesBuildError(f"history manifest has unsafe file size: {source}")
        records.append({"path": path, "sha256": digest, "size": size})
    if sum(item["size"] for item in records) > MAX_HISTORY_BUNDLE_BYTES:
        raise PagesBuildError(f"history bundle exceeds the size limit: {source}")
    return records


def _verify_manifest_payloads(
    payloads: Mapping[str, bytes],
    records: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> None:
    if set(payloads) != set(BUNDLE_FILENAMES):
        raise PagesBuildError(f"history source is not a four-file bundle: {source}")
    for record in records:
        name = str(record["path"])
        payload = payloads[name]
        if len(payload) != record["size"]:
            raise PagesBuildError(f"history manifest size mismatch for {source}/{name}")
        if hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise PagesBuildError(f"history manifest SHA-256 mismatch for {source}/{name}")


def _preflight_history_zip(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise PagesBuildError(f"history ZIP is missing or unsafe: {path}")
    if path.stat().st_size > MAX_HISTORY_BUNDLE_BYTES:
        raise PagesBuildError(f"history ZIP exceeds the compressed size limit: {path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            total = 0
            for info in archive.infolist():
                if info.file_size > MAX_HISTORY_FILE_BYTES:
                    raise PagesBuildError(
                        f"history ZIP member exceeds the size limit: {info.filename}"
                    )
                total += info.file_size
                if total > MAX_HISTORY_BUNDLE_BYTES:
                    raise PagesBuildError(
                        f"history ZIP exceeds the uncompressed size limit: {path}"
                    )
                if (
                    info.file_size > 0
                    and info.file_size / max(info.compress_size, 1)
                    > MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise PagesBuildError(
                        f"history ZIP member has an unsafe compression ratio: {info.filename}"
                    )
    except zipfile.BadZipFile as exc:
        raise PagesBuildError(f"invalid history ZIP: {path}") from exc


def _validate_with_recorded_producer(
    *,
    root: Path,
    payloads: Mapping[str, bytes],
    run_id: str,
    execution_lane: str,
    protocol_commit: str,
    source: str,
) -> ValidatedRunBundle:
    commit = _git(
        root,
        ["rev-parse", "--verify", f"{protocol_commit}^{{commit}}"],
        text=True,
    )
    if commit.returncode or str(commit.stdout).strip() != protocol_commit:
        raise PagesBuildError(
            f"history producer commit is unavailable for {source}: {protocol_commit}"
        )
    with tempfile.TemporaryDirectory(prefix="evidenceradar-pages-producer-") as directory:
        temporary = Path(directory)
        bundle = temporary / "bundle"
        producer = temporary / "producer"
        bundle.mkdir()
        for name in BUNDLE_FILENAMES:
            (bundle / name).write_bytes(payloads[name])
        added = False
        try:
            worktree = _git(
                root,
                ["worktree", "add", "--detach", str(producer), protocol_commit],
                text=True,
            )
            if worktree.returncode:
                detail = str(worktree.stderr).strip() or str(worktree.stdout).strip()
                raise PagesBuildError(
                    f"cannot materialize history producer {protocol_commit}: {detail}"
                )
            added = True
            validator = producer / "tools" / "validate_delivery_bundle.py"
            if not validator.is_file() or validator.is_symlink():
                raise PagesBuildError(
                    f"history producer has no safe delivery validator: {protocol_commit}"
                )
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            validation = subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--root",
                    str(producer),
                    "--bundle",
                    str(bundle),
                    "--canonical-state",
                    str(bundle / STATE_FILE),
                    "--expected-lane",
                    execution_lane,
                    "--expected-protocol-commit",
                    protocol_commit,
                    "--require-current-producer",
                    "--require-semantic-contract-v3",
                    "--reject-dirty",
                ],
                cwd=producer,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if validation.returncode:
                detail = validation.stdout.strip()
                if len(detail) > 4000:
                    detail = detail[-4000:]
                raise PagesBuildError(
                    f"history bundle failed its recorded producer validator: {source}"
                    + (f"\n{detail}" if detail else "")
                )
        finally:
            if added:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(producer)],
                    cwd=root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
    return ValidatedRunBundle(
        run_id=run_id,
        protocol_commit=protocol_commit,
        payloads={name: bytes(payloads[name]) for name in BUNDLE_FILENAMES},
        source=source,
    )


def _validate_with_current_contract(
    *,
    root: Path,
    payloads: Mapping[str, bytes],
    source: str,
) -> None:
    """Apply current semantic checks, deferring only renderer parity to its producer."""

    with tempfile.TemporaryDirectory(prefix="evidenceradar-pages-current-") as directory:
        bundle = Path(directory)
        for name in BUNDLE_FILENAMES:
            (bundle / name).write_bytes(payloads[name])
        errors, _run = validate_delivery_bundle(
            root,
            bundle,
            canonical_state=bundle / STATE_FILE,
            require_current_producer=False,
            require_semantic_contract_v3=True,
            reject_dirty=True,
        )
    blocking = [error for error in errors if error != HISTORICAL_RENDER_DRIFT_ERROR]
    if blocking:
        raise PagesBuildError(
            f"history bundle failed the current delivery contract: {source}\n"
            + "\n".join(blocking)
        )


def _validate_append_only_history(previous_value: Any, current_value: Any) -> None:
    previous_runs = (
        previous_value.get("runs") if isinstance(previous_value, dict) else None
    )
    current_runs = current_value.get("runs") if isinstance(current_value, dict) else None
    if not isinstance(previous_runs, list) or not all(
        isinstance(item, dict) and isinstance(item.get("run_id"), str)
        for item in previous_runs
    ):
        raise PagesBuildError("previous Pages history manifest is malformed")
    if not isinstance(current_runs, list):
        raise PagesBuildError("Pages history manifest runs must be an array")
    current_by_id = {
        item.get("run_id"): item for item in current_runs if isinstance(item, dict)
    }
    for previous_entry in previous_runs:
        previous_run_id = previous_entry["run_id"]
        if current_by_id.get(previous_run_id) != previous_entry:
            raise PagesBuildError(
                "Pages history manifest is append-only; prior entry was "
                f"removed or changed: {previous_run_id}"
            )


def _manifest_history_candidates(
    root: Path,
    manifest_path: Path,
    *,
    baseline_commit: str | None,
) -> list[ValidatedRunBundle]:
    selected = Path(manifest_path)
    if selected.is_symlink() or not selected.is_file():
        raise PagesBuildError(f"Pages history manifest is missing or unsafe: {selected}")
    if selected.absolute().parent != selected.parent.resolve():
        raise PagesBuildError("Pages history manifest path contains a symlink")
    manifest_path = selected.resolve()
    if manifest_path.stat().st_size > MAX_HISTORY_MANIFEST_BYTES:
        raise PagesBuildError("Pages history manifest exceeds the size limit")
    try:
        manifest = strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PagesBuildError(f"cannot read Pages history manifest: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "format",
        "manifest_version",
        "runs",
    }:
        raise PagesBuildError("Pages history manifest has an unsupported shape")
    if manifest.get("format") != PAGES_HISTORY_FORMAT:
        raise PagesBuildError("Pages history manifest has an unsupported format")
    if str(manifest.get("manifest_version")) != "1":
        raise PagesBuildError("Pages history manifest has an unsupported version")
    if baseline_commit is not None:
        previous_value = _previous_history_manifest(
            root,
            manifest_path,
            baseline_commit,
        )
        if previous_value is not None:
            _validate_append_only_history(previous_value, manifest)
        for commit, historical_value in _first_parent_history_manifests(
            root, manifest_path
        ):
            try:
                _validate_append_only_history(historical_value, manifest)
            except PagesBuildError as exc:
                raise PagesBuildError(
                    "Pages history is not append-only relative to mainline "
                    f"revision {commit}: {exc}"
                ) from exc
    entries = manifest.get("runs")
    if not isinstance(entries, list):
        raise PagesBuildError("Pages history manifest runs must be an array")
    run_ids = [entry.get("run_id") for entry in entries if isinstance(entry, dict)]
    if len(run_ids) != len(entries) or run_ids != sorted(run_ids):
        raise PagesBuildError("Pages history manifest runs must be sorted objects")
    normalized: set[str] = set()
    for value in run_ids:
        run_id = _safe_run_id(value, source=str(manifest_path))
        normalized_id = quote(run_id, safe="-._~+").casefold()
        if normalized_id in normalized:
            raise PagesBuildError(f"duplicate/colliding run_id in history manifest: {run_id}")
        normalized.add(normalized_id)
    accepted: list[ValidatedRunBundle] = []
    for entry in entries:
        assert isinstance(entry, dict)
        run_id = _safe_run_id(entry.get("run_id"), source=str(manifest_path))
        protocol_commit = entry.get("protocol_commit")
        if not isinstance(protocol_commit, str) or re.fullmatch(
            r"[0-9a-f]{40}", protocol_commit
        ) is None:
            raise PagesBuildError(f"history entry has invalid protocol_commit: {run_id}")
        has_directory = "directory" in entry
        has_archive = "archive" in entry
        expected_keys = {"run_id", "protocol_commit", "files"}
        if has_directory == has_archive:
            raise PagesBuildError(
                f"history entry must select exactly one source type: {run_id}"
            )
        expected_keys.add("directory" if has_directory else "archive")
        if has_archive:
            expected_keys.add("archive_sha256")
        if set(entry) != expected_keys:
            raise PagesBuildError(f"history entry has unsupported fields: {run_id}")
        records = _history_file_records(entry.get("files"), source=run_id)
        if has_directory:
            if entry.get("directory") != run_id:
                raise PagesBuildError(
                    f"history directory must be the exact run_id component: {run_id}"
                )
            directory = manifest_path.parent / run_id
            payloads = _read_four_files(directory, source=str(directory))
            source = str(directory)
        else:
            archive_name = f"EvidenceRadar-WorkRun-{run_id}.zip"
            if entry.get("archive") != archive_name:
                raise PagesBuildError(f"history archive name is not canonical: {run_id}")
            expected_archive_sha = entry.get("archive_sha256")
            if not isinstance(expected_archive_sha, str) or SHA256_RE.fullmatch(
                expected_archive_sha
            ) is None:
                raise PagesBuildError(f"history archive SHA-256 is invalid: {run_id}")
            archive = manifest_path.parent / archive_name
            sidecar = manifest_path.parent / (archive_name + ".sha256")
            if archive.is_symlink() or sidecar.is_symlink():
                raise PagesBuildError(f"history archive or checksum is a symlink: {run_id}")
            if (
                not sidecar.is_file()
                or sidecar.stat().st_size < 1
                or sidecar.stat().st_size > MAX_CHECKSUM_SIDECAR_BYTES
            ):
                raise PagesBuildError(
                    f"history archive checksum is missing or unsafe: {run_id}"
                )
            _preflight_history_zip(archive)
            try:
                verified = verify_workrun_archive(archive, checksum=sidecar)
            except PromotionError as exc:
                raise PagesBuildError(
                    f"unsafe or unverifiable history archive {archive}: {exc}"
                ) from exc
            if verified.archive_sha256 != expected_archive_sha:
                raise PagesBuildError(f"history archive SHA-256 drift: {run_id}")
            payloads = verified.payloads
            source = str(archive)
        _verify_manifest_payloads(payloads, records, source=source)
        try:
            run = strict_json_loads(payloads[RUN_FILE].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PagesBuildError(f"history Run artifact is invalid: {run_id}") from exc
        if not isinstance(run, dict):
            raise PagesBuildError(f"history Run artifact must be an object: {run_id}")
        if run.get("run_id") != run_id or run.get("protocol_commit") != protocol_commit:
            raise PagesBuildError(f"history manifest provenance mismatch: {run_id}")
        lane = run.get("execution_lane")
        if lane not in {"github_actions", "chatgpt_work"}:
            raise PagesBuildError(f"history entry has invalid execution_lane: {run_id}")
        _validate_with_current_contract(
            root=root,
            payloads=payloads,
            source=source,
        )
        accepted.append(
            _validate_with_recorded_producer(
                root=root,
                payloads=payloads,
                run_id=run_id,
                execution_lane=lane,
                protocol_commit=protocol_commit,
                source=source,
            )
        )
    return accepted


def _add_immutable_run(
    archive: dict[str, ValidatedRunBundle],
    candidate: ValidatedRunBundle,
) -> None:
    candidate_key = quote(candidate.run_id, safe="-._~+").casefold()
    for archived_run_id, archived in archive.items():
        archived_key = quote(archived_run_id, safe="-._~+").casefold()
        if archived_key == candidate_key and archived_run_id != candidate.run_id:
            raise PagesBuildError(
                "immutable run path collision after URL/case normalization: "
                f"{archived_run_id!r} and {candidate.run_id!r}"
            )
    existing = archive.get(candidate.run_id)
    if existing is None:
        archive[candidate.run_id] = candidate
        return
    if existing.payloads != candidate.payloads:
        raise PagesBuildError(
            "immutable run_id collision has different validated bytes: "
            f"{candidate.run_id!r} from {existing.source} and {candidate.source}"
        )


def _write_run_bundle(record: ValidatedRunBundle, destination: Path) -> None:
    destination.mkdir(parents=True)
    for name in BUNDLE_FILENAMES:
        (destination / name).write_bytes(record.payloads[name])
    (destination / "index.html").write_bytes(record.payloads[REPORT_FILE])


def _snapshot_landing_page(
    *,
    run_id: str,
    finished_at: str,
    protocol_commit: str,
    report_url: str,
    immutable_report_url: str,
) -> str:
    """Render an explicit snapshot notice instead of presenting history as live data."""

    escaped = {
        "run_id": html.escape(run_id),
        "finished_at": html.escape(finished_at),
        "protocol_commit": html.escape(protocol_commit),
        "report_url": html.escape(report_url, quote=True),
        "immutable_report_url": html.escape(immutable_report_url, quote=True),
    }
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EvidenceRadar published snapshot</title>
  <style>
    body {{ font: 16px/1.6 system-ui, sans-serif; margin: 0; color: #172033; background: #f5f7fb; }}
    main {{ max-width: 760px; margin: 10vh auto; padding: 2rem; background: white; border-radius: 14px; box-shadow: 0 8px 30px #17203318; }}
    .notice {{ border-left: 5px solid #b45309; padding: .8rem 1rem; background: #fff7ed; }}
    dt {{ font-weight: 700; margin-top: .8rem; }}
    dd {{ margin-left: 0; overflow-wrap: anywhere; }}
    a.button {{ display: inline-block; margin: 1rem .6rem 0 0; padding: .65rem 1rem; border-radius: 8px; color: white; background: #1d4ed8; text-decoration: none; }}
  </style>
</head>
<body><main>
  <h1>EvidenceRadar 已發布快照</h1>
  <p class="notice"><strong>這不是即時監測畫面。</strong>「latest」只代表目前已發布的最新快照，不表示資料更新到現在。</p>
  <dl>
    <dt>執行 ID</dt><dd>{escaped["run_id"]}</dd>
    <dt>快照完成時間</dt><dd>{escaped["finished_at"]}</dd>
    <dt>產製協定版本</dt><dd>{escaped["protocol_commit"]}</dd>
  </dl>
  <a class="button" href="{escaped["report_url"]}">開啟已發布報告</a>
  <a href="{escaped["immutable_report_url"]}">永久快照連結</a>
</main></body>
</html>
"""


def _archive_inventory(archive: Mapping[str, ValidatedRunBundle]) -> dict[str, Any]:
    return {
        "format": "evidenceradar-pages-run-index",
        "manifest_version": "1",
        "run_count": len(archive),
        "runs": [
            {
                "run_id": run_id,
                "protocol_commit": archive[run_id].protocol_commit,
                "files": [
                    {
                        "path": name,
                        "sha256": hashlib.sha256(
                            archive[run_id].payloads[name]
                        ).hexdigest(),
                        "size": len(archive[run_id].payloads[name]),
                    }
                    for name in BUNDLE_FILENAMES
                ],
            }
            for run_id in sorted(archive)
        ],
    }


def build_pages_site(
    *,
    root: Path,
    bundle: Path,
    output_dir: Path,
    repository: str,
    base_url: str | None = None,
    canonical_state: Path | None = None,
    require_current_producer: bool = True,
    history_manifests: Sequence[Path] | None = None,
    history_baseline_ref: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    bundle = Path(bundle).resolve()
    output_dir = Path(output_dir).resolve()
    history_manifest_paths = tuple(Path(path) for path in history_manifests or ())
    internal_history = False
    for path in history_manifest_paths:
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        internal_history = True
    if internal_history and history_baseline_ref is None:
        raise PagesBuildError(
            "repository Pages history requires an explicit previous revision"
        )
    baseline_commit = (
        _resolve_history_baseline(root, history_baseline_ref)
        if history_baseline_ref is not None
        else None
    )
    if output_dir == bundle or bundle in output_dir.parents:
        raise PagesBuildError("Pages output cannot be inside the current bundle")
    errors, run = validate_delivery_bundle(
        root,
        bundle,
        canonical_state=canonical_state,
        require_current_producer=require_current_producer,
        require_semantic_contract_v3=True,
        reject_dirty=True,
    )
    deferred_renderer_drift = False
    if not require_current_producer and HISTORICAL_RENDER_DRIFT_ERROR in errors:
        deferred_renderer_drift = True
        errors = [error for error in errors if error != HISTORICAL_RENDER_DRIFT_ERROR]
    if errors:
        raise PagesBuildError("delivery bundle is not publishable:\n" + "\n".join(errors))
    if run is None:
        raise PagesBuildError("validated delivery is missing Run metadata")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PagesBuildError(f"Pages output directory must be empty: {output_dir}")

    base_url = str(base_url or github_pages_base_url(repository)).rstrip("/")
    if not base_url.startswith("https://"):
        raise PagesBuildError("Pages base URL must use HTTPS")
    run_id = _safe_run_id(run.get("run_id"), source=str(bundle))
    encoded_run_id = quote(run_id, safe="-._~+")
    current_payloads = {
        name: (bundle / name).read_bytes()
        for name in BUNDLE_FILENAMES
    }
    immutable_runs: dict[str, ValidatedRunBundle] = {}
    for history_manifest in history_manifest_paths:
        for candidate in _manifest_history_candidates(
            root,
            history_manifest,
            baseline_commit=baseline_commit,
        ):
            _add_immutable_run(immutable_runs, candidate)
    if history_manifest_paths and run_id not in immutable_runs:
        raise PagesBuildError(
            f"current run_id is missing from the approved Pages history: {run_id}"
        )
    if deferred_renderer_drift and not history_manifest_paths:
        raise PagesBuildError(
            "current renderer drift requires an approved history manifest and "
            "recorded-producer validation"
        )
    _add_immutable_run(
        immutable_runs,
        ValidatedRunBundle(
            run_id=run_id,
            protocol_commit=str(run.get("protocol_commit") or ""),
            payloads=current_payloads,
            source=str(bundle),
        ),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    latest_dir = output_dir / "latest"
    latest_dir.mkdir(parents=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True)

    for name in BUNDLE_FILENAMES:
        source = bundle / name
        shutil.copyfile(source, latest_dir / name)
    for archived_run_id in sorted(immutable_runs):
        _write_run_bundle(
            immutable_runs[archived_run_id],
            runs_dir / archived_run_id,
        )
    inventory = _archive_inventory(immutable_runs)
    (runs_dir / "index.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(bundle / REPORT_FILE, latest_dir / "index.html")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    finished_at = str(run.get("finished_at") or "")
    protocol_commit = str(run.get("protocol_commit") or "")
    latest_report_url = _url(base_url, "latest/EvidenceRadar_Report.html")
    immutable_report_url = _url(base_url, f"runs/{encoded_run_id}/")
    (output_dir / "index.html").write_text(
        _snapshot_landing_page(
            run_id=run_id,
            finished_at=finished_at,
            protocol_commit=protocol_commit,
            report_url=latest_report_url,
            immutable_report_url=immutable_report_url,
        ),
        encoding="utf-8",
    )

    links = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_Public_Links",
        "repository": repository,
        "run_id": run_id,
        "execution_lane": run.get("execution_lane"),
        "protocol_commit": protocol_commit,
        "snapshot_finished_at": finished_at,
        "publication_semantics": {
            "status": "PUBLISHED_SNAPSHOT_NOT_LIVE",
            "notice": (
                "latest means the latest published snapshot; it does not imply "
                "that monitoring is current to wall-clock time"
            ),
        },
        "report_url": _url(base_url),
        "links_json_url": _url(base_url, "links.json"),
        "latest": {
            "report_html": latest_report_url,
            "state_json": _url(base_url, "latest/EvidenceRadar_State.json"),
            "evidence_json": _url(base_url, "latest/EvidenceRadar_Evidence.json"),
            "run_json": _url(base_url, "latest/EvidenceRadar_Run.json"),
        },
        "immutable_run": {
            "report_html": immutable_report_url,
            "state_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_State.json"),
            "evidence_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_Evidence.json"),
            "run_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_Run.json"),
        },
        "immutable_archive": {
            "index_json": _url(base_url, "runs/index.json"),
            "run_count": len(immutable_runs),
        },
    }
    payload = json.dumps(links, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (output_dir / "links.json").write_text(payload, encoding="utf-8")
    (latest_dir / "links.json").write_text(payload, encoding="utf-8")
    print(json.dumps(links, ensure_ascii=False, sort_keys=True))
    return links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, default=Path("artifacts/current"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True, help="GitHub owner/name")
    parser.add_argument("--base-url", help="configured Pages base URL, including a custom domain")
    parser.add_argument("--canonical-state", type=Path)
    parser.add_argument(
        "--history-manifest",
        action="append",
        default=[],
        type=Path,
        help="append-only approved Pages history manifest",
    )
    parser.add_argument(
        "--history-baseline-ref",
        help=(
            "full SHA of the previously deployed revision used to enforce "
            "append-only history"
        ),
    )
    parser.add_argument(
        "--skip-current-producer-check",
        action="store_true",
        help=(
            "for archive rebuilds, defer only known renderer byte drift to the "
            "approved bundle's recorded-producer validator"
        ),
    )
    args = parser.parse_args(argv)
    try:
        build_pages_site(
            root=args.root,
            bundle=args.bundle,
            output_dir=args.output_dir,
            repository=args.repository,
            base_url=args.base_url,
            canonical_state=args.canonical_state,
            require_current_producer=not args.skip_current_producer_check,
            history_manifests=args.history_manifest,
            history_baseline_ref=args.history_baseline_ref,
        )
    except PagesBuildError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
