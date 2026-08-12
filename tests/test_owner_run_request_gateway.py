"""Contract tests for the repository-owner issue trigger for owner_daily."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "legacy" / "github-actions" / "daily-radar.yml"


class OwnerRunRequestGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_issue_entrypoint_is_explicit_and_owner_authenticated(self) -> None:
        self.assertIn("issues:\n    types: [opened]", self.workflow)
        self.assertIn("github.event.issue.user.login == github.repository_owner", self.workflow)
        self.assertIn("<!-- evidenceradar-run-request:v1 -->", self.workflow)

    def test_handoff_issues_cannot_recurse_into_stage_a(self) -> None:
        condition = self.workflow[
            self.workflow.index("    if: >-") : self.workflow.index("    runs-on:")
        ]
        self.assertIn("github.event_name != 'issues'", condition)
        self.assertIn("github.event.issue.user.login == github.repository_owner", condition)
        self.assertIn("evidenceradar-run-request:v1", condition)
        self.assertNotIn("evidenceradar-work-queue:v1", condition)

    def test_successful_queue_exposes_handoff_and_closes_request(self) -> None:
        self.assertIn("id: queue_request", self.workflow)
        self.assertIn('echo "handoff_number=$existing" >> "$GITHUB_OUTPUT"', self.workflow)
        self.assertIn("Complete owner run request", self.workflow)
        self.assertIn("steps.queue_request.outputs.handoff_number != ''", self.workflow)
        self.assertIn('gh issue close "$REQUEST_ISSUE_NUMBER" --reason completed', self.workflow)

    def test_failed_request_stays_open_with_observable_run(self) -> None:
        failure = self.workflow[self.workflow.index("Report owner run request failure") :]
        self.assertIn("always()", failure)
        self.assertIn("steps.queue_request.outputs.handoff_number == ''", failure)
        self.assertIn("GITHUB_RUN_ID", failure)
        self.assertIn("intentionally left open", failure)
        self.assertNotIn("gh issue close", failure)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
