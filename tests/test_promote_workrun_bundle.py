"""Regression tests for the offline WorkRun canonicalizer."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests import test_delivery_bundle as delivery_fixture
from tests.test_delivery_bundle import create_bundle
from tools import promote_workrun_bundle as promotion
from tools.package_work_delivery import package_work_delivery
from tools.run_github_radar import render_report_from_documents
from tools.validate_delivery_bundle import validate_delivery_bundle

ROOT = Path(__file__).resolve().parents[1]


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _event_id(work_id: str, event: dict) -> str:
    payload = "|".join(
        [
            work_id,
            str(event.get("event_type") or ""),
            str(event.get("occurred_at") or ""),
            str(event.get("source") or ""),
            str(event.get("source_field") or ""),
        ]
    )
    return "event:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rewrite_base_bundle(
    bundle: Path,
    canonical_state: Path,
) -> None:
    state = _load(bundle / promotion.STATE_FILE)
    evidence = _load(bundle / promotion.EVIDENCE_FILE)
    run = _load(bundle / promotion.RUN_FILE)

    historical_work = copy.deepcopy(state["works"][0])
    historical_work_id = "doi:10.1000/canonical.base"
    historical_work["work_id"] = historical_work_id
    historical_work["identifiers"] = {"doi": "10.1000/canonical.base"}
    historical_work["title"] = "Canonical historical fixture"
    historical_work["normalized_title"] = "canonical historical fixture"
    historical_work["source_urls"] = ["https://doi.org/10.1000/canonical.base"]
    historical_event = copy.deepcopy(state["notified_events"][0])
    historical_event["work_id"] = historical_work_id
    historical_event["source_url"] = "https://doi.org/10.1000/canonical.base"
    historical_event["event_id"] = _event_id(historical_work_id, historical_event)
    historical_work["notified_event_ids"] = [historical_event["event_id"]]
    state["works"].append(historical_work)
    state["notified_events"].append(historical_event)

    report = render_report_from_documents(run, evidence)
    run["report_sha256"] = hashlib.sha256(report.encode("utf-8")).hexdigest()
    _write(bundle / promotion.STATE_FILE, state)
    _write(bundle / promotion.EVIDENCE_FILE, evidence)
    _write(bundle / promotion.RUN_FILE, run)
    (bundle / promotion.REPORT_FILE).write_text(report, encoding="utf-8")
    canonical_state.parent.mkdir(parents=True, exist_ok=True)
    _write(canonical_state, state)
    errors, _ = validate_delivery_bundle(
        ROOT,
        bundle,
        canonical_state=canonical_state,
        require_semantic_contract_v3=True,
    )
    if errors:
        raise AssertionError("invalid canonical test fixture: " + " | ".join(errors))


def _tree_hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _write_runtime_fixture(
    directory: Path,
    source_commit: str,
    *,
    omit_path: str | None = None,
) -> tuple[Path, Path]:
    archive = directory / "EvidenceRadar-Runtime-fixture.zip"
    checksum = directory / (archive.name + ".sha256")
    files = {
        "config/radar_master.json": b"{}\n",
        "runtime/VERSION": b"fixture\n",
        "tools/featured_selection.py": b"# fixture helper\n",
        "tools/publisher_feed.py": b"# fixture helper\n",
        "tools/radar_control.py": b"# fixture helper\n",
        "tools/run_github_radar.py": b"# fixture producer\n",
        "tools/run_local_runtime.py": b"# fixture local runner\n",
        "tools/validate_delivery_bundle.py": b"# fixture validator\n",
        "tools/verify_runtime_release.py": b"# fixture verifier\n",
    }
    if omit_path is not None:
        files.pop(omit_path)
    records = [
        {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
        for path, payload in sorted(files.items())
    ]
    manifest = {
        "archive_name": archive.name,
        "artifacts_packaged": False,
        "checksum_sidecar": checksum.name,
        "execution_host": "local_runtime",
        "execution_lane": "chatgpt_work",
        "file_count": len(records),
        "files": records,
        "format": promotion.RUNTIME_FORMAT,
        "git_commit": source_commit,
        "git_dirty": False,
        "git_state": "clean",
        "immutable_source": True,
        "manifest_version": "1",
        "python_version": "3.12",
        "required_entrypoints": sorted(promotion.RUNTIME_REQUIRED_ENTRYPOINTS),
        "runtime_version": "fixture",
        "semantic_contract": "3",
        "source_commit": source_commit,
        "state_packaged": False,
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as runtime_zip:
        for path, payload in sorted(files.items()):
            runtime_zip.writestr(path, payload)
        runtime_zip.writestr(
            promotion.RUNTIME_MANIFEST,
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


class PromoteWorkRunBundleTests(unittest.TestCase):
    def test_runtime_structure_rejects_missing_master_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            archive, checksum = _write_runtime_fixture(
                temporary,
                _git_head(),
                omit_path="config/radar_master.json",
            )
            with self.assertRaisesRegex(
                promotion.PromotionError, "omits required executable files"
            ):
                promotion.verify_runtime_archive(archive, checksum=checksum)

    def _inputs(self, temporary: Path) -> dict[str, object]:
        producer_commit = _git_head()
        source_bundle, _source_state = create_bundle(
            temporary / "source",
            protocol_commit=producer_commit,
        )
        packaged = package_work_delivery(
            source_bundle,
            temporary / "delivery",
            source_date_epoch=0,
            validation_root=ROOT,
            expected_lane="github_actions",
        )
        real_execute = delivery_fixture.execute

        def execute_base(**kwargs: object) -> object:
            kwargs["run_id"] = "canonical-base-fixture"
            return real_execute(**kwargs)

        with mock.patch.object(delivery_fixture, "execute", side_effect=execute_base):
            base_bundle, base_state = create_bundle(
                temporary / "base",
                protocol_commit=producer_commit,
            )
        _rewrite_base_bundle(base_bundle, base_state)
        runtime_archive = temporary / "Runtime-fixture.zip"
        runtime_archive.write_bytes(b"verified by fixture mock")
        return {
            "producer_commit": producer_commit,
            "source_bundle": source_bundle,
            "archive": packaged.archive_path,
            "checksum": packaged.checksum_path,
            "base_bundle": base_bundle,
            "base_state": base_state,
            "runtime": runtime_archive,
        }

    def test_stages_union_without_mutating_inputs_or_retrieval_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            inputs = self._inputs(temporary)
            source_bundle = inputs["source_bundle"]
            base_bundle = inputs["base_bundle"]
            base_state = inputs["base_state"]
            assert isinstance(source_bundle, Path)
            assert isinstance(base_bundle, Path)
            assert isinstance(base_state, Path)
            before_bundle = _tree_hashes(base_bundle)
            before_state = hashlib.sha256(base_state.read_bytes()).hexdigest()
            source_run = _load(source_bundle / promotion.RUN_FILE)
            source_ledger = promotion._canonical_json_bytes(
                promotion._retrieval_ledger(source_run)
            )
            output = temporary / "staging" / "canonicalized"
            runtime_sha = "d" * 64

            with (
                mock.patch.object(promotion, "_validate_target_producer"),
                mock.patch.object(
                    promotion,
                    "_validate_source_bundle_with_runtime",
                ) as source_validator,
                mock.patch.object(
                    promotion,
                    "verify_runtime_archive",
                    return_value={
                        "manifest": {
                            "source_commit": inputs["producer_commit"],
                            "files": [],
                        },
                        "archive_sha256": runtime_sha,
                    },
                ),
                mock.patch.object(
                    promotion,
                    "_validate_runtime_against_source_commit",
                ),
            ):
                result = promotion.promote_workrun_bundle(
                    root=ROOT,
                    workrun_archive=inputs["archive"],
                    workrun_checksum=inputs["checksum"],
                    runtime_archive=inputs["runtime"],
                    canonical_bundle=base_bundle,
                    canonical_state=base_state,
                    target_producer_commit=inputs["producer_commit"],
                    output_dir=output,
                )

            source_validator.assert_called_once()
            self.assertEqual(set(promotion.CANONICAL_FILES), {p.name for p in output.iterdir()})
            run = _load(output / promotion.RUN_FILE)
            state = _load(output / promotion.STATE_FILE)
            report_bytes = (output / promotion.REPORT_FILE).read_bytes()
            self.assertEqual("github-actions-delivery-fixture", run["run_id"])
            self.assertEqual(run["run_id"], state["last_run_id"])
            self.assertEqual(["canonical-base-fixture"], run["parent_run_ids"])
            self.assertEqual(run["parent_run_ids"], state["parent_run_ids"])
            self.assertEqual(inputs["producer_commit"], run["protocol_commit"])
            self.assertEqual(inputs["producer_commit"], state["protocol_commit"])
            self.assertEqual(2, len(state["works"]))
            self.assertEqual(2, len(state["notified_events"]))
            self.assertEqual(
                source_ledger,
                promotion._canonical_json_bytes(promotion._retrieval_ledger(run)),
            )
            self.assertIn(
                f"RETRIEVAL_LEDGER_SHA256:{result.retrieval_ledger_sha256}",
                run["notes"],
            )
            self.assertIn("DISCOVERY_REUSED_NO_NETWORK", run["notes"])
            self.assertEqual(
                hashlib.sha256(report_bytes).hexdigest(),
                run["report_sha256"],
            )
            self.assertEqual(before_bundle, _tree_hashes(base_bundle))
            self.assertEqual(before_state, hashlib.sha256(base_state.read_bytes()).hexdigest())

    def test_tampered_workrun_member_fails_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            inputs = self._inputs(temporary)
            archive = inputs["archive"]
            assert isinstance(archive, Path)
            with zipfile.ZipFile(archive, "r") as source_zip:
                entries = [(item, source_zip.read(item.filename)) for item in source_zip.infolist()]
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
                for item, payload in entries:
                    if item.filename == promotion.RUN_FILE:
                        payload += b"\n"
                    target_zip.writestr(item.filename, payload)
            output = temporary / "must-not-exist"
            with self.assertRaisesRegex(
                promotion.PromotionError,
                "checksum mismatch|size mismatch|SHA-256 mismatch",
            ):
                promotion.promote_workrun_bundle(
                    root=ROOT,
                    workrun_archive=archive,
                    runtime_archive=inputs["runtime"],
                    canonical_bundle=inputs["base_bundle"],
                    canonical_state=inputs["base_state"],
                    target_producer_commit=inputs["producer_commit"],
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_runtime_and_workrun_producer_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            inputs = self._inputs(temporary)
            output = temporary / "must-not-exist"
            with (
                mock.patch.object(promotion, "_validate_target_producer"),
                mock.patch.object(
                    promotion,
                    "verify_runtime_archive",
                    return_value={
                        "manifest": {"source_commit": "0" * 40, "files": []},
                        "archive_sha256": "d" * 64,
                    },
                ),
                self.assertRaisesRegex(
                    promotion.PromotionError,
                    "does not match the WorkRun",
                ),
            ):
                promotion.promote_workrun_bundle(
                    root=ROOT,
                    workrun_archive=inputs["archive"],
                    runtime_archive=inputs["runtime"],
                    canonical_bundle=inputs["base_bundle"],
                    canonical_state=inputs["base_state"],
                    target_producer_commit=inputs["producer_commit"],
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_canonical_state_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            inputs = self._inputs(temporary)
            external_state = temporary / "different-state.json"
            state = _load(inputs["base_state"])
            state["notes"].append("deliberate mismatch")
            _write(external_state, state)
            output = temporary / "must-not-exist"
            with (
                mock.patch.object(promotion, "_validate_target_producer"),
                mock.patch.object(
                    promotion,
                    "verify_runtime_archive",
                    return_value={
                        "manifest": {
                            "source_commit": inputs["producer_commit"],
                            "files": [],
                        },
                        "archive_sha256": "d" * 64,
                    },
                ),
                mock.patch.object(
                    promotion,
                    "_validate_runtime_against_source_commit",
                ),
                self.assertRaisesRegex(promotion.PromotionError, "canonical bundle/State"),
            ):
                promotion.promote_workrun_bundle(
                    root=ROOT,
                    workrun_archive=inputs["archive"],
                    runtime_archive=inputs["runtime"],
                    canonical_bundle=inputs["base_bundle"],
                    canonical_state=external_state,
                    target_producer_commit=inputs["producer_commit"],
                    output_dir=output,
                )
            self.assertFalse(output.exists())

    def test_missing_source_or_target_commit_is_rejected(self) -> None:
        for label in ("source producer", "target producer"):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    promotion.PromotionError,
                    rf"{label} commit does not exist",
                ):
                    promotion._validate_commit_exists(
                        ROOT,
                        "0" * 40,
                        label=label,
                    )

    def test_runtime_archive_hashes_and_git_binding_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            archive, checksum = _write_runtime_fixture(temporary, _git_head())
            verified = promotion.verify_runtime_archive(
                archive,
                checksum=checksum,
            )
            self.assertEqual(_git_head(), verified["manifest"]["source_commit"])

            producer_path = "tools/delivery_contract.py"
            producer_payload = subprocess.run(
                ["git", "show", f"{_git_head()}:{producer_path}"],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            record = {
                "path": producer_path,
                "sha256": hashlib.sha256(producer_payload).hexdigest(),
                "size": len(producer_payload),
            }
            promotion._validate_runtime_against_source_commit(
                ROOT,
                {"files": [record]},
                _git_head(),
            )
            record["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                promotion.PromotionError,
                "differs from source producer commit",
            ):
                promotion._validate_runtime_against_source_commit(
                    ROOT,
                    {"files": [record]},
                    _git_head(),
                )

    def test_staging_target_cannot_be_under_canonical_locations(self) -> None:
        with self.assertRaisesRegex(
            promotion.PromotionError,
            "must not be inside a canonical location",
        ):
            promotion._validate_staging_target(
                root=ROOT,
                output_dir=ROOT / "artifacts" / "current" / "staging",
                canonical_bundle=ROOT / "artifacts" / "current",
                canonical_state=ROOT / "state" / "current" / promotion.STATE_FILE,
            )

    def test_source_runtime_validator_is_forced_offline(self) -> None:
        runtime_verifier = """\
