from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_github_radar.py"
OUTPUT = ROOT / "config" / "output.yml"
VERSION = ROOT / "runtime" / "VERSION"
RUNTIME_TEST = ROOT / "tests" / "test_runtime_release.py"
SCALING_TEST = ROOT / "tests" / "test_copilot_translation_scaling.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_block(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"{label}: start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"{label}: end marker not found")
    return text[:start_at] + replacement + text[end_at:]


def main() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from dataclasses import dataclass, field\n",
        "from concurrent.futures import ThreadPoolExecutor, as_completed\nfrom dataclasses import dataclass, field\n",
        "concurrent futures import",
    )

    replacement = r'''def _copilot_translation_batch_adaptive(
    items: list[dict[str, str]],
    *,
    timeout_seconds: int,
    environ: dict[str, str],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Translate one Copilot batch, recursively splitting a failed batch.

    Copilot CLI response time grows materially with batch size.  A failed
    multi-item request must not discard otherwise translatable records: retry
    the same bounded input as two smaller batches until a single item remains.
    The caller still fails publication if any single item cannot be translated.
    """

    if not items:
        return {}, []
    try:
        returned = _copilot_translation_batch(
            items,
            timeout_seconds=timeout_seconds,
            environ=environ,
        )
        expected_ids = {item["id"] for item in items}
        if set(returned) != expected_ids:
            raise RadarRuntimeError("Copilot CLI translation returned an incomplete batch")
        for source_item in items:
            translated = returned[source_item["id"]]
            title_zh = str(translated.get("title_zh_tw") or "").strip()
            summary_zh = str(translated.get("summary_zh_tw") or "").strip()
            if not _valid_title_translation(source_item["title"], title_zh):
                raise RadarRuntimeError("Copilot CLI returned an invalid title translation")
            if source_item["source_excerpt"] and summary_zh and not _contains_han(summary_zh):
                raise RadarRuntimeError("Copilot CLI returned a non-Chinese summary")
        return returned, []
    except (OSError, ValueError, TypeError, RadarRuntimeError):
        if len(items) == 1:
            return {}, [items[0]["id"]]
        midpoint = len(items) // 2
        left, left_failures = _copilot_translation_batch_adaptive(
            items[:midpoint],
            timeout_seconds=timeout_seconds,
            environ=environ,
        )
        right, right_failures = _copilot_translation_batch_adaptive(
            items[midpoint:],
            timeout_seconds=timeout_seconds,
            environ=environ,
        )
        return {**left, **right}, [*left_failures, *right_failures]


def _copilot_translation_batches_adaptive(
    items: list[dict[str, str]],
    *,
    batch_size: int,
    max_workers: int,
    timeout_seconds: int,
    environ: dict[str, str],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Run bounded Copilot batches concurrently and merge deterministically."""

    batches = [items[offset : offset + batch_size] for offset in range(0, len(items), batch_size)]
    if not batches:
        return {}, []
    worker_count = max(1, min(max_workers, len(batches)))
    completed: dict[int, tuple[dict[str, dict[str, str]], list[str]]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _copilot_translation_batch_adaptive,
                batch,
                timeout_seconds=timeout_seconds,
                environ=environ,
            ): index
            for index, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            completed[futures[future]] = future.result()

    returned: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    for index in range(len(batches)):
        batch_returned, batch_failures = completed[index]
        returned.update(batch_returned)
        failures.extend(batch_failures)
    return returned, failures


def translate_candidate_summaries_zh_tw(
    candidates: list[Candidate],
    *,
    rendering: dict[str, Any],
    session: requests.Session,
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, tuple[str, str]], list[dict[str, str]]]:
    """Translate every displayed title; publication mode fails closed on gaps.

    Unit/library callers may keep an explicit non-publishable zh-TW placeholder
    so bundle-contract tests do not depend on an external model.  Official
    publication sets ``EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION=1`` and then
    every candidate must have a validated title translation.
    """

    if environ is None:
        environ = dict(os.environ)
    require_translation = (
        str(environ.get("EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION") or "").strip() == "1"
    )
    max_chars = int(rendering.get("candidate_summary_max_chars", 320))
    if not candidates:
        return {}, []
    fallback = {
        candidate.work_id: candidate_content_summary(candidate, max_chars=max_chars)
        for candidate in candidates
    }
    config = rendering.get("summary_translation", {})
    if not isinstance(config, dict) or not bool(config.get("enabled", False)):
        if require_translation:
            raise RadarRuntimeError(
                "zh-TW title translation is required for publication but summary_translation is disabled"
            )
        return fallback, [{
            "code": "SUMMARY_TRANSLATION_NOT_CONFIGURED",
            "message": "繁中題名翻譯未啟用；僅允許非發佈 fixture 使用待翻譯 placeholder。",
            "severity": "INFO",
        }]

    key_env = str(config.get("api_key_env") or "EVIDENCERADAR_TRANSLATION_API_KEY")
    api_key = str(environ.get(key_env) or "").strip()
    copilot_enabled = str(environ.get("EVIDENCERADAR_COPILOT_TRANSLATION") or "").strip() == "1"
    if not api_key and not copilot_enabled:
        if require_translation:
            raise RadarRuntimeError(
                "zh-TW title translation provider unavailable; publication aborted"
            )
        return fallback, [{
            "code": "SUMMARY_TRANSLATION_NOT_CONFIGURED",
            "message": "未設定繁中翻譯 provider；僅允許非發佈 fixture 使用待翻譯 placeholder。",
            "severity": "INFO",
        }]

    model_env = str(config.get("model_env") or "EVIDENCERADAR_TRANSLATION_MODEL")
    model = str(environ.get(model_env) or config.get("default_model") or "gpt-5-mini").strip()
    openai_batch_size = max(1, min(int(config.get("batch_size", 20)), 20))
    openai_timeout_seconds = max(15, min(int(config.get("timeout_seconds", 90)), 240))
    copilot_batch_size = max(1, min(int(config.get("copilot_batch_size", 10)), 10))
    copilot_max_workers = max(1, min(int(config.get("copilot_max_workers", 2)), 2))
    copilot_timeout_seconds = max(30, min(int(config.get("copilot_timeout_seconds", 75)), 120))
    summaries = dict(fallback)
    failures: list[str] = []
    translated_ids: set[str] = set()
    all_items = _translation_items(candidates, max_chars=max_chars)
    returned: dict[str, dict[str, str]] = {}

    if api_key:
        provider_basis = "OPENAI"
        for offset in range(0, len(all_items), openai_batch_size):
            batch = all_items[offset : offset + openai_batch_size]
            try:
                returned.update(_openai_translation_batch(
                    session,
                    batch,
                    api_key=api_key,
                    model=model,
                    timeout_seconds=openai_timeout_seconds,
                ))
            except (OSError, ValueError, TypeError, requests.RequestException, RadarRuntimeError):
                failures.extend(item["id"] for item in batch)
    else:
        provider_basis = "COPILOT"
        returned, failures = _copilot_translation_batches_adaptive(
            all_items,
            batch_size=copilot_batch_size,
            max_workers=copilot_max_workers,
            timeout_seconds=copilot_timeout_seconds,
            environ=environ,
        )

    for source_item in all_items:
        work_id = source_item["id"]
        translated = returned.get(work_id, {})
        title_zh = str(translated.get("title_zh_tw") or "").strip()
        summary_zh = str(translated.get("summary_zh_tw") or "").strip()
        if not _valid_title_translation(source_item["title"], title_zh):
            failures.append(work_id)
            continue
        if source_item["source_excerpt"] and summary_zh and not _contains_han(summary_zh):
            failures.append(work_id)
            continue
        title_sentence = f"中文題名：{title_zh.rstrip('。')}。"
        if source_item["source_excerpt"] and summary_zh:
            summary_text = _truncate_summary(f"{title_sentence}{summary_zh}", max_chars)
            basis = f"TRANSLATED_TITLE_AND_ABSTRACT_ZH_TW_{provider_basis}"
        else:
            summary_text = _truncate_summary(title_sentence, max_chars)
            basis = f"TRANSLATED_TITLE_ZH_TW_{provider_basis}"
        summaries[work_id] = (summary_text, basis)
        translated_ids.add(work_id)

    unresolved = sorted(
        set(failures) | ({item["id"] for item in all_items} - translated_ids)
    )
    if unresolved and require_translation:
        sample = ", ".join(unresolved[:5])
        raise RadarRuntimeError(
            f"zh-TW title translation incomplete for {len(unresolved)} candidates; publication aborted ({sample})"
        )
    warnings: list[dict[str, str]] = []
    if unresolved:
        warnings.append({
            "code": "SUMMARY_TRANSLATION_PARTIAL",
            "message": (
                f"繁中題名翻譯缺少 {len(unresolved)} 筆；僅非發佈 fixture 可保留待翻譯 placeholder。"
            ),
            "severity": "WARNING",
        })
    return summaries, warnings


'''
    text = replace_block(
        text,
        "def translate_candidate_summaries_zh_tw(\n",
        "def build_candidate_ledger(\n",
        replacement,
        "translation scaling block",
    )
    RUNNER.write_text(text, encoding="utf-8")

    output = OUTPUT.read_text(encoding="utf-8")
    output = replace_once(
        output,
        '''    batch_size: 20\n    timeout_seconds: 60\n    fail_closed_to_zh_tw_metadata_template: true\n''',
        '''    batch_size: 20\n    timeout_seconds: 60\n    copilot_batch_size: 10\n    copilot_timeout_seconds: 75\n    copilot_max_workers: 2\n    publication_missing_translation_behavior: fail_closed\n    nonpublication_fixture_fallback: explicit_zh_tw_placeholder\n''',
        "translation runtime config",
    )
    OUTPUT.write_text(output, encoding="utf-8")

    if VERSION.read_text(encoding="utf-8") != "1.0.1\n":
        raise SystemExit("runtime VERSION is not the expected 1.0.1 base")
    VERSION.write_text("1.0.2\n", encoding="utf-8")

    runtime_test = RUNTIME_TEST.read_text(encoding="utf-8")
    if runtime_test.count('"1.0.1"') != 2:
        raise SystemExit("runtime tests: expected exactly two 1.0.1 assertions")
    RUNTIME_TEST.write_text(runtime_test.replace('"1.0.1"', '"1.0.2"'), encoding="utf-8")

    SCALING_TEST.write_text(r'''"""Regression tests for bounded Copilot title-translation scaling."""

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
''', encoding="utf-8")

    print("scalable Copilot translation patch prepared")


if __name__ == "__main__":
    main()
