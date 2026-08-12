from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from tools.translation_handoff import request_sha256
from tools.validate_gpt_work_artifacts import validate_document
from tools.work_translation_queue import (
    BATCH_RESPONSE_TYPE,
    WorkTranslationQueueError,
    build_batch_plan,
    build_batch_request,
    build_submission,
    empty_checkpoint,
    extract_request_artifact,
    finalize_response,
    merge_batch_response,
    validate_batch_plan,
    validate_checkpoint,
    validate_submission,
)

ROOT = Path(__file__).resolve().parents[1]


def bound_request() -> dict:
    candidates = [
        {
            "immutable_candidate_id": "doi:10.1000/a",
            "title_en": "RCT outcomes in 2026",
            "source_excerpt": "This RCT evaluates 20 adults.",
            "metadata": {
                "category": "clinical_medicine",
                "publication_date": "2026-08-10",
                "authors": ["A. Author"],
                "venue": "Journal A",
                "identifiers": {"doi": "10.1000/a"},
            },
        },
        {
            "immutable_candidate_id": "doi:10.1000/b",
            "title_en": "VO2 response in 12 runners",
            "source_excerpt": "The study examines VO2 in 12 runners.",
            "metadata": {
                "category": "sports_science",
                "publication_date": "2026-08-10",
                "authors": ["B. Author"],
                "venue": "Journal B",
                "identifiers": {"doi": "10.1000/b"},
            },
        },
        {
            "immutable_candidate_id": "doi:10.1000/c",
            "title_en": "AI assisted learning in 2026",
            "source_excerpt": "This study explores AI-assisted learning.",
            "metadata": {
                "category": "education",
                "publication_date": "2026-08-10",
                "authors": ["C. Author"],
                "venue": "Journal C",
                "identifiers": {"doi": "10.1000/c"},
            },
        },
    ]
    request = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_TranslationRequest",
        "run_id": "queue-unit",
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
        "candidates": candidates,
        "resume_context": {"frozen": True},
    }
    request["request_sha256"] = request_sha256(request)
    return request


TRANSLATIONS = {
    "doi:10.1000/a": {
        "title_zh_tw": "2026 年 RCT 結果指標",
        "summary_zh_tw": "此 RCT 評估 20 名成人。",
    },
    "doi:10.1000/b": {
        "title_zh_tw": "12 名跑者的 VO2 反應",
        "summary_zh_tw": "本研究檢視 12 名跑者的 VO2。",
    },
    "doi:10.1000/c": {
        "title_zh_tw": "2026 年 AI 輔助學習",
        "summary_zh_tw": "本研究探索 AI 輔助學習。",
    },
}


def batch_response(request: dict, plan: dict, batch_index: int) -> dict:
    batch = plan["batches"][batch_index - 1]
    return {
        "schema_version": "1.0",
        "artifact_type": BATCH_RESPONSE_TYPE,
        "request_sha256": request["request_sha256"],
        "batch_plan_sha256": plan["batch_plan_sha256"],
        "batch_index": batch_index,
        "batch_id": batch["batch_id"],
        "items": [
            {
                "immutable_candidate_id": candidate_id,
                **TRANSLATIONS[candidate_id],
            }
            for candidate_id in batch["candidate_ids"]
        ],
    }


