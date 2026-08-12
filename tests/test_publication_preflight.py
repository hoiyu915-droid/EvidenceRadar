from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.delivery_contract import BUNDLE_FILENAMES, publication_stage_paths
from tools.publication_preflight import (
    PublicationPreflightError,
    validate_publication_preflight,
)


class PublicationPreflightTests(unittest.TestCase):
    RUN_ID = "chatgpt-work-20260813T063700+0900"

    def fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir()
        for name in BUNDLE_FILENAMES:
            (source / name).write_text("{}\n", encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {"run_id": self.RUN_ID, "canonical_files": list(BUNDLE_FILENAMES)}
            ),
            encoding="utf-8",
        )
        return source, manifest

    def test_exact_contract_and_stage_plan_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, manifest = self.fixture(Path(directory))
            staged = list(publication_stage_paths(self.RUN_ID))
            result = validate_publication_preflight(
                run_id=self.RUN_ID,
                manifest=manifest,
                source_dir=source,
                staged_paths=staged,
            )
            self.assertEqual("READY", result["status"])
            self.assertIn(
                "artifacts/current/EvidenceRadar_Evidence.json",
                result["authorized_stage_paths"],
            )

    def test_retrieval_ledger_alias_is_rejected(self) -> None:
        staged = list(publication_stage_paths(self.RUN_ID))
        staged.remove("artifacts/current/EvidenceRadar_Evidence.json")
        staged.append("artifacts/current/EvidenceRadar_RetrievalLedger.json")
        with self.assertRaisesRegex(PublicationPreflightError, "stage plan drift"):
            validate_publication_preflight(run_id=self.RUN_ID, staged_paths=staged)

    def test_manifest_name_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, manifest = self.fixture(Path(directory))
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["canonical_files"][2] = "EvidenceRadar_RetrievalLedger.json"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(PublicationPreflightError, "canonical_files drift"):
                validate_publication_preflight(
                    run_id=self.RUN_ID, manifest=manifest, source_dir=source
                )

    def test_missing_canonical_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, manifest = self.fixture(Path(directory))
            (source / "EvidenceRadar_Evidence.json").unlink()
            with self.assertRaisesRegex(PublicationPreflightError, "canonical artifact"):
                validate_publication_preflight(
                    run_id=self.RUN_ID, manifest=manifest, source_dir=source
                )

    def test_unsafe_run_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(PublicationPreflightError, "unsafe"):
            validate_publication_preflight(run_id="../escape")


if __name__ == "__main__":
    unittest.main()
