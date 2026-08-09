#!/usr/bin/env python3
"""Execute an extracted immutable EvidenceRadar Runtime against external State."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "RUNTIME_MANIFEST.json"
BUNDLE_FILENAMES = (
    "EvidenceRadar_Report.html",
    "EvidenceRadar_State.json",
    "EvidenceRadar_Evidence.json",
    "EvidenceRadar_Run.json",
)
RECEIPT_NAME = "EvidenceRadar_RuntimeReceipt.json"


class LocalRuntimeError(RuntimeError):
    """Raised when local Runtime execution cannot satisfy the release contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _outside_runtime(path: Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return resolved
    raise LocalRuntimeError(f"{label} must be outside the immutable Runtime directory: {resolved}")


def _load_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalRuntimeError(f"cannot read verified Runtime manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise LocalRuntimeError("Runtime manifest must be a JSON object")
    return value


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_checked(command: list[str], *, label: str) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_runtime_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise LocalRuntimeError(
            f"{label} failed with rc={result.returncode}:\n{result.stdout.rstrip()}"
        )
    return result.stdout


def verify_runtime_tree() -> dict[str, Any]:
    _run_checked(
        [
            sys.executable,
            str(ROOT / "tools" / "verify_runtime_release.py"),
            "--root",
            str(ROOT),
        ],
        label="Runtime source verification",
    )
    return _load_manifest()


def _check_environment(manifest: dict[str, Any]) -> None:
    expected = str(manifest.get("python_version") or "")
    observed = f"{sys.version_info.major}.{sys.version_info.minor}"
    if observed != expected:
        raise LocalRuntimeError(
            f"Runtime requires Python {expected}; current interpreter is {observed}"
        )
    missing: list[str] = []
    for import_name, package_name in (("requests", "requests"), ("yaml", "PyYAML")):
        if importlib.util.find_spec(import_name) is None:
            missing.append(package_name)
    if missing:
        raise LocalRuntimeError(
            "missing Runtime dependencies: "
            + ", ".join(missing)
            + "; install with 'python -m pip install -r requirements.txt'"
        )


def build_runner_command(
    *,
    state: Path,
    output_dir: Path,
    runs_dir: Path | None,
    protocol_commit: str,
    end_at: str | None,
    run_id: str | None,
    publisher_target_min: int | None,
    publisher_hard_max: int | None,
) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "run_github_radar.py"),
        "--root",
        str(ROOT),
        "--output-dir",
        str(output_dir),
        "--state",
        str(state),
        "--execution-lane",
        "github_actions",
        "--protocol-commit",
        protocol_commit,
    ]
    if runs_dir is not None:
        command.extend(["--runs-dir", str(runs_dir)])
    if end_at:
        command.extend(["--end-at", end_at])
    if run_id:
        command.extend(["--run-id", run_id])
    if publisher_target_min is not None:
        command.extend(["--publisher-target-min", str(publisher_target_min)])
    if publisher_hard_max is not None:
        command.extend(["--publisher-hard-max", str(publisher_hard_max)])
    return command


def _validate_bundle(*, state: Path, output_dir: Path, protocol_commit: str) -> None:
    _run_checked(
        [
            sys.executable,
            str(ROOT / "tools" / "validate_delivery_bundle.py"),
            "--root",
            str(ROOT),
            "--bundle",
            str(output_dir),
            "--canonical-state",
            str(state),
            "--expected-lane",
            "github_actions",
            "--expected-protocol-commit",
            protocol_commit,
            "--require-semantic-contract-v3",
        ],
        label="Canonical delivery validation",
    )


