from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import tools.run_github_radar as radar_runtime
from tools.run_github_radar import Candidate, RadarRuntimeError, event_record, execute
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
                publisher_target_min=3,
                publisher_hard_max=5,
                protocol_commit="d" * 40,
                discoverer=discoverer,
                publisher_probe=publisher_probe,
                translation_request_path=request_path,
            )
            self.assertEqual("TRANSLATION_REQUIRED", stage_a["run_status"])
            self.assertFalse(state.exists())
            self.assertFalse(output.exists())

            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual("owner_daily", request["resume_context"]["profile_id"])
            self.assertEqual(
                {"target_min": 3, "hard_max": 5},
                request["resume_context"]["publisher_limits"],
            )
            self.assertEqual(
                hashlib.sha256(
                    (ROOT / "config" / "radar_master.json").read_bytes()
                ).hexdigest(),
                request["resume_context"]["master_control_sha256"],
            )
            self.assertEqual(
                sorted(set(request["resume_context"]["resolved_stream_ids"])),
                request["resume_context"]["resolved_stream_ids"],
            )
            self.assertEqual(
                sorted(set(request["resume_context"]["resolved_source_ids"])),
                request["resume_context"]["resolved_source_ids"],
            )
            response = {
                "schema_version": "1.0",
                "artifact_type": "EvidenceRadar_TranslationResponse",
                "request_sha256": request["request_sha256"],
                "items": [{
                    "immutable_candidate_id": item.work_id,
                    "title_zh_tw": "針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析",
                    "summary_zh_tw": "",
                }],
            }
            response_path.write_text(
                json.dumps(response, ensure_ascii=False), encoding="utf-8"
            )

            def no_rediscovery(*_args, **_kwargs):
                raise AssertionError("Stage B must not repeat discovery")

            def write_tampered_request(label, mutate):
                tampered_request = copy.deepcopy(request)
                mutate(tampered_request)
                tampered_request["request_sha256"] = request_sha256(
                    tampered_request
                )
                tampered_response = copy.deepcopy(response)
                tampered_response["request_sha256"] = tampered_request[
                    "request_sha256"
                ]
                tampered_request_path = temporary / f"request-{label}.json"
                tampered_response_path = temporary / f"response-{label}.json"
                tampered_request_path.write_text(
                    json.dumps(tampered_request, ensure_ascii=False),
                    encoding="utf-8",
                )
                tampered_response_path.write_text(
                    json.dumps(tampered_response, ensure_ascii=False),
                    encoding="utf-8",
                )
                return tampered_request_path, tampered_response_path

            tampered_cases = (
                (
                    "master-hash",
                    lambda value: value["resume_context"].__setitem__(
                        "master_control_sha256", "0" * 64
                    ),
                    "master control SHA-256 mismatch",
                ),
                (
                    "resolved-streams",
                    lambda value: value["resume_context"].__setitem__(
                        "resolved_stream_ids",
                        value["resume_context"]["resolved_stream_ids"][:-1],
                    ),
                    "resolved stream IDs mismatch",
                ),
                (
                    "resolved-sources",
                    lambda value: value["resume_context"].__setitem__(
                        "resolved_source_ids",
                        value["resume_context"]["resolved_source_ids"][:-1],
                    ),
                    "resolved source IDs mismatch",
                ),
                (
                    "publisher-limit-type",
                    lambda value: value["resume_context"]["publisher_limits"].__setitem__(
                        "target_min", "3"
                    ),
                    "publisher limits are invalid",
                ),
                (
                    "window-hours",
                    lambda value: value["window"].__setitem__("hours", 71),
                    "duration disagrees with hours",
                ),
                (
                    "window-start",
                    lambda value: value["window"].__setitem__(
                        "start",
                        (
                            datetime.fromisoformat(value["window"]["start"])
                            + timedelta(hours=1)
                        ).isoformat(),
                    ),
                    "duration disagrees with hours",
                ),
                (
                    "window-hours-zero",
                    lambda value: value["window"].__setitem__("hours", 0),
                    "hours must be a positive integer",
                ),
                (
                    "window-naive-start",
                    lambda value: value["window"].__setitem__(
                        "start",
                        datetime.fromisoformat(
                            value["window"]["start"]
                        ).replace(tzinfo=None).isoformat(),
                    ),
                    "start/end must be timezone-aware",
                ),
            )
            for label, mutate, expected_error in tampered_cases:
                tampered_request_path, tampered_response_path = (
                    write_tampered_request(label, mutate)
                )
                with self.subTest(tamper=label), self.assertRaisesRegex(
                    RadarRuntimeError, expected_error
                ):
                    execute(
                        root=ROOT,
                        output_dir=output,
                        state_path=state,
                        end_at=None,
                        run_id=None,
                        execution_lane="github_actions",
                        protocol_commit="d" * 40,
                        discoverer=no_rediscovery,
                        publisher_probe=no_rediscovery,
                        translation_request_path=tampered_request_path,
                        translation_response_path=tampered_response_path,
                    )

            with self.assertRaisesRegex(RadarRuntimeError, "publisher target mismatch"):
                execute(
                    root=ROOT,
                    output_dir=output,
                    state_path=state,
                    end_at=None,
                    run_id=None,
                    execution_lane="github_actions",
                    publisher_target_min=4,
                    protocol_commit="d" * 40,
                    discoverer=no_rediscovery,
                    publisher_probe=no_rediscovery,
                    translation_request_path=request_path,
                    translation_response_path=response_path,
                )
            with self.assertRaisesRegex(RadarRuntimeError, "profile mismatch"):
                execute(
                    root=ROOT,
                    output_dir=output,
                    state_path=state,
                    end_at=None,
                    run_id=None,
                    execution_lane="github_actions",
                    protocol_commit="d" * 40,
                    profile_id="medicine_reader",
                    discoverer=no_rediscovery,
                    publisher_probe=no_rediscovery,
                    translation_request_path=request_path,
                    translation_response_path=response_path,
                )

            state.parent.mkdir(parents=True)
            state.write_bytes((ROOT / "examples" / "EvidenceRadar_State.json").read_bytes())
            with self.assertRaisesRegex(RadarRuntimeError, "canonical State changed"):
                execute(
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
            state.unlink()

            original_load_yaml = radar_runtime.load_yaml

            def load_yaml_with_window_drift(path):
                value = original_load_yaml(path)
                if Path(path).name == "output.yml":
                    value = copy.deepcopy(value)
                    value.setdefault("window", {})["rolling_hours"] = 24
                return value

            with patch.object(
                radar_runtime,
                "load_yaml",
                side_effect=load_yaml_with_window_drift,
            ):
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
            final_state = json.loads(state.read_text(encoding="utf-8"))
            report = (output / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
            self.assertEqual("handoff-e2e", stage_b["run_id"])
            self.assertEqual("針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析", run["candidates"][0]["title_zh_tw"])
            self.assertEqual("CHATBOT_TITLE_ZH_TW", run["candidates"][0]["summary_basis"])
            self.assertIn("CHATBOT_TRANSLATION_HANDOFF_V1", run["notes"])
            self.assertEqual(3, run["rendering"]["publisher_target_min"])
            self.assertEqual(5, run["rendering"]["publisher_hard_max"])
            self.assertEqual(request["window"], run["window"])
            self.assertEqual("owner_daily", final_state["profile_id"])
            self.assertEqual(
                sorted(run["source_coverage"]["requested"]),
                final_state["resolved_source_ids"],
            )
            self.assertEqual(
                request["request_sha256"], final_state["runtime_request_sha256"]
            )
            self.assertEqual(
                hashlib.sha256(
                    (ROOT / "config" / "radar_master.json").read_bytes()
                ).hexdigest(),
                final_state["master_control_sha256"],
            )
            self.assertIn("針灸治療憂鬱症的療效與安全性：系統性回顧與統合分析", report)
            self.assertIn("Efficacy and safety of acupuncture treatment for depression", report)
            self.assertTrue(state.is_file())


if __name__ == "__main__":
    unittest.main()
