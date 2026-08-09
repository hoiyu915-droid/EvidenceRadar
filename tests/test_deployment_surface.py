"""Text-level contract tests for the optional GitHub Actions deployment lane."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-radar.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
PUBLIC_RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "public-release.yml"
DOC = ROOT / "docs" / "GITHUB_DEPLOYMENT.md"


class GithubDeploymentSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.pages_workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
        cls.public_release_workflow = PUBLIC_RELEASE_WORKFLOW.read_text(encoding="utf-8")
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
        self.assertIn("cas_retry:", self.workflow)
        self.assertIn("default: false", self.workflow)
        self.assertIn("type: boolean", self.workflow)

    def test_write_permission_and_non_cancelling_concurrency_are_explicit(self) -> None:
        self.assertRegex(self.workflow, r"permissions:\s*\n(?:\s+\w+:\s+\w+\s*\n)+\s+contents:\s+write")
        self.assertIn("actions: write", self.workflow)
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
        self.assertIn(
            "EVIDENCERADAR_TRANSLATION_API_KEY: ${{ secrets.EVIDENCERADAR_TRANSLATION_API_KEY }}",
            self.workflow,
        )
        self.assertNotIn("Bearer ", self.workflow)

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
        self.assertIn("python tools/validate_delivery_bundle.py", self.workflow)
        self.assertIn('--expected-protocol-commit "$GITHUB_SHA"', self.workflow)
        self.assertIn("--require-current-producer", self.workflow)
        self.assertIn("uses: actions/upload-artifact@v7.0.1", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)
        self.assertIn("cmp --silent", self.workflow)

    def test_run_delivery_is_unique_and_uploaded_before_writeback(self) -> None:
        self.assertIn("RADAR_DELIVERY_OUTPUT_DIR", self.workflow)
        self.assertIn("$RUNNER_TEMP/evidenceradar-work-delivery-", self.workflow)
        self.assertNotIn("RADAR_DELIVERY_OUTPUT_DIR: ${{ runner.temp }}", self.workflow)
        self.assertIn("tools/package_work_delivery.py", self.workflow)
        self.assertIn("--source-dir \"$source_dir\"", self.workflow)
        self.assertIn("--run-id \"$run_id\"", self.workflow)
        self.assertIn("EvidenceRadar-WorkRun-$run_id.zip", self.workflow)
        self.assertIn("EvidenceRadar-WorkRun-$run_id.zip.sha256", self.workflow)
        package_index = self.workflow.index("Package immutable run delivery")
        upload_index = self.workflow.index("Upload the run artifacts")
        writeback_index = self.workflow.index("Commit generated artifacts and state")
        self.assertLess(package_index, upload_index)
        self.assertLess(upload_index, writeback_index)
        self.assertIn("github.run_attempt", self.workflow)

    def test_cas_conflict_has_one_retry_and_never_greenlights_stale_writeback(self) -> None:
        conflict_start = self.workflow.index('if [ "$remote_sha" != "$GITHUB_SHA" ]')
        conflict_block = self.workflow[conflict_start:]
        self.assertIn('if [ "$CAS_RETRY" = "true" ]', conflict_block)
        self.assertIn("gh workflow run daily-radar.yml", conflict_block)
        self.assertIn("-f publisher_target_min=", conflict_block)
        self.assertIn("-f publisher_hard_max=", conflict_block)
        self.assertIn("-f cas_retry=true", conflict_block)
        self.assertIn("same publisher budget", conflict_block)
        self.assertIn("exit 1", conflict_block)
        self.assertNotIn("exit 0", conflict_block)

    def test_writeback_stages_only_generated_paths_and_handles_noop(self) -> None:
        self.assertIn('git add -- "$RADAR_OUTPUT_DIR" "$RADAR_STATE_PATH"', self.workflow)
        self.assertIn('git add -- "$RADAR_RUNS_DIR"', self.workflow)
        self.assertNotRegex(self.workflow, r"git\s+add\s+(?:--all|-A|\.)\b")
        self.assertIn("git diff --cached --quiet", self.workflow)
        self.assertIn("No generated changes; safe exit.", self.workflow)
        self.assertNotIn("git pull --rebase origin", self.workflow)
        self.assertIn('remote_sha="$(git rev-parse "origin/$GITHUB_REF_NAME")"', self.workflow)
        self.assertIn('if [ "$remote_sha" != "$GITHUB_SHA" ]', self.workflow)
        self.assertIn("generated bundle was not published", self.workflow)
        self.assertIn("No stale State was rebased or pushed", self.workflow)
        self.assertIn("git push origin", self.workflow)

    def test_successful_token_writeback_explicitly_dispatches_validation_and_pages(self) -> None:
        push_index = self.workflow.index('git push origin "HEAD:$GITHUB_REF_NAME"')
        release_index = self.workflow.index("gh workflow run public-release.yml", push_index)
        pages_index = self.workflow.index("gh workflow run pages.yml", release_index)
        self.assertLess(push_index, release_index)
        self.assertLess(release_index, pages_index)
        self.assertIn('--ref "$GITHUB_REF_NAME"', self.workflow[release_index:])
        self.assertIn("workflow_dispatch:", self.public_release_workflow)

    def test_public_release_validates_the_canonical_current_bundle(self) -> None:
        self.assertIn("fetch-depth: 0", self.public_release_workflow)
        self.assertIn("Validate canonical delivery bundle", self.public_release_workflow)
        self.assertIn("--bundle artifacts/current", self.public_release_workflow)
        self.assertIn("--canonical-state state/current/EvidenceRadar_State.json", self.public_release_workflow)
        self.assertIn("--require-current-producer", self.public_release_workflow)

    def test_pages_deploys_only_a_validated_bundle_and_emits_links(self) -> None:
        for marker in (
            "permissions:",
            "pages: write",
            "id-token: write",
            "actions/configure-pages@v6.0.0",
            "Fail closed if GitHub Pages is not enabled",
            "gh api \"repos/${GITHUB_REPOSITORY}/pages\"",
            'build_type\" != \"workflow',
            "python tools/build_pages_site.py",
            "--bundle artifacts/current",
            "actions/upload-pages-artifact@v5.0.0",
            "actions/deploy-pages@v5.0.0",
            "links.json",
        ):
            self.assertIn(marker, self.pages_workflow)

    def test_documentation_covers_template_secrets_state_and_work_boundary(self) -> None:
        for marker in (
            "Use this template",
            "fork",
            "Actions",
            "Secrets",
            "OPENALEX_API_KEY",
            "NCBI_EMAIL",
            "NCBI_API_KEY",
            "EVIDENCERADAR_TRANSLATION_API_KEY",
            "contents: write",
            "60 天",
            "ChatGPT Work",
            "EvidenceRadar_State.json",
            "publisher_target_min",
            "publisher_hard_max",
            "cas_retry",
            "CAS conflict",
            "人工 rerun",
            "Recovery artifact 是 Actions upload",
            "Settings → Pages",
            "Source 不是",
            "repository-first mode",
            "run-id",
            "links.json",
            "GITHUB_TOKEN",
            "explicit workflow_dispatch",
        ):
            self.assertIn(marker, self.documentation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
