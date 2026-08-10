from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.run_github_radar import Candidate, event_record, execute
from tools.validate_gpt_work_artifacts import validate_document
from tools.translation_handoff import (
    TranslationHandoffError,
    request_sha256,
    validate_translation_response,
)


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Tokyo")


def bound_request(*, excerpt: str = "This study evaluates adults using an RCT design.") -> dict:
    request = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_TranslationRequest",
        "run_id": "handoff-unit",
        "created_at": "2026-08-10T12:00:00+09:00",
        "execution_lane": "github_actions",
        "protocol_commit": "a" * 40,
        "base_state_sha256": "b" * 64,
        "window": {
            "start": "2026-08-07T12:00:00+09:00",
            "end": "2026-08-10T12:00:00+09:00",
            "hours": 72,
        },
        "instructions": ["Translate faithfully."],
        "candidates": [
            {
                "immutable_candidate_id": "doi:10.1000/rct.2026",
                "title_en": "RCT outcomes in 2026: Phase 2 study",
                "source_excerpt": excerpt,
                "metadata": {
                    "category": "clinical_medicine",
                    "publication_date": "2026-08-10",
                    "authors": ["A. Author"],
                    "venue": "Journal",
                    "identifiers": {"doi": "10.1000/rct.2026"},
                },
            }
        ],
        "resume_context": {"frozen": True},
    }
    request["request_sha256"] = request_sha256(request)
    return request


def valid_response(request: dict) -> dict:
    return {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_TranslationResponse",
        "request_sha256": request["request_sha256"],
        "items": [
            {
                "immutable_candidate_id": "doi:10.1000/rct.2026",
                "title_zh_tw": "2026 年 RCT 結果指標：第 2 期研究",
                "summary_zh_tw": "本研究以 RCT 設計評估成人。",
            }
        ],
    }


class TranslationHandoffUnitTests(unittest.TestCase):
    def test_valid_response(self) -> None:
        request = bound_request()
        response = valid_response(request)
        request_schema = json.loads((ROOT / "schemas/evidence-radar-translation-request.schema.json").read_text())
        response_schema = json.loads((ROOT / "schemas/evidence-radar-translation-response.schema.json").read_text())
        self.assertEqual([], validate_document(request, request_schema))
        self.assertEqual([], validate_document(response, response_schema))
        result = validate_translation_response(request, response)
        self.assertEqual("2026 年 RCT 結果指標：第 2 期研究", result["doi:10.1000/rct.2026"]["title_zh_tw"])

    def test_missing_extra_and_duplicate_ids_fail(self) -> None:
        request = bound_request()
        missing = valid_response(request)
        missing["items"] = []
        with self.assertRaisesRegex(TranslationHandoffError, "missing"):
            validate_translation_response(request, missing)

        extra = valid_response(request)
        extra["items"][0]["immutable_candidate_id"] = "doi:extra"
        with self.assertRaisesRegex(TranslationHandoffError, "unknown"):
            validate_translation_response(request, extra)

        duplicate = valid_response(request)
        duplicate["items"].append(copy.deepcopy(duplicate["items"][0]))
        with self.assertRaisesRegex(TranslationHandoffError, "duplicate"):
            validate_translation_response(request, duplicate)

    def test_stale_or_modified_request_fails(self) -> None:
        request = bound_request()
        response = valid_response(request)
        stale = copy.deepcopy(response)
        stale["request_sha256"] = "c" * 64
        with self.assertRaisesRegex(TranslationHandoffError, "stale"):
            validate_translation_response(request, stale)

        modified = copy.deepcopy(request)
        modified["candidates"][0]["title_en"] = "Changed title"
        with self.assertRaisesRegex(TranslationHandoffError, "SHA-256 mismatch"):
            validate_translation_response(modified, response)

    def test_title_year_number_abbreviation_filler_and_han_fail_closed(self) -> None:
        request = bound_request()
        cases = (
            ("RCT 結果指標：第 2 期研究", "number/year"),
            ("2026 年結果指標：第 2 期研究", "abbreviation"),
            ("2026 年 RCT 題名所示：第 2 期研究", "filler"),
            ("RCT outcomes in 2026: Phase 2 study", "Traditional Chinese"),
        )
        for title, message in cases:
            response = valid_response(request)
            response["items"][0]["title_zh_tw"] = title
            with self.subTest(title=title):
                with self.assertRaisesRegex(TranslationHandoffError, message):
                    validate_translation_response(request, response)

    def test_empty_excerpt_requires_empty_summary_and_uses_title_only(self) -> None:
        request = bound_request(excerpt="")
        response = valid_response(request)
        response["items"][0]["summary_zh_tw"] = "這是一項研究。"
        with self.assertRaisesRegex(TranslationHandoffError, "must be empty"):
            validate_translation_response(request, response)
        response["items"][0]["summary_zh_tw"] = ""
        self.assertTrue(validate_translation_response(request, response))

    def test_unsupported_result_and_number_claims_fail(self) -> None:
        request = bound_request(excerpt="This study evaluates adults using an RCT design.")
        response = valid_response(request)
        response["items"][0]["summary_zh_tw"] = "結果顯示風險顯著降低 25%。"
        with self.assertRaises(TranslationHandoffError):
            validate_translation_response(request, response)


