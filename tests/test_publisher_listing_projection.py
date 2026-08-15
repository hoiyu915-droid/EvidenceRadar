from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from radar_control import load_master_runtime  # noqa: E402, I001


class PublisherListingProjectionTests(unittest.TestCase):
    def test_catalog_keeps_semantic_adapter_while_runner_uses_inventory_shim(self) -> None:
        runtime = load_master_runtime(
            ROOT / "config" / "radar_master.json", profile_id="owner_daily"
        )
        catalog = runtime.streams["source_catalog"]["cambridge_core_oa"]
        self.assertEqual("publisher_listing", catalog["adapter"])
        self.assertEqual("publisher_listing", catalog["configured_adapter"])
        self.assertEqual("rss_atom", runtime.source_adapters["cambridge_core_oa"])
        self.assertNotIn(
            "cambridge_core_oa",
            runtime.streams["source_check_contract"]["bounded_verification_sources"],
        )


if __name__ == "__main__":
    unittest.main()
