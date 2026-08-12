"""Offline contract tests for the non-PubMed discovery adapters.

Each adapter receives a mocked ``requests.Session`` and response.  These
fixtures exercise the provider-specific parsing boundary only: no test opens a
socket, and an empty provider response must remain an empty candidate list.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from tools.run_github_radar import (
    Candidate,
    _arxiv_identifier,
    _arxiv_search_query,
    _candidate_from_acl_atom,
    _europe_pmc_query,
    _parse_pmlr_atom,
    candidate_oa_status,
    fetch_acl_anthology,
    fetch_arxiv,
    fetch_europe_pmc,
    fetch_openreview,
    fetch_pmlr,
    fetch_rss_atom,
    fulltext_metadata,
    qualifying_event,
)

START = date(2026, 8, 6)
END = date(2026, 8, 9)


def _response(*, payload: object | None = None, text: str = "") -> mock.Mock:
    response = mock.Mock()
    response.status_code = 200
    response.url = "https://fixture.example/source"
    response.headers = {}
    response.text = text
    response.content = text.encode("utf-8")
    response.raise_for_status.return_value = None
    if payload is not None:
        response.json.return_value = payload
    return response


def _session(response: mock.Mock) -> mock.Mock:
    session = mock.Mock()
    session.get.return_value = response
    return session


def _call(adapter, session: mock.Mock) -> list[Candidate]:
    return adapter(
        session,
        "fixture query",
        "fixture_stream",
        "llm_research",
        START,
        END,
        10,
    )


class SourceAdapterEmptyResponseTests(unittest.TestCase):
    """A successful empty provider response is not an access failure."""

    def test_europe_pmc_empty_result_is_empty(self) -> None:
        response = _response(payload={"hitCount": 0, "resultList": {"result": []}})
        self.assertEqual([], _call(fetch_europe_pmc, _session(response)))

    def test_arxiv_empty_feed_is_empty(self) -> None:
        response = _response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
        )
        self.assertEqual([], _call(fetch_arxiv, _session(response)))

    def test_openreview_empty_notes_is_empty(self) -> None:
        response = _response(payload={"notes": []})
        self.assertEqual([], _call(fetch_openreview, _session(response)))

    def test_acl_anthology_empty_feed_is_empty(self) -> None:
        response = _response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
        )
        self.assertEqual([], _call(fetch_acl_anthology, _session(response)))

    def test_pmlr_empty_listing_is_empty(self) -> None:
        response = _response(text="<html><body><main></main></body></html>")
        self.assertEqual([], _call(fetch_pmlr, _session(response)))

    def test_arxiv_query_requires_configured_topic_anchor(self) -> None:
        query = _arxiv_search_query(
            "chatbot anthropomorphism emotional dependence social connection",
            START,
            END,
        )
        self.assertIn('all:"chatbot" AND', query)
        self.assertIn('all:"anthropomorphism"', query)
        self.assertIn("submittedDate:[202608060000 TO 202608092359]", query)

    def test_europe_pmc_query_covers_all_supported_event_dates(self) -> None:
        query = _europe_pmc_query("fixture", START, END)
        self.assertIn("FIRST_PDATE:[2026-08-06 TO 2026-08-09]", query)
        self.assertIn("FIRST_IDATE:[2026-08-06 TO 2026-08-09]", query)
        self.assertIn("E_PDATE:[2026-08-06 TO 2026-08-09]", query)

    def test_arxiv_identifier_preserves_legacy_archive_prefix(self) -> None:
        self.assertEqual(
            "quant-ph/0601001",
            _arxiv_identifier("http://arxiv.org/abs/quant-ph/0601001v2"),
        )


class SourceAdapterFieldParsingTests(unittest.TestCase):
    def _assert_common_candidate_contract(self, item: Candidate) -> None:
        self.assertEqual("fixture_stream", item.stream)
        self.assertEqual("llm_research", item.category)
        self.assertTrue(item.source)

    def _assert_event_contract(self, item: Candidate) -> None:
        self.assertTrue(item.events)
        for event in item.events:
            for field in (
                "event_type",
                "occurred_at",
                "source",
                "source_field",
                "source_url",
                "precision",
                "confidence",
            ):
                self.assertTrue(event.get(field), f"missing event field {field}")

    def test_europe_pmc_parses_identity_and_event(self) -> None:
        response = _response(
            payload={
                "hitCount": 1,
                "resultList": {
                    "result": [
                        {
                            "id": "123456",
                            "source": "MED",
                            "title": "Europe PMC evidence fixture",
                            "authorString": "Lee A; Wong B",
                            "journalTitle": "Fixture Journal",
                            "pubYear": "2026",
                            "firstPublicationDate": "2026-08-08",
                            "doi": "10.1000/EPMC.1",
                            "pmcid": "PMC123456",
                            "isOpenAccess": "Y",
                            "abstractText": "A source-level abstract.",
                        }
                    ]
                },
            }
        )
        results = _call(fetch_europe_pmc, _session(response))
        self.assertEqual(1, len(results))
        item = results[0]
        self.assertEqual("Europe PMC evidence fixture", item.title)
        self.assertEqual("10.1000/epmc.1", item.doi)
        self.assertEqual("123456", item.pmid)
        self.assertEqual("PMC123456", item.pmcid)
        self.assertEqual("2026-08-08", item.publication_date)
        self.assertTrue(item.open_access)
        self.assertEqual("YES", candidate_oa_status(item))
        self.assertIn(
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/",
            item.fulltext_urls(),
        )
        self.assertIn(
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/pdf",
            item.fulltext_urls(),
        )
        self._assert_common_candidate_contract(item)
        self.assertTrue(item.events)
        self.assertEqual("2026-08-08", item.events[0]["occurred_at"])
        self.assertTrue(item.events[0]["source_url"].startswith("http"))

    def test_arxiv_parses_identifier_authors_and_event(self) -> None:
        response = _response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom">'
                "<entry>"
                "<id>http://arxiv.org/abs/2608.12345v1</id>"
                "<title>  ArXiv grounding fixture  </title>"
                "<published>2026-08-08T12:00:00Z</published>"
                "<updated>2026-08-08T12:00:00Z</updated>"
                "<summary>Grounding summary.</summary>"
                "<author><name>A. Lee</name></author>"
                '<link rel="alternate" href="https://arxiv.org/abs/2608.12345v1" />'
                "</entry>"
                "</feed>"
            )
        )
        results = _call(fetch_arxiv, _session(response))
        self.assertEqual(1, len(results))
        item = results[0]
        self.assertEqual("ArXiv grounding fixture", item.title)
        self.assertEqual("2608.12345", item.arxiv_id)
        self.assertEqual("2026-08-08", item.publication_date)
        self.assertIn("A. Lee", item.authors)
        self._assert_common_candidate_contract(item)
        self.assertTrue(item.is_preprint)
        self.assertEqual("preprint", item.document_type)
        self.assertEqual([], item.events)
        self.assertIsNone(
            qualifying_event(
                item,
                datetime(2026, 8, 6, tzinfo=ZoneInfo("Asia/Tokyo")),
                datetime(2026, 8, 9, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo")),
                ZoneInfo("Asia/Tokyo"),
            )
        )
        self.assertIn("arxiv.org", item.landing_url)
        self.assertEqual("YES", candidate_oa_status(item))
        self.assertEqual(
            ["https://arxiv.org/pdf/2608.12345"],
            item.fulltext_urls(),
        )
        self.assertIn("https://arxiv.org/abs/2608.12345", item.discovery_urls())
        self.assertIn("https://arxiv.org/pdf/2608.12345", item.discovery_urls())

    def test_openreview_is_discovery_only_even_with_venue_and_doi(self) -> None:
        response = _response(
            payload={
                "notes": [
                    {
                        "id": "fixture-note-1",
                        "forum": "fixture-note-1",
                        "cdate": 1_754_640_000_000,
                        "content": {
                            "title": {"value": "OpenReview evidence fixture"},
                            "abstract": {"value": "Reviewable abstract."},
                            "authors": {"value": ["A. Lee", "B. Wong"]},
                            "venue": {"value": "Fixture Workshop"},
                            "publication_date": {"value": "2026-08-08T00:00:00Z"},
                            "doi": {"value": "10.1000/OPENREVIEW.1"},
                        },
                    }
                ]
            }
        )
        results = _call(fetch_openreview, _session(response))
        self.assertEqual(1, len(results))
        item = results[0]
        self.assertEqual("OpenReview evidence fixture", item.title)
        self.assertEqual("10.1000/openreview.1", item.doi)
        self.assertEqual("2026-08-08", item.publication_date)
        self.assertEqual(["A. Lee", "B. Wong"], item.authors)
        self.assertEqual("Fixture Workshop", item.venue)
        self._assert_common_candidate_contract(item)
        self.assertTrue(item.is_preprint)
        self.assertEqual("preprint", item.document_type)
        self.assertEqual([], item.events)

    def test_openreview_normalizes_numeric_content_publication_date(self) -> None:
        published_ms = int(
            datetime(2026, 8, 8, tzinfo=timezone.utc).timestamp() * 1000
        )
        response = _response(
            payload={
                "notes": [
                    {
                        "id": "fixture-note-numeric-date",
                        "content": {
                            "title": {"value": "OpenReview numeric date fixture"},
                            "date": {"value": published_ms},
                        },
                    }
                ]
            }
        )
        item = _call(fetch_openreview, _session(response))[0]
        self.assertEqual("2026-08-08", item.publication_date)
        self.assertEqual([], item.events)

    def test_acl_anthology_parses_anthology_id_doi_and_event(self) -> None:
        response = _response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom">'
                "<entry>"
                "<id>https://aclanthology.org/2026.acl-long.1/</id>"
                "<title>ACL Anthology evidence fixture</title>"
                "<published>2026-08-08T00:00:00Z</published>"
                "<updated>2026-08-08T00:00:00Z</updated>"
                "<summary>Anthology abstract.</summary>"
                "<author><name>A. Lee</name></author>"
                '<link rel="alternate" href="https://aclanthology.org/2026.acl-long.1/" />'
                '<link rel="doi" href="https://doi.org/10.1000/ACL.1" />'
                "</entry>"
                "</feed>"
            )
        )
        results = _call(fetch_acl_anthology, _session(response))
        self.assertEqual(1, len(results))
        item = results[0]
        self.assertEqual("ACL Anthology evidence fixture", item.title)
        self.assertEqual("2026.acl-long.1", item.anthology_id)
        self.assertEqual("10.1000/acl.1", item.doi)
        self.assertEqual("2026-08-08", item.publication_date)
        self._assert_common_candidate_contract(item)
        self._assert_event_contract(item)
        self.assertTrue(item.events)
        self.assertEqual("atom:published", item.events[0]["source_field"])
        self.assertIn("aclanthology.org", item.events[0]["source_url"])

    def test_acl_updated_only_atom_entry_has_no_formal_event_and_is_excluded(self) -> None:
        feed_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry>"
            "<id>https://aclanthology.org/2026.acl-long.2/</id>"
            "<title>ACL updated-only fixture query</title>"
            "<updated>2026-08-08T00:00:00Z</updated>"
            '<link rel="alternate" href="https://aclanthology.org/2026.acl-long.2/" />'
            "</entry>"
            "</feed>"
        )
        root = ET.fromstring(feed_text)
        entry = root.find("{http://www.w3.org/2005/Atom}entry")
        self.assertIsNotNone(entry)
        candidate = _candidate_from_acl_atom(
            entry, stream="fixture_stream", category="llm_research"
        )
        self.assertIsNotNone(candidate)
        self.assertEqual("", candidate.publication_date)
        self.assertEqual([], candidate.events)

        self.assertEqual(
            [], _call(fetch_acl_anthology, _session(_response(text=feed_text)))
        )

    def test_pmlr_parses_listing_identity_and_event(self) -> None:
        response = _response(
            text=(
                '<html><body><article class="paper"><h3>'
                '<a href="/v250/fixture.html">PMLR evidence fixture</a></h3>'
                '<div class="authors">A. Lee; B. Wong</div>'
                '<time datetime="2026-08-08">2026-08-08</time>'
                '<p>Proceedings abstract.</p>'
                "</article></body></html>"
            )
        )
        results = _call(fetch_pmlr, _session(response))
        self.assertEqual(1, len(results))
        item = results[0]
        self.assertEqual("PMLR evidence fixture", item.title)
        self.assertEqual("2026-08-08", item.publication_date)
        self.assertIn("A. Lee", item.authors)
        self.assertTrue(item.landing_url.endswith("/v250/fixture.html"))
        self._assert_common_candidate_contract(item)
        self._assert_event_contract(item)
        self.assertTrue(item.events)
        self.assertEqual("2026-08-08", item.events[0]["occurred_at"])

    def test_pmlr_updated_only_atom_entry_has_no_formal_event_and_is_excluded(self) -> None:
        atom_text = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry>"
            "<id>https://proceedings.mlr.press/v250/fixture-updated.html</id>"
            "<title>PMLR updated-only fixture query</title>"
            "<updated>2026-08-08T00:00:00Z</updated>"
            '<link rel="alternate" href="https://proceedings.mlr.press/v250/fixture-updated.html" />'
            "</entry>"
            "</feed>"
        )
        candidates = _parse_pmlr_atom(
            ET.fromstring(atom_text),
            stream="fixture_stream",
            category="llm_research",
        )
        self.assertEqual(1, len(candidates))
        self.assertEqual("", candidates[0].publication_date)
        self.assertEqual([], candidates[0].events)

        index_response = _response(
            text='<html><body><a href="v250">Volume 250</a></body></html>'
        )
        atom_response = _response(text=atom_text)
        session = mock.Mock()
        session.get.side_effect = [index_response, atom_response]
        self.assertEqual([], _call(fetch_pmlr, session))


class SourceCatalogOATests(unittest.TestCase):
    @staticmethod
    def _record() -> dict[str, object]:
        return {
            "title": "Publisher feed fixture query",
            "publication_date": "2026-08-08",
            "landing_url": "https://publisher.example/articles/fixture",
            "feed_url": "https://publisher.example/feed.xml",
            "source_field": "atom:published",
            "authors": ["A. Lee"],
            "venue": "Fixture Journal",
            "doi": "10.1000/feed.1",
        }

    def _fetch(self, oa_mode: str) -> Candidate:
        with mock.patch(
            "tools.run_github_radar.fetch_feed_records",
            return_value=[self._record()],
        ):
            candidates = fetch_rss_atom(
                mock.Mock(),
                "fixture query",
                "fixture_stream",
                "llm_research",
                START,
                END,
                10,
                source_id="fixture_journal",
                source_config={"oa_mode": oa_mode},
                cache={},
            )
        self.assertEqual(1, len(candidates))
        return candidates[0]

    def test_fully_oa_source_projects_oa_yes_without_claiming_access(self) -> None:
        candidate = self._fetch("fully_oa")
        self.assertTrue(candidate.open_access)
        self.assertEqual("YES", candidate_oa_status(candidate))
        self.assertEqual("publisher_verified", candidate.events[0]["confidence"])
        self.assertIn(
            {
                "source": "fixture_journal",
                "evidence_type": "source_catalog_oa_mode",
                "value": "fully_oa",
                "url": "https://publisher.example/feed.xml",
            },
            candidate.oa_evidence,
        )
        self.assertNotIn(
            "provider_open_access_flag",
            {item["evidence_type"] for item in candidate.oa_evidence},
        )
        metadata = fulltext_metadata(candidate)
        self.assertEqual("YES", metadata["oa_status"])
        self.assertEqual("NOT_CHECKED", metadata["access_status"])

    def test_mixed_oa_source_remains_unknown(self) -> None:
        candidate = self._fetch("mixed")
        self.assertIsNone(candidate.open_access)
        self.assertEqual("UNKNOWN", candidate_oa_status(candidate))
        self.assertEqual([], candidate.oa_evidence)

    def test_crossref_print_date_does_not_claim_first_online_event(self) -> None:
        record = self._record()
        record["source_field"] = "crossref:published-print"
        record["event_confidence"] = "publisher_supplied_citation"
        with mock.patch(
            "tools.run_github_radar.fetch_feed_records", return_value=[record]
        ):
            [candidate] = fetch_rss_atom(
                mock.Mock(),
                "fixture query",
                "fixture_stream",
                "llm_research",
                START,
                END,
                10,
                source_id="fixture_journal",
                source_config={"oa_mode": "fully_oa"},
                cache={},
            )
        self.assertEqual("formal_version_verified", candidate.events[0]["event_type"])
        self.assertEqual(
            "publisher_supplied_citation", candidate.events[0]["confidence"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
