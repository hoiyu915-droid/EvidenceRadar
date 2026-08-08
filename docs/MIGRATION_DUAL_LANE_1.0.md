# Dual-lane 1.0 migration note

This change expands EvidenceRadar from a ChatGPT Work-only runtime contract to
two explicit lanes: `chatgpt_work` and `github_actions`. It does not reactivate
the archived crawler under `legacy/python-runtime/`.

## Compatibility

- State, Evidence and Run keep `schema_version: "1.0"`.
- Existing valid State and Run artifacts remain valid. The new
  `execution_lane`, `protocol_commit`, `base_state_sha256` and
  `parent_run_ids` fields are optional in the 1.0 schemas for backward
  compatibility, but required by protocol for newly produced dual-lane runs.
- Unknown fields still fail closed because top-level schemas retain
  `additionalProperties: false`.
- Evidence schema and claim support states are unchanged.
- The Featured selection target remains 5–8 per active category. The new
  10–15 setting applies only to the bounded publisher-page access budget.

## State paths

Files immediately under historical `state/` remain provenance snapshots. The
GitHub lane reads and advances only:

```text
state/current/EvidenceRadar_State.json
```

Current readable output uses `artifacts/current/`; immutable run bundles use
`runs/<run_id>/`. The previous `.manual-run` marker is removed because
`workflow_dispatch` inputs and `config/deployment.yml` are now the auditable
manual-run surface.

## Moving an existing Work deployment

1. Keep the most recent valid Work `EvidenceRadar_State.json` unchanged.
2. Install the new Work Pack and read the new protocol/config.
3. For the first dual-lane Work run, set `execution_lane: chatgpt_work`, record
   the Work Pack source commit and hash the canonical JSON form of the input
   State into `base_state_sha256`.
4. If GitHub has already produced canonical State, merge both branches with
   `tools/merge_radar_state.py`; validate the result before accepting it.
5. Preserve `STATE_HISTORY_INCOMPLETE` when earlier history was already
   incomplete. Migration does not fabricate a complete history baseline.

## Rollback

Disable `.github/workflows/daily-radar.yml` to stop automated execution. This
does not delete canonical State or run bundles. A Work-only deployment may
continue using the same schemas and artifacts, but must retain the provenance
fields on new output so a later merge remains possible.
