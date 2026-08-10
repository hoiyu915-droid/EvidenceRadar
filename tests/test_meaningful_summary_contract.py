from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import requests

from tools.run_github_radar import (
    Candidate,
    RadarRuntimeError,
    classify_publication,
    translate_candidate_summaries_zh_tw,
)


class _Completed:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class MeaningfulSummaryContractTests(unittest.TestCase):
    def _candidate(self, *, abstract: str = "") -> Candidate:
        return Candidate(
            title="Time trends in stroke incidence and modifiable risk factor prevalence in England (2018-2024): A record linkage population-based study",
            stream="clinical",
            category="clinical_medicine",
            source="Europe PMC",
            publication_date="2026-08-07",
            pmid="12345678",
            abstract=abstract,
        )

    def _rendering(self) -> dict[str, object]:
        return {
            "candidate_summary_max_chars": 320,
            "summary_translation": {
                "enabled": True,
                "batch_size": 20,
                "timeout_seconds": 90,
                "api_key_env": "EVIDENCERADAR_TRANSLATION_API_KEY",
                "model_env": "EVIDENCERADAR_TRANSLATION_MODEL",
                "default_model": "gpt-5-mini",
            },
        }

    @patch("tools.run_github_radar.subprocess.run")
    def test_copilot_fallback_must_translate_title(self, run) -> None:
        candidate = self._candidate()
        run.return_value = _Completed(json.dumps({
            "items": [{
                "id": candidate.work_id,
                "title_zh_tw": "英格蘭 2018-2024 年中風發生率與可改變危險因子盛行趨勢：一項以病歷連結為基礎的全人口研究",
                "summary_zh_tw": "",
            }]
        }, ensure_ascii=False))
        summaries, warnings = translate_candidate_summaries_zh_tw(
            [candidate],
            rendering=self._rendering(),
            session=requests.Session(),
            environ={"EVIDENCERADAR_COPILOT_TRANSLATION": "1", "GITHUB_TOKEN": "test"},
        )
        text, basis = summaries[candidate.work_id]
        self.assertIn("中文題名：英格蘭 2018-2024 年中風發生率", text)
        self.assertNotIn("題名所示", text)
        self.assertEqual(basis, "TRANSLATED_TITLE_ZH_TW_COPILOT")
        self.assertEqual(warnings, [])

    @patch("tools.run_github_radar.subprocess.run")
    def test_filler_translation_fails_closed(self, run) -> None:
        candidate = self._candidate()
        run.return_value = _Completed(json.dumps({
            "items": [{
                "id": candidate.work_id,
                "title_zh_tw": "這篇研究探討題名所示的研究問題",
                "summary_zh_tw": "",
            }]
        }, ensure_ascii=False))
        with self.assertRaises(RadarRuntimeError):
            translate_candidate_summaries_zh_tw(
                [candidate],
                rendering=self._rendering(),
                session=requests.Session(),
                environ={
                    "EVIDENCERADAR_COPILOT_TRANSLATION": "1",
                    "EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION": "1",
                    "GITHUB_TOKEN": "test",
                },
            )

    def test_no_translation_provider_fails_closed(self) -> None:
        with self.assertRaises(RadarRuntimeError):
            translate_candidate_summaries_zh_tw(
                [self._candidate()],
                rendering=self._rendering(),
                session=requests.Session(),
                environ={"EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION": "1"},
            )

    def test_trial_design_paper_does_not_get_results_rct_badge(self) -> None:
        value = classify_publication(
            title=(
                "Conversational artificial intelligence health support in atrial fibrillation "
                "self-management (CHAT-AF-S): rationale and randomised controlled trial design"
            ),
            source="PubMed",
            is_preprint=False,
            provider_publication_types=["Journal Article", "Randomized Controlled Trial"],
        )
        self.assertNotIn("randomized_controlled_trial", value["study_designs"])


if __name__ == "__main__":
    unittest.main()
