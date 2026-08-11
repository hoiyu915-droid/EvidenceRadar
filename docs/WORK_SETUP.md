# EvidenceRadar ChatGPT Work setup

This Work Pack is the portable policy and artifact contract for EvidenceRadar.
ChatGPT Work is the execution environment: it performs the live web searches,
opens primary or authoritative source pages, checks evidence, and writes the
four per-run artifacts. The repository and this pack provide versioned policy,
configuration, schemas, examples, reusable instructions, the active V3
renderer and validation tools. GitHub stores the versioned package; it is not
part of a Work run after download.

## Work run completion boundary

An instruction to execute Radar starts one end-to-end Work run. The same run
must perform search, verification, deduplication, translation, JSON assembly,
canonical HTML rendering, validation, packaging, and delivery. A translation
request, checkpoint, `TRANSLATION_REQUIRED`, Stage A, Stage B, or publication
handoff is never a completed Work result. Those states belong to the separate
`github_actions` transport and must not replace the Work run requested by the
user.

The Work run stops successfully only when the downloadable
`EvidenceRadar_Report.html`, `EvidenceRadar_State.json`,
`EvidenceRadar_Evidence.json`, and `EvidenceRadar_Run.json` all exist and pass
the bundle validators. Internal batching may support recovery inside the run;
it must continue until the terminal four-file contract is satisfied.

