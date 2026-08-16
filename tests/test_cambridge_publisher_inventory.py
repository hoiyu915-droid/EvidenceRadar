from __future__ import annotations

import copy
import re
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
    def __init__(self, slugs: list[str], *, incomplete_slug: str = "") -> None:
        self.slugs = slugs
        self.incomplete_slug = incomplete_slug
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        parsed = urlsplit(url)
        match = re.search(r"/core/journals/([^/]+)/listing", parsed.path)
        if match is None:
            raise AssertionError(f"unexpected Cambridge URL: {url}")
        slug = match.group(1)
        page = int(parse_qs(parsed.query).get("pageNum", ["1"])[0])
        index = self.slugs.index(slug) + 1
        if page == 1:
            published = "16 August 2026"
        elif slug == self.incomplete_slug:
            published = "15 August 2026"
        else:
            published = "12 August 2026"
        return _Response(
            url,
            article(
                f"{slug} fixture page {page}",
                slug,
                f"S{index:08d}{page:02d}",
                published,
            ),
        )


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

    def test_curated_journal_shards_execute_once_and_are_reused(self) -> None:
        runtime, streams = _cambridge_runtime(
            "owner_cambridge_llm", "owner_cambridge_human_ai"
        )
        source = streams["source_catalog"][CAMBRIDGE]
        inventory = source["adapter_config"]["inventory"]
        shards = inventory["shards"]
        slugs = [str(shard["journal_slug"]) for shard in shards]
        titles = {str(shard["journal_title"]) for shard in shards}
        self.assertEqual(11, len(slugs))

        session = _Session(slugs)
        result = discover_candidates(
            streams,
            runtime.scoring,
            self.start,
            self.end,
            session=session,
        )

        self.assertEqual(22, len(session.calls), "11 journal shards should each close on page 2")
        self.assertEqual({CAMBRIDGE}, result.checked_sources)
        self.assertEqual({CAMBRIDGE}, result.searched_sources)
        self.assertNotIn(CAMBRIDGE, result.unavailable_sources)
        self.assertEqual({"SUCCESS"}, {row["status"] for row in result.queries})
        self.assertEqual(11, len(result.all_candidates))
        self.assertEqual(titles, {candidate.venue for candidate in result.all_candidates})
        for candidate in result.all_candidates:
            self.assertIsNone(candidate.open_access)
            self.assertTrue(candidate.venue)
            self.assertFalse(
                any(
                    item.get("evidence_type") == "publisher_listing_oa_inventory"
                    for item in candidate.oa_evidence
                )
            )
            self.assertEqual(
                {"owner_cambridge_human_ai", "owner_cambridge_llm"},
                set(candidate.observed_streams),
            )

        self.assertEqual(2, len(result.source_access))
        first, second = result.source_access
        self.assertEqual(22, first["http_requests_attempted"])
        self.assertFalse(first["cache_reused"])
        self.assertEqual(0, second["http_requests_attempted"])
        self.assertTrue(second["cache_reused"])
        for access in result.source_access:
            self.assertEqual("publisher_listing_shards", access["retrieval_backend"])
            self.assertEqual("curated_journal_articles", access["publisher_inventory_scope"])
            self.assertEqual("curated_journal_allowlist", access["shard_strategy"])
            self.assertEqual(11, access["selected_journal_count"])
            self.assertEqual("article", access["coverage_unit"])
            self.assertFalse(access["journal_level_coverage"])
            self.assertTrue(access["window_closed"])
            self.assertFalse(access["page_bound_reached"])

    def test_one_incomplete_shard_makes_parent_partial(self) -> None:
        runtime, streams = _cambridge_runtime("owner_cambridge_llm")
        source = streams["source_catalog"][CAMBRIDGE]
        source["adapter_config"]["pagination"]["max_pages"] = 2
        shards = source["adapter_config"]["inventory"]["shards"]
        slugs = [str(shard["journal_slug"]) for shard in shards]
        incomplete_slug = slugs[0]
        session = _Session(slugs, incomplete_slug=incomplete_slug)

        result = discover_candidates(
            streams,
            runtime.scoring,
            self.start,
            self.end,
            session=session,
        )

        self.assertEqual(22, len(session.calls))
        self.assertEqual("PARTIAL", result.queries[0]["status"])
        self.assertIn(CAMBRIDGE, result.unavailable_sources)
        access = result.source_access[0]
        self.assertFalse(access["retrieval_complete"])
        self.assertTrue(access["page_bound_reached"])
        self.assertFalse(access["window_closed"])
        self.assertTrue(
            any(
                incomplete_slug in value
                and "did not close the requested window within" in value
                for value in access["observation_errors"]
            )
        )


if __name__ == "__main__":
    unittest.main()
