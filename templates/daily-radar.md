# Evidence Radar — {{ generated_at }}

- Generated: `{{ generated_at_iso }}`
- Timezone: `{{ timezone }}`
- Featured: `{{ featured_count }}`
- Candidate Pool: `{{ candidate_count }}`（包含 Featured；下方僅列其餘候選）
- Status: `AUTO-TRIAGE` — 尚未完成全文與引用核實

## Featured

> 每日精選 5–8 篇；不足時不為湊數降低門檻。

### Anchor Evidence

{{ anchor_items }}

### Strong Watch

{{ strong_watch_items }}

### Weird but Important

{{ weird_items }}

## Candidate Pool

> 總數最多 30 篇，包含 Featured。以下不重複列出 Featured；入池不代表已通過完整證據審核。

{{ candidate_items }}

## Featured item format

```markdown
#### {{ rank }}. [{{ title }}]({{ primary_url }})

- **Tags:** `[{{ evidence_tier }}]` `[{{ stream }}]` `[{{ study_design }}]` `{{ oa_tag }}`
- **Source:** {{ journal_or_venue }} · {{ publication_date }}
- **Authors:** {{ authors }}
- **Why flagged:** {{ one_line_reason }}
- **Abstract signal:** {{ abstract_signal }}
- **Main caveat:** {{ main_caveat }}
- **Scores:** total `{{ total_score }}` · evidence `{{ evidence_score }}` · relevance `{{ relevance_score }}` · interest `{{ interest_score }}` · practical `{{ practical_score }}`
- **IDs:** {{ identifiers }}
```

## Candidate item format

```markdown
1. **[{{ title }}]({{ primary_url }})**
   - `[{{ evidence_tier }}]` `[{{ stream }}]` `[{{ study_design }}]` `{{ oa_tag }}` · score `{{ total_score }}`
   - {{ journal_or_venue }} · {{ publication_date }}
   - {{ one_line_reason }}
   - {{ identifiers }}
```

## Run Notes

- Retrieved: `{{ retrieved_count }}`
- Deduplicated: `{{ deduplicated_count }}`
- Excluded before Candidate Pool: `{{ excluded_count }}`
- Warnings: {{ warnings }}

## Interpretation Guardrail

> 本檔是發現與分流層，不是最終證據審核。正式引用前仍須完成 DOI/PMID 存在性、全文、校正／撤稿、方法與斷言核對。
