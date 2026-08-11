# EvidenceRadar dual-lane protocol

## 1. Authority and execution lanes

This file is the canonical execution contract for EvidenceRadar. The same
policy, configuration, schemas and four-artifact contract apply to two
independent lanes:

Newly produced bundles implement `SEMANTIC_CONTRACT_V3` in addition to the V2
OA/full-text rules. The detailed field and transition contract is
[`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md). Validators keep
legacy V2 bundle compatibility, but a V3 marker enables all V3 fail-closed
invariants.

| Lane | Trigger | Verification boundary | State handoff |
|---|---|---|---|
| `chatgpt_work` | user-launched `daily`, `focused` or `deep_verify` run | live search, direct source reading, event and claim review | imported/exported JSON artifact |
| `github_actions` | archived maintainer fixture only; not active on main | automated discovery, event-metadata gate and bounded publisher-page access audit | historical repository State |

`chatgpt_work` is the only active execution path. The `github_actions` producer
and its former workflows are retained under `legacy/github-actions/` solely for
maintainer regression and historical deployment reference; GitHub does not
schedule or dispatch Radar from `main`. The archived lane must never be
selected, started or awaited in response to a user's request to run Radar.
Neither lane may present the other lane's prior output, memory,
archived report, search snippet or metadata as current claim evidence. Codex is
maintainer tooling and is not an EvidenceRadar runtime lane.

The repository is authoritative for source and reviewed policy. The released
Work Pack is the immutable execution input for `chatgpt_work`; GitHub has no
execution role after that package is downloaded. GitHub Actions may write only
generated artifacts, immutable run bundles and canonical State; ChatGPT Work
has no implicit repository write access.

### ChatGPT Work terminal boundary

A user-launched `chatgpt_work` execution is one end-to-end run. It starts with
the configured live searches and ends only after State, Evidence and Run JSON
have been completed, the canonical HTML has been rendered, the four-file bundle
has passed validation, and the files have been returned to the user. Internal
translation batches and checkpoints do not divide this run into separately
completed stages.

`TRANSLATION_REQUIRED`, TranslationRequest, Stage A and Stage B are
`github_actions` handoff concepts only. They are not valid success results for
`chatgpt_work`, and Work must not ask the user to wait for another invocation
before receiving the HTML and JSON artifacts. A genuine hard blocker is
reported as a blocked run with its exact cause, never as completed execution.

### Archived ordinary ChatGPT translation handoff

The inactive legacy `github_actions` lane used two publication stages. Stage A performed discovery,
deduplication, classification and source-access audit, then writes
`EvidenceRadar_TranslationRequest.json` and exits normally with
`TRANSLATION_REQUIRED`. The request contains the complete frozen resume context
and a canonical SHA-256. It does not advance State or publish the four-file
bundle.

The user uploads that request to an ordinary ChatGPT chatbot and receives
`EvidenceRadar_TranslationResponse.json`. Stage B accepts only translations
with exact candidate-ID parity and the matching request SHA-256. It rejects a
stale State, missing/extra/duplicate IDs, changed request content, missing
numbers/years/abbreviations, filler, non-Chinese titles and unsupported result
claims. Stage B resumes from the frozen context without another search. Normal
runtime use requires no OpenAI API key, GitHub token, Codex, Copilot, or
ChatGPT Work.

## 2. Modes and window

| Mode | Window | Scope | Verification |
|---|---:|---|---|
| `daily` | 72 hours | all five categories | lane-specific boundary above |
| `focused` | user supplied | selected categories/questions | verify all reported claims |
| `deep_verify` | user supplied | selected candidate IDs | full claim and conflict audit |

Default mode is `daily` with an exact rolling 72-hour window in `Asia/Tokyo`.
Date-only evidence on the cutoff calendar day is boundary-ambiguous and must be
excluded.

## 3. Required inputs

Read these files before discovery:

- `config/streams.yml`
- `config/scoring.yml`
- `config/output.yml`
- `config/deployment.yml`
- `docs/research_taxonomy.md`
- latest valid `EvidenceRadar_State.json`, when available

The GitHub lane reads `state/current/EvidenceRadar_State.json`. A Work run uses
the embedded Work Pack State, or a newer validated State already retained in
the user's Work project. If both are absent or invalid, keep same-run
deduplication and set `STATE_HISTORY_INCOMPLETE`.

Every new State and Run artifact records these direct provenance fields:

- `execution_lane`
- `protocol_commit`
- `base_state_sha256`
- `parent_run_ids`

`base_state_sha256` uses the UTF-8 SHA-256 of canonical JSON (sorted object
keys, no insignificant whitespace, unescaped Unicode). With no valid prior
State, it is the SHA-256 of the empty byte string.

## 4. Source plan and discovery

For every enabled stream:

1. Search categories independently so one category cannot consume another's
   quota.
2. Record the query, source target, execution time, URL, access status and
   result count.
3. Build candidates without drafting outcome claims.
4. Preserve original title, authors, venue, date, identifiers and discovery
   URLs.
5. Do not infer that a source was searched because another result cites it.

Minimum source targets are defined in `config/streams.yml`. The GitHub lane
implements discovery checks for PubMed, Europe PMC, OpenAlex, arXiv,
OpenReview, ACL Anthology and PMLR. Publisher and formal-proceedings targets
remain bounded verification-stage checks. A primary registry, repository,
proceedings page or publisher page is still required at the verification level
claimed by the report.

### OA and full-text access semantics

OA availability and observed access are independent audit facts. New candidates
record `oa_status`, `oa_evidence`, `access_status`, `fulltext_kind`,
`download_urls` and per-location access observations. `oa_status: YES` may be
paired with `access_status: BLOCKED`, `PAYWALLED`, `FAILED` or `NOT_CHECKED`.
An OA signal from PMCID, arXiv or provider metadata does not prove that this run
opened the full text.

Preserve direct repository HTML/PDF URLs when available. DOI resolvers, PubMed,
OpenAlex, Europe PMC index records, arXiv abstract pages and other discovery
landing pages are not substantive full-text evidence merely because they return
HTTP 200. Only the exact direct location actually probed may be marked
`ACCESSIBLE`, `BLOCKED`, `PAYWALLED` or `FAILED`; unprobed locations remain
`NOT_CHECKED`.

### Per-run source CHECK contract

For every distinct source named by an enabled stream, emit exactly one
`source_coverage.checks` summary in Run and the corresponding `coverage.checks`
summary in Evidence. A source receives a CHECK even when no candidate is found,
the lane has no adapter, the request is blocked, or a bounded verification
budget skips it. Each summary records the configured `source_id`, `stage`,
timestamp, result count and a human-readable summary.

The allowed CHECK statuses are:

- `SUCCESS`: the source operation completed and returned one or more results;
- `NO_RESULTS`: the source was queried successfully but returned zero results;
- `FAILED`: the source operation was attempted and failed, including an access
  block or provider error;
- `NOT_ATTEMPTED`: the source was configured but this lane did not make a
  request (for example, no adapter or no eligible bounded-verification item).

`source_coverage.checked` is the set of source IDs with a CHECK record. It is
deliberately not a synonym for success and may include all four statuses.
`searched` records sources for which a request was made; `unavailable` records
sources with a failed or not-attempted operation. `all_configured_sources_checked`
is true only when every requested source has its own CHECK summary. These
coverage fields are audit facts and do not turn metadata into claim evidence.

### Executor receipts and controlled expansion

Every query, source access and CHECK must map to one unique
`Run.retrieval_attempts` receipt written from the executor's actual operation.
Receipts separate `DISCOVERY`, `CONTENT_FETCH`, `CLAIM_VERIFY` and `FOLLOWUP`,
and retain request fingerprint, result ID hash, pagination, result count and
limit state. A model statement that a search occurred is not a receipt.
`NO_RESULTS` requires a real successful request with zero results;
`NOT_ATTEMPTED` requests zero pages. `FAILED` retains no results; an interrupted
operation that retained results is `PARTIAL`.

When a provider-specific actual query differs from the configured query, write
one `search_expansions` record with both forms and the reason. State
`source_registry` assigns one stable ID to each canonical URL, while
`source_observations` append access depth/outcome linked to a receipt. A known
PDF URL without a successful direct fetch remains `NONE/NOT_CHECKED` (or the
observed blocked outcome), never `FULL_TEXT`.

## 5. Identity and event gate

Deduplicate in this order:

```text
DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title
```

Compare every alias and event identifier against prior State. A previously
notified work may re-enter only for a verified new event. At least one of these
events must fall inside the exact rolling window:

- `version_of_record_first_online`
- `first_formal_indexing`
- `formal_proceedings_release`
- `oa_fulltext_first_available`
- `author_accepted_manuscript_first_available`
- `embargo_lifted`
- `preprint_to_peer_reviewed_upgrade`
- `formal_version_verified`

Each event requires `occurred_at`, `source`, `source_field`, `source_url`,
`precision` and `confidence`. Search-engine freshness, metadata-only changes,
issue assignment, correction publication or re-indexing are not qualifying
events by themselves.

Each candidate also records an `event_class`. `BACKFILL_INDEXING` and
`CORRECTION_NOTICE` remain in the complete candidate pool for audit but are not
ordinary Featured items. A correction or retraction title takes precedence over
an otherwise missing or ambiguous qualifying event.

## 6. Classification and ranking

Apply `docs/research_taxonomy.md` and `config/scoring.yml`.

- Category assignment answers the research problem, not the venue.
- Every category has an independent Candidate Pool.
- Featured target remains 5–8 per active category; insufficient evidence is
  never padded.
- Preserve active LLM/Human–AI direction diversity before score-only ranking.
- Correspondence, protocols, editorials, retracted/flagged items and
  title-irrelevant results are excluded from ordinary ranking.
- GitHub Actions may use deterministic metadata ranking only; its scores are
  not evidence-quality adjudication.

## 7. Publisher-access budget: 10–15

For each GitHub run, aim to retain 10 accessible publisher/source-page audit
records and make no more than 15 publisher-page attempts. Apply the shared
settings in `config/deployment.yml`:

- at most two requests per resolved domain;
- delay between requests;
- stop the affected domain on HTTP `401`, `403` or `429`;
- record every failure and access gap;
- finish below 10 when eligible sources are insufficient or blocked;
- never pad the output and never exceed the hard maximum of 15.

This 10–15 budget limits publisher network probes only. It never limits the
candidate ledger or readable candidate display and remains separate from the
5–8 Featured target. An accessible page is an audit record, not proof of a
paper's substantive claims.

## 8. Evidence governance

For a fully reviewed Work item, open a primary or authoritative page and
record research design, population, claim text, support state, source URL,
locator, visible numbers, sign, unit, direction, comparator, limitations and
correction/retraction status.

Allowed support states are `SUPPORTED`, `PARTIAL`, `CONFLICT` and `UNVERIFIED`.
`UNVERIFIED` claims may appear only in a candidate/audit section and must not
appear as conclusions.

The GitHub lane must leave the claims ledger empty unless an independently
defined full-verification implementation is added later. It retains every
deduplicated discovery candidate in Run and State, including lower-priority,
blocked and unprobed candidates. Routing score changes order, not epistemic
value. The readable report shows approximately 5–8 Featured candidates per
active category and keeps every deduplicated candidate in a searchable,
expandable complete pool. The complete pool displays every deduplicated candidate;
publisher access success is never a display gate.

`SUPPORTED` requires an explicitly observed accessible direct full-text
location and a locator that can be audited. A publisher/discovery URL labelled
`FULL_TEXT`, a DOI redirect, or a manually asserted status is insufficient.
`PARTIAL`, `CONFLICT` and `UNVERIFIED` items cannot be counted as verified.

Every V3 claim declares `claim_kind`, `claim_origin`, citation binding IDs and
support reason. Citation bindings reuse stable source IDs and record canonical
URL, exact locator, extraction origin, observed access depth and support scope.
`MODEL_INFERENCE` belongs only in the separate inference ledger; it cannot be
a citation binding or source-supported claim. Topic alignment remains a
separate routing judgment and never raises evidence quality.

Numeric claims also reference structured effect estimates. Preserve effect
measure, population, exposure, comparator, outcome, denominator, timeframe,
analysis set, estimator, method and uncertainty. Results with incompatible
definitions remain in `conflict_groups`; do not average or narratively smooth
them into agreement.

State persists a canonical claim registry and explicit work/claim relations.
Reusing a claim ID with changed text/kind/origin is invalid. A newly promoted
`SUPPORTED` claim requires a current-run accessible observation at the depth
claimed by its binding. A newer version, VOR, correction, retraction or
contradictory claim creates a relation instead of overwriting history.

### Gap-driven follow-up

Source, content, identity, claim and numeric gaps persist in `State.gaps` with
bounded attempts, cooldown and resolution criteria. A follow-up is legal only
for a pre-existing OPEN gap and must bind its trigger, scope/parent candidate,
actual query, backend, time, executor receipt, result and resolved gap IDs.
Routine padding or a `NOT_ATTEMPTED` record is not a follow-up. Reaching the
attempt ceiling produces `UNRESOLVABLE`; a successful receipt is required for
`RESOLVED`.

## 9. Source coverage and run status

Run carries a top-level `source_coverage` object with
`requested`, `checked`, `searched`, `unavailable`,
`all_configured_sources_checked` and `checks`. Evidence mirrors the same fields
inside its `coverage` object (and retains the older `*_sources` names as
compatibility aliases). A checked source is not necessarily a successful source;
inspect each CHECK status and summary.

`publisher` and `formal_proceedings_or_publisher` are
`bounded_verification` sources. They still receive a CHECK summary for every
run. A run with no eligible item is `NOT_ATTEMPTED`; `NO_RESULTS` is reserved
for a source that was actually queried and returned zero results. The budget
limits requests, not whether the source's check is represented.

- `COMPLETE`: all required sources and every reported claim were directly
  verified.
- `PARTIAL_SOURCE_COVERAGE`: useful output exists, but source coverage or claim
  review is incomplete.
- `SOURCE_ACCESS_GAP`: a required verification source was attempted but could
  not be accessed.
- `STATE_HISTORY_INCOMPLETE`: valid prior cross-run State was unavailable.
- `NO_QUALIFYING_ITEMS`: coverage was complete and no event passed the gate.

The primary status must never overstate completeness. History incompleteness
takes precedence. Automated candidates with unreviewed claims cannot make a
GitHub run `COMPLETE`.

## 10. Required artifacts

Every lane creates and validates all four artifacts:

1. `EvidenceRadar_Report.html`
2. `EvidenceRadar_State.json`
3. `EvidenceRadar_Evidence.json`
4. `EvidenceRadar_Run.json`

The three JSON files must conform to `schemas/`. HTML is the primary readable
delivery and must agree with Evidence and Run. State advances only after all
artifacts validate. Partial runs preserve their limitations and do not promote
unverified scientific claims.

The report includes generated time and exact window, lane and status, source
coverage, category candidate sections, triage/event/access/review status,
claim support where available, caveats, identifiers, direct links,
conflicts/gaps and the statement that the report is research triage rather
than individual medical advice. Run and the readable report both contain the
complete deduplicated candidate set; the report groups it by category.

Every displayed candidate includes `content_summary` and `summary_basis` in
Run, `summary_language: zh-TW`, structured `title_zh_tw`, and a visible
Traditional Chinese title/preview in HTML while retaining the English original.
`CHATBOT_TITLE_ZH_TW` is used when no source excerpt exists;
`CHATBOT_TITLE_AND_ABSTRACT_ZH_TW` is used only after the SHA-bound response
passes exact ID, language, filler, number/year/abbreviation and unsupported
result checks. Legacy summary bases remain schema-compatible. Every basis is
navigation metadata, not claim verification.
The self-contained HTML provides text search, category/triage/source/OA/access
filters, collapsible category sections, Featured/full-pool separation and
expandable audit details without removing any candidate from the report.

HTML delivery is part of the artifact contract, not a best-effort preview.
Every report carries `evidenceradar-run-id`, `evidenceradar-execution-lane`,
`evidenceradar-protocol-commit`, `evidenceradar-displayed-candidates` and
`evidenceradar-featured-candidates` meta values. Every rendered candidate
carries exactly one `data-evidenceradar-work-id` and an explicit Featured/full
pool marker. The complete Run ledger, Featured ID set, displayed subset and the
HTML marker set must agree before State can advance or a public link can be
published.

For V3, Work must not hand-author the final HTML. After State, Evidence and Run
JSON are ready, run:

```sh
python3 tools/render_report_from_artifacts.py --bundle /path/to/run
```

The renderer synchronizes the current claim registry and report hash, then
uses the same pure projection enforced by `validate_delivery_bundle.py`.
Candidate summaries are navigation-only; every substantive claim is bound to
an Evidence claim ID. Extra report prose or numbers that are absent from the
canonical claim ledger make the byte-parity gate fail.

## 11. State synchronization and concurrency

GitHub Actions serializes its writeback lane with a repository-scoped
concurrency group. An immutable run bundle is written under `runs/<run_id>/`;
current delivery lives under `artifacts/current/`; canonical State lives at
`state/current/EvidenceRadar_State.json`.

The runner snapshots both canonical JSON identity and the exact input file
bytes. Its final State write is a locked compare-and-swap: if any local lane
changes that file after the read, the stale run keeps its current/immutable
recovery bundle, fails visibly and does not replace canonical State. Repository
branch CAS remains a second, separate guard at publication time.

When a Work State and repository State diverge, union them with the deterministic
merge tool before accepting repository writeback:

```sh
python tools/merge_radar_state.py \
  state/current/EvidenceRadar_State.json \
  /path/to/work/EvidenceRadar_State.json \
  --execution-lane chatgpt_work \
  --protocol-commit COMMIT \
  --output /path/to/merged/EvidenceRadar_State.json
```

The merge is identity-aware and idempotent: it unions aliases and notification
events, preserves the earliest first-seen and latest last-seen timestamps,
uses the greatest observed count, and emits deterministic ordering. OA evidence,
download URLs and full-text locations are deterministic unions. A rediscovery
with `NOT_CHECKED` must not erase an earlier observed access result; mixed
per-location observations derive an aggregate `MIXED` state rather than letting
the newest lane silently overwrite the other. Validate the merged artifact
before replacing canonical State.
V3 merges also retain stable source registries/observations, remap work-scoped
gaps after identity upgrades, conservatively merge canonical claim status and
preserve explicit work/claim relations. Divergent gap receipts consume the
combined bounded-attempt budget; the latest snapshot controls mutable gap
status, and reaching the ceiling becomes `UNRESOLVABLE` unless a successful
resolution receipt exists.

## 12. Public deployment contract

- GitHub stores reviewed source, immutable packages and optional published
  artifacts. Its active workflows validate and package releases; they do not
  execute Radar discovery, translation or report generation.
- ChatGPT Work downloads the latest released Work Pack and SHA-256 sidecar once,
  verifies the embedded clean source commit and State, then executes in a fresh
  Work-VM run-id directory without another GitHub operation.
- Every Work delivery is validated before packaging and returned as a unique
  `EvidenceRadar-WorkRun-<run_id>.zip`, manifest and checksum. A Work-VM path is
  not a public URL and Work has no implicit repository writeback.
- Credentials stay in repository secrets or the user's Work environment; they
  never enter config, artifacts, logs or the Work Pack.
- Each deployment owns its State. Upstream historical data is not silently
  shared with downstream deployments.
- A GitHub blob/raw URL and a ChatGPT Work local path are not direct HTML
  delivery. A reviewed current bundle may be published through GitHub Pages;
  the deployment emits `links.json` with stable latest and immutable-run URLs.
- Public deployment runs `tools/validate_delivery_bundle.py` in addition to
  per-file schema validation. It rejects stale producer files, lane/protocol
  mismatches, divergent canonical State and JSON/HTML candidate-count drift.
- Repository publication, when separately requested after user review, is a
  compare-and-swap operation. It never changes the already delivered Work
  result and never schedules or resumes Radar execution.

See `docs/GITHUB_DEPLOYMENT.md` and `docs/WORK_SETUP.md`.

## 13. Stop conditions

Stop and deliver the validated artifacts when every reported item satisfies
the verification boundary declared by its lane.

Do not:

- claim comprehensive coverage after a source gap;
- use memory, old output or snippets as current evidence;
- fabricate a DOI, PMID, date, quote or locator;
- declare a search receipt without an executor operation;
- use topic alignment or `MODEL_INFERENCE` as source support;
- promote a claim without a current verifiable support event;
- silently drop sign, unit, comparator or direction;
- collapse incompatible effect measures or unresolved numeric conflicts;
- hand-edit substantive prose into V3 HTML after canonical rendering;
- advance canonical State after invalid artifact generation;
- overwrite a divergent State instead of merging it;
- trigger TA/TP03 or image generation without a separate user request.
