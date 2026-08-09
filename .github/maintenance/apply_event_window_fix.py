from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tools" / "run_github_radar.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''        candidate.event_class = classification\n        if classification == "BACKFILL_INDEXING":\n''',
        '''        candidate.event_class = classification\n        if event is None:\n            candidate.triage_reasons = sorted(\n                set(candidate.triage_reasons) | {"MISSING_QUALIFYING_EVENT"}\n            )\n            # Missing the event gate must never masquerade as a priority\n            # recommendation.  REVIEW_REQUIRED remains visible for safety,\n            # but is separately excluded from Featured by event_status below.\n            if candidate.triage_status == "PRIORITY":\n                candidate.triage_status = "LOWER_PRIORITY"\n        if classification == "BACKFILL_INDEXING":\n''',
        "demote missing event",
    )

    text = replace_once(
        text,
        '''        eligible = [\n            item\n            for item in items\n            if str(item.get("event_class") or "OTHER") not in excluded_event_classes\n        ]\n''',
        '''        eligible = [\n            item\n            for item in items\n            if str(item.get("event_status") or "") != "NO_QUALIFYING_EVENT"\n            and str(item.get("event_class") or "OTHER") not in excluded_event_classes\n        ]\n''',
        "featured event gate",
    )

    text = replace_once(
        text,
        '''    displayed = [item for item in candidate_records if item["displayed_in_report"]]\n    featured_work_ids = select_featured_work_ids(\n''',
        '''    displayed = [item for item in candidate_records if item["displayed_in_report"]]\n    window_hours = max(1, round((end - start).total_seconds() / 3600))\n    featured_work_ids = select_featured_work_ids(\n''',
        "window hours",
    )

    text = replace_once(
        text,
        '''    summary_labels = {\n        "TRANSLATED_ABSTRACT_EXCERPT_ZH_TW": "AI 輔助繁中摘要",\n        "PROVIDER_ABSTRACT_ZH_TW": "來源繁中摘要節錄",\n        "ZH_TW_METADATA_TEMPLATE": "繁中主題簡述",\n        "TITLE_ONLY_ZH_TW": "題名層級繁中簡述",\n        "PROVIDER_ABSTRACT_EXCERPT": "舊版來源摘要節錄",\n        "TITLE_ONLY": "舊版題名層級簡述",\n    }\n''',
        '''    summary_labels = {\n        "TRANSLATED_ABSTRACT_EXCERPT_ZH_TW": "AI 輔助繁中摘要",\n        "PROVIDER_ABSTRACT_ZH_TW": "來源繁中摘要節錄",\n        "ZH_TW_METADATA_TEMPLATE": "繁中主題簡述",\n        "TITLE_ONLY_ZH_TW": "題名層級繁中簡述",\n        "PROVIDER_ABSTRACT_EXCERPT": "舊版來源摘要節錄",\n        "TITLE_ONLY": "舊版題名層級簡述",\n    }\n    event_type_labels = {\n        "version_of_record_first_online": "正式版首次上線",\n        "first_formal_indexing": "首次正式索引",\n        "formal_proceedings_release": "正式 proceedings 釋出",\n        "oa_fulltext_first_available": "OA 全文首次可得",\n        "author_accepted_manuscript_first_available": "作者接受稿首次可得",\n        "embargo_lifted": "embargo 解禁",\n        "preprint_to_peer_reviewed_upgrade": "預印本升級為同儕審查版本",\n        "formal_version_verified": "正式版本事件已驗證",\n    }\n''',
        "event labels",
    )

    text = replace_once(
        text,
        '''        priority_count = len([\n            item for item in items\n            if item["triage_status"] in {"PRIORITY", "REVIEW_REQUIRED"}\n        ])\n''',
        '''        priority_count = len([\n            item for item in items\n            if item["triage_status"] in {"PRIORITY", "REVIEW_REQUIRED"}\n            and item.get("event_status") != "NO_QUALIFYING_EVENT"\n        ])\n''',
        "category priority count",
    )

    text = replace_once(
        text,
        '''            publication_date = str(item.get("publication_date") or "日期未提供")\n            discovery_sources = [str(value) for value in item["discovery_sources"]]\n''',
        '''            publication_date = str(item.get("publication_date") or "日期未提供")\n            if event:\n                event_type = str(event.get("event_type") or "")\n                occurred_at = str(event.get("occurred_at") or "")\n                event_source = str(event.get("source") or "")\n                event_field = str(event.get("source_field") or "")\n                window_reason = (\n                    f"納入：{event_type_labels.get(event_type, event_type)}於 {occurred_at}；"\n                    f"{event_source} / {event_field} 落在本輪 {window_hours} 小時觀測窗。"\n                )\n            else:\n                window_reason = (\n                    f"未納入：沒有合格事件落在本輪 {window_hours} 小時觀測窗；"\n                    "此項僅因完整候選池保留政策而顯示。"\n                )\n            discovery_sources = [str(value) for value in item["discovery_sources"]]\n''',
        "window reason",
    )

    text = replace_once(
        text,
        '''            event_class_label = {\n                "NEW_PUBLICATION": "新發表",\n                "BACKFILL_INDEXING": "回補索引",\n                "CORRECTION_NOTICE": "更正／撤回稽核",\n                "OTHER": "事件待分流",\n            }.get(classification, classification)\n''',
        '''            if str(item.get("event_status") or "") == "NO_QUALIFYING_EVENT":\n                event_class_label = f"無 {window_hours}h 新事件"\n            else:\n                event_class_label = {\n                    "NEW_PUBLICATION": "新發表",\n                    "BACKFILL_INDEXING": "回補索引",\n                    "CORRECTION_NOTICE": "更正／撤回稽核",\n                    "OTHER": "事件待分流",\n                }.get(classification, classification)\n''',
        "no-event label",
    )

    text = replace_once(
        text,
        '''                '<div class="paper-meta">'\n                f'<span><b>日期</b>{html.escape(publication_date)}</span>'\n                f'<span><b>來源／期刊</b>{html.escape(venue)}</span>'\n                f'<span><b>事件</b>{html.escape(str(item["event_status"]))}</span>'\n                '</div>'\n''',
        '''                '<div class="paper-meta">'\n                f'<span><b>日期</b>{html.escape(publication_date)}</span>'\n                f'<span class="window-reason"><b>{window_hours} 小時納入理由</b>{html.escape(window_reason)}</span>'\n                f'<span><b>來源／期刊</b>{html.escape(venue)}</span>'\n                f'<span><b>事件</b>{html.escape(str(item["event_status"]))}</span>'\n                '</div>'\n''',
        "report window reason field",
    )

    text = replace_once(
        text,
        '''            f'<span>{featured_count} / {len(items)} 項；特殊索引與更正項目保留在完整池</span></div>'\n''',
        '''            f'<span>{featured_count} / {len(items)} 項；無合格 {window_hours} 小時事件、特殊索引與更正項目保留在完整池</span></div>'\n''',
        "featured explanation",
    )

    text = replace_once(
        text,
        '''    priority_total = len([\n        item for item in displayed\n        if item["triage_status"] in {"PRIORITY", "REVIEW_REQUIRED"}\n    ])\n''',
        '''    priority_total = len([\n        item for item in displayed\n        if item["triage_status"] in {"PRIORITY", "REVIEW_REQUIRED"}\n        and item.get("event_status") != "NO_QUALIFYING_EVENT"\n    ])\n''',
        "global priority count",
    )

    text = replace_once(
        text,
        '''.paper-meta{display:grid;grid-template-columns:.75fr 1.5fr .9fr;gap:8px;margin-top:auto}.paper-meta span{min-width:0;color:#475569;font-size:.82rem}.paper-meta b{display:block;color:#64748b;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}.authors{margin:10px 0 0;color:#52606d;font-size:.82rem}\n''',
        '''.paper-meta{display:grid;grid-template-columns:minmax(110px,.65fr) minmax(0,2.35fr);gap:8px;margin-top:auto}.paper-meta span{min-width:0;color:#475569;font-size:.82rem}.paper-meta b{display:block;color:#64748b;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}.window-reason{padding:7px 9px;border-radius:8px;background:#f8fafc}.authors{margin:10px 0 0;color:#52606d;font-size:.82rem}\n''',
        "meta layout",
    )

    text = replace_once(
        text,
        '''@media(max-width:620px){.page-shell{width:min(100% - 18px,1280px)}.hero{margin-top:9px;border-radius:16px}.metrics{grid-template-columns:1fr 1fr}.controls{position:static;grid-template-columns:1fr}.control-actions{grid-column:auto}.paper-grid{padding:0 9px 11px}.paper-card{padding:14px}.paper-meta{grid-template-columns:1fr 1fr}.audit-grid{grid-template-columns:1fr}.category-count{white-space:normal}.panel>summary,.category>summary{padding:14px}.preview-heading{display:block}.preview-heading span{display:block;margin-top:2px}.featured-heading{display:block;padding:0 9px 8px}.featured-heading span{display:block;margin-top:2px}.full-pool{margin-left:9px;margin-right:9px}.run-line{gap:6px 10px}.run-line span{width:100%}}\n''',
        '''@media(max-width:620px){.page-shell{width:min(100% - 18px,1280px)}.hero{margin-top:9px;border-radius:16px}.metrics{grid-template-columns:1fr 1fr}.controls{position:static;grid-template-columns:1fr}.control-actions{grid-column:auto}.paper-grid{padding:0 9px 11px}.paper-card{padding:14px}.paper-meta{grid-template-columns:1fr}.audit-grid{grid-template-columns:1fr}.category-count{white-space:normal}.panel>summary,.category>summary{padding:14px}.preview-heading{display:block}.preview-heading span{display:block;margin-top:2px}.featured-heading{display:block;padding:0 9px 8px}.featured-heading span{display:block;margin-top:2px}.full-pool{margin-left:9px;margin-right:9px}.run-line{gap:6px 10px}.run-line span{width:100%}}\n''',
        "mobile meta layout",
    )

    text = replace_once(
        text,
        '''<p class="panel-intro">每類先顯示約 {featured_target_per_category}–{featured_hard_max_per_category} 項精選；完整去重候選仍保留在收合的完整池，可搜尋、展開與依事件分流。分數只協助閱讀順序，不代表研究價值。</p>\n''',
        '''<p class="panel-intro">今日精選只接受本輪 {window_hours} 小時內有合格事件的候選；每類再依閱讀層級與分數顯示約 {featured_target_per_category}–{featured_hard_max_per_category} 項。完整去重候選仍保留在收合的完整池，可搜尋、展開與依事件分流；無合格事件者不會被標成今日優先。分數只協助閱讀順序，不代表研究價值。</p>\n''',
        "candidate pool explanation",
    )

    TARGET.write_text(text, encoding="utf-8")
    print(f"patched {TARGET}")


if __name__ == "__main__":
    main()
