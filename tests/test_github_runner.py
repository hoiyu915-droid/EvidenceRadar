from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import requests

from tools.run_github_radar import (
    Candidate,
    RadarRuntimeError,
    _request,
    event_in_window,
    event_record,
    execute,
    discover_candidates,
    fetch_openalex,
    fetch_pubmed,
    load_prior_state,
    probe_publisher_pages,
    state_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Tokyo")


def candidate(index: int, *, domain: str | None = None) -> Candidate:
    host = domain or f"publisher-{index}.example"
    return Candidate(
        title=f"Auditable Evidence Candidate {index}",
        stream="clinical_medicine",
        category="clinical_medicine",
        source="pubmed",
        publication_date="2026-08-08",
        venue="Example Journal",
        doi=f"10.1000/example.{index}",
        landing_url=f"https://{host}/article/{index}",
        events=[
            event_record(
                "version_of_record_first_online",
                "2026-08-08",
                "pubmed",
                "ArticleDate",
                f"https://{host}/article/{index}",
                "date",
                "provider_metadata",
            )
        ],
        score=90,
    )


class FakeResponse:
    def __init__(self, url: str, status_code: int = 200, location: str = "") -> None:
        self.url = url
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.statuses = statuses or {}
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(url, self.statuses.get(url, 200))


class GithubRunnerTests(unittest.TestCase):
    def test_malformed_source_payload_becomes_auditable_gap(self) -> None:
        streams = {
            "candidate_guidance": {"suggested_max_per_query": 2},
            "streams": {
                "llm_stream": {
                    "sources": ["openalex"],
                    "queries": ["fixture"],
                    "relevance_terms": ["fixture"],
                }
            },
        }
        scoring = {
            "categories": {"llm_research": {"streams": ["llm_stream"]}},
            "category_min_relevance": {"llm_research": 0},
        }
        with mock.patch(
            "tools.run_github_radar.fetch_openalex",
            side_effect=AttributeError("malformed upstream payload"),
        ):
            candidates, queries, access, searched, unavailable = discover_candidates(
                streams,
                scoring,
                datetime(2026, 8, 6, 12, 0, tzinfo=TZ),
                datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
                session=FakeSession(),
            )
        self.assertEqual([], candidates)
        self.assertEqual("FAILED", queries[0]["status"])
        self.assertEqual("FAILED", access[0]["status"])
        self.assertEqual(set(), searched)
        self.assertEqual({"openalex"}, unavailable)

    def test_request_errors_redact_provider_credentials(self) -> None:
        class ErrorSession:
            def get(self, _url: str, **_kwargs: object) -> object:
                raise requests.RequestException(
                    "https://api.example/?api_key=top-secret&email=user@example.test"
                )

        with mock.patch.dict(
            os.environ,
            {"OPENALEX_API_KEY": "top-secret", "NCBI_EMAIL": "user@example.test"},
        ):
            with self.assertRaises(RadarRuntimeError) as raised:
                _request(ErrorSession(), "https://api.example/", attempts=1)
        message = str(raised.exception)
        self.assertNotIn("top-secret", message)
        self.assertNotIn("user@example.test", message)
        self.assertIn("api_key=[REDACTED]", message)
        self.assertIn("email=[REDACTED]", message)

    def test_prior_state_hash_uses_canonical_json_not_file_formatting(self) -> None:
        state = {"artifact_type": "EvidenceRadar_State", "works": [], "label": "雷達"}
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            first.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            second.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            loaded_first, hash_first = load_prior_state(first)
            loaded_second, hash_second = load_prior_state(second)
        self.assertEqual(state, loaded_first)
        self.assertEqual(state, loaded_second)
        self.assertEqual(state_sha256(state), hash_first)
        self.assertEqual(hash_first, hash_second)

    def test_schema_invalid_prior_state_is_not_accepted_as_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"artifact_type":"EvidenceRadar_State"}', encoding="utf-8")
            loaded, digest = load_prior_state(
                path,
                schema_path=ROOT / "schemas" / "evidence-radar-state.schema.json",
            )
        self.assertIsNone(loaded)
        self.assertEqual("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", digest)

    def test_pubmed_adapter_preserves_identity_and_date_precision(self) -> None:
        class ApiResponse:
            def __init__(self, *, payload: dict[str, object] | None = None, body: str = "") -> None:
                self._payload = payload
                self.text = body

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                assert self._payload is not None
                return self._payload

        article = """<PubmedArticleSet><PubmedArticle>
          <MedlineCitation><PMID>12345</PMID><Article>
            <ArticleTitle>Exercise and auditable outcomes</ArticleTitle>
            <ArticleDate><Year>2026</Year><Month>08</Month><Day>08</Day></ArticleDate>
            <Journal><Title>Example Journal</Title></Journal>
            <AuthorList><Author><LastName>Lee</LastName><Initials>A</Initials></Author></AuthorList>
            <Abstract><AbstractText>Exercise physiology outcome.</AbstractText></Abstract>
          </Article></MedlineCitation>
          <PubmedData>
            <ArticleIdList><ArticleId IdType="doi">10.1000/Test.DOI</ArticleId></ArticleIdList>
            <History><PubMedPubDate PubStatus="pubmed"><Year>2026</Year><Month>08</Month><Day>09</Day><Hour>2</Hour><Minute>3</Minute></PubMedPubDate></History>
          </PubmedData>
        </PubmedArticle></PubmedArticleSet>"""

        class PubmedSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, _url: str, **_kwargs: object) -> ApiResponse:
                self.calls += 1
                if self.calls <= 2:
                    return ApiResponse(payload={"esearchresult": {"idlist": ["12345"]}})
                return ApiResponse(body=article)

        results = fetch_pubmed(
            PubmedSession(),
            "fixture",
            "sport_science",
            "sport_science",
            date(2026, 8, 6),
            date(2026, 8, 9),
            10,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(1, len(results))
        self.assertEqual("10.1000/test.doi", results[0].doi)
        self.assertEqual("12345", results[0].pmid)
        self.assertEqual("2026-08-08", results[0].publication_date)
        self.assertEqual("2026-08-09", results[0].events[-1]["occurred_at"])
        self.assertEqual("date", results[0].events[-1]["precision"])

    def test_openalex_adapter_reconstructs_abstract_and_ids(self) -> None:
        class OpenAlexResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "https://doi.org/10.1000/OA.1",
                            "display_name": "Grounded retrieval audit",
                            "publication_date": "2026-08-08",
                            "type": "article",
                            "authorships": [{"author": {"display_name": "A. Lee"}}],
                            "primary_location": {
                                "landing_page_url": "https://journal.example/w1",
                                "source": {"display_name": "Journal"},
                            },
                            "best_oa_location": None,
                            "open_access": {"is_oa": True},
                            "abstract_inverted_index": {"Grounded": [0], "retrieval": [1]},
                            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/987/"},
                        }
                    ]
                }

        class OpenAlexSession:
            def get(self, _url: str, **_kwargs: object) -> OpenAlexResponse:
                return OpenAlexResponse()

        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "fixture-key"}):
            results = fetch_openalex(
                OpenAlexSession(),
                "fixture",
                "llm_l3_retrieval_grounding",
                "llm_research",
                date(2026, 8, 6),
                date(2026, 8, 9),
                10,
            )
        self.assertEqual(1, len(results))
        self.assertEqual("Grounded retrieval", results[0].abstract)
        self.assertEqual("10.1000/oa.1", results[0].doi)
        self.assertEqual("987", results[0].pmid)
        self.assertEqual("https://openalex.org/W1", results[0].openalex_id)

    def test_openalex_adapter_fails_closed_without_api_key(self) -> None:
        class NoCallSession:
            def get(self, _url: str, **_kwargs: object) -> object:
                raise AssertionError("network must not be called without a key")

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RadarRuntimeError, "OPENALEX_API_KEY"):
                fetch_openalex(
                    NoCallSession(),
                    "fixture",
                    "llm_l3_retrieval_grounding",
                    "llm_research",
                    date(2026, 8, 6),
                    date(2026, 8, 9),
                    10,
                )

    def test_execute_writes_valid_four_artifact_bundle_and_deduplicates_next_run(self) -> None:
        end_at = datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        item = candidate(1)

        def discoverer(*_args: object, **_kwargs: object):
            return (
                [item],
                [
                    {
                        "query_id": "query-001",
                        "category": "clinical_medicine",
                        "query": "fixture query",
                        "searched_at": end_at.isoformat(),
                        "source_ids": ["pubmed"],
                        "status": "SUCCESS",
                        "result_count": 1,
                    }
                ],
                [
                    {
                        "source_id": "query-001-pubmed",
                        "url": "https://pubmed.ncbi.nlm.nih.gov/",
                        "accessed_at": end_at.isoformat(),
                        "status": "SUCCESS",
                        "result_count": 1,
                    }
                ],
                {"pubmed"},
                set(),
            )

        probe_inputs: list[list[Candidate]] = []

        def publisher_probe(items: list[Candidate], *_args: object, **_kwargs: object):
            probe_inputs.append(list(items))
            if not items:
                return [], [], ["Publisher output target was 10-15; only 0 source pages were accessible."]
            access = {
                "source_id": "publisher-001",
                "url": items[0].landing_url,
                "accessed_at": end_at.isoformat(),
                "status": "SUCCESS",
                "result_count": 1,
            }
            return [(items[0], access)], [access], []

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            first_output = temporary / "first"
            state_path = temporary / "canonical" / "EvidenceRadar_State.json"
            summary = execute(
                root=ROOT,
                output_dir=first_output,
                state_path=state_path,
                runs_dir=temporary / "runs",
                end_at=end_at,
                run_id="github-actions-first",
                execution_lane="github_actions",
                protocol_commit="a" * 40,
                discoverer=discoverer,
                publisher_probe=publisher_probe,
            )

            self.assertEqual("STATE_HISTORY_INCOMPLETE", summary["run_status"])
            self.assertEqual(
                {
                    "EvidenceRadar_Report.html",
                    "EvidenceRadar_State.json",
                    "EvidenceRadar_Evidence.json",
                    "EvidenceRadar_Run.json",
                },
                {path.name for path in first_output.iterdir()},
            )
            evidence = json.loads((first_output / "EvidenceRadar_Evidence.json").read_text())
            state = json.loads((first_output / "EvidenceRadar_State.json").read_text())
            run = json.loads((first_output / "EvidenceRadar_Run.json").read_text())
            self.assertEqual([], evidence["claims"])
            self.assertEqual("github_actions", state["execution_lane"])
            self.assertEqual("a" * 40, state["protocol_commit"])
            self.assertEqual(1, len(state["notified_events"]))
            self.assertEqual(0, run["counts"]["verified_works"])
            self.assertEqual(1, run["counts"]["notified_events"])
            self.assertEqual("a" * 40, run["protocol_commit"])
            warning_codes = {warning["code"] for warning in run["warnings"]}
            self.assertIn("AUTOMATED_SOURCE_ADAPTER_GAP", warning_codes)
            self.assertIn("SOURCE_ADAPTER_FAILED", warning_codes)
            self.assertTrue(state_path.is_file())
            self.assertEqual(
                (first_output / "EvidenceRadar_State.json").read_bytes(),
                state_path.read_bytes(),
            )
            self.assertEqual(
                {
                    "EvidenceRadar_Report.html",
                    "EvidenceRadar_State.json",
                    "EvidenceRadar_Evidence.json",
                    "EvidenceRadar_Run.json",
                },
                {path.name for path in (temporary / "runs" / "github-actions-first").iterdir()},
            )

            second_output = temporary / "second"
            second = execute(
                root=ROOT,
                output_dir=second_output,
                state_path=state_path,
                end_at=end_at + timedelta(hours=1),
                run_id="github-actions-second",
                execution_lane="github_actions",
                protocol_commit="b" * 40,
                discoverer=discoverer,
                publisher_probe=publisher_probe,
            )
            second_state = json.loads((second_output / "EvidenceRadar_State.json").read_text())
            self.assertEqual(0, second["publisher_output"])
            self.assertEqual([], probe_inputs[-1])
            self.assertEqual(1, len(second_state["notified_events"]))
            self.assertEqual(["github-actions-first"], second_state["parent_run_ids"])
            self.assertNotEqual("0" * 64, second_state["base_state_sha256"])

    def test_publisher_probe_enforces_hard_maximum(self) -> None:
        session = FakeSession()
        successes, access, warnings = probe_publisher_pages(
            [candidate(index) for index in range(20)],
            {
                "target_min_per_run": 10,
                "hard_max_per_run": 15,
                "per_domain_hard_max": 2,
                "request_delay_seconds": 0,
            },
            session=session,
            accessed_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
        )
        self.assertEqual(15, len(session.calls))
        self.assertEqual(15, len(successes))
        self.assertEqual(15, len(access))
        self.assertEqual([], warnings)

    def test_publisher_probe_rejects_unsafe_budget(self) -> None:
        with self.assertRaisesRegex(RadarRuntimeError, "target_min_per_run"):
            probe_publisher_pages(
                [],
                {"target_min_per_run": 16, "hard_max_per_run": 15},
                session=FakeSession(),
                accessed_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
            )
        with self.assertRaisesRegex(RadarRuntimeError, "cannot exceed 15"):
            probe_publisher_pages(
                [],
                {"target_min_per_run": 10, "hard_max_per_run": 16},
                session=FakeSession(),
                accessed_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
            )

    def test_publisher_probe_stops_a_blocked_domain(self) -> None:
        blocked = "https://blocked.example/article/1"
        session = FakeSession({blocked: 403})
        items = [candidate(index, domain="blocked.example") for index in range(1, 5)]
        items.append(candidate(5, domain="open.example"))
        successes, access, warnings = probe_publisher_pages(
            items,
            {
                "target_min_per_run": 2,
                "hard_max_per_run": 5,
                "per_domain_hard_max": 5,
                "request_delay_seconds": 0,
                "stop_domain_on_http_status": [403, 429],
            },
            session=session,
            accessed_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
        )
        self.assertEqual([blocked, "https://open.example/article/5"], session.calls)
        self.assertEqual(1, len(successes))
        self.assertEqual(2, len(access))
        self.assertTrue(any("blocked.example" in warning for warning in warnings))

    def test_doi_redirect_does_not_bypass_resolved_domain_cap(self) -> None:
        class RedirectSession(FakeSession):
            def get(self, url: str, **_kwargs: object) -> FakeResponse:
                self.calls.append(url)
                if url.startswith("https://doi.org/"):
                    suffix = url.rsplit("/", 1)[-1]
                    return FakeResponse(url, 302, f"https://same.example/article/{suffix}")
                return FakeResponse(url, 200)

        session = RedirectSession()
        successes, access, _warnings = probe_publisher_pages(
            [candidate(index, domain="doi.org") for index in range(1, 5)],
            {
                "target_min_per_run": 2,
                "hard_max_per_run": 4,
                "per_domain_hard_max": 2,
                "request_delay_seconds": 0,
            },
            session=session,
            accessed_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
        )
        publisher_calls = [url for url in session.calls if "same.example" in url]
        self.assertEqual(2, len(publisher_calls))
        self.assertEqual(2, len(successes))
        self.assertEqual(4, len(access))

    def test_date_only_event_on_cutoff_day_is_excluded(self) -> None:
        end = datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        start = end - timedelta(hours=72)
        cutoff_event = event_record(
            "first_formal_indexing",
            start.date().isoformat(),
            "pubmed",
            "EDAT",
            "https://pubmed.ncbi.nlm.nih.gov/1/",
            "date",
            "provider_metadata",
        )
        next_day_event = dict(cutoff_event, occurred_at=(start.date() + timedelta(days=1)).isoformat())
        self.assertFalse(event_in_window(cutoff_event, start, end, TZ))
        self.assertTrue(event_in_window(next_day_event, start, end, TZ))


if __name__ == "__main__":
    unittest.main()
