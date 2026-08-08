"""Text-level contract tests for the optional GitHub Actions deployment lane."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-radar.yml"
DOC = ROOT / "docs" / "GITHUB_DEPLOYMENT.md"


class GithubDeploymentSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.documentation = DOC.read_text(encoding="utf-8")

    def test_daily_schedule_uses_tokyo_non_top_of_hour(self) -> None:
        self.assertRegex(self.workflow, r"schedule:\s*\n\s*- cron:\s*[\"']17 6 \* \* \*[\"']")
        self.assertRegex(self.workflow, r"cron:\s*[\"']17 6 \* \* \*[\"']\s*\n\s*timezone:\s*[\"']Asia/Tokyo[\"']")

    def test_manual_dispatch_exposes_ten_to_fifteen_inputs(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        target_start = self.workflow.index("      publisher_target_min:")
        target_end = self.workflow.index("\n      publisher_hard_max:", target_start)
        target_block = self.workflow[target_start:target_end]
        hard_max_start = target_end + 1
        hard_max_end = self.workflow.index("\n\n# The optional", hard_max_start)
        hard_max_block = self.workflow[hard_max_start:hard_max_end]
        self.assertIn("default: 10", target_block)
        self.assertIn("default: 15", hard_max_block)
        self.assertIn("type: number", self.workflow)

    def test_write_permission_and_non_cancelling_concurrency_are_explicit(self) -> None:
        self.assertRegex(self.workflow, r"permissions:\s*\n\s+contents:\s+write")
        self.assertIn("concurrency:", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_runner_command_and_layout_are_centralized(self) -> None:
        for variable, value in (
            ("RADAR_OUTPUT_DIR", "artifacts/current"),
            ("RADAR_REPORT_PATH", "artifacts/current/EvidenceRadar_Report.html"),
            ("RADAR_STATE_PATH", "state/current/EvidenceRadar_State.json"),
            ("RADAR_EVIDENCE_PATH", "artifacts/current/EvidenceRadar_Evidence.json"),
            ("RADAR_RUN_PATH", "artifacts/current/EvidenceRadar_Run.json"),
            ("RADAR_RUNS_DIR", "runs"),
        ):
            self.assertIn(f'{variable}: "{value}"', self.workflow)
        self.assertIn("python tools/run_github_radar.py", self.workflow)
        for option in (
            "--root \"$RADAR_ROOT\"",
            "--output-dir \"$RADAR_OUTPUT_DIR\"",
            "--state \"$RADAR_STATE_PATH\"",
            "--runs-dir \"$RADAR_RUNS_DIR\"",
            "--execution-lane \"$EXECUTION_LANE\"",
            "--publisher-target-min \"$PUBLISHER_TARGET_MIN\"",
            "--publisher-hard-max \"$PUBLISHER_HARD_MAX\"",
        ):
            self.assertIn(option, self.workflow)

    def test_maintenance_and_runtime_tests_are_run(self) -> None:
        self.assertIn("pip install -r requirements.txt", self.workflow)
        self.assertIn("python tools/validate_public_release.py", self.workflow)
        self.assertIn("python -m unittest discover -s tests -v", self.workflow)
        self.assertIn("working-directory: legacy/python-runtime", self.workflow)
        self.assertIn("run: python -m pytest -q tests", self.workflow)

    def test_provider_configuration_is_mapped_without_literal_credentials(self) -> None:
        self.assertIn("OPENALEX_API_KEY: ${{ secrets.OPENALEX_API_KEY }}", self.workflow)
        self.assertIn("NCBI_EMAIL: ${{ vars.NCBI_EMAIL }}", self.workflow)
        self.assertIn("NCBI_API_KEY: ${{ secrets.NCBI_API_KEY }}", self.workflow)

    def test_four_artifacts_are_checked_validated_and_uploaded(self) -> None:
        for artifact in (
            "EvidenceRadar_Report.html",
            "EvidenceRadar_State.json",
            "EvidenceRadar_Evidence.json",
            "EvidenceRadar_Run.json",
        ):
            self.assertIn(artifact, self.workflow)
        self.assertIn("Verify four EvidenceRadar artifacts", self.workflow)
        self.assertIn("python tools/validate_gpt_work_artifacts.py", self.workflow)
        self.assertIn("uses: actions/upload-artifact@v4", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)
        self.assertIn("cmp --silent", self.workflow)

    def test_writeback_stages_only_generated_paths_and_handles_noop(self) -> None:
        self.assertIn('git add -- "$RADAR_OUTPUT_DIR" "$RADAR_STATE_PATH"', self.workflow)
        self.assertIn('git add -- "$RADAR_RUNS_DIR"', self.workflow)
        self.assertNotRegex(self.workflow, r"git\s+add\s+(?:--all|-A|\.)\b")
        self.assertIn("git diff --cached --quiet", self.workflow)
        self.assertIn("No generated changes; safe exit.", self.workflow)
        self.assertIn("git pull --rebase origin", self.workflow)
        self.assertIn("git push origin", self.workflow)

    def test_documentation_covers_template_secrets_state_and_work_boundary(self) -> None:
        for marker in (
            "Use this template",
            "fork",
            "Actions",
            "Secrets",
            "OPENALEX_API_KEY",
            "NCBI_EMAIL",
            "NCBI_API_KEY",
            "contents: write",
            "60 天",
            "ChatGPT Work",
            "EvidenceRadar_State.json",
            "publisher_target_min",
            "publisher_hard_max",
        ):
            self.assertIn(marker, self.documentation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