class TranslationHandoffE2ETests(unittest.TestCase):
    def test_stage_a_does_not_advance_state_and_stage_b_does_not_rediscover(self) -> None:
        end_at = datetime(2026, 8, 10, 12, 0, tzinfo=TZ)
        # Real candidate retained in the canonical 2026-08-10 Radar ledger.
        item = Candidate(
            title="Efficacy and safety of acupuncture treatment for depression: A systematic review and meta-analysis.",
            stream="clinical_medicine",
            category="clinical_medicine",
            source="pubmed",
            publication_date="2026-08-10",
            authors=["Ma J", "Peng M"],
            venue="Medicine",
            doi="10.1097/md.0000000000050133",
            landing_url="https://doi.org/10.1097/md.0000000000050133",
            events=[
                event_record(
                    "version_of_record_first_online",
                    "2026-08-10",
                    "pubmed",
                    "ArticleDate",
                    "https://doi.org/10.1097/md.0000000000050133",
                    "date",
                    "provider_metadata",
                )
            ],
            score=90,
        )

        def discoverer(*_args, **_kwargs):
            return (
                [item],
                [{
                    "query_id": "query-001",
                    "category": "clinical_medicine",
                    "query": "fixture",
                    "searched_at": end_at.isoformat(),
                    "source_ids": ["pubmed"],
                    "status": "SUCCESS",
                    "result_count": 1,
                }],
                [{
                    "source_id": "query-001-pubmed",
                    "provider": "pubmed",
                    "url": "https://pubmed.ncbi.nlm.nih.gov/",
                    "accessed_at": end_at.isoformat(),
                    "status": "SUCCESS",
                    "result_count": 1,
                }],
                {"pubmed"},
                set(),
            )

        def publisher_probe(items, *_args, **_kwargs):
            access = {
                "source_id": "publisher-001",
                "provider": "publisher",
                "work_id": items[0].work_id,
                "url": items[0].landing_url,
                "accessed_at": end_at.isoformat(),
                "status": "SUCCESS",
                "http_status": 200,
                "result_count": 1,
            }
            return [(items[0], access)], [access], []

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            state = temporary / "state" / "EvidenceRadar_State.json"
            output = temporary / "output"
            request_path = temporary / "EvidenceRadar_TranslationRequest.json"
            response_path = temporary / "EvidenceRadar_TranslationResponse.json"
            stage_a = execute(
                root=ROOT,
                output_dir=output,
                state_path=state,
                end_at=end_at,
                run_id="handoff-e2e",
                execution_lane="github_actions",
                protocol_commit="d" * 40,
                discoverer=discoverer,
                publisher_probe=publisher_probe,
                translation_request_path=request_path,
            )
            self.assertEqual("TRANSLATION_REQUIRED", stage_a["run_status"])
            self.assertFalse(state.exists())
            self.assertFalse(output.exists())

            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.write_text(json.dumps({
                "schema_version": "1.0",
                "artifact_type": "EvidenceRadar_TranslationResponse",
                "request_sha256": request["request_sha256"],
                "items": [{
                    "immutable_candidate_id": item.work_id,
                    "title_zh_tw": "針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析",
                    "summary_zh_tw": "",
                }],
            }, ensure_ascii=False), encoding="utf-8")

            def no_rediscovery(*_args, **_kwargs):
                raise AssertionError("Stage B must not repeat discovery")

            stage_b = execute(
                root=ROOT,
                output_dir=output,
                state_path=state,
                end_at=None,
                run_id=None,
                execution_lane="github_actions",
                protocol_commit="d" * 40,
                discoverer=no_rediscovery,
                publisher_probe=no_rediscovery,
                translation_request_path=request_path,
                translation_response_path=response_path,
            )
            run = json.loads((output / "EvidenceRadar_Run.json").read_text(encoding="utf-8"))
            report = (output / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
            self.assertEqual("handoff-e2e", stage_b["run_id"])
            self.assertEqual("針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析", run["candidates"][0]["title_zh_tw"])
            self.assertEqual("CHATBOT_TITLE_ZH_TW", run["candidates"][0]["summary_basis"])
            self.assertIn("CHATBOT_TRANSLATION_HANDOFF_V1", run["notes"])
            self.assertIn("針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析", report)
            self.assertIn("Efficacy and safety of acupuncture treatment for depression", report)
            self.assertTrue(state.is_file())


if __name__ == "__main__":
    unittest.main()
