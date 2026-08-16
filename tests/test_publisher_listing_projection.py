from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from radar_control import load_master, load_master_runtime  # noqa: E402, I001


class PublisherListingProjectionTests(unittest.TestCase):
    def test_catalog_keeps_semantic_adapter_and_explicit_dispatch_path(self) -> None:
        runtime = load_master_runtime(
            ROOT / "config" / "radar_master.json", profile_id="owner_daily"
        )
        catalog = runtime.streams["source_catalog"]["cambridge_core_oa"]
        self.assertEqual("publisher_listing", catalog["adapter"])
        self.assertEqual("publisher_listing", catalog["configured_adapter"])
        self.assertEqual("rss_atom", catalog["dispatch_adapter"])
        self.assertEqual("rss_atom", runtime.source_adapters["cambridge_core_oa"])
        self.assertNotIn(
            "cambridge_core_oa",
            runtime.streams["source_check_contract"]["bounded_verification_sources"],
        )

    def test_cambridge_selection_projects_eleven_curated_journal_shards(self) -> None:
        master = load_master(ROOT / "config" / "radar_master.json")
        source = master["sources"]["cambridge_core_oa"]
        self.assertEqual("verify_per_work", source["oa_mode"])
        self.assertEqual("fully_oa", source["configured_oa_mode"])
        self.assertTrue(
            source["endpoint"].startswith(
                "https://www.cambridge.org/core/publications/open-access/listing"
            )
        )
        inventory = source["adapter_config"]["inventory"]
        self.assertEqual("curated_journal_articles", inventory["scope"])
        self.assertEqual("https://www.cambridge.org/core/journals", inventory["family_url"])
        self.assertEqual("article", inventory["coverage_unit"])
        self.assertFalse(inventory["journal_level_coverage"])
        self.assertFalse(inventory["article_oa_guarantee"])
        self.assertEqual("curated_journal_allowlist", inventory["shard_strategy"])
        self.assertEqual(11, inventory["selected_journal_count"])
        self.assertEqual(11, len(inventory["shards"]))
        self.assertIn("(?P<container>", inventory["container_path_regex"])

        selection = json.loads(
            (ROOT / "config" / "cambridge_journal_selection.json").read_text(
                encoding="utf-8"
            )
        )
        selected_slugs = {journal["slug"] for journal in selection["journals"]}
        self.assertEqual(
            {
                "natural-language-processing",
                "ai-edam",
                "data-and-policy",
                "behavioral-and-brain-sciences",
                "language-and-cognition",
                "psychological-medicine",
                "bjpsych-open",
                "epidemiology-and-psychiatric-sciences",
                "journal-of-nutritional-science",
                "nutrition-research-reviews",
                "british-journal-of-nutrition",
            },
            selected_slugs,
        )
        self.assertEqual(
            selected_slugs,
            {shard["journal_slug"] for shard in inventory["shards"]},
        )


if __name__ == "__main__":
    unittest.main()
