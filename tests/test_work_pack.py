"""Reproducibility and safety tests for the public ChatGPT Work Pack."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from tools.build_work_pack import build_work_pack, collect_source_files, load_pack_spec


ROOT = Path(__file__).resolve().parents[1]


class WorkPackTests(unittest.TestCase):
    def test_allowlist_contains_portable_contract_and_dependency_free_tools(self) -> None:
        spec = load_pack_spec(ROOT / "release" / "work-pack-manifest.json")
        paths = {relative for relative, _source in collect_source_files(ROOT, spec)}
        for required in (
            "EVIDENCE_RADAR_PROTOCOL.md",
            "config/deployment.yml",
            "docs/WORK_SETUP.md",
            "docs/MIGRATION_DUAL_LANE_1.0.md",
            "schemas/evidence-radar-state.schema.json",
            "tools/delivery_contract.py",
            "tools/merge_radar_state.py",
            "tools/validate_delivery_bundle.py",
            "tools/validate_gpt_work_artifacts.py",
        ):
            self.assertIn(required, paths)
        for forbidden in (
            ".github/workflows/daily-radar.yml",
            "tools/run_github_radar.py",
            "tools/build_work_pack.py",
            "legacy/python-runtime/src/run.py",
            "state/literature_registry.json",
        ):
            self.assertNotIn(forbidden, paths)

    def test_build_is_byte_reproducible_and_manifest_verifies_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_work_pack(ROOT, root / "first", source_date_epoch=0)
            second = build_work_pack(ROOT, root / "second", source_date_epoch=0)
            first_bytes = first.archive_path.read_bytes()
            second_bytes = second.archive_path.read_bytes()
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first.archive_sha256, hashlib.sha256(first_bytes).hexdigest())
            self.assertEqual(first.archive_sha256, second.archive_sha256)
            self.assertEqual(
                f"{first.archive_sha256}  {first.archive_path.name}\n",
                first.checksum_path.read_text(encoding="utf-8"),
            )

            with ZipFile(first.archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(sorted(names[:-1]), names[:-1])
                self.assertEqual("manifest.json", names[-1])
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual("evidenceradar-work-pack", manifest["format"])
                self.assertEqual("1.1.0", manifest["pack_version"])
                self.assertEqual(len(manifest["files"]), manifest["file_count"])
                self.assertTrue(manifest["reproducible"])
                for item in manifest["files"]:
                    self.assertEqual(
                        item["sha256"],
                        hashlib.sha256(archive.read(item["path"])).hexdigest(),
                    )
                    self.assertEqual(item["size"], len(archive.read(item["path"])))
                for info in archive.infolist():
                    path = PurePosixPath(info.filename)
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertEqual((1980, 1, 1, 0, 0, 0), info.date_time)

    def test_archive_excludes_history_credentials_and_active_crawler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = build_work_pack(ROOT, Path(directory), source_date_epoch=0)
            with ZipFile(result.archive_path) as archive:
                names = archive.namelist()
                self.assertFalse(any(name.startswith(("daily/", "state/", "legacy/", ".github/")) for name in names))
                self.assertNotIn("tools/run_github_radar.py", names)
                self.assertNotIn("tools/build_work_pack.py", names)
                self.assertFalse(any(".env" in name.casefold() for name in names))

    def test_fresh_extraction_validates_and_merges_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_work_pack(ROOT, temporary / "dist", source_date_epoch=0)
            extracted = temporary / "extracted"
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
            validation = subprocess.run(
                [sys.executable, "tools/validate_gpt_work_artifacts.py", "--root", "."],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, validation.returncode, validation.stdout)
            delivery_help = subprocess.run(
                [sys.executable, "tools/validate_delivery_bundle.py", "--help"],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, delivery_help.returncode, delivery_help.stdout)
            merged_dir = temporary / "merged"
            merged_dir.mkdir()
            merged_state = merged_dir / "EvidenceRadar_State.json"
            merge = subprocess.run(
                [
                    sys.executable,
                    "tools/merge_radar_state.py",
                    "examples/EvidenceRadar_State.json",
                    "examples/EvidenceRadar_State.json",
                    "--execution-lane",
                    "chatgpt_work",
                    "--protocol-commit",
                    "test-commit",
                    "--output",
                    str(merged_state),
                ],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, merge.returncode, merge.stdout)
            merged_validation = subprocess.run(
                [
                    sys.executable,
                    "tools/validate_gpt_work_artifacts.py",
                    "--root",
                    ".",
                    str(merged_state),
                ],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, merged_validation.returncode, merged_validation.stdout)


if __name__ == "__main__":
    unittest.main()
