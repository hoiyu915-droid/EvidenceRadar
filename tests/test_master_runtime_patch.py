from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.apply_master_control_runtime import (
    RuntimePatchError,
    patch_runner,
    patch_runner_source,
    validate_runner_source,
)

RUNNER_ENTRY = ROOT / "tools" / "run_github_radar.py"
RUNNER_IMPL = ROOT / "tools" / "run_github_radar_core.py"


class MasterRuntimePatchTests(unittest.TestCase):
    def test_current_runner_contains_complete_master_control_integration(self) -> None:
        source = RUNNER_IMPL.read_text(encoding="utf-8")
        patched = patch_runner_source(source)
        self.assertIn('"rss_atom": fetch_rss_atom', patched)
        self.assertIn('master_path = root / "config" / "radar_master.json"', patched)
        self.assertIn('adapter_key = str(source_config.get("adapter") or discovery_source)', patched)
        self.assertIn('parser.add_argument(\n        "--profile"', patched)
        self.assertIn('featured_policy = featured_policy_from_output(', patched)
        self.assertIn('featured_policy=featured_policy', patched)
        self.assertIn('parse_featured_policy_note(run.get("notes", []))', patched)
        self.assertIn('featured_policy_note(featured_policy)', patched)
        self.assertIn('f"RADAR_PROFILE:{streams.get(\'control_plane\', {}).get(\'profile_id\', \'legacy\')}"', patched)
        self.assertIn("def _apply_master_runtime_limits(", patched)
        self.assertIn("translation request profile mismatch", patched)
        self.assertIn("authoritative master control is missing", patched)
        self.assertEqual(source, patched)
        validate_runner_source(source)
        self.assertFalse(patch_runner(RUNNER_ENTRY, check_only=True))

    def test_patch_check_does_not_mutate_runner_copy(self) -> None:
        entry_source = RUNNER_ENTRY.read_text(encoding="utf-8")
        implementation_source = RUNNER_IMPL.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "run_github_radar.py"
            implementation = root / "run_github_radar_core.py"
            entry.write_text(entry_source, encoding="utf-8")
            implementation.write_text(implementation_source, encoding="utf-8")
            self.assertFalse(patch_runner(entry, check_only=True))
            self.assertEqual(entry.read_text(encoding="utf-8"), entry_source)
            self.assertEqual(
                implementation.read_text(encoding="utf-8"), implementation_source
            )

    def test_partial_or_write_mode_integration_fails_closed(self) -> None:
        source = RUNNER_IMPL.read_text(encoding="utf-8")
        partial = source.replace("def _apply_master_runtime_limits(", "def removed_limits(")
        with self.assertRaisesRegex(RuntimePatchError, "missing marker"):
            validate_runner_source(partial)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.py"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(RuntimePatchError, "write mode was retired"):
                patch_runner(path, check_only=False)


if __name__ == "__main__":
    unittest.main()
