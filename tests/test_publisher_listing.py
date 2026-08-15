from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from publisher_listing import (  # noqa: E402
    fetch_publisher_listing_records,
    parse_publisher_listing,
)


ADAPTER_CONFIG = {
    "template": "publisher_listing_v1",
    "pagination": {"parameter": "pageNum", "start_page": 1, "max_pages": 3},
    "freshness": {
        "field": "published_online",
        "order": "desc",
        "stop_when_older_than_window": True,
        "authoritative_label": "Published online by Cambridge University Press",
    },
    "extract": {
        "article_href_contains": "/article/",
        "date_formats": ["%d %B %Y"],
        "minimum_title_chars": 12,
    },
    "verification": {
        "article_page_required": True,
        "accepted_manuscript_marker": "Accepted manuscript",
    },
}


def article(title: str, slug: str, published: str, *, extra: str = "") -> str:
    return f"""
    <article>
      <h2><a href="/core/journals/fixture/article/{slug}/ABC123">{title}</a></h2>
      <p>{extra}</p>
      <p>Published online by Cambridge University Press: {published}</p>
      <p>doi:10.1017/{slug}</p>
    </article>
    """


class _Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.history = []

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
        return _Response(url, self.pages[page])


class PublisherListingTests(unittest.TestCase):
    def source_config(self) -> dict:
        return {
            "endpoint": (
                "https://www.cambridge.org/core/publications/open-access/listing"
                "?statuses=PUBLISHED"
            ),
            "adapter_config": ADAPTER_CONFIG,
        }

    def test_parser_uses_publisher_online_date_and_ignores_issue_month(self) -> None:
        page = article(
            "Large language models support reflective design work",
            "S0000000000000001",
            "16 August 2026",
            extra="Volume 9, August 2026",
        )
        records, errors, unusable = parse_publisher_listing(
            page,
            listing_url="https://www.cambridge.org/core/publications/open-access/listing",
            source_id="cambridge_core_oa",
            adapter_config=ADAPTER_CONFIG,
        )
        self.assertEqual([], errors)
        self.assertEqual(0, unusable)
        self.assertEqual(1, len(records))
        self.assertEqual("2026-08-16", records[0]["publication_date"])
        self.assertEqual("publisher_listing:published_online", records[0]["source_field"])
        self.assertEqual("10.1017/s0000000000000001", records[0]["doi"])

    def test_parser_rejects_issue_only_date(self) -> None:
        page = """
        <article>
          <a href="/core/journals/fixture/article/issue-only/ABC123">
            An issue month must never become an online publication event
          </a>
          <p>Volume 9, August 2026</p>
        </article>
        """
        records, errors, unusable = parse_publisher_listing(
            page,
            listing_url="https://www.cambridge.org/core/publications/open-access/listing",
            source_id="cambridge_core_oa",
            adapter_config=ADAPTER_CONFIG,
        )
        self.assertEqual([], records)
        self.assertEqual(1, unusable)
        self.assertTrue(any("published-online date" in error for error in errors))

    def test_accepted_manuscript_is_not_promoted_to_vor(self) -> None:
        page = article(
            "Accepted manuscript fixture with a trustworthy title",
            "S0000000000000002",
            "16 August 2026",
            extra="Accepted manuscript",
        )
        records, errors, unusable = parse_publisher_listing(
            page,
            listing_url="https://www.cambridge.org/core/publications/open-access/listing",
            source_id="cambridge_core_oa",
            adapter_config=ADAPTER_CONFIG,
        )
        self.assertEqual([], records)
        self.assertEqual(1, unusable)
        self.assertTrue(any("Accepted Manuscript" in error for error in errors))

    def test_inventory_pages_until_window_is_closed_then_reuses_cache(self) -> None:
        pages = {
            1: (
                article(
                    "Large language model evaluation in collaborative systems",
                    "S0000000000000003",
                    "16 August 2026",
                )
                + article(
                    "Clinical cohort methods for psychiatric care pathways",
                    "S0000000000000004",
                    "15 August 2026",
                )
            ),
            2: article(
                "Older control article outside the rolling discovery window",
                "S0000000000000005",
                "12 August 2026",
            ),
        }
        session = _Session(pages)
        cache: dict[str, object] = {}
        rows = fetch_publisher_listing_records(
            session,
            source_id="cambridge_core_oa",
            source_config=self.source_config(),
            query='"large language model"',
            start_date=date(2026, 8, 13),
            end_date=date(2026, 8, 16),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("2026-08-16", rows[0]["publication_date"])
        self.assertEqual(2, len(session.calls))
        observation = cache["source_observation:cambridge_core_oa"]
        self.assertTrue(observation["retrieval_complete"])
        self.assertEqual("publisher_listing", observation["retrieval_backend"])
        self.assertEqual(2, observation["window_record_count"])
        self.assertEqual(2, observation["inventory_pages_requested"])
        self.assertEqual(2, observation["inventory_pages_received"])

        again = fetch_publisher_listing_records(
            session,
            source_id="cambridge_core_oa",
            source_config=self.source_config(),
            query="*",
            start_date=date(2026, 8, 13),
            end_date=date(2026, 8, 16),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(2, len(again))
        self.assertEqual(2, len(session.calls))


if __name__ == "__main__":
    unittest.main()