This lane is user-launched. ChatGPT Scheduled Tasks currently cannot access a
project's files when the task is created in a project that has files, so this
pack does not claim unattended Work scheduling. See the official
[Scheduled Tasks documentation](https://help.openai.com/en/articles/10291617-tasks-in-chatgpt).

## 0. User entry: execute Radar

The user only needs to ask ChatGPT Work to execute Radar. Work downloads these
two GitHub Release assets once:

```text
https://github.com/hoiyu915-droid/EvidenceRadar/releases/latest/download/EvidenceRadar-WorkPack-current.zip
https://github.com/hoiyu915-droid/EvidenceRadar/releases/latest/download/EvidenceRadar-WorkPack-current.zip.sha256
```

GitHub is the source and package store. It is not an execution coordinator for
this lane. After download, Work must not start or poll Actions, create an issue
or pull request, fetch a second repository file, or wait for translation Stage
B. ChatGPT Work itself performs the searches, evidence review and Traditional
Chinese translation.

## 1. Verify and install the released pack

1. Download `EvidenceRadar-WorkPack-current.zip` and its matching `.sha256`
   sidecar from the URLs above.
2. Verify the archive before opening it. On macOS or Linux:

   ```sh
   shasum -a 256 -c EvidenceRadar-WorkPack-current.zip.sha256
   ```

   On systems with GNU coreutils, `sha256sum -c` accepts the same sidecar.
3. Extract the archive into a fresh read-only package directory and keep its
   relative paths unchanged.
4. Set `PYTHONDONTWRITEBYTECODE=1`, then run
   `python3 tools/verify_work_pack.py --root .`. The verifier checks the
   clean source commit, every file SHA-256, the embedded State, the
   `chatgpt_work` capability declaration and the absence of GitHub control-plane
   entrypoints.
5. Read `WORK_ENTRY.md`, then `templates/gpt-work-instructions.md`.

The pack includes `requirements.txt`, the current `config/radar_master.json`,
the canonical State snapshot, V3 renderer, validators, delivery packager and
State merge tool. The renderer imports report-projection functions from a
library-only `run_github_radar.py` whose CLI is disabled inside the pack. The
local Runtime entrypoint and Stage B automation are excluded. Create an
isolated environment before using the packaged tools:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Daily reports, CI files, credentials and secret-bearing material remain
excluded. Use the embedded `state/current/EvidenceRadar_State.json` as the base
unless the Work project already contains a newer State returned by an earlier
validated Work run.

## 2. Start a run

Before searching, read the following files from the extracted pack:

- `EVIDENCE_RADAR_PROTOCOL.md`
- `config/radar_master.json`
- `config/streams.yml`
- `config/scoring.yml`
- `config/output.yml`
- `config/deployment.yml`
- `docs/research_taxonomy.md`
- `docs/SEMANTIC_CONTRACT_V3.md`
- `docs/MIGRATION_DUAL_LANE_1.0.md` when moving an existing project
- `state/current/EvidenceRadar_State.json`

`config/radar_master.json` is authoritative for source routing, profiles and
limits. Resolve one profile in memory and keep the extracted files unchanged;
`owner_daily` is the routine owner-delivery profile. A missing master file,
unknown profile or unavailable configured source is a hard error, never
permission to fall back to the legacy source catalog. `streams.yml`,
`output.yml` and `deployment.yml` remain compatibility inputs, not mutable
pre-run materializations.

Use one of the protocol modes:

- `daily`: the exact 72-hour rolling window and all configured categories;
- `focused`: a user-supplied window and selected categories or questions;
- `deep_verify`: a full claim and conflict audit for selected candidate IDs.

Search categories independently, apply the event gate before ranking, and open
a primary or authoritative page for every reported work. Preserve source URLs,
locators, visible numbers, units, direction, comparator, caveats, and
correction or retraction status. A source access gap stays visible in the run
status; it is never reported as complete coverage.

Record every actual search/fetch/claim-verification operation in
`Run.retrieval_attempts`. The receipt must come from the operation that was
executed and reconcile with `queries`, `source_access`, source CHECKs and the
candidate result-ID hash; a prose statement that a search occurred is not a
receipt. Provider query rewrites go in `search_expansions`. A second pass may be
called a follow-up only when it references a pre-existing OPEN `State.gaps`
record and includes the actual trigger, query, backend, time and receipt.

Reuse the stable `source_registry`; append a `source_observation` for each
actual access result. OA status, known download URL, access depth and access
outcome are separate. A blocked or unprobed OA PDF never becomes `FULL_TEXT`.

Every claim needs `claim_kind`, `claim_origin`, citation bindings and exact
locator. Store model reasoning only in `Evidence.inferences` with
`origin: MODEL_INFERENCE`. Numeric claims also require structured effect
estimates (population, exposure, comparator, outcome, denominator, timeframe,
effect measure, analysis set, method and uncertainty); preserve incompatible
results in a conflict group.

## 3. Deliver and carry state

Every completed Work run creates these four files in the current project:

```text
EvidenceRadar_Report.html
EvidenceRadar_State.json
EvidenceRadar_Evidence.json
EvidenceRadar_Run.json
```

Validate each JSON file against the matching schema under `schemas/` before
advancing the State artifact. The HTML report is the primary user delivery and
must agree with the evidence ledger and run status. If no prior State is
available or it is invalid, use `STATE_HISTORY_INCOMPLETE` while retaining
same-run deduplication and recording the limitation.

Every displayed candidate must carry `content_summary`, `summary_basis` and
`summary_language: zh-TW`. Write the summary directly in Traditional Chinese
after reading the source. Use `PROVIDER_ABSTRACT_ZH_TW` when the provider
already supplies Chinese, `TRANSLATED_ABSTRACT_EXCERPT_ZH_TW` for a faithful
translation, and `TITLE_ONLY_ZH_TW` only when no abstract is available. A
summary is navigation text and must not be promoted to a verified claim.

The examples are structural fixtures only. Replace their placeholder claims,
dates, identifiers, and URLs with values observed during the current run; do
not treat an example as current research evidence.

The dependency-free validator can check a returned bundle locally:

```sh
python3 tools/validate_gpt_work_artifacts.py \
  EvidenceRadar_State.json \
  EvidenceRadar_Evidence.json \
  EvidenceRadar_Run.json
```

Do not hand-write the final HTML. After the three JSON ledgers are ready, first
run the canonical renderer from the same fixed checkout/pack:

```sh
python3 tools/render_report_from_artifacts.py --bundle "$WORK_RUN_DIR"
```

It synchronizes the current claim registry and claim count, renders every
visible candidate/claim, binds `report_sha256`, and writes only after schema and
cross-bundle validation pass. Any later manual edit makes canonical byte parity
fail; rerun the renderer after correcting JSON instead.

The schema validator is necessary but not sufficient for delivery. The second
validator checks all four artifacts together, including Run/State provenance,
source coverage, candidate counts and the actual HTML item markers. Bind it to
the extracted Work Pack manifest:

```sh
python3 tools/validate_delivery_bundle.py \
  --root "$WORK_PACK_DIR" \
  --bundle "$WORK_RUN_DIR" \
  --expected-lane chatgpt_work \
  --manifest "$WORK_PACK_DIR/manifest.json" \
  --require-semantic-contract-v3
```

```sh
python3 tools/package_work_delivery.py \
  --source-dir "$WORK_RUN_DIR" \
  --output-dir "$WORK_DELIVERY_DIR" \
  --run-id "$RUN_ID" \
  --validation-root "$WORK_PACK_DIR" \
  --input-manifest "$WORK_PACK_DIR/manifest.json" \
  --expected-lane chatgpt_work
```

After the package validator succeeds, create the four collision-safe direct
attachments beside it:

```sh
python3 tools/materialize_delivery_aliases.py \
  --source-dir "$WORK_RUN_DIR" \
  --output-dir "$WORK_DELIVERY_DIR"
```

Return those timestamp-prefixed HTML and three JSON aliases as actual files.
The ZIP and checksum are optional additional integrity attachments; neither a
GitHub upload nor a public URL is part of execution.

`EvidenceRadar_Report.html` and all three JSON artifacts must be returned by
Work as actual downloadable files. A local filesystem path is not delivery.
Publishing to Pages is a separate, explicitly authorized operation and is not a
condition for completing Radar.

## 4. Configuration and boundaries

Change selection, source, scoring, and rendering policy in `config/` and keep
the resulting settings with the Work project. The publisher output setting is
the configured per-run range; hard limits and the no-padding rule remain in
force even when fewer qualifying works are found. The protocol's five
categories and LLM/Human–AI direction-diversity rules apply independently.

GitHub hosts versioned source and Work Pack assets. It does not receive implicit
writeback from ChatGPT Work. When a Work State differs from the embedded seed,
return it to the user as one of the four artifacts; do not publish it merely to
finish the run. Do not add an external execution server or credentials to a
Work Pack.
