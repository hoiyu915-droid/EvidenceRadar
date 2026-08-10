"""Regression contract for publication-only zh-TW translation enforcement."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-radar.yml"


class TranslationPublicationScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_hard_gate_is_scoped_to_real_producer_step(self) -> None:
        jobs_index = self.workflow.index("\njobs:\n")
        global_env = self.workflow[:jobs_index]
        self.assertNotIn("EVIDENCERADAR_COPILOT_TRANSLATION", global_env)
        self.assertNotIn("EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION", global_env)
        self.assertNotIn("EVIDENCERADAR_COPILOT_MODEL", global_env)

        run_start = self.workflow.index("      - name: Run EvidenceRadar GitHub lane")
        verify_start = self.workflow.index("      - name: Verify four EvidenceRadar artifacts", run_start)
        run_block = self.workflow[run_start:verify_start]
        self.assertIn('GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}', run_block)
        self.assertIn('EVIDENCERADAR_COPILOT_TRANSLATION: "1"', run_block)
        self.assertIn('EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION: "1"', run_block)
        self.assertIn("EVIDENCERADAR_COPILOT_MODEL:", run_block)

    def test_validation_suite_runs_before_publication_mode_step(self) -> None:
        test_index = self.workflow.index("      - name: Validate repository and active-lane tests")
        run_index = self.workflow.index("      - name: Run EvidenceRadar GitHub lane")
        self.assertLess(test_index, run_index)
        validation_block = self.workflow[test_index:run_index]
        self.assertNotIn("EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION", validation_block)
        self.assertNotIn("GITHUB_TOKEN:", validation_block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
