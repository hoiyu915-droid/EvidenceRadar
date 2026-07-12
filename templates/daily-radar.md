# Evidence Radar — {{ generated_at }}

- Generated: `{{ generated_at_iso }}`
- Timezone: `{{ timezone }}`
- Featured: `{{ featured_count }}`
- Candidate Pool: `{{ candidate_count }}`

## Featured

> 每日精選 5–8 篇。依證據強度、個人相關性、新穎性與實務影響排序；不得為湊數降低門檻。

### Anchor Evidence

{{ anchor_items }}

### Strong Watch

{{ strong_watch_items }}

### Weird but Important

{{ weird_items }}

## Candidate Pool

> 最多 30 篇，高召回率候選池。此區入選不代表已通過完整證據審核。

{{ candidate_items }}

## Candidate item format

```markdown
### {{ rank }}. {{ title }}

- Tags: `[{{ evidence_tier }}] [{{ stream }}] [{{ study_design }}] {{ oa_tag }}`
- Source: {{ journal_or_venue }} · {{ publication_date }}
- Why flagged: {{ one_line_reason }}
- Scores: relevance `{{ relevance_score }}` · interest `{{ interest_score }}`
- IDs: DOI `{{ doi }}` · PMID `{{ pmid }}` · PMCID `{{ pmcid }}` · OpenAlex `{{ openalex_id }}`
```

## Run Notes

- Retrieved: `{{ retrieved_count }}`
- Deduplicated: `{{ deduplicated_count }}`
- Excluded: `{{ excluded_count }}`
- Warnings: {{ warnings }}