def _write_receipt(
    *,
    receipt_path: Path,
    manifest: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    run_path = output_dir / "EvidenceRadar_Run.json"
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalRuntimeError(f"cannot read validated Run artifact: {exc}") from exc
    if not isinstance(run, dict):
        raise LocalRuntimeError("validated Run artifact is not a JSON object")
    artifacts: dict[str, str] = {}
    for name in BUNDLE_FILENAMES:
        path = output_dir / name
        if not path.is_file():
            raise LocalRuntimeError(f"validated bundle is missing {name}")
        artifacts[name] = _sha256(path.read_bytes())
    receipt = {
        "format": "evidenceradar-runtime-execution-receipt",
        "manifest_version": "1",
        "runtime_version": manifest.get("runtime_version"),
        "runtime_source_commit": manifest.get("source_commit"),
        "runtime_manifest_sha256": _sha256(MANIFEST_PATH.read_bytes()),
        "execution_lane": "github_actions",
        "execution_host": "local_runtime",
        "run_id": run.get("run_id"),
        "finished_at": run.get("finished_at"),
        "artifact_sha256": artifacts,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.with_name(receipt_path.name + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(receipt_path)
    return receipt


def execute_local_runtime(args: argparse.Namespace) -> dict[str, Any]:
    state = _outside_runtime(args.state, label="State path")
    output_dir = _outside_runtime(args.output_dir, label="output directory")
    runs_dir = _outside_runtime(args.runs_dir, label="runs directory") if args.runs_dir else None
    receipt = _outside_runtime(
        args.receipt or (output_dir.parent / RECEIPT_NAME),
        label="Runtime receipt path",
    )
    if not state.is_file():
        raise LocalRuntimeError(
            "State file does not exist; provide the latest canonical EvidenceRadar_State.json"
        )
    if state.is_symlink():
        raise LocalRuntimeError("State path must not be a symlink")
    if output_dir == state or output_dir in state.parents:
        raise LocalRuntimeError("output directory must not contain the mutable State input path")
    if runs_dir is not None and runs_dir == output_dir:
        raise LocalRuntimeError("runs directory must be separate from the current output directory")

    manifest = verify_runtime_tree()
    _check_environment(manifest)
    protocol_commit = str(manifest.get("source_commit") or "")
    if len(protocol_commit) != 40:
        raise LocalRuntimeError("verified Runtime manifest does not contain a full source commit")

    output_dir.mkdir(parents=True, exist_ok=True)
    if runs_dir is not None:
        runs_dir.mkdir(parents=True, exist_ok=True)
    runner_output = _run_checked(
        build_runner_command(
            state=state,
            output_dir=output_dir,
            runs_dir=runs_dir,
            protocol_commit=protocol_commit,
            end_at=args.end_at,
            run_id=args.run_id,
            publisher_target_min=args.publisher_target_min,
            publisher_hard_max=args.publisher_hard_max,
        ),
        label="EvidenceRadar canonical producer",
    )
    _validate_bundle(state=state, output_dir=output_dir, protocol_commit=protocol_commit)
    verify_runtime_tree()
    receipt_value = _write_receipt(
        receipt_path=receipt,
        manifest=manifest,
        output_dir=output_dir,
    )
    return {
        "runtime_version": manifest.get("runtime_version"),
        "source_commit": protocol_commit,
        "execution_lane": "github_actions",
        "execution_host": "local_runtime",
        "run_id": receipt_value.get("run_id"),
        "output_dir": str(output_dir),
        "state": str(state),
        "receipt": str(receipt),
        "producer_stdout": runner_output.strip(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="external canonical State path")
    parser.add_argument("--output-dir", type=Path, required=True, help="external current bundle directory")
    parser.add_argument("--runs-dir", type=Path, help="external immutable run-history directory")
    parser.add_argument("--receipt", type=Path, help="external Runtime execution receipt path")
    parser.add_argument("--end-at", help="optional ISO-8601 run-window end passed to the canonical producer")
    parser.add_argument("--run-id", help="optional explicit run ID")
    parser.add_argument("--publisher-target-min", type=int)
    parser.add_argument("--publisher-hard-max", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = execute_local_runtime(args)
    except (LocalRuntimeError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
