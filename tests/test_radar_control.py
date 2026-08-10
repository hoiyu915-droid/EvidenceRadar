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

    def test_source_classification_profiles_and_limits_are_authoritative(self) -> None:
        authority = set(self.master["control_plane"]["authoritative_for"])
        self.assertTrue(
            {"sources", "taxonomy", "routing_categories", "stream_routing", "profiles", "limits"}
            <= authority
        )
        self.assertEqual(
            self.master["control_plane"]["production_profile"], "owner_daily"
        )
        self.assertEqual(self.master["limits"]["discovery"]["max_per_query"], 40)
        self.assertIsNone(self.master["limits"]["discovery"]["max_per_category"])
        self.assertIsNone(self.master["limits"]["discovery"]["global_candidate_hard_max"])
        self.assertEqual(self.master["limits"]["ranking_pool"]["max_per_category"], 30)

    def test_verified_oa_feeds_are_active_first_class_sources(self) -> None:
        sources = self.master["sources"]
        for source_id in [
            "nature_communications",
            "communications_physics",
            "communications_chemistry",
            "scientific_reports",
            "jama_network_open",
        ]:
            self.assertEqual(sources[source_id]["adapter"], "rss_atom")
            self.assertEqual(sources[source_id]["stage"], "discovery")
            self.assertTrue(sources[source_id]["enabled"])
            self.assertEqual(sources[source_id]["status"], "active")
            self.assertTrue(sources[source_id]["feeds"])

    def test_unverified_or_semantically_blocked_oa_sources_remain_planned(self) -> None:
        sources = self.master["sources"]
        for source_id in [
            "science_advances",
            "physical_review_x",
            "plos_biology",
            "plos_medicine",
            "jmlr_first_party",
            "tmlr_first_party",
            "openaire",
            "unpaywall",
            "core",
            "doaj",
            "pmc_oa",
            "scientific_data",
        ]:
            self.assertFalse(sources[source_id]["enabled"])
            self.assertEqual(sources[source_id]["status"], "planned")

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
        self.assertNotIn("jama_network_open", requested)
        self.assertIn("nature_physics", requested)
        self.assertIn("nature_communications", requested)
        self.assertIn("communications_physics", requested)

    def test_combined_production_profile_has_27_streams(self) -> None:
        runtime = self.runtime("current_plus_general")
        self.assertEqual(len(runtime.streams["streams"]), 27)
        self.assertIn("clinical_medicine", runtime.streams["streams"])
        self.assertIn("general_nature_interdisciplinary", runtime.streams["streams"])
        self.assertIn("oa_jama_network_open", runtime.streams["streams"])
        self.assertIn("oa_nature_communications", runtime.streams["streams"])

    def test_reader_profiles_do_not_activate_every_catalog_source(self) -> None:
        medicine = self.runtime("medicine_reader")
        medicine_sources = {
            source for stream in medicine.streams["streams"].values() for source in stream["sources"]
        }
        self.assertIn("jama_network_open", medicine_sources)
        self.assertNotIn("openreview", medicine_sources)
        self.assertNotIn("communications_physics", medicine_sources)

        llm = self.runtime("llm_reader")
        llm_sources = {
            source for stream in llm.streams["streams"].values() for source in stream["sources"]
        }
        self.assertIn("openreview", llm_sources)
        self.assertIn("pmlr", llm_sources)
        self.assertNotIn("pubmed", llm_sources)
        self.assertNotIn("nature_communications", llm_sources)

    def test_owner_daily_is_reader_scoped_and_oa_biased(self) -> None:
        runtime = self.runtime("owner_daily")
        requested = {
            source
            for stream in runtime.streams["streams"].values()
            for source in stream["sources"]
        }
        self.assertEqual(runtime.category_order, [
            "clinical_medicine",
            "sport_science",
            "sport_nutrition_fitness",
            "llm_research",
            "human_ai",
        ])
        self.assertEqual(len(requested), 11)
        self.assertIn("jama_network_open", requested)
        self.assertIn("nature_communications", requested)
        self.assertNotIn("scientific_reports", requested)
        self.assertNotIn("nature_physics", requested)
        self.assertNotIn("communications_physics", requested)
        self.assertEqual(runtime.limits["selection"]["final_digest"], {"target": 20, "hard_max": 32})
        self.assertEqual(
            runtime.limits["selection"]["per_category"]["llm_research"],
            {"target": 6, "hard_max": 10},
        )

    def test_llm_prestige_catalog_is_known_but_not_silently_active(self) -> None:
        sources = self.master["sources"]
        for source_id in [
            "tacl_acl_anthology",
            "colm_openreview",
            "iclr_openreview",
            "neurips_proceedings",
        ]:
            self.assertIn(source_id, sources)
            self.assertFalse(sources[source_id]["enabled"])
            self.assertEqual(sources[source_id]["status"], "planned")
        owner = self.runtime("owner_daily")
        owner_sources = {
            source for stream in owner.streams["streams"].values() for source in stream["sources"]
        }
        self.assertTrue(
            {"tacl_acl_anthology", "colm_openreview", "iclr_openreview", "neurips_proceedings"}.isdisjoint(owner_sources)
        )

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
