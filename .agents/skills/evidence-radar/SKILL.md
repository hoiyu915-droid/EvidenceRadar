---
name: evidence-radar
description: Execute EvidenceRadar end to end in ChatGPT Work when a user asks to run, execute, refresh, or update Radar. Perform live research-source access, record exact receipts and Traditional Chinese navigation summaries, then use the packaged deterministic executor to return the validated four-file delivery.
---

# Execute EvidenceRadar

Verify the extracted pack with `python3 tools/verify_work_pack.py --root .`.
Read `WORK_ENTRY.md`, `templates/gpt-work-instructions.md`, the protocol, master
control and semantic contract in the order specified by `WORK_ENTRY.md`.

Use ChatGPT Work's web tools to perform every configured search and primary-page
read. Keep the tool-result URL, timestamp, status, result count and paging facts
from the operation that actually ran. Never fabricate an executor receipt from
prose or reuse an example as current evidence.

For `publisher_listing` sources, use only the verified `endpoint` and
`adapter_config` from `config/radar_master.json`. Treat the configured
`published_online` field and publisher label as the freshness authority; issue
month, volume month, print date and search-engine crawl time are not qualifying
publication events. Page the first-party listing until the exact window is
closed or the configured page bound is reached. Open retained first-party
article pages to confirm identity, online date, OA/license, manuscript version
and the HTML/PDF locations actually observed. Preserve Accepted Manuscripts as
such instead of silently promoting them to a Version of Record. If dates,
ordering or pagination are incomplete, preserve a source gap rather than
claiming `NO_RESULTS`.

Create one strict `EvidenceRadar_WorkInput` JSON file outside the verified pack.
Set `schema_version` to `1.0`; include `end_at`, `profile_id`,
`raw_candidate_count`, the complete `queries` and `source_access` ledgers,
sorted `checked_sources`, `searched_sources` and `unavailable_sources`, ordered
`priority_candidate_ids`, bounded `publisher_access`, `publisher_warnings`, and
the complete candidate array. Each candidate item must contain exactly:

- `work_id`: the identity derived by the protocol;
- `candidate`: the complete candidate fields used by the executor;
- `translation`: `title_zh_tw` plus `summary_zh_tw` from the source actually read.

For a candidate with a source excerpt, provide a faithful Traditional Chinese
summary. When no excerpt exists, keep `summary_zh_tw` empty. Preserve numbers,
uncertainty and design qualifiers; summaries are navigation, not claims.

Run one terminal command with fresh external directories:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_work_radar.py \
  --root "$WORK_PACK_DIR" \
  --input "$WORK_INPUT_JSON" \
  --run-dir "$WORK_RUN_DIR" \
  --delivery-dir "$WORK_DELIVERY_DIR"
```

Treat success only as `status: COMPLETE` with four `delivery_aliases` plus the
unique archive and checksum. Return the four aliases to the user. Do not expose
an intermediate translation handoff, invoke GitHub control flow, or modify the
verified pack. If the executor fails, correct the input observation or report
the exact external blocker; never label a partial run complete.