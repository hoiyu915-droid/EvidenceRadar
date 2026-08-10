from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.apply_master_runtime_config import RuntimeConfigError, effective_configs  # noqa: E402


class ApplyMasterRuntimeConfigTests(unittest.TestCase):
    def test_effective_configs_project_master_limits_and_remove_ghost_cap(self) -> None:
        streams, output, deployment, summary = effective_configs(ROOT, "current_plus_general")
        self.assertEqual(streams["candidate_guidance"]["suggested_max_per_query"], 40)
        self.assertNotIn("hard_max_per_category", streams["candidate_guidance"])
        self.assertIsNone(streams["candidate_guidance"]["max_per_category"])
        self.assertEqual(output["selection"]["featured"]["target_min"], 5)
        self.assertEqual(output["selection"]["featured"]["hard_max"], 8)
        self.assertEqual(deployment["publisher_output"]["target_min_per_run"], 10)
        self.assertEqual(deployment["publisher_output"]["hard_max_per_run"], 15)
        self.assertEqual(deployment["publisher_output"]["per_domain_hard_max"], 2)
        self.assertEqual(summary["profile_id"], "current_plus_general")

    def test_profile_override_changes_runtime_projection_only_for_that_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "config", root / "config")
            master_path = root / "config" / "radar_master.json"
            master = json.loads(master_path.read_text(encoding="utf-8"))
            master["profiles"]["llm_reader"]["limits"] = {
                "selection": {
                    "featured_target_per_category": 7,
                    "featured_hard_max_per_category": 10,
                },
                "verification": {"publisher_hard_max_per_run": 12},
            }
            master_path.write_text(json.dumps(master, ensure_ascii=False) + "\n", encoding="utf-8")
            _streams, output, deployment, _summary = effective_configs(root, "llm_reader")
            self.assertEqual(output["selection"]["featured"]["target_min"], 7)
            self.assertEqual(output["selection"]["featured"]["hard_max"], 10)
            self.assertEqual(deployment["publisher_output"]["target_min_per_run"], 10)
            self.assertEqual(deployment["publisher_output"]["hard_max_per_run"], 12)

    def test_unimplemented_candidate_cap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "config", root / "config")
            master_path = root / "config" / "radar_master.json"
            master = json.loads(master_path.read_text(encoding="utf-8"))
            master["limits"]["discovery"]["max_per_category"] = 30
            master_path.write_text(json.dumps(master, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeConfigError):
                effective_configs(root, "current_focus")


if __name__ == "__main__":
    unittest.main()