class WorkTranslationQueueTests(unittest.TestCase):
    def test_plan_is_deterministic_and_preserves_request_order(self) -> None:
        request = bound_request()
        plan = build_batch_plan(request, max_items=2, max_source_chars=10_000)
        self.assertEqual(plan, build_batch_plan(request, max_items=2, max_source_chars=10_000))
        self.assertEqual(2, plan["batch_count"])
        self.assertEqual(
            ["doi:10.1000/a", "doi:10.1000/b"],
            plan["batches"][0]["candidate_ids"],
        )
        self.assertEqual(plan, validate_batch_plan(request, plan))
        batch = build_batch_request(request, plan, batch_index=2)
        self.assertEqual(["doi:10.1000/c"], [item["immutable_candidate_id"] for item in batch["candidates"]])

    def test_checkpoint_accepts_only_complete_validated_batches(self) -> None:
        request = bound_request()
        plan = build_batch_plan(request, max_items=2, max_source_chars=10_000)
        checkpoint = empty_checkpoint(request, plan)
        first = batch_response(request, plan, 1)
        partial = copy.deepcopy(first)
        partial["items"].pop()
        with self.assertRaisesRegex(WorkTranslationQueueError, "every planned candidate"):
            merge_batch_response(request, plan, checkpoint, partial)

        checkpoint = merge_batch_response(request, plan, checkpoint, first)
        self.assertEqual(2, len(checkpoint["items"]))
        self.assertEqual(1, len(checkpoint["completed_batch_ids"]))
        self.assertEqual(checkpoint, validate_checkpoint(request, plan, checkpoint))

        changed = batch_response(request, plan, 1)
        changed["items"][0]["summary_zh_tw"] = "此 RCT 評估 20 名成年參與者。"
        with self.assertRaisesRegex(WorkTranslationQueueError, "cannot be silently replaced"):
            merge_batch_response(request, plan, checkpoint, changed)

    def test_finalize_and_submission_are_sha_bound(self) -> None:
        request = bound_request()
        plan = build_batch_plan(request, max_items=2, max_source_chars=10_000)
        checkpoint = empty_checkpoint(request, plan)
        for batch_index in (1, 2):
            checkpoint = merge_batch_response(
                request,
                plan,
                checkpoint,
                batch_response(request, plan, batch_index),
            )
        response = finalize_response(request, plan, checkpoint)
        submission = build_submission(
            request,
            response,
            repository="hoiyu915-droid/EvidenceRadar",
            artifact_id=123,
            workflow_run_id=456,
            artifact_name="evidenceradar-translation-request-456-1",
            handoff_issue_number=7,
            created_at="2026-08-10T13:00:00+09:00",
        )
        self.assertEqual(submission, validate_submission(request, submission))

        checkpoint_schema = json.loads(
            (ROOT / "schemas/evidence-radar-translation-checkpoint.schema.json").read_text()
        )
        submission_schema = json.loads(
            (ROOT / "schemas/evidence-radar-translation-submission.schema.json").read_text()
        )
        self.assertEqual([], validate_document(checkpoint, checkpoint_schema))
        self.assertEqual([], validate_document(submission, submission_schema))

        stale = copy.deepcopy(submission)
        stale["request_artifact"]["artifact_id"] = 999
        with self.assertRaisesRegex(WorkTranslationQueueError, "SHA-256 mismatch"):
            validate_submission(request, stale)

    def test_incomplete_checkpoint_cannot_finalize(self) -> None:
        request = bound_request()
        plan = build_batch_plan(request, max_items=2, max_source_chars=10_000)
        checkpoint = merge_batch_response(
            request,
            plan,
            empty_checkpoint(request, plan),
            batch_response(request, plan, 1),
        )
        with self.assertRaisesRegex(WorkTranslationQueueError, "incomplete"):
            finalize_response(request, plan, checkpoint)

    def test_actions_artifact_extraction_is_exact_and_safe(self) -> None:
        request = bound_request()
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            good = temporary / "good.zip"
            with ZipFile(good, "w") as archive:
                archive.writestr(
                    "EvidenceRadar_TranslationRequest.json",
                    json.dumps(request, ensure_ascii=False),
                )
            output = temporary / "request.json"
            extract_request_artifact(good, output)
            self.assertEqual(request, json.loads(output.read_text(encoding="utf-8")))

            bad = temporary / "bad.zip"
            with ZipFile(bad, "w") as archive:
                archive.writestr("../EvidenceRadar_TranslationRequest.json", "{}")
            with self.assertRaisesRegex(WorkTranslationQueueError, "exactly"):
                extract_request_artifact(bad, temporary / "bad-request.json")


if __name__ == "__main__":
    unittest.main()
