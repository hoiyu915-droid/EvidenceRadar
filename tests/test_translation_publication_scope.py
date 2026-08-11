"""Regression contract for publication-only zh-TW translation enforcement."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "legacy" / "github-actions" / "daily-radar.yml"


class TranslationPublicationScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_hosted_lane_emits_manual_handoff_request_without_model_provider(self) -> None:
        jobs_index = self.workflow.index("\njobs:\n")
        global_env = self.workflow[:jobs_index]
        self.assertNotIn("EVIDENCERADAR_COPILOT_TRANSLATION", global_env)
        self.assertNotIn("EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION", global_env)
        self.assertNotIn("EVIDENCERADAR_COPILOT_MODEL", global_env)
        self.assertNotIn("EVIDENCERADAR_TRANSLATION_API_KEY", global_env)

        run_start = self.workflow.index("      - name: Run EvidenceRadar GitHub lane")
        verify_start = self.workflow.index("      - name: Upload TranslationRequest", run_start)
        run_block = self.workflow[run_start:verify_start]
        self.assertIn("--translation-request", run_block)
        self.assertIn("TRANSLATION_REQUIRED", run_block)
        self.assertNotIn("copilot", self.workflow.casefold())
        self.assertNotIn("api.openai.com", self.workflow)

    def test_validation_suite_runs_before_publication_mode_step(self) -> None:
        test_index = self.workflow.index("      - name: Validate repository and active-lane tests")
        run_index = self.workflow.index("      - name: Run EvidenceRadar GitHub lane")
        self.assertLess(test_index, run_index)
        validation_block = self.workflow[test_index:run_index]
        self.assertNotIn("EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION", validation_block)
        self.assertNotIn("GITHUB_TOKEN:", validation_block)

    def test_formal_stage_a_validates_but_never_rewrites_runtime_inputs(self) -> None:
        master_commands = [
            line.strip()
            for line in self.workflow.splitlines()
            if line.strip().startswith("python tools/apply_master_")
        ]
        self.assertEqual(2, len(master_commands))
        for command in master_commands:
            self.assertIn("--check", command)
        self.assertGreaterEqual(
            self.workflow.count("git diff --exit-code -- tools config"), 2
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
