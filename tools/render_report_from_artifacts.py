#!/usr/bin/env python3
"""Render and validate the canonical SEMANTIC_CONTRACT_V3 HTML report."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# The released Work Pack is an immutable input.  Prevent imports of packaged
# helper modules from creating __pycache__ files inside the extracted tree.
sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_github_radar import RadarRuntimeError, render_report_from_documents, validate_documents
from tools.validate_delivery_bundle import validate_delivery_payload


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RadarRuntimeError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RadarRuntimeError(f"{path} must contain a JSON object")
    return value


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


_CANONICAL_WRITE_ORDER = (
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Report.html",
    "EvidenceRadar_Run.json",
    # State is the durable cursor and must advance only after its companion
    # artifacts have been installed.
    "EvidenceRadar_State.json",
)
_TRANSACTION_JOURNAL = ".evidenceradar-render-transaction.json"
_TRANSACTION_LOCK = ".evidenceradar-render.lock"


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes_durable(path: Path, payload: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _commit_staged(staged: Path, target: Path) -> None:
    """Install one staged artifact; kept separate for fault-injection tests."""

    os.replace(staged, target)
    _fsync_directory(target.parent)


def _remove_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _rollback_bundle_transaction(bundle: Path, journal: dict[str, Any]) -> None:
    rollback_errors: list[str] = []
    entries = journal.get("entries", [])
    token = str(journal.get("token") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RadarRuntimeError("canonical artifact transaction token is malformed")
    if not isinstance(entries, list) or {
        str(entry.get("name") or "")
        for entry in entries
        if isinstance(entry, dict)
    } != set(_CANONICAL_WRITE_ORDER):
        raise RadarRuntimeError("canonical artifact transaction entries are incomplete")
    for entry in reversed(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            rollback_errors.append("transaction journal contains a malformed entry")
            continue
        name = str(entry.get("name") or "")
        if name not in _CANONICAL_WRITE_ORDER:
            rollback_errors.append(f"transaction journal contains unsafe target {name!r}")
            continue
        expected_staged = f".{name}.{token}.staged"
        expected_backup = f".{name}.{token}.backup"
        if entry.get("staged") != expected_staged or entry.get("backup") != expected_backup:
            rollback_errors.append(f"transaction journal paths are unsafe for {name!r}")
            continue
        target = bundle / name
        staged = bundle / expected_staged
        backup = bundle / expected_backup
        try:
            if bool(entry.get("existed")):
                if backup.is_symlink() or not backup.is_file():
                    raise OSError(f"missing rollback copy {backup.name}")
                os.replace(backup, target)
            else:
                _remove_if_present(target)
            _remove_if_present(staged)
        except OSError as exc:
            rollback_errors.append(f"{name}: {exc}")
    _fsync_directory(bundle)
    if rollback_errors:
        raise RadarRuntimeError(
            "canonical artifact rollback failed: " + "; ".join(rollback_errors)
        )


def _recover_pending_transaction(bundle: Path) -> None:
    journal_path = bundle / _TRANSACTION_JOURNAL
    if not journal_path.exists():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RadarRuntimeError(
            f"cannot recover canonical artifact transaction: {exc}"
        ) from exc
    if not isinstance(journal, dict):
        raise RadarRuntimeError("canonical artifact transaction journal is malformed")
    _rollback_bundle_transaction(bundle, journal)
    _remove_if_present(journal_path)
    _fsync_directory(bundle)


def _recover_bundle_under_lock(bundle: Path) -> None:
    bundle.mkdir(parents=True, exist_ok=True)
    with (bundle / _TRANSACTION_LOCK).open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _recover_pending_transaction(bundle)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _transactional_write_bundle(
    bundle: Path,
    payloads: dict[str, str],
    *,
    expected_originals: dict[str, bytes | None],
) -> None:
    """Durably install the four canonical artifacts or restore all originals.

    A journal plus byte-for-byte backups also makes an interrupted prior render
    recoverable on the next invocation.  State is deliberately committed last.
    """

    if set(payloads) != set(_CANONICAL_WRITE_ORDER):
        raise RadarRuntimeError("canonical transaction requires exactly four artifacts")
    bundle.mkdir(parents=True, exist_ok=True)
    lock_path = bundle / _TRANSACTION_LOCK
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _recover_pending_transaction(bundle)
            for name in _CANONICAL_WRITE_ORDER:
                target = bundle / name
                if target.is_symlink():
                    raise RadarRuntimeError(
                        f"canonical artifact must not be a symlink: {target}"
                    )
                actual = target.read_bytes() if target.is_file() else None
                if actual != expected_originals.get(name):
                    raise RadarRuntimeError(
                        "canonical bundle changed during rendering; no artifacts were written"
                    )
            token = uuid.uuid4().hex
            entries: list[dict[str, Any]] = []
            for name in _CANONICAL_WRITE_ORDER:
                target = bundle / name
                staged = bundle / f".{name}.{token}.staged"
                backup = bundle / f".{name}.{token}.backup"
                existed = target.is_file() and not target.is_symlink()
                if target.exists() and not existed:
                    raise RadarRuntimeError(f"canonical artifact is not a regular file: {target}")
                _write_bytes_durable(staged, payloads[name].encode("utf-8"))
                if existed:
                    _write_bytes_durable(backup, target.read_bytes())
                entries.append(
                    {
                        "name": name,
                        "staged": staged.name,
                        "backup": backup.name,
                        "existed": existed,
                    }
                )

            journal = {"version": 1, "token": token, "entries": entries}
            journal_path = bundle / _TRANSACTION_JOURNAL
            _atomic_text(
                journal_path,
                json.dumps(journal, ensure_ascii=False, sort_keys=True) + "\n",
            )
            _fsync_directory(bundle)
            try:
                for entry in entries:
                    _commit_staged(bundle / entry["staged"], bundle / entry["name"])
            except BaseException as exc:
                try:
                    _rollback_bundle_transaction(bundle, journal)
                    _remove_if_present(journal_path)
                    _fsync_directory(bundle)
                except RadarRuntimeError as rollback_exc:
                    raise RadarRuntimeError(
                        "canonical artifact transaction failed and rollback was incomplete: "
                        f"{rollback_exc}"
                    ) from exc
                raise RadarRuntimeError(
                    "canonical artifact transaction failed; all artifacts were rolled back"
                ) from exc

            _remove_if_present(journal_path)
            for entry in entries:
                _remove_if_present(bundle / entry["backup"])
            _fsync_directory(bundle)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _sync_claim_registry(
    state: dict[str, Any], evidence: dict[str, Any], run: dict[str, Any]
) -> None:
    """Project current Evidence claims into durable cross-run State.

    Historical claims remain present.  A claim ID is immutable with respect to
    its text hash and semantic kind; the cross-bundle validator rejects a Work
    author who reuses an ID for different content.
    """

    run_id = str(run.get("run_id") or "")
    registry = {
        str(item.get("claim_id")): dict(item)
        for item in state.get("claim_registry", [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    claims = [item for item in evidence.get("claims", []) if isinstance(item, dict)]
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            continue
        previous = registry.get(claim_id, {})
        projected = {
            "claim_id": claim_id,
            "work_id": str(claim.get("work_id") or ""),
            "claim_kind": str(claim.get("claim_kind") or ""),
            "claim_origin": str(claim.get("claim_origin") or ""),
            "claim_text_sha256": hashlib.sha256(
                str(claim.get("claim_text") or "").encode("utf-8")
            ).hexdigest(),
            "status": str(claim.get("status") or ""),
            "source_ids": sorted(
                {str(value) for value in claim.get("source_ids", []) if str(value)}
            ),
            "status_binding_ids": sorted(
                {
                    str(value)
                    for value in claim.get("citation_binding_ids", [])
                    if str(value)
                }
            ),
            "first_seen_run": str(previous.get("first_seen_run") or run_id),
            "last_seen_run": run_id,
            "last_status_change_run": (
                str(previous.get("last_status_change_run") or run_id)
                if previous.get("status") == claim.get("status")
                else run_id
            ),
        }
        for field in ("work_id", "claim_kind", "claim_origin", "claim_text_sha256"):
            if previous and previous.get(field) != projected[field]:
                raise RadarRuntimeError(
                    f"claim_id {claim_id!r} was reused with different immutable {field}"
                )
        registry[claim_id] = projected
    state["claim_registry"] = [registry[key] for key in sorted(registry)]
    counts = run.get("counts")
    if isinstance(counts, dict):
        counts["claims"] = len(claims)


def render_bundle(root: Path, bundle: Path) -> str:
    root = root.resolve()
    bundle = bundle.resolve()
    _recover_bundle_under_lock(bundle)
    expected_originals = {
        name: (bundle / name).read_bytes() if (bundle / name).is_file() else None
        for name in _CANONICAL_WRITE_ORDER
    }
    state = _load_object(bundle / "EvidenceRadar_State.json")
    evidence = _load_object(bundle / "EvidenceRadar_Evidence.json")
    run = _load_object(bundle / "EvidenceRadar_Run.json")
    if "SEMANTIC_CONTRACT_V3" not in run.get("notes", []):
        raise RadarRuntimeError("canonical renderer requires SEMANTIC_CONTRACT_V3")
    _sync_claim_registry(state, evidence, run)
    report = render_report_from_documents(run, evidence)
    run["report_sha256"] = hashlib.sha256(report.encode("utf-8")).hexdigest()
    documents = {
        "EvidenceRadar_State.json": state,
        "EvidenceRadar_Evidence.json": evidence,
        "EvidenceRadar_Run.json": run,
    }
    validate_documents(root, documents)
    errors = validate_delivery_payload(
        root,
        report_html=report,
        state=state,
        evidence=evidence,
        run=run,
        expected_lane=str(run.get("execution_lane") or ""),
        expected_protocol_commit=str(run.get("protocol_commit") or ""),
        require_semantic_contract_v3=True,
    )
    if errors:
        raise RadarRuntimeError(
            "canonical delivery validation failed:\n" + "\n".join(errors)
        )
    _transactional_write_bundle(
        bundle,
        {
            "EvidenceRadar_State.json": json.dumps(
                state, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            "EvidenceRadar_Evidence.json": json.dumps(
                evidence, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            "EvidenceRadar_Run.json": json.dumps(
                run, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
            "EvidenceRadar_Report.html": report,
        },
        expected_originals=expected_originals,
    )
    return run["report_sha256"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest = render_bundle(args.root, args.bundle)
    except RadarRuntimeError as exc:
        parser.error(str(exc))
    print(f"canonical V3 report rendered: sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
