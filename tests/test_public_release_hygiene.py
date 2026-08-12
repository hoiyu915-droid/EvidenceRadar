"""Supply-chain policy tests for the public-release validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.validate_public_release import action_pin_errors


class PublicReleaseHygieneTests(unittest.TestCase):
    def test_action_tags_are_rejected_and_full_commits_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "fixture.yml"
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@v7\n"
                f"  - uses: actions/setup-python@{'a' * 40} # v7.0.0\n",
                encoding="utf-8",
            )
            errors = action_pin_errors([workflow])

        self.assertEqual(1, len(errors))
        self.assertIn("actions/checkout@v7", errors[0])


if __name__ == "__main__":
    unittest.main()
