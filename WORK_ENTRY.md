# Execute EvidenceRadar in ChatGPT Work

This file is the only user-facing entrypoint in the released Work Pack.

When the user asks to execute or run Radar, complete one end-to-end
`chatgpt_work` run in the current conversation.  Do not ask the user to select
an execution lane and do not return an intermediate handoff.

## Required result

Continue through source discovery, primary-source reading, event verification,
deduplication, Traditional Chinese translation, State merge, canonical render,
bundle validation and delivery.  Success means all four downloadable files
exist:

1. `EvidenceRadar_Report.html`
2. `EvidenceRadar_State.json`
3. `EvidenceRadar_Evidence.json`
4. `EvidenceRadar_Run.json`

The HTML and three JSON files are one atomic delivery.  A search result list,
checkpoint, TranslationRequest, `TRANSLATION_REQUIRED`, Stage A status, Stage B
status, GitHub issue, pull request or workflow run is not a completed Radar run.

## Package and GitHub boundary

Before extraction, verify the downloaded ZIP's signed provenance against
`hoiyu915-droid/EvidenceRadar/.github/workflows/work-pack-release.yml` using the
released `EvidenceRadar-WorkPack-current.sigstore.json` bundle, then verify its
checksum. Before execution, verify the extracted tree with:

```sh
python3 tools/verify_work_pack.py --root .
```

`manifest.json` binds this package to one clean source commit.  Use the embedded
`state/current/EvidenceRadar_State.json` as the base State unless the current
Work project already contains a newer validated State returned by an earlier
run.  Never fetch a second State or policy file after the package is verified.

GitHub is only the versioned source and package storage boundary. After the ZIP,
checksum and provenance bundle have been downloaded and verified, do not invoke GitHub Actions, create an
issue or pull request, poll a workflow, publish a branch, or fetch moving
repository files.  Live searches of PubMed, publishers and the other configured
research sources are part of Radar execution and are not GitHub control-plane
activity.

Do not invoke `run_github_radar.py`, `run_local_runtime.py`, or any translation
handoff automation. The canonical renderer imports projection functions from
the packaged `run_github_radar.py`, but its CLI is disabled by the Work Pack
manifest and must fail if invoked. The local Runtime and handoff programs are
absent. ChatGPT Work performs the live research review and Traditional Chinese
translation itself, records those executed observations in one strict external
`EvidenceRadar_WorkInput` JSON ledger, then invokes `tools/run_work_radar.py`.

## Execution contract

Read, in order:

1. `manifest.json`
2. this file
3. `templates/gpt-work-instructions.md`
4. `EVIDENCE_RADAR_PROTOCOL.md`
5. `config/radar_master.json`
6. `docs/SEMANTIC_CONTRACT_V3.md`

Use profile `owner_daily` unless the user explicitly selects another profile.
Keep the extracted package read-only and create a new external run directory.
Copy the selected base State into that directory before merging any current-run
observations.

Set `PYTHONDONTWRITEBYTECODE=1` for packaged Python commands.  The packaged
entrypoints also enforce this themselves so validation, merge, render and
delivery cannot add `__pycache__` files to the verified package.

Source statuses describe observations, not overall completeness.  Cache reuse,
local `0/0` replay, `SUCCESS`, `FAILED`, or `NOT_ATTEMPTED` must never upgrade
`PARTIAL_SOURCE_COVERAGE`, `SOURCE_ACCESS_GAP`, or
`STATE_HISTORY_INCOMPLETE` to `COMPLETE`.

Write the three JSON ledgers first.  Render the HTML only with the packaged
canonical renderer, run both packaged validators, then materialize the four
timestamped delivery aliases.  Return the actual files to the user.  Do not
require publication or a public URL.

The deterministic terminal path is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_work_radar.py \
  --root "$WORK_PACK_DIR" \
  --input "$WORK_INPUT_JSON" \
  --run-dir "$WORK_RUN_DIR" \
  --delivery-dir "$WORK_DELIVERY_DIR"
```

The input, run and delivery paths must be outside the verified package and both
output directories must be fresh. Success is the executor's `status: COMPLETE`
with four `delivery_aliases`; its single command performs State advancement,
canonical rendering, both validators, immutable packaging and alias creation.
