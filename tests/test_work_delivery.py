"""Tests for uniquely named ChatGPT Work run delivery packages."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from tests.test_delivery_bundle import create_bundle
from tools.package_work_delivery import CANONICAL_FILES, WorkDeliveryError, package_work_delivery

ROOT = Path(__file__).resolve().parents[1]


class WorkDeliveryTests(unittest.TestCase):
    def _copy_current_bundle(self, root: Path) -> Path:
        source, _canonical = create_bundle(root)
        return source

    def test_package_contains_canonical_files_manifest_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._copy_current_bundle(root)

            result = package_work_delivery(
                source,
                root / "deliveries",
                source_date_epoch=0,
                validation_root=ROOT,
                expected_lane="github_actions",
            )
            run_id = json.loads((source / "EvidenceRadar_Run.json").read_text(encoding="utf-8"))["run_id"]
            expected_prefix = f"EvidenceRadar-WorkRun-{run_id}"
            self.assertEqual(expected_prefix, result.bundle_dir.name)
            self.assertEqual(f"{expected_prefix}.zip", result.archive_path.name)
            self.assertEqual(f"{expected_prefix}.zip.sha256", result.checksum_path.name)
            self.assertEqual(
                f"{result.archive_sha256}  {result.archive_path.name}\n",
                result.checksum_path.read_text(encoding="utf-8"),
            )

            with ZipFile(result.archive_path) as archive:
                self.assertEqual([*CANONICAL_FILES, "manifest.json"], archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual("evidenceradar-work-delivery", manifest["format"])
                self.assertEqual(run_id, manifest["run_id"])
                self.assertEqual("github_actions", manifest["execution_lane"])
                self.assertTrue(manifest["protocol_commit"])
                self.assertEqual(list(CANONICAL_FILES), manifest["canonical_files"])
                self.assertEqual(4, manifest["file_count"])
                for record in manifest["files"]:
                    payload = archive.read(record["path"])
                    self.assertEqual(record["size"], len(payload))
                    self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())

    def test_rejects_existing_run_id_to_prevent_stale_attachment_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._copy_current_bundle(root)
            output = root / "deliveries"
            package_work_delivery(
                source,
                output,
                source_date_epoch=0,
                validation_root=ROOT,
                expected_lane="github_actions",
            )
            with self.assertRaisesRegex(WorkDeliveryError, "already exists"):
                package_work_delivery(
                    source,
                    output,
                    source_date_epoch=0,
                    validation_root=ROOT,
                    expected_lane="github_actions",
                )

    def test_rejects_run_id_mismatch_and_invalid_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._copy_current_bundle(root)
            with self.assertRaisesRegex(WorkDeliveryError, "does not match"):
                package_work_delivery(
                    source,
                    root / "mismatch",
                    run_id="other",
                    source_date_epoch=0,
                    validation_root=ROOT,
                    expected_lane="github_actions",
                )
            report = source / "EvidenceRadar_Report.html"
            report.write_text("<html><body>invalid marker</body></html>", encoding="utf-8")
            with self.assertRaisesRegex(WorkDeliveryError, "delivery validation failed"):
                package_work_delivery(
                    source,
                    root / "invalid-html",
                    source_date_epoch=0,
                    validation_root=ROOT,
                    expected_lane="github_actions",
                )

    def test_rejects_schema_drift_before_writing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._copy_current_bundle(root)
            state_path = source / "EvidenceRadar_State.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(WorkDeliveryError, "schema validation failed"):
                package_work_delivery(
                    source,
                    root / "invalid-schema",
                    source_date_epoch=0,
                    validation_root=ROOT,
                    expected_lane="github_actions",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
