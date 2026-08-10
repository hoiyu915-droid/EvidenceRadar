from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_github_radar.py"
RUNNER_TEST = ROOT / "tests" / "test_github_runner.py"
CONTRACT_TEST = ROOT / "tests" / "test_meaningful_summary_contract.py"
RUNTIME_TEST = ROOT / "tests" / "test_runtime_release.py"
DEPLOYMENT = ROOT / "config" / "deployment.yml"


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
        '''    if topics:\n        opening = f"待繁中題名翻譯；目前僅能確認這是{study_kind}，涉及「{'、'.join(topics)}」。"\n    else:\n        opening = "待繁中題名翻譯；本輪不得以模板句代替題名內容。"\n    return _truncate_summary(opening, max_chars)\n''',
        '''    if topics:\n        opening = f"這篇{study_kind}涉及「{'、'.join(topics)}」；待繁中題名翻譯。"\n    else:\n        opening = f"這篇{study_kind}待繁中題名翻譯；不得以「題名所示」等模板句代替內容。"\n    if not candidate.abstract.strip():\n        opening += "來源未提供摘要。"\n    return _truncate_summary(opening, max_chars)\n''',
        "nonpublication placeholder",
    )

    translation_function = r'''def translate_candidate_summaries_zh_tw(
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
    batch_size = max(1, min(int(config.get("batch_size", 20)), 20))
    timeout_seconds = max(15, min(int(config.get("timeout_seconds", 90)), 240))
    summaries = dict(fallback)
    failures: list[str] = []
    translated_ids: set[str] = set()
    all_items = _translation_items(candidates, max_chars=max_chars)

    for offset in range(0, len(all_items), batch_size):
        batch = all_items[offset : offset + batch_size]
        try:
            if api_key:
                returned = _openai_translation_batch(
                    session,
                    batch,
                    api_key=api_key,
                    model=model,
                    timeout_seconds=timeout_seconds,
                )
                provider_basis = "OPENAI"
            else:
                returned = _copilot_translation_batch(
                    batch,
                    timeout_seconds=timeout_seconds,
                    environ=environ,
                )
                provider_basis = "COPILOT"
        except (OSError, ValueError, TypeError, requests.RequestException, RadarRuntimeError):
            failures.extend(item["id"] for item in batch)
            continue

        for source_item in batch:
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
        translation_function,
        "publication-mode translation function",
    )
    RUNNER.write_text(text, encoding="utf-8")

    runner_test = RUNNER_TEST.read_text(encoding="utf-8")
    runner_test = replace_once(
        runner_test,
        '''        translated_payload = {\n            "summaries": [\n                {\n                    "id": item.work_id,\n                    "summary": "本研究評估 10 mg 介入措施對憂鬱症的影響。",\n                }\n            ]\n        }\n''',
        '''        translated_payload = {\n            "items": [\n                {\n                    "id": item.work_id,\n                    "title_zh_tw": "可稽核證據候選 1",\n                    "summary_zh_tw": "本研究評估 10 mg 介入措施對憂鬱症的影響。",\n                }\n            ]\n        }\n''',
        "OpenAI mock payload",
    )
    runner_test = replace_once(
        runner_test,
        '''        self.assertEqual(\n            "TRANSLATED_ABSTRACT_EXCERPT_ZH_TW",\n            summaries[item.work_id][1],\n        )\n''',
        '''        self.assertEqual(\n            "TRANSLATED_TITLE_AND_ABSTRACT_ZH_TW_OPENAI",\n            summaries[item.work_id][1],\n        )\n        self.assertIn("中文題名：可稽核證據候選 1", summaries[item.work_id][0])\n''',
        "OpenAI basis assertion",
    )
    RUNNER_TEST.write_text(runner_test, encoding="utf-8")

    contract_test = CONTRACT_TEST.read_text(encoding="utf-8")
    contract_test = replace_once(
        contract_test,
        '''                environ={"EVIDENCERADAR_COPILOT_TRANSLATION": "1", "GITHUB_TOKEN": "test"},\n            )\n\n    def test_no_translation_provider_fails_closed(self) -> None:\n''',
        '''                environ={\n                    "EVIDENCERADAR_COPILOT_TRANSLATION": "1",\n                    "EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION": "1",\n                    "GITHUB_TOKEN": "test",\n                },\n            )\n\n    def test_no_translation_provider_fails_closed(self) -> None:\n''',
        "filler fail-closed env",
    )
    contract_test = replace_once(
        contract_test,
        '''                environ={},\n            )\n\n    def test_trial_design_paper_does_not_get_results_rct_badge(self) -> None:\n''',
        '''                environ={"EVIDENCERADAR_REQUIRE_ZH_TITLE_TRANSLATION": "1"},\n            )\n\n    def test_trial_design_paper_does_not_get_results_rct_badge(self) -> None:\n''',
        "no-provider fail-closed env",
    )
    CONTRACT_TEST.write_text(contract_test, encoding="utf-8")

    runtime_test = RUNTIME_TEST.read_text(encoding="utf-8")
    if runtime_test.count('"1.0.0"') != 2:
        raise SystemExit("runtime tests: expected exactly two 1.0.0 assertions")
    RUNTIME_TEST.write_text(runtime_test.replace('"1.0.0"', '"1.0.1"'), encoding="utf-8")

    deployment = DEPLOYMENT.read_text(encoding="utf-8")
    deployment = replace_once(
        deployment,
        '''    missing_behavior: emit_zh_tw_metadata_template_without_english_excerpt\n''',
        '''    missing_behavior: use_github_copilot_cli_fallback_or_fail_closed_in_publication_mode\n''',
        "deployment translation missing behavior",
    )
    DEPLOYMENT.write_text(deployment, encoding="utf-8")

    print("summary publication-mode integration patch prepared")


if __name__ == "__main__":
    main()
