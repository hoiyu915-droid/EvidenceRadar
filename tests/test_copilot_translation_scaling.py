"""Regression tests for bounded Copilot title-translation scaling."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.run_github_radar import (
    RadarRuntimeError,
    _copilot_translation_batches_adaptive,
)


def _items(count: int) -> list[dict[str, str]]:
    titles = [
        "Alpha academic study",
        "Beta academic study",
        "Gamma academic study",
        "Delta academic study",
        "Epsilon academic study",
        "Zeta academic study",
        "Eta academic study",
        "Theta academic study",
        "Iota academic study",
        "Kappa academic study",
    ]
    return [
        {"id": f"item-{index}", "title": titles[index], "source_excerpt": ""}
        for index in range(count)
    ]


class CopilotTranslationScalingTests(unittest.TestCase):
    @patch("tools.run_github_radar._copilot_translation_batch")
    def test_failed_multi_item_batch_splits_until_valid(self, translate_batch) -> None:
        def side_effect(items, *, timeout_seconds, environ):
            self.assertEqual(75, timeout_seconds)
            self.assertEqual("token", environ["GITHUB_TOKEN"])
            if len(items) > 2:
                raise RadarRuntimeError("simulated oversized batch")
            return {
                item["id"]: {"title_zh_tw": "學術研究", "summary_zh_tw": ""}
                for item in items
            }

        translate_batch.side_effect = side_effect
        returned, failures = _copilot_translation_batches_adaptive(
            _items(4),
            batch_size=4,
            max_workers=2,
            timeout_seconds=75,
            environ={"GITHUB_TOKEN": "token"},
        )
        self.assertEqual({f"item-{index}" for index in range(4)}, set(returned))
        self.assertEqual([], failures)
        self.assertEqual(3, translate_batch.call_count)

    @patch("tools.run_github_radar._copilot_translation_batch")
    def test_single_item_failure_remains_fail_closed(self, translate_batch) -> None:
        translate_batch.side_effect = RadarRuntimeError("simulated provider failure")
        returned, failures = _copilot_translation_batches_adaptive(
            _items(1),
            batch_size=10,
            max_workers=2,
            timeout_seconds=75,
            environ={"GITHUB_TOKEN": "token"},
        )
        self.assertEqual({}, returned)
        self.assertEqual(["item-0"], failures)
        self.assertEqual(1, translate_batch.call_count)

    @patch("tools.run_github_radar._copilot_translation_batch")
    def test_incomplete_batch_is_retried_as_smaller_batches(self, translate_batch) -> None:
        def side_effect(items, *, timeout_seconds, environ):
            if len(items) == 4:
                items = items[:2]
            return {
                item["id"]: {"title_zh_tw": "學術研究", "summary_zh_tw": ""}
                for item in items
            }

        translate_batch.side_effect = side_effect
        returned, failures = _copilot_translation_batches_adaptive(
            _items(4),
            batch_size=4,
            max_workers=2,
            timeout_seconds=75,
            environ={"GITHUB_TOKEN": "token"},
        )
        self.assertEqual({f"item-{index}" for index in range(4)}, set(returned))
        self.assertEqual([], failures)
        self.assertEqual(3, translate_batch.call_count)


if __name__ == "__main__":
    unittest.main()
