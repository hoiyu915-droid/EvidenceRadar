from __future__ import annotations

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

    def test_publisher_listing_uses_article_level_oa_inventory_semantics(self) -> None:
        master = load_master(ROOT / "config" / "radar_master.json")
        source = master["sources"]["cambridge_core_oa"]
        self.assertEqual("publisher_oa_articles", source["oa_mode"])
        self.assertEqual("fully_oa", source["configured_oa_mode"])
        inventory = source["adapter_config"]["inventory"]
        self.assertEqual("publisher_oa_articles", inventory["scope"])
        self.assertEqual("article", inventory["coverage_unit"])
        self.assertFalse(inventory["journal_level_coverage"])
        self.assertEqual("catalog_or_subject_optional", inventory["shard_strategy"])
        self.assertIn("(?P<container>", inventory["container_path_regex"])


if __name__ == "__main__":
    unittest.main()
