# Evidence Radar — {{ generated_at }}

- Generated: `{{ generated_at_iso }}`
- Timezone: `{{ timezone }}`
- Featured: `{{ featured_total }}` across four independent categories
- Candidate Pool: `{{ candidate_total }}` total; maximum `30` per category
- Status: `AUTO-TRIAGE` — 尚未完成全文與引用核實

{{#categories}}
## {{ category_title }}

- Featured: `{{ category_featured_count }}`
- Candidate Pool: `{{ category_candidate_count }}`（包含 Featured）

> 每類獨立排序與截斷；其他類別不得吃掉本類配額。

### Anchor Evidence

{{ anchor_items }}

### Strong Watch

{{ strong_watch_items }}

### Weird but Important

{{ weird_items }}

### Candidate Pool

> 本類最多 30 篇，以下不重複列出 Featured；入池不代表已通過完整證據審核。

{{ candidate_items }}

{{/categories}}

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
- Excluded before category pools: `{{ excluded_count }}`
- Warnings: {{ warnings }}

## Interpretation Guardrail

> 本檔是發現與分流層，不是最終證據審核。正式引用前仍須完成 DOI/PMID 存在性、全文、校正／撤稿、方法與斷言核對。
