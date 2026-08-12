from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from publisher_feed import (  # noqa: E402
    PublisherFeedError,
    _fetch_crossref_inventory,
    _matches_query,
    _merge_feed_and_registry_records,
    fetch_feed_records,
    parse_feed,
)

NATURE_LIKE_RDF = '''<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
 <item rdf:about="https://www.nature.com/articles/example">
  <title>Quantum widgets improve measurement</title>
  <link>https://www.nature.com/articles/example</link>
  <dc:date>2026-08-10</dc:date>
  <dc:creator>A. Example</dc:creator>
  <description>Recent physics result.</description>
  <prism:doi>10.1038/example</prism:doi>
  <prism:publicationName>Nature Physics</prism:publicationName>
 </item>
</rdf:RDF>'''

JAMA_RELATIVE_PRISM_RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:prism="http://purl.org/rss/1.0/modules/prism/">
 <channel>
  <item>
   <title>Physical Fitness and All-Cause Mortality in Older Adults</title>
   <link>https://jamanetwork.com/journals/jamanetworkopen/fullarticle/fixture</link>
   <pubDate>Mon, 10 Aug 2026 00:00:00 GMT</pubDate>
   <description>A cohort study of physical fitness and mortality.</description>
   <dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">article-fixture</dc:identifier>
   <prism:doi xmlns:prism="prism">https://doi.org/10.1001/JAMANetworkOpen.2026.28227</prism:doi>
  </item>
 </channel>
</rss>'''

ATOM = '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <title>Social science result</title>
  <link rel="alternate" href="https://example.org/work" />
  <published>2026-08-09T10:00:00Z</published>
  <summary>Governance and public policy.</summary>
  <author><name>B. Example</name></author>
 </entry>
</feed>'''

ATOM_UPDATED_ONLY = '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <title>Old record with a new metadata edit</title>
  <link rel="alternate" href="https://example.org/old" />
  <updated>2026-08-10T10:00:00Z</updated>
 </entry>
</feed>'''

ATOM_OLD_PUBLISHED_NEW_UPDATED = '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <title>Old publication with current update</title>
  <link rel="alternate" href="https://example.org/old-published" />
  <published>2025-01-02T10:00:00Z</published>
  <updated>2026-08-10T10:00:00Z</updated>
 </entry>
</feed>'''


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return _Response(self.text)


