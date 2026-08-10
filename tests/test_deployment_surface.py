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
        hard_max_end = self.workflow.index("\n\n# Hosted Actions", hard_max_start)
        hard_max_block = self.workflow[hard_max_start:hard_max_end]
        self.assertIn("default: 10", target_block)
        self.assertIn("default: 15", hard_max_block)
        self.assertIn("type: number", self.workflow)
        self.assertNotIn("cas_retry:", self.workflow)

    def test_read_permission_and_non_cancelling_concurrency_are_explicit(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertNotIn("actions: write", self.workflow)
        self.assertIn("concurrency:", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_runner_command_and_layout_are_centralized(self) -> None:
        for variable, value in (
            ("RADAR_STATE_PATH", "state/current/EvidenceRadar_State.json"),
        ):
            self.assertIn(f'{variable}: "{value}"', self.workflow)
        self.assertIn("python tools/run_github_radar.py", self.workflow)
        for option in (
            "--root \"$RADAR_ROOT\"",
            "--output-dir \"$RADAR_OUTPUT_DIR\"",
            "--state \"$RADAR_STATE_PATH\"",
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
        self.assertIn("RADAR_TRANSLATION_REQUEST:", self.workflow)
        self.assertNotIn("EVIDENCERADAR_TRANSLATION_API_KEY", self.workflow)
        self.assertNotIn("EVIDENCERADAR_COPILOT_TRANSLATION", self.workflow)
        self.assertNotIn("Bearer ", self.workflow)

    def test_stage_a_uploads_only_the_translation_request(self) -> None:
        self.assertIn("EvidenceRadar_TranslationRequest.json", self.workflow)
        self.assertIn("TRANSLATION_REQUIRED", self.workflow)
        self.assertIn("--translation-request", self.workflow)
        self.assertIn("uses: actions/upload-artifact@v7.0.1", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)
        self.assertNotIn("Verify four EvidenceRadar artifacts", self.workflow)
        self.assertNotIn("EvidenceRadar_Report.html", self.workflow)

    def test_runner_temp_is_scoped_to_steps(self) -> None:
        top_level_env = self.workflow[
            self.workflow.index("\nenv:\n") : self.workflow.index("\n\njobs:\n")
        ]
        self.assertNotIn("runner.temp", top_level_env)
        self.assertIn(
            "RADAR_OUTPUT_DIR: ${{ runner.temp }}/evidenceradar-pending-output",
            self.workflow,
        )
        self.assertIn(
            "RADAR_TRANSLATION_REQUEST: ${{ runner.temp }}/EvidenceRadar_TranslationRequest.json",
            self.workflow,
        )
        self.assertIn(
            "path: ${{ runner.temp }}/EvidenceRadar_TranslationRequest.json",
            self.workflow,
        )

    def test_request_artifact_name_is_unique_per_actions_attempt(self) -> None:
        self.assertIn("evidenceradar-translation-request-", self.workflow)
        self.assertIn("github.run_id", self.workflow)
        self.assertIn("github.run_attempt", self.workflow)

    def test_hosted_stage_a_has_no_writeback_or_publication_dispatch(self) -> None:
        self.assertNotIn("git push", self.workflow)
        self.assertNotIn("git add", self.workflow)
        self.assertNotIn("gh workflow run public-release.yml", self.workflow)
        self.assertNotIn("gh workflow run pages.yml", self.workflow)

    def test_publication_workflows_remain_explicit_and_separate(self) -> None:
        self.assertIn("workflow_dispatch:", self.public_release_workflow)

    def test_public_release_validates_the_canonical_current_bundle(self) -> None:
        self.assertIn("fetch-depth: 0", self.public_release_workflow)
        self.assertIn(
            "Validate committed canonical bundle against its recorded producer",
            self.public_release_workflow,
        )
        self.assertIn("EvidenceRadar_Run.json", self.public_release_workflow)
        self.assertIn('git cat-file -e "$protocol_commit^{commit}"', self.public_release_workflow)
        self.assertIn("git worktree add --detach", self.public_release_workflow)
        self.assertIn(
            'python "$producer_root/tools/validate_delivery_bundle.py"',
            self.public_release_workflow,
        )
        self.assertIn(
            '--bundle "$GITHUB_WORKSPACE/artifacts/current"',
            self.public_release_workflow,
        )
        self.assertIn(
            '--canonical-state "$GITHUB_WORKSPACE/state/current/EvidenceRadar_State.json"',
            self.public_release_workflow,
        )
        self.assertIn("--require-current-producer", self.public_release_workflow)
        self.assertIn("--require-semantic-contract-v3", self.public_release_workflow)
        tests_index = self.public_release_workflow.index("Run standard-library tests")
        bundle_index = self.public_release_workflow.index(
            "Validate committed canonical bundle against its recorded producer"
        )
        self.assertLess(tests_index, bundle_index)

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
        install_index = self.pages_workflow.index(
            "python -m pip install -r requirements.txt"
        )
        build_index = self.pages_workflow.index("python tools/build_pages_site.py")
        self.assertLess(install_index, build_index)

    def test_pages_push_requires_a_canonical_artifact_change(self) -> None:
        push_paths = self.pages_workflow[
            self.pages_workflow.index("  push:\n") : self.pages_workflow.index(
                "  workflow_dispatch:", self.pages_workflow.index("  push:\n")
            )
        ]
        self.assertIn('"artifacts/current/**"', push_paths)
        self.assertIn('"state/current/EvidenceRadar_State.json"', push_paths)
        for forbidden in (
            '"schemas/**"',
            '"config/**"',
            '"tools/',
            '".github/workflows/pages.yml"',
        ):
            self.assertNotIn(forbidden, push_paths)

    def test_documentation_covers_template_secrets_state_and_work_boundary(self) -> None:
        for marker in (
            "Use this template",
            "fork",
            "Actions",
            "Secrets",
            "OPENALEX_API_KEY",
            "NCBI_EMAIL",
            "NCBI_API_KEY",
            "EvidenceRadar_TranslationRequest.json",
            "TRANSLATION_REQUIRED",
            "60 天",
            "ChatGPT Work",
            "EvidenceRadar_State.json",
            "publisher_target_min",
            "publisher_hard_max",
            "contents: read",
            "Settings → Pages",
            "Source 不是",
            "repository-first mode",
            "run-id",
            "links.json",
            "TranslationRequest",
        ):
            self.assertIn(marker, self.documentation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
