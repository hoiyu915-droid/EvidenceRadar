from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "translation-stage-b.yml"
PROMPT = ROOT / "templates" / "work-stage-b-automation.md"


class WorkAutomationSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_submission_is_the_only_automatic_stage_b_trigger(self) -> None:
        pull_request = self.workflow[
            self.workflow.index("  pull_request:") : self.workflow.index("  push:")
        ]
        push = self.workflow[
            self.workflow.index("  push:") : self.workflow.index("\npermissions:")
        ]
        self.assertIn('".github/evidenceradar-translation-submission.json"', pull_request)
        self.assertIn('".github/evidenceradar-translation-submission.json"', push)
        for forbidden in ('"tools/**"', '"schemas/**"', '"artifacts/current/**"'):
            self.assertNotIn(forbidden, pull_request)
            self.assertNotIn(forbidden, push)

    def test_pr_validates_but_only_main_push_resumes(self) -> None:
        self.assertIn("if: github.event_name == 'pull_request'", self.workflow)
        self.assertIn("Stop after PR validation", self.workflow)
        for marker in (
            "Check out the exact Stage A producer",
            "Resume Stage B without rediscovery",
            "Validate and package the Stage B publication candidate",
            "Upload immutable Stage B publication candidate",
            "Advance queue entry to ready-to-publish",
        ):
            block = self.workflow[self.workflow.index(marker) - 120 : self.workflow.index(marker) + 160]
            self.assertIn("if: github.event_name == 'push'", block)

    def test_exact_request_producer_and_current_state_are_both_bound(self) -> None:
        for marker in (
            'git cat-file -e "$PRODUCER_COMMIT^{commit}"',
            'git merge-base --is-ancestor "$PRODUCER_COMMIT" HEAD',
            'git worktree add --detach "$RUNNER_TEMP/evidenceradar-producer"',
            '--protocol-commit "$PRODUCER_COMMIT"',
            '--translation-request "$RUNNER_TEMP/EvidenceRadar_TranslationRequest.json"',
            '--translation-response "$RUNNER_TEMP/EvidenceRadar_TranslationResponse.json"',
            '--state "$GITHUB_WORKSPACE/state/current/EvidenceRadar_State.json"',
            "--require-current-producer",
            "--require-semantic-contract-v3",
            "--reject-dirty",
        ):
            self.assertIn(marker, self.workflow)

    def test_queue_issue_cannot_redirect_stage_b(self) -> None:
        for marker in (
            "evidenceradar-work-queue:v1",
            '"evidenceradar-handoff" not in labels',
            '"repository": os.environ["GITHUB_REPOSITORY"]',
            '"artifact_id": int(os.environ["ARTIFACT_ID"])',
            '"request_sha256": summary["request_sha256"]',
            '"status": "TRANSLATION_REQUIRED"',
        ):
            self.assertIn(marker, self.workflow)

    def test_runtime_remains_free_of_model_credentials(self) -> None:
        for forbidden in (
            "OPENAI_API_KEY",
            "EVIDENCERADAR_TRANSLATION_API_KEY",
            "copilot",
            "api.openai.com",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("actions: read", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)

    def test_publication_is_an_artifact_not_a_direct_state_write(self) -> None:
        self.assertIn("actions/upload-artifact@v7.0.1", self.workflow)
        self.assertIn("evidenceradar-publication-", self.workflow)
        self.assertNotIn("git add", self.workflow)
        self.assertNotIn("git push", self.workflow)
        self.assertNotIn("gh pr create", self.workflow)
        self.assertNotIn("artifacts/current/EvidenceRadar_Report.html", self.workflow)

    def test_durable_work_prompt_is_present(self) -> None:
        self.assertTrue(PROMPT.is_file())
        prompt = PROMPT.read_text(encoding="utf-8")
        for marker in (
            "evidenceradar-handoff",
            "evidenceradar-ready-to-publish",
            "work_translation_queue.py",
            "8 validated batches",
            "request_sha256",
            "auto-merge",
            "links.json",
        ):
            self.assertIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