import socket

try:
    socket.create_connection(('example.test', 443))
except RuntimeError as exc:
    if 'network disabled' not in str(exc):
        raise
else:
    raise SystemExit(8)
"""
        validator = """\
import pathlib
import socket
import sys

bundle = pathlib.Path(sys.argv[sys.argv.index('--bundle') + 1])
expected = {
    'EvidenceRadar_Report.html',
    'EvidenceRadar_State.json',
    'EvidenceRadar_Evidence.json',
    'EvidenceRadar_Run.json',
}
if {item.name for item in bundle.iterdir()} != expected:
    raise SystemExit(7)
try:
    socket.create_connection(('example.test', 443))
except RuntimeError as exc:
    if 'network disabled' not in str(exc):
        raise
else:
    raise SystemExit(8)
"""
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            runtime_archive = temporary / "Runtime.zip"
            with zipfile.ZipFile(runtime_archive, "w") as runtime_zip:
                runtime_zip.writestr("tools/validate_delivery_bundle.py", validator)
                runtime_zip.writestr(
                    "tools/verify_runtime_release.py",
                    runtime_verifier,
                )
            payloads = {name: b"fixture" for name in promotion.CANONICAL_FILES}
            promotion._validate_source_bundle_with_runtime(
                runtime_archive=runtime_archive,
                runtime_manifest={
                    "files": [{"path": "tools/validate_delivery_bundle.py"}]
                    + [{"path": "tools/verify_runtime_release.py"}]
                },
                payloads=payloads,
                execution_lane="chatgpt_work",
                source_protocol_commit=_git_head(),
            )


if __name__ == "__main__":
    unittest.main()
