"""Tests for the immutable local Runtime release contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

from tools.build_runtime_release import (
    RuntimeReleaseError,
    build_runtime_release,
)
from tools.run_local_runtime import LocalRuntimeError, _outside_runtime, build_runner_command
from tools.verify_runtime_release import RuntimeVerificationError, verify_archive, verify_extracted_root


ROOT = Path(__file__).resolve().parents[1]


class RuntimeReleaseTests(unittest.TestCase):
    def test_runtime_build_is_byte_reproducible_and_binds_clean_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            first = build_runtime_release(ROOT, temporary / "first", source_date_epoch=0)
            second = build_runtime_release(ROOT, temporary / "second", source_date_epoch=0)
            first_bytes = first.archive_path.read_bytes()
            second_bytes = second.archive_path.read_bytes()
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first.archive_sha256, hashlib.sha256(first_bytes).hexdigest())
            self.assertEqual(first.archive_sha256, second.archive_sha256)
            manifest = first.manifest
            self.assertEqual("evidenceradar-runtime-release", manifest["format"])
            self.assertEqual("1.0.1", manifest["runtime_version"])
            self.assertRegex(manifest["source_commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(manifest["source_commit"], manifest["git_commit"])
            self.assertFalse(manifest["git_dirty"])
            self.assertEqual("clean", manifest["git_state"])
            self.assertTrue(manifest["immutable_source"])
            self.assertFalse(manifest["state_packaged"])
            self.assertFalse(manifest["artifacts_packaged"])
            self.assertEqual("github_actions", manifest["execution_lane"])
            self.assertEqual("local_runtime", manifest["execution_host"])
            self.assertEqual(
                f"{first.archive_sha256}  {first.archive_path.name}\n",
                first.checksum_path.read_text(encoding="utf-8"),
            )

    def test_archive_and_fresh_extraction_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_runtime_release(ROOT, temporary / "dist", source_date_epoch=0)
            verified = verify_archive(result.archive_path, result.checksum_path)
            self.assertEqual(result.archive_sha256, verified["archive_sha256"])
            extracted = temporary / "runtime"
            extracted.mkdir()
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
                names = archive.namelist()
            for forbidden_prefix in (
                ".git/",
                ".github/",
                "artifacts/",
                "daily/",
                "legacy/",
                "runs/",
                "state/",
                "tests/",
            ):
                self.assertFalse(any(name.startswith(forbidden_prefix) for name in names))
            self.assertIn("RUNTIME_MANIFEST.json", names)
            self.assertIn("tools/run_local_runtime.py", names)
            self.assertIn("tools/verify_runtime_release.py", names)
            root_result = verify_extracted_root(extracted)
            self.assertEqual("1.0.1", root_result["manifest"]["runtime_version"])

            verifier = subprocess.run(
                [sys.executable, "tools/verify_runtime_release.py", "--root", "."],
                cwd=extracted,
                env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, verifier.returncode, verifier.stdout)
            local_help = subprocess.run(
                [sys.executable, "tools/run_local_runtime.py", "--help"],
                cwd=extracted,
                env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, local_help.returncode, local_help.stdout)

    def test_extracted_verifier_rejects_runtime_source_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_runtime_release(ROOT, temporary / "dist", source_date_epoch=0)
            extracted = temporary / "runtime"
            extracted.mkdir()
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
            target = extracted / "runtime" / "README.md"
            target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            with self.assertRaises(RuntimeVerificationError):
                verify_extracted_root(extracted)

    def test_runtime_builder_rejects_dirty_or_gitless_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "tools.build_runtime_release._git_state",
                return_value={
                    "git_commit": "a" * 40,
                    "source_commit": "a" * 40 + "-dirty",
                    "git_dirty": True,
                    "git_state": "dirty",
                },
            ):
                with self.assertRaises(RuntimeReleaseError):
                    build_runtime_release(ROOT, Path(directory), source_date_epoch=0)

    def test_local_runtime_paths_must_be_external(self) -> None:
        with self.assertRaises(LocalRuntimeError):
            _outside_runtime(ROOT / "state" / "EvidenceRadar_State.json", label="State path")
        external = Path(tempfile.gettempdir()) / "evidenceradar-runtime-test-state.json"
        self.assertEqual(external.resolve(strict=False), _outside_runtime(external, label="State path"))

    def test_local_runner_preserves_canonical_lane_and_manifest_commit(self) -> None:
        state = Path("/tmp/state.json")
        output = Path("/tmp/output")
        runs = Path("/tmp/runs")
        command = build_runner_command(
            state=state,
            output_dir=output,
            runs_dir=runs,
            protocol_commit="b" * 40,
            end_at="2026-08-10T05:00:00+09:00",
            run_id="local-runtime-smoke",
            publisher_target_min=0,
            publisher_hard_max=0,
        )
        self.assertIn("--execution-lane", command)
        lane_index = command.index("--execution-lane")
        self.assertEqual("github_actions", command[lane_index + 1])
        commit_index = command.index("--protocol-commit")
        self.assertEqual("b" * 40, command[commit_index + 1])
        self.assertIn("--runs-dir", command)

    def test_runtime_docs_define_release_state_and_output_separation(self) -> None:
        guide = (ROOT / "docs" / "RUNTIME_RELEASE.md").read_text(encoding="utf-8")
        runtime_readme = (ROOT / "runtime" / "README.md").read_text(encoding="utf-8")
        for document in (guide, runtime_readme):
            self.assertIn("immutable", document.casefold())
            self.assertIn("EvidenceRadar_State.json", document)
            self.assertIn("RUNTIME_MANIFEST.json", document)
            self.assertIn("local_runtime", document)
        self.assertIn("GitHub Release", guide)
        self.assertIn("runtime/VERSION", guide)


if __name__ == "__main__":
    unittest.main()