class PublisherFeedTests(unittest.TestCase):
    def test_xml_entity_expansion_is_rejected(self) -> None:
        malicious = """<?xml version='1.0'?>
<!DOCTYPE feed [<!ENTITY x 'expanded'>]>
<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>&x;</title></entry></feed>"""
        with self.assertRaisesRegex(PublisherFeedError, "invalid XML"):
            parse_feed(
                malicious,
                feed_url="https://example.test/feed.xml",
                source_id="fixture",
            )

    def test_http_error_response_is_counted_as_received_inventory_page(self) -> None:
        class FailingResponse(_Response):
            url = "https://example.test/failed"

            def raise_for_status(self) -> None:
                raise RuntimeError("HTTP 503")

        class FailingSession:
            def get(self, *_args, **_kwargs):
                return FailingResponse("")

        with self.assertRaises(PublisherFeedError) as feed_context:
            fetch_feed_records(
                FailingSession(),
                source_id="fixture",
                source_config={"feeds": ["https://example.test/feed.xml"]},
                query="*",
                start_date=date(2026, 8, 8),
                end_date=date(2026, 8, 11),
                max_results=10,
                cache={},
            )
        self.assertEqual(1, feed_context.exception.pages_requested)
        self.assertEqual(1, feed_context.exception.pages_received)

        with self.assertRaises(PublisherFeedError) as crossref_context:
            _fetch_crossref_inventory(
                FailingSession(),
                source_id="fixture",
                issn="1234-5678",
                start_date=date(2026, 8, 8),
                end_date=date(2026, 8, 11),
                user_agent="fixture",
                timeout=5,
            )
        self.assertEqual(1, crossref_context.exception.pages_requested)
        self.assertEqual(1, crossref_context.exception.pages_received)

    def test_generic_title_collision_does_not_merge_distinct_records(self) -> None:
        records = _merge_feed_and_registry_records(
            [
                {
                    "title": "Editorial",
                    "landing_url": "https://example.test/editorial-1",
                    "publication_date": "2026-08-09",
                },
                {
                    "title": "Editorial",
                    "landing_url": "https://example.test/editorial-2",
                    "publication_date": "2026-08-10",
                },
            ],
            [],
        )
        self.assertEqual(2, len(records))

    def test_title_only_bridge_requires_date_and_bibliographic_match(self) -> None:
        feed = {
            "title": "A distinctive trial changes clinical practice",
            "landing_url": "https://publisher.test/article",
            "publication_date": "2026-08-09",
            "authors": ["Ada Example"],
            "venue": "Fixture Journal",
        }
        registry = {
            "title": feed["title"],
            "landing_url": "https://doi.org/10.1000/fixture",
            "publication_date": "2026-08-09",
            "authors": ["Ada Example"],
            "venue": "Fixture Journal",
            "doi": "10.1000/fixture",
        }
        records = _merge_feed_and_registry_records([feed], [registry])
        self.assertEqual(1, len(records))
        self.assertEqual("https://publisher.test/article", records[0]["landing_url"])
        self.assertEqual("10.1000/fixture", records[0]["doi"])

        mismatched = dict(registry, publication_date="2026-08-10")
        self.assertEqual(
            2,
            len(_merge_feed_and_registry_records([feed], [mismatched])),
        )

    def test_parse_nature_style_rdf(self) -> None:
        rows = parse_feed(
            NATURE_LIKE_RDF,
            feed_url="https://www.nature.com/subjects/physics.rss",
            source_id="nature_physics",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["publication_date"], "2026-08-10")
        self.assertEqual(rows[0]["doi"], "10.1038/example")
        self.assertEqual(rows[0]["venue"], "Nature Physics")
        self.assertEqual(rows[0]["title"], "Quantum widgets improve measurement")

    def test_parse_jama_relative_prism_namespace_doi(self) -> None:
        rows = parse_feed(
            JAMA_RELATIVE_PRISM_RSS,
            feed_url="https://jamanetwork.com/rss/site_214/187.xml",
            source_id="jama_network_open",
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(
            "10.1001/jamanetworkopen.2026.28227",
            rows[0]["doi"],
        )
        self.assertEqual("2026-08-10", rows[0]["publication_date"])

    def test_jama_feed_and_crossref_dedupe_on_canonical_doi(self) -> None:
        [feed_record] = parse_feed(
            JAMA_RELATIVE_PRISM_RSS,
            feed_url="https://jamanetwork.com/rss/site_214/187.xml",
            source_id="jama_network_open",
        )
        registry_record = {
            "title": "Registry title deliberately differs from the RSS title",
            "landing_url": "https://doi.org/10.1001/jamanetworkopen.2026.28227",
            "publication_date": "2026-08-10",
            "doi": "doi:10.1001/JAMANETWORKOPEN.2026.28227",
            "event_confidence": "publisher_supplied_citation",
        }

        records = _merge_feed_and_registry_records(
            [feed_record],
            [registry_record],
        )

        self.assertEqual(1, len(records))
        self.assertEqual(
            "10.1001/jamanetworkopen.2026.28227",
            records[0]["doi"],
        )
        self.assertEqual(
            "https://jamanetwork.com/journals/jamanetworkopen/fullarticle/fixture",
            records[0]["landing_url"],
        )

    def test_parse_atom(self) -> None:
        rows = parse_feed(ATOM, feed_url="https://example.org/feed.atom", source_id="example")
        self.assertEqual(rows[0]["publication_date"], "2026-08-09")
        self.assertEqual(rows[0]["authors"], ["B. Example"])

    def test_window_query_and_cache(self) -> None:
        session = _Session(NATURE_LIKE_RDF)
        cache = {}
        cfg = {"feeds": ["https://www.nature.com/subjects/physics.rss"]}
        rows = fetch_feed_records(
            session,
            source_id="nature_physics",
            source_config=cfg,
            query="quantum measurement",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(len(rows), 1)
        rows2 = fetch_feed_records(
            session,
            source_id="nature_physics",
            source_config=cfg,
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=10,
            cache=cache,
        )
        self.assertEqual(len(rows2), 1)
        self.assertEqual(session.calls, 1)

    def test_redirects_remain_one_logical_feed_or_crossref_inventory_page(self) -> None:
        class RedirectedFeedResponse(_Response):
            history = [object()]

        class RedirectedFeedSession:
            def get(self, *_args, **_kwargs):
                return RedirectedFeedResponse(NATURE_LIKE_RDF)

        feed_cache: dict[str, object] = {}
        fetch_feed_records(
            RedirectedFeedSession(),
            source_id="nature_physics",
            source_config={"feeds": ["https://example.test/feed.xml"]},
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=10,
            cache=feed_cache,
        )
        feed_observation = feed_cache["source_observation:nature_physics"]
        self.assertEqual(1, feed_observation["inventory_pages_requested"])
        self.assertEqual(1, feed_observation["inventory_pages_received"])

        class RedirectedCrossrefResponse:
            history = [object()]
            url = "https://api.crossref.org/fixture"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"message": {"total-results": 0, "items": []}}

        class RedirectedCrossrefSession:
            def get(self, *_args, **_kwargs):
                return RedirectedCrossrefResponse()

        _records, _url, requested, received, _unusable = _fetch_crossref_inventory(
            RedirectedCrossrefSession(),
            source_id="fixture",
            issn="1234-5678",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            user_agent="fixture",
            timeout=5,
        )
        self.assertEqual(1, requested)
        self.assertEqual(1, received)

    def test_atom_updated_is_never_used_as_publication_date(self) -> None:
        rows = parse_feed(
            ATOM_UPDATED_ONLY,
            feed_url="https://example.org/feed.atom",
            source_id="example",
        )
        self.assertEqual("", rows[0]["publication_date"])
        self.assertEqual("2026-08-10T10:00:00Z", rows[0]["updated_raw"])
        self.assertEqual("", rows[0]["source_field"])
        cache: dict[str, object] = {}
        self.assertEqual(
            [],
            fetch_feed_records(
                _Session(ATOM_UPDATED_ONLY),
                source_id="example",
                source_config={"feeds": ["https://example.org/feed.atom"]},
                query="*",
                start_date=date(2026, 8, 8),
                end_date=date(2026, 8, 11),
                max_results=10,
                cache=cache,
            ),
        )
        observation = cache["source_observation:example"]
        self.assertFalse(observation["retrieval_complete"])
        self.assertEqual(1, observation["unusable_record_count"])

    def test_new_update_does_not_move_old_published_record_into_window(self) -> None:
        self.assertEqual(
            [],
            fetch_feed_records(
                _Session(ATOM_OLD_PUBLISHED_NEW_UPDATED),
                source_id="example",
                source_config={"feeds": ["https://example.org/feed.atom"]},
                query="*",
                start_date=date(2026, 8, 8),
                end_date=date(2026, 8, 11),
                max_results=10,
            ),
        )

    def test_query_tokens_use_boundaries(self) -> None:
        self.assertFalse(
            _matches_query(
                "Synergistic effect of paired copper sites in a protein domain",
                "human AI interaction",
            )
        )
        self.assertTrue(
            _matches_query(
                "Trust in human-AI interaction",
                "human AI interaction",
            )
        )

    def test_quoted_query_is_exact_phrase_or_without_token_fallback(self) -> None:
        query = '"large language model" "retrieval-augmented generation"'
        self.assertTrue(
            _matches_query(
                "Evaluating a large language model for clinical decisions",
                query,
            )
        )
        self.assertTrue(
            _matches_query(
                "Grounding with retrieval-augmented generation",
                query,
            )
        )
        self.assertFalse(
            _matches_query(
                "A large clinical cohort with a mechanistic animal model",
                query,
            )
        )

    def test_crossref_issn_inventory_is_complete_and_cached(self) -> None:
        class CrossrefResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "message": {
                        "total-results": 2,
                        "items": [
                            {
                                "DOI": "10.1000/ai.1",
                                "title": ["Human AI interaction and trust"],
                                "URL": "https://doi.org/10.1000/ai.1",
                                "container-title": ["Nature Communications"],
                                "published-online": {"date-parts": [[2026, 8, 9]]},
                            },
                            {
                                "DOI": "10.1000/chem.1",
                                "title": ["Paired copper sites in catalysis"],
                                "URL": "https://doi.org/10.1000/chem.1",
                                "container-title": ["Nature Communications"],
                                "published-online": {"date-parts": [[2026, 8, 10]]},
                            },
                        ],
                        "next-cursor": "unused",
                    }
                }

        class CrossrefSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return CrossrefResponse()

        session = CrossrefSession()
        cache = {}
        config = {"crossref_issn": "2041-1723"}
        first = fetch_feed_records(
            session,
            source_id="nature_communications",
            source_config=config,
            query="human AI interaction",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=40,
            cache=cache,
        )
        second = fetch_feed_records(
            session,
            source_id="nature_communications",
            source_config=config,
            query="copper catalysis",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=40,
            cache=cache,
        )
        self.assertEqual(["10.1000/ai.1"], [item["doi"] for item in first])
        self.assertEqual("publisher_supplied_citation", first[0]["event_confidence"])
        self.assertEqual(["10.1000/chem.1"], [item["doi"] for item in second])
        self.assertEqual(1, session.calls)
        self.assertEqual(
            {
                "retrieval_complete": True,
                "retrieval_backend": "crossref_journal_window",
                "feed_entry_count": 0,
                "registry_record_count": 2,
                "unusable_record_count": 0,
                "window_record_count": 2,
                "inventory_url": "https://api.crossref.org/journals/2041-1723/works",
                "inventory_pages_requested": 1,
                "inventory_pages_received": 1,
                "errors": [],
            },
            cache["source_observation:nature_communications"],
        )

    def test_crossref_inventory_follows_cursor_until_total_is_complete(self) -> None:
        class Response:
            def __init__(self, item: dict, cursor: str) -> None:
                self.item = item
                self.cursor = cursor
                self.url = "https://api.crossref.org/inventory"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "message": {
                        "total-results": 2,
                        "items": [self.item],
                        "next-cursor": self.cursor,
                    }
                }

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                day = 8 + self.calls
                return Response(
                    {
                        "DOI": f"10.1000/page.{self.calls}",
                        "title": [f"Paged item {self.calls}"],
                        "published-online": {"date-parts": [[2026, 8, day]]},
                    },
                    f"cursor-{self.calls}",
                )

        session = Session()
        cache: dict[str, object] = {}
        records = fetch_feed_records(
            session,
            source_id="nature_communications",
            source_config={"crossref_issn": "2041-1723"},
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=40,
            cache=cache,
        )
        self.assertEqual(2, session.calls)
        self.assertEqual(
            (2, 2),
            (
                cache["source_observation:nature_communications"][
                    "inventory_pages_requested"
                ],
                cache["source_observation:nature_communications"][
                    "inventory_pages_received"
                ],
            ),
        )
        self.assertEqual(
            ["10.1000/page.2", "10.1000/page.1"],
            [record["doi"] for record in records],
        )

    def test_crossref_pagination_counts_raw_items_not_only_normalized_records(self) -> None:
        class Response:
            def __init__(self, item: dict, cursor: str) -> None:
                self.item = item
                self.cursor = cursor
                self.url = "https://api.crossref.org/inventory"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "message": {
                        "total-results": 2,
                        "items": [self.item],
                        "next-cursor": self.cursor,
                    }
                }

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                item = (
                    {"DOI": "10.1000/malformed-without-title"}
                    if self.calls == 1
                    else {
                        "DOI": "10.1000/usable",
                        "title": ["Usable second-page item"],
                        "published-online": {"date-parts": [[2026, 8, 10]]},
                    }
                )
                return Response(item, f"cursor-{self.calls}")

        session = Session()
        cache: dict[str, object] = {}
        records = fetch_feed_records(
            session,
            source_id="nature_communications",
            source_config={"crossref_issn": "2041-1723"},
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=40,
            cache=cache,
        )
        self.assertEqual(2, session.calls)
        self.assertEqual(["10.1000/usable"], [record["doi"] for record in records])
        observation = cache["source_observation:nature_communications"]
        self.assertFalse(observation["retrieval_complete"])
        self.assertEqual(1, observation["unusable_record_count"])

    def test_hybrid_inventory_preserves_first_party_values_and_adds_registry_records(self) -> None:
        class Response:
            def __init__(self, *, text: str = "", payload: dict | None = None) -> None:
                self.text = text
                self.payload = payload
                self.url = "https://api.crossref.org/inventory"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                assert self.payload is not None
                return self.payload

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, url, *_args, **_kwargs):
                self.calls += 1
                if "crossref.org" not in str(url):
                    return Response(text=NATURE_LIKE_RDF)
                return Response(
                    payload={
                        "message": {
                            "total-results": 2,
                            "items": [
                                {
                                    "DOI": "10.1038/example",
                                    "title": ["Quantum widgets improve measurement"],
                                    "published-online": {"date-parts": [[2026, 8, 9]]},
                                },
                                {
                                    "DOI": "10.1000/registry-only",
                                    "title": ["Registry only quantum result"],
                                    "published-online": {"date-parts": [[2026, 8, 11]]},
                                },
                            ],
                            "next-cursor": "unused",
                        }
                    }
                )

        session = Session()
        cache: dict[str, object] = {}
        records = fetch_feed_records(
            session,
            source_id="nature_communications",
            source_config={
                "feeds": ["https://www.nature.com/subjects/physics.rss"],
                "crossref_issn": "2041-1723",
            },
            query="*",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 11),
            max_results=40,
            cache=cache,
        )
        self.assertEqual(2, session.calls)
        self.assertEqual(2, len(records))
        duplicate = next(item for item in records if item["doi"] == "10.1038/example")
        self.assertEqual("dc:date", duplicate["source_field"])
        self.assertEqual("publisher_verified", duplicate["event_confidence"])
        self.assertEqual(
            {
                "retrieval_complete": True,
                "retrieval_backend": "rss_atom+crossref_journal_window",
                "feed_entry_count": 1,
                "registry_record_count": 2,
                "unusable_record_count": 0,
                "window_record_count": 2,
                "inventory_url": "https://api.crossref.org/inventory",
                "inventory_pages_requested": 2,
                "inventory_pages_received": 2,
                "errors": [],
            },
            cache["source_observation:nature_communications"],
        )

    def test_crossref_empty_page_before_declared_total_fails_closed(self) -> None:
        class Response:
            def __init__(self, items: list[dict], cursor: str) -> None:
                self.items = items
                self.cursor = cursor
                self.url = "https://api.crossref.org/inventory"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "message": {
                        "total-results": 2,
                        "items": self.items,
                        "next-cursor": self.cursor,
                    }
                }

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(
                        [
                            {
                                "DOI": "10.1000/only-one",
                                "title": ["Only returned item"],
                                "published-online": {"date-parts": [[2026, 8, 10]]},
                            }
                        ],
                        "next-page",
                    )
                return Response([], "stalled")

        with self.assertRaisesRegex(PublisherFeedError, "received 1 of 2"):
            fetch_feed_records(
                Session(),
                source_id="nature_communications",
                source_config={"crossref_issn": "2041-1723"},
                query="*",
                start_date=date(2026, 8, 8),
                end_date=date(2026, 8, 11),
                max_results=40,
                cache={},
            )

    def test_crossref_max_results_selection_is_stable_across_api_order(self) -> None:
        items = [
            {
                "DOI": f"10.1000/stable.{index:02d}",
                "title": [f"Stable item {index:02d}"],
                "published-online": {
                    "date-parts": [[2026, 8, 8 + (index % 4)]]
                },
            }
            for index in range(45)
        ]

        class Response:
            def __init__(self, values: list[dict]) -> None:
                self.values = values
                self.url = "https://api.crossref.org/inventory"

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "message": {
                        "total-results": len(self.values),
                        "items": self.values,
                        "next-cursor": "unused",
                    }
                }

        class Session:
            def __init__(self, values: list[dict]) -> None:
                self.values = values

            def get(self, *_args, **_kwargs):
                return Response(self.values)

        def selected(values: list[dict]) -> list[str]:
            return [
                str(record["doi"])
                for record in fetch_feed_records(
                    Session(values),
                    source_id="nature_communications",
                    source_config={"crossref_issn": "2041-1723"},
                    query="*",
                    start_date=date(2026, 8, 8),
                    end_date=date(2026, 8, 11),
                    max_results=40,
                    cache={},
                )
            ]

        self.assertEqual(selected(items), selected(list(reversed(items))))


if __name__ == "__main__":
    unittest.main()
