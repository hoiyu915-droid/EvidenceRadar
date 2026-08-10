from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from radar_control import RadarControlError, compile_runtime, load_master  # noqa: E402


class RadarControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = ROOT / "config" / "radar_master.json"
        cls.master = load_master(cls.path)
        cls.legacy_streams = yaml.safe_load(
            (ROOT / "config" / "streams.yml").read_text(encoding="utf-8")
        )
        cls.legacy_scoring = yaml.safe_load(
            (ROOT / "config" / "scoring.yml").read_text(encoding="utf-8")
        )

    def runtime(self, profile: str):
        return compile_runtime(
            self.master,
            legacy_streams=self.legacy_streams,
            legacy_scoring=self.legacy_scoring,
            profile_id=profile,
        )

    def test_master_is_valid_and_cross_disciplinary(self) -> None:
        domains = self.master["taxonomy"]["domains"]
        self.assertGreaterEqual(len(domains), 14)
        for domain in [
            "chemistry", "physics_astronomy", "engineering", "social_sciences", "humanities"
        ]:
            self.assertIn(domain, domains)

    def test_source_and_classification_control_is_explicitly_authoritative(self) -> None:
        authority = set(self.master["control_plane"]["authoritative_for"])
        self.assertTrue(
            {"sources", "taxonomy", "routing_categories", "stream_routing", "profiles"}
            <= authority
        )
        self.assertEqual(
            self.master["control_plane"]["production_profile"], "current_plus_general"
        )

    def test_nature_subject_feeds_are_first_class_sources(self) -> None:
        sources = self.master["sources"]
        for source_id in [
            "nature_physics",
            "nature_chemistry",
            "nature_engineering",
            "nature_computer_science",
            "nature_earth_environment",
            "nature_social_science",
        ]:
            self.assertEqual(sources[source_id]["adapter"], "rss_atom")
            self.assertEqual(sources[source_id]["stage"], "discovery")
            self.assertEqual(sources[source_id]["discovery_tier"], "supplemental")
            self.assertTrue(sources[source_id]["enabled"])

    def test_current_profile_preserves_existing_category_order_and_queries(self) -> None:
        runtime = self.runtime("current_focus")
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
        self.assertIn("llm_l9_evaluation_measurement", runtime.streams["streams"])
        self.assertEqual(
            runtime.streams["streams"]["clinical_medicine"]["queries"],
            self.legacy_streams["streams"]["clinical_medicine"]["queries"],
        )

    def test_general_profile_has_no_biomedical_dependency(self) -> None:
        runtime = self.runtime("general_research")
        requested = {
            source
            for stream in runtime.streams["streams"].values()
            for source in stream["sources"]
        }
        self.assertNotIn("pubmed", requested)
        self.assertNotIn("europe_pmc", requested)
        self.assertIn("nature_physics", requested)
        self.assertIn("nature_social_science", requested)

    def test_combined_production_profile_has_22_streams(self) -> None:
        runtime = self.runtime("current_plus_general")
        self.assertEqual(len(runtime.streams["streams"]), 22)
        self.assertIn("clinical_medicine", runtime.streams["streams"])
        self.assertIn("general_nature_interdisciplinary", runtime.streams["streams"])

    def test_scoring_categories_are_profile_derived(self) -> None:
        runtime = self.runtime("general_research")
        self.assertEqual(set(runtime.scoring["categories"]), set(runtime.category_order))
        self.assertNotIn("clinical_medicine", runtime.scoring["categories"])

    def test_unknown_stream_source_fails_closed(self) -> None:
        broken = copy.deepcopy(self.master)
        broken["stream_routing"]["general_nature_physics"]["sources"] = ["does_not_exist"]
        with self.assertRaises(RadarControlError):
            compile_runtime(
                broken,
                legacy_streams=self.legacy_streams,
                legacy_scoring=self.legacy_scoring,
                profile_id="general_research",
            )


if __name__ == "__main__":
    unittest.main()
