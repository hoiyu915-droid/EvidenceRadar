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
    DiscoveryResult,
    RadarRuntimeError,
    _request,
    candidate_content_summary,
    candidate_source_excerpt,
    event_in_window,
    event_record,
    execute,
    discover_candidates,
    fetch_openalex,
    fetch_pubmed,
    load_prior_state,
    probe_publisher_pages,
    state_sha256,
    translate_candidate_summaries_zh_tw,
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
    def test_discovery_checks_every_configured_discovery_source(self) -> None:
        configured = {
            "pubmed",
            "europe_pmc",
            "openalex",
            "arxiv",
            "openreview",
            "acl_anthology",
            "pmlr",
        }
        streams = {
            "candidate_guidance": {"suggested_max_per_query": 2},
            "streams": {
                "all_sources": {
                    "sources": sorted(configured),
                    "queries": ["fixture"],
                    "relevance_terms": ["fixture"],
                }
            },
        }
        scoring = {
            "categories": {"llm_research": {"streams": ["all_sources"]}},
            "category_min_relevance": {"llm_research": 0},
        }
        patches = {
            source: mock.patch(
                f"tools.run_github_radar.fetch_{source}", return_value=[]
            )
            for source in configured
        }
        mocks = {source: patcher.start() for source, patcher in patches.items()}
        self.addCleanup(lambda: [patcher.stop() for patcher in patches.values()])

        result = discover_candidates(
            streams,
            scoring,
            datetime(2026, 8, 6, 12, 0, tzinfo=TZ),
            datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
            session=FakeSession(),
        )

        self.assertEqual(configured, result.checked_sources)
        self.assertEqual(configured, result.searched_sources)
        self.assertEqual(set(), result.unavailable_sources)
        self.assertEqual(7, len(result.queries))
        self.assertEqual({"NO_RESULTS"}, {item["status"] for item in result.queries})
        self.assertEqual(configured, {item["provider"] for item in result.source_access})
        for fetcher in mocks.values():
            fetcher.assert_called_once()

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

    def test_provider_failure_opens_same_run_circuit_without_losing_checks(self) -> None:
        streams = {
            "candidate_guidance": {"suggested_max_per_query": 2},
            "streams": {
                "llm_stream": {
                    "sources": ["openalex"],
                    "queries": ["first", "second"],
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
            side_effect=RadarRuntimeError("provider timeout"),
        ) as fetcher:
            result = discover_candidates(
                streams,
                scoring,
                datetime(2026, 8, 6, 12, 0, tzinfo=TZ),
                datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
                session=FakeSession(),
            )

        fetcher.assert_called_once()
        self.assertEqual(["FAILED", "NOT_ATTEMPTED"], [item["status"] for item in result.queries])
        self.assertEqual({"openalex"}, result.checked_sources)
        self.assertEqual({"openalex"}, result.unavailable_sources)

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
            self.assertNotIn("AUTOMATED_SOURCE_ADAPTER_GAP", warning_codes)
            self.assertIn("SOURCE_ADAPTER_FAILED", warning_codes)
            self.assertTrue(run["source_coverage"]["all_configured_sources_checked"])
            self.assertEqual(
                set(run["source_coverage"]["requested"]),
                set(run["source_coverage"]["checked"]),
            )
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
            second_run = json.loads((second_output / "EvidenceRadar_Run.json").read_text())
            self.assertEqual(0, second["publisher_output"])
            self.assertEqual([], probe_inputs[-1])
            verification_checks = {
                check["source_id"]: check["status"]
                for check in second_run["source_coverage"]["checks"]
                if check["stage"] == "bounded_verification"
            }
            self.assertEqual(
                {
                    "formal_proceedings_or_publisher": "NOT_ATTEMPTED",
                    "publisher": "NOT_ATTEMPTED",
                },
                verification_checks,
            )
            self.assertEqual(1, len(second_state["notified_events"]))
            self.assertEqual(2, second_state["works"][0]["seen_count"])
            self.assertEqual(["github-actions-first"], second_state["parent_run_ids"])
            self.assertNotEqual("0" * 64, second_state["base_state_sha256"])

    def test_candidate_display_and_state_are_not_capped_by_publisher_budget(self) -> None:
        end_at = datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
        items = [candidate(index) for index in range(1, 161)]

        def discoverer(*_args: object, **_kwargs: object):
            return (
                items,
                [
                    {
                        "query_id": "query-001",
                        "category": "clinical_medicine",
                        "query": "fixture query",
                        "searched_at": end_at.isoformat(),
                        "source_ids": ["pubmed"],
                        "status": "SUCCESS",
                        "result_count": 160,
                    }
                ],
                [
                    {
                        "source_id": "query-001-pubmed",
                        "url": "https://pubmed.ncbi.nlm.nih.gov/",
                        "accessed_at": end_at.isoformat(),
                        "status": "SUCCESS",
                        "result_count": 160,
                    }
                ],
                {"pubmed"},
                set(),
            )

        def fast_publisher_probe(items, config, **kwargs):
            return probe_publisher_pages(items, config, sleep=lambda _seconds: None, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            output = temporary / "output"
            state_path = temporary / "state" / "EvidenceRadar_State.json"
            summary = execute(
                root=ROOT,
                output_dir=output,
                state_path=state_path,
                end_at=end_at,
                run_id="github-actions-display-all",
                execution_lane="github_actions",
                protocol_commit="c" * 40,
                session=FakeSession(),
                discoverer=discoverer,
                publisher_probe=fast_publisher_probe,
                publisher_target_min=10,
                publisher_hard_max=15,
            )

            run = json.loads((output / "EvidenceRadar_Run.json").read_text())
            state = json.loads((output / "EvidenceRadar_State.json").read_text())
            evidence = json.loads((output / "EvidenceRadar_Evidence.json").read_text())
            report = (output / "EvidenceRadar_Report.html").read_text()

        self.assertEqual(160, summary["candidate_ledger"])
        self.assertEqual(160, summary["displayed_candidates"])
        self.assertEqual(15, summary["publisher_attempted"])
        self.assertEqual(160, len(run["candidates"]))
        self.assertEqual(160, run["counts"]["displayed_candidates"])
        self.assertEqual(15, run["counts"]["publisher_attempted"])
        self.assertEqual(15, run["counts"]["publisher_accessible"])
        self.assertEqual(145, run["counts"]["publisher_not_attempted"])
        self.assertEqual(160, len(state["works"]))
        self.assertEqual(15, len(state["notified_events"]))
        self.assertEqual(15, len(evidence["works"]))
        self.assertTrue(all(item["content_summary"] for item in run["candidates"]))
        self.assertEqual(
            {"TITLE_ONLY_ZH_TW"},
            {item["summary_basis"] for item in run["candidates"]},
        )
        self.assertEqual({"zh-TW"}, {item["summary_language"] for item in run["candidates"]})
        self.assertEqual(160, report.count('class="content-preview"'))
        self.assertIn('id="candidate-search"', report)
        self.assertIn('id="category-filter"', report)
        self.assertIn('id="triage-filter"', report)
        self.assertIn('id="source-filter"', report)
        self.assertIn("Auditable Evidence Candidate 160", report)
        self.assertIn("候選顯示不受 publisher 10–15 探測額度限制", report)

    def test_candidate_content_summary_is_zh_tw_and_source_excerpt_is_bounded(self) -> None:
        item = candidate(1)
        item.abstract = (
            "Background context is important but generic. "
            "This study evaluates a focused intervention and its measured outcomes. "
            "Additional implementation details follow for readers."
        )
        excerpt = candidate_source_excerpt(item, max_chars=120)
        self.assertTrue(excerpt.startswith("This study evaluates"))
        self.assertLessEqual(len(excerpt), 120)

        summary, basis = candidate_content_summary(item, max_chars=120)
        self.assertEqual("ZH_TW_METADATA_TEMPLATE", basis)
        self.assertIn("這篇", summary)
        self.assertNotIn("This study evaluates", summary)
        self.assertLessEqual(len(summary), 120)

        item.abstract = ""
        fallback, fallback_basis = candidate_content_summary(item, max_chars=180)
        self.assertEqual("TITLE_ONLY_ZH_TW", fallback_basis)
        self.assertIn("來源未提供摘要", fallback)

    def test_summary_translation_uses_zh_tw_and_preserves_numbers(self) -> None:
        item = candidate(1)
        item.abstract = "This study evaluates a 10 mg intervention for depression."
        translated_payload = {
            "summaries": [
                {
                    "id": item.work_id,
                    "summary": "本研究評估 10 mg 介入措施對憂鬱症的影響。",
                }
            ]
        }
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(translated_payload, ensure_ascii=False),
                        }
                    ]
                }
            ]
        }
        session = mock.Mock()
        session.post.return_value = response
        summaries, warnings = translate_candidate_summaries_zh_tw(
            [item],
            rendering={
                "candidate_summary_max_chars": 160,
                "summary_translation": {
                    "enabled": True,
                    "provider": "openai_responses",
                    "api_key_env": "TEST_TRANSLATION_KEY",
                    "batch_size": 20,
                    "timeout_seconds": 30,
                },
            },
            session=session,
            environ={"TEST_TRANSLATION_KEY": "fixture-secret"},
        )

        self.assertEqual([], warnings)
        self.assertEqual(
            "TRANSLATED_ABSTRACT_EXCERPT_ZH_TW",
            summaries[item.work_id][1],
        )
        self.assertIn("10 mg", summaries[item.work_id][0])

    def test_summary_translation_without_key_fails_closed_to_chinese(self) -> None:
        item = candidate(1)
        item.abstract = "This study evaluates a focused intervention."
        session = mock.Mock()
        summaries, warnings = translate_candidate_summaries_zh_tw(
            [item],
            rendering={
                "candidate_summary_max_chars": 160,
                "summary_translation": {
                    "enabled": True,
                    "provider": "openai_responses",
                    "api_key_env": "TEST_TRANSLATION_KEY",
                },
            },
            session=session,
            environ={},
        )

        summary, basis = summaries[item.work_id]
        self.assertEqual("ZH_TW_METADATA_TEMPLATE", basis)
        self.assertIn("這篇", summary)
        self.assertNotIn("This study", summary)
        self.assertEqual("SUMMARY_TRANSLATION_NOT_CONFIGURED", warnings[0]["code"])
        session.post.assert_not_called()

    def test_discovery_retains_lower_priority_and_counts_before_deduplication(self) -> None:
        streams = {
            "candidate_guidance": {"suggested_max_per_query": 2},
            "streams": {
                "clinical_medicine": {
                    "sources": ["pubmed"],
                    "queries": ["first", "second"],
                    "relevance_terms": ["candidate"],
                }
            },
        }
        scoring = {
            "categories": {"clinical_medicine": {"streams": ["clinical_medicine"]}},
            "category_min_relevance": {"clinical_medicine": 100},
        }
        first = candidate(1)
        first.title = "Protocol candidate retained for review"
        second = candidate(1)
        second.title = first.title
        with mock.patch(
            "tools.run_github_radar.fetch_pubmed",
            side_effect=[[first], [second]],
        ):
            result = discover_candidates(
                streams,
                scoring,
                datetime(2026, 8, 6, 12, 0, tzinfo=TZ),
                datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
                session=FakeSession(),
            )

        self.assertIsInstance(result, DiscoveryResult)
        self.assertEqual(2, result.raw_candidate_count)
        self.assertEqual(1, len(result.all_candidates))
        self.assertEqual(0, len(result.priority_candidates))
        self.assertEqual("LOWER_PRIORITY", result.all_candidates[0].triage_status)
        self.assertEqual(["query-001", "query-002"], result.all_candidates[0].query_ids)

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
        for item in items:
            item.doi = ""
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

    def test_publisher_url_prefers_formal_doi_over_discovery_landing_page(self) -> None:
        item = candidate(1, domain="repository.example")
        self.assertEqual("https://doi.org/10.1000/example.1", item.publisher_url())

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
