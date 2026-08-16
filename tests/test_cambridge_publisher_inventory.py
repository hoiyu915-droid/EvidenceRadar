from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.radar_control import load_master_runtime  # noqa: E402, I001
from tools.run_github_radar import discover_candidates  # noqa: E402


CAMBRIDGE = "cambridge_core_oa"


def article(title: str, journal_slug: str, article_slug: str, published: str) -> str:
    return f"""
    <article>
      <h2><a href="/core/journals/{journal_slug}/article/{article_slug}/ABC123">{title}</a></h2>
      <p>Published online by Cambridge University Press: {published}</p>
      <p>doi:10.1017/{article_slug}</p>
    </article>
    """


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.history: list[object] = []
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        del chunk_size
        yield self.content


class _Session:
    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        page = int(parse_qs(urlsplit(url).query).get("pageNum", ["1"])[0])
        if page not in self.pages:
            raise AssertionError(f"unexpected Cambridge inventory page: {page}")
        return _Response(url, self.pages[page])


def _cambridge_runtime(*stream_ids: str):
    runtime = load_master_runtime(
        ROOT / "config" / "radar_master.json", profile_id="owner_daily"
    )
    streams = copy.deepcopy(runtime.streams)
    streams["streams"] = {
        stream_id: copy.deepcopy(runtime.streams["streams"][stream_id])
        for stream_id in stream_ids
    }
    for config in streams["streams"].values():
        config["sources"] = [CAMBRIDGE]
        config["queries"] = ["*"]
    return runtime, streams


class CambridgePublisherInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        timezone = ZoneInfo("Asia/Tokyo")
        self.start = datetime(2026, 8, 13, 12, 0, tzinfo=timezone)
        self.end = datetime(2026, 8, 16, 12, 0, tzinfo=timezone)
        self.pages = {
            1: (
                article(
                    "Large language models in collaborative decision support",
                    "behavioral-and-brain-sciences",
                    "S0000000000000001",
                    "16 August 2026",
                )
                + article(
                    "Clinical exercise interventions in healthy ageing",
                    "ageing-and-society",
                    "S0000000000000002",
                    "15 August 2026",
                )
            ),
            2: article(
                "Older publisher control record outside the rolling window",
                "epidemiology-and-infection",
                "S0000000000000003",
                "12 August 2026",
            ),
        }

    def test_semantic_publisher_adapter_executes_and_inventory_is_reused(self) -> None:
        runtime, streams = _cambridge_runtime(
            "owner_cambridge_llm", "owner_cambridge_human_ai"
        )
        session = _Session(self.pages)
        result = discover_candidates(
            streams,
            runtime.scoring,
            self.start,
            self.end,
            session=session,
        )

        self.assertEqual(2, len(session.calls), "publisher inventory should be fetched once")
        self.assertEqual({CAMBRIDGE}, result.checked_sources)
        self.assertEqual({CAMBRIDGE}, result.searched_sources)
        self.assertNotIn(CAMBRIDGE, result.unavailable_sources)
        self.assertTrue(result.queries)
        self.assertNotIn("NOT_ATTEMPTED", {row["status"] for row in result.queries})
        self.assertEqual({"SUCCESS"}, {row["status"] for row in result.queries})

        self.assertEqual(2, len(result.all_candidates))
        for candidate in result.all_candidates:
            self.assertTrue(candidate.open_access)
            self.assertTrue(candidate.venue.startswith("cambridge-core:"))
            evidence = {
                (item.get("evidence_type"), item.get("value"))
                for item in candidate.oa_evidence
            }
            self.assertIn(
                ("publisher_listing_oa_inventory", "article_open_access"), evidence
            )
            self.assertNotIn(("source_catalog_oa_mode", "fully_oa"), evidence)
            self.assertEqual(
                {"owner_cambridge_human_ai", "owner_cambridge_llm"},
                set(candidate.observed_streams),
            )

        self.assertEqual(2, len(result.source_access))
        first, second = result.source_access
        self.assertEqual(2, first["http_requests_attempted"])
        self.assertFalse(first["cache_reused"])
        self.assertEqual(0, second["http_requests_attempted"])
        self.assertTrue(second["cache_reused"])
        for access in result.source_access:
            self.assertEqual("publisher_listing", access["retrieval_backend"])
            self.assertEqual("publisher_oa_articles", access["publisher_inventory_scope"])
            self.assertEqual("article", access["coverage_unit"])
            self.assertFalse(access["journal_level_coverage"])
            self.assertTrue(access["window_closed"])
            self.assertFalse(access["page_bound_reached"])

    def test_page_bound_is_partial_not_complete_coverage(self) -> None:
        runtime, streams = _cambridge_runtime("owner_cambridge_llm")
        source = streams["source_catalog"][CAMBRIDGE]
        source["adapter_config"]["pagination"]["max_pages"] = 1
        session = _Session({1: self.pages[1]})

        result = discover_candidates(
            streams,
            runtime.scoring,
            self.start,
            self.end,
            session=session,
        )

        self.assertEqual(1, len(session.calls))
        self.assertEqual("PARTIAL", result.queries[0]["status"])
        self.assertIn(CAMBRIDGE, result.unavailable_sources)
        access = result.source_access[0]
        self.assertFalse(access["retrieval_complete"])
        self.assertTrue(access["page_bound_reached"])
        self.assertFalse(access["window_closed"])
        self.assertTrue(
            any(
                "did not close the requested window within" in value
                for value in access["observation_errors"]
            )
        )


if __name__ == "__main__":
    unittest.main()
