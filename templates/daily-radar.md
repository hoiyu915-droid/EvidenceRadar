# EvidenceRadar — {{ generated_at }}

- **Run status:** `{{ run_status }}`
- **Window:** {{ cutoff_at }} → {{ end_at }}（{{ window_hours }} hours, {{ timezone }}）
- **State continuity:** {{ state_continuity }}
- **Candidate count:** {{ candidate_total }}
- **Reported count:** {{ reported_total }}

## Source coverage

| Category | Planned source | Query/read time | Result | Notes |
|---|---|---|---|---|
{{ source_coverage_rows }}

> `NO_QUALIFYING_ITEMS` is valid only when all required source coverage is complete.

## 今日結論

{{ concise_conclusions }}

## Categories

{{#categories}}
### {{ category_title }}

- Candidate Pool: {{ category_candidate_count }}
- Reported: {{ category_reported_count }}
- Direction coverage: {{ direction_coverage }}

{{#reported_items}}
#### {{ rank }}. [{{ title }}]({{ primary_url }})

- **Identity:** {{ identifiers }}
- **Venue/date:** {{ journal_or_venue }} · {{ publication_date }}
- **Qualifying event:** {{ event_type }} · {{ event_at }}
- **Event evidence:** [{{ event_source }} · {{ event_source_field }}]({{ event_url }}) · {{ event_precision }} · {{ event_confidence }}
- **Design:** {{ study_design }}
- **內容簡述（繁中）:** {{ content_summary }} · `{{ summary_basis }}`
- **Claim:** `{{ support_state }}` — {{ claim_text }}
- **Numeric surface:** {{ numeric_surface }}
- **Locator:** {{ locator }}
- **Caveat:** {{ main_caveat }}
- **Correction/retraction check:** {{ correction_status }}
{{/reported_items}}

{{#candidate_only_items}}
- `UNVERIFIED` [{{ title }}]({{ primary_url }}) — {{ content_summary }} · {{ audit_reason }}
{{/candidate_only_items}}
{{/categories}}

## Conflicts and gaps

{{ conflicts_and_gaps }}

## Run notes

- Queries executed: {{ query_count }}
- Sources read: {{ source_read_count }}
- Same-run duplicates: {{ same_run_duplicates }}
- Cross-run duplicates: {{ cross_run_duplicates }}
- Warnings: {{ warnings }}

## Interpretation guardrail

> 本報告是來源連結的研究發現與分流層，不是個別醫療建議。正式引用或決策前仍須核對全文、方法、版本、校正／撤稿、claim 與 locator。
