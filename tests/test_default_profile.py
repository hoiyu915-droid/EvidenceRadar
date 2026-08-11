from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from tools.radar_control import compile_runtime, load_master


ROOT = Path(__file__).resolve().parents[1]


class DefaultProfileTests(unittest.TestCase):
    def test_unspecified_profile_resolves_to_owner_daily(self) -> None:
        master = load_master(ROOT / "config" / "radar_master.json")
        control = master["control_plane"]
        self.assertEqual(control["default_profile"], "owner_daily")
        self.assertEqual(control["production_profile"], "owner_daily")

        runtime = compile_runtime(
            master,
            legacy_streams=yaml.safe_load(
                (ROOT / "config" / "streams.yml").read_text(encoding="utf-8")
            ),
            legacy_scoring=yaml.safe_load(
                (ROOT / "config" / "scoring.yml").read_text(encoding="utf-8")
            ),
            profile_id=None,
        )
        self.assertEqual(
            runtime.category_order,
            [
                "clinical_medicine",
                "sport_science",
                "sport_nutrition_fitness",
                "llm_research",
                "human_ai",
            ],
        )
        self.assertEqual(
            runtime.limits["selection"]["final_digest"],
            {"target": 20, "hard_max": 32},
        )
        self.assertIn("oa_jama_network_open", runtime.streams["streams"])
        self.assertIn("owner_ncomms_llm", runtime.streams["streams"])


if __name__ == "__main__":
    unittest.main()
