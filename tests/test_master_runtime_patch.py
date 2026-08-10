from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.apply_master_control_runtime import patch_runner, patch_runner_source


class MasterRuntimePatchTests(unittest.TestCase):
    def test_current_runner_accepts_master_patch_fail_closed(self) -> None:
        source = (ROOT / "tools" / "run_github_radar.py").read_text(encoding="utf-8")
        patched = patch_runner_source(source)
        self.assertIn('"rss_atom": fetch_rss_atom', patched)
        self.assertIn('master_path = root / "config" / "radar_master.json"', patched)
        self.assertIn('adapter_key = str(source_config.get("adapter") or discovery_source)', patched)
        self.assertIn('parser.add_argument(\n        "--profile"', patched)
        self.assertNotEqual(source, patched)

    def test_patch_check_does_not_mutate_runner_copy(self) -> None:
        source = (ROOT / "tools" / "run_github_radar.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.py"
            path.write_text(source, encoding="utf-8")
            patch_runner(path, check_only=True)
            self.assertEqual(path.read_text(encoding="utf-8"), source)


if __name__ == "__main__":
    unittest.main()
