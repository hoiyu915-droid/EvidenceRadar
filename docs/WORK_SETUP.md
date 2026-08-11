# EvidenceRadar ChatGPT Work setup

This Work Pack is the portable policy and artifact contract for EvidenceRadar.
ChatGPT Work is the execution environment: it performs the live web searches,
opens primary or authoritative source pages, checks evidence, and writes the
four per-run artifacts. The repository and this pack provide versioned policy,
configuration, schemas, examples, reusable instructions, the active V3
renderer/runner and validation tools. The Work Pack itself is not a background service; the
separate GitHub Actions lane may run on its own schedule.

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

## 0. Repository-first mode (direct public-repository execution)

For a normal Work run, the Work VM can read and execute the public repository
directly; a released Work Pack is not required. Use this mode when the task
starts from the public GitHub repository:

1. Open the repository's `main` branch and resolve it to one commit SHA before
   reading files. If the Work VM has a shell, the equivalent immutable
   checkout is:

   ```sh
   git clone --depth 1 --branch main \
     https://github.com/hoiyu915-droid/EvidenceRadar.git \
     "$WORK_SOURCE_DIR/EvidenceRadar"
   git -C "$WORK_SOURCE_DIR/EvidenceRadar" rev-parse HEAD
   ```

   If it only has browser/repository access, record the commit shown by the
   GitHub `main` page and read raw files at that commit. Do not use a moving
   `main` URL after the SHA has been recorded.
2. Read `EVIDENCE_RADAR_PROTOCOL.md`, `config/`, `schemas/`,
   `docs/research_taxonomy.md`, the templates and the relevant tools from that
   exact checkout. Keep the checkout read-only.
3. Create a fresh Work-VM run directory, for example
   `work-runs/<run_id>/`, and write all four artifacts there. This is local Work
   state, not an implicit GitHub writeback.
4. Render the final HTML from the three JSON ledgers, validate the four files,
   then run the repository's
   `tools/package_work_delivery.py` from the same immutable checkout. It emits
   `EvidenceRadar-WorkRun-<run_id>.zip`, a matching unique run-id directory and
   `.zip.sha256` sidecar. Attach the unique ZIP plus checksum, not four bare
   files whose names may collide with an old Work attachment.

The resulting delivery is self-contained: the archive root has the canonical
`EvidenceRadar_Report.html`, `EvidenceRadar_State.json`,
`EvidenceRadar_Evidence.json`, `EvidenceRadar_Run.json` and `manifest.json`.
The manifest records the run id, `chatgpt_work` lane, protocol commit and the
SHA-256/size of each canonical file. A second run must use a new run id; the
packager refuses to overwrite an existing run directory or archive. This
run-id packaging is also the recovery artifact when a later GitHub publication
attempt sees a branch conflict.

Repository-first mode and Work Pack mode are alternatives for one run. Do not
mix policy files from one mode with the recorded source commit of the other.

## 1. Install a released pack

1. Download `EvidenceRadar-WorkPack-v<version>.zip` and its matching
   `.sha256` file from the release.
2. Verify the archive before opening it. On macOS or Linux:

   ```sh
   shasum -a 256 -c EvidenceRadar-WorkPack-v<version>.zip.sha256
   ```

   On systems with GNU coreutils, `sha256sum -c` accepts the same sidecar.
3. Extract the archive into the ChatGPT Work project used for EvidenceRadar.
   Keep the relative paths in the archive unchanged.
4. Read `manifest.json` and confirm the pack version, schema version, source
   commit marker, and file checksums. A `-dirty` commit marker identifies a
   pack built from a working tree with local changes; it is intentionally
   visible rather than silently treated as a clean release.

The pack includes `requirements.txt`, the current
`config/radar_master.json`, `tools/run_github_radar.py`, its master-control
helpers, the V3 canonical renderer, validators, delivery packager and State
merge tool. Create an isolated environment before using the renderer or active
runner:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

The active runner may seed an automated discovery/source-access audit; it does
not create reviewed scientific claims. Daily reports, cross-run State, the
legacy crawler, CI files, credentials and other secret-bearing material remain
excluded. Carry the latest `EvidenceRadar_State.json` separately when a new run
needs cross-run deduplication.

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
- the latest `EvidenceRadar_State.json`, when available

`config/radar_master.json` is authoritative for source routing, profiles and
limits. Resolve one profile in memory and keep the extracted/checked-out files
unchanged; `owner_daily` is the routine owner-delivery profile. A direct runner
invocation must pass `--profile owner_daily` (or the explicitly selected
profile). A missing master file, unknown profile or unavailable configured
adapter is a hard error, never permission to fall back to the legacy source
catalog. `streams.yml`, `output.yml` and `deployment.yml` remain compatibility
inputs, not mutable pre-run materializations.

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
source coverage, candidate counts and the actual HTML item markers. For an
extracted released Work Pack, use its input manifest:

```sh
python3 tools/validate_delivery_bundle.py \
  --root . \
  --bundle . \
  --expected-lane chatgpt_work \
  --manifest manifest.json \
  --require-semantic-contract-v3
```

For repository-first mode, do not pass a guessed or output-delivery manifest.
Bind validation to the immutable clean checkout instead:

```sh
python3 tools/validate_delivery_bundle.py \
  --root "$WORK_SOURCE_DIR/EvidenceRadar" \
  --bundle "$WORK_RUN_DIR" \
  --expected-lane chatgpt_work \
  --expected-protocol-commit "$PROTOCOL_COMMIT" \
  --require-current-producer \
  --require-semantic-contract-v3
python3 tools/package_work_delivery.py \
  --source-dir "$WORK_RUN_DIR" \
  --output-dir "$WORK_DELIVERY_DIR" \
  --run-id "$RUN_ID" \
  --validation-root "$WORK_SOURCE_DIR/EvidenceRadar" \
  --expected-lane chatgpt_work \
  --require-current-producer
```

`EvidenceRadar_Report.html` must be returned by Work as an actual downloadable
file. A local Work filesystem path is not a clickable public link. For a stable
browser URL, the validated and uniquely packaged bundle must be reviewed and
published to a GitHub repository with Pages enabled. After the Pages deployment
succeeds, use the deployed `links.json`; its `report_url` points to the current
report and `immutable_run.report_html` points to the preserved run. Do not
announce a guessed Pages URL before the deployment reports success.

## 4. Configuration and boundaries

Change selection, source, scoring, and rendering policy in `config/` and keep
the resulting settings with the Work project. The publisher output setting is
the configured per-run range; hard limits and the no-padding rule remain in
force even when fewer qualifying works are found. The protocol's five
categories and LLM/Human–AI direction-diversity rules apply independently.

GitHub may host versioned packs and separately execute its automated lane, but
it does not receive implicit repository writeback from ChatGPT Work. When Work
and GitHub State diverge, retain both and run the included deterministic merge
tool according to `docs/MIGRATION_DUAL_LANE_1.0.md`; validate before accepting
the result. Do not add MCP, an external server, a scheduled runner, or
credentials to a Work Pack.
