# EvidenceRadar ChatGPT Work setup

This Work Pack is the portable policy and artifact contract for EvidenceRadar.
ChatGPT Work is the execution environment: it performs the live web searches,
opens primary or authoritative source pages, checks evidence, and writes the
four per-run artifacts. The repository and this pack provide versioned policy,
configuration, schemas, examples, reusable instructions and dependency-free
State/validation tools. The Work Pack itself is not a background service; the
separate GitHub Actions lane may run on its own schedule.

This lane is user-launched. ChatGPT Scheduled Tasks currently cannot access a
project's files when the task is created in a project that has files, so this
pack does not claim unattended Work scheduling. See the official
[Scheduled Tasks documentation](https://help.openai.com/en/articles/10291617-tasks-in-chatgpt).

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

The pack has no third-party runtime dependencies. It contains only the
protocol, configuration, taxonomy, templates, schemas, examples, setup and
migration guides, `tools/validate_gpt_work_artifacts.py`, and
`tools/merge_radar_state.py`. Daily reports, cross-run state, the automated
runner, legacy Python code, credentials, and other secret-bearing material are
deliberately excluded. Carry the latest `EvidenceRadar_State.json` separately
when a new run needs cross-run deduplication.

## 2. Start a run

Before searching, read the following files from the extracted pack:

- `EVIDENCE_RADAR_PROTOCOL.md`
- `config/streams.yml`
- `config/scoring.yml`
- `config/output.yml`
- `config/deployment.yml`
- `docs/research_taxonomy.md`
- `docs/MIGRATION_DUAL_LANE_1.0.md` when moving an existing project
- the latest `EvidenceRadar_State.json`, when available

Use one of the protocol modes:

- `daily`: the exact 72-hour rolling window and all configured categories;
- `focused`: a user-supplied window and selected categories or questions;
- `deep_verify`: a full claim and conflict audit for selected candidate IDs.

Search categories independently, apply the event gate before ranking, and open
a primary or authoritative page for every reported work. Preserve source URLs,
locators, visible numbers, units, direction, comparator, caveats, and
correction or retraction status. A source access gap stays visible in the run
status; it is never reported as complete coverage.

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
