# EvidenceRadar GPT Work Protocol

## 1. Authority and runtime

This file is the canonical execution contract for EvidenceRadar.

- Runtime owner: ChatGPT Work conversation/project
- Retrieval: live web search plus direct source-page reads
- Persistent handoff: user-carried JSON artifacts
- Repository role: versioned protocol and read-only historical snapshots
- Forbidden runtime dependencies: MCP, external server, Codex, GitHub Actions, repository writeback

Memory, prior chat text and archived reports are navigation aids only. Every claim about the current research window requires a source opened during the current run.

## 2. Modes

| Mode | Window | Scope | Verification |
|---|---:|---|---|
| `daily` | 72 hours | all five categories | verify all reported items |
| `focused` | user supplied | selected categories/questions | verify all reported items |
| `deep_verify` | user supplied | selected candidate IDs | full claim and conflict audit |

Default mode is `daily` with an exact 72-hour rolling window in `Asia/Tokyo`.

## 3. Required inputs

Read these policy files before searching:

- `config/streams.yml`
- `config/scoring.yml`
- `config/output.yml`
- `docs/research_taxonomy.md`
- latest `EvidenceRadar_State.json`, when available

If the State artifact is absent or invalid, continue with same-run deduplication and set the run status to `STATE_HISTORY_INCOMPLETE`.

## 4. Phase A — source plan

For every enabled stream:

1. Convert query guidance into concise web-search queries.
2. Prefer primary source pages and authoritative registries.
3. Record each query, source target, execution time, URL and access result.
4. Do not infer that a source was searched merely because another result cites it.

Minimum source targets:

- Clinical/sport: PubMed, Europe PMC when relevant, and publisher or journal page for final verification.
- LLM/Human–AI: arXiv, OpenReview, ACL Anthology/PMLR or formal publisher/proceedings page as applicable; OpenAlex may support discovery but cannot be the only verification source.
- Correction/retraction check: PubMed publication type and publisher correction/retraction notice when applicable.

## 5. Phase B — discovery

1. Search categories independently so one category cannot consume another category's quota.
2. Build candidates without drafting outcome claims.
3. Preserve original title, authors, venue, date, identifiers and discovery URLs.
4. Deduplicate in this order:

```text
DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title
```

5. Compare aliases against the prior State artifact when available.
6. A previously notified work may re-enter only for a verified new event such as preprint-to-formal upgrade or first readable full text.

## 6. Phase C — event gate

At least one qualifying event must fall inside the exact rolling window:

- `version_of_record_first_online`
- `first_formal_indexing`
- `formal_proceedings_release`
- `oa_fulltext_first_available`
- `author_accepted_manuscript_first_available`
- `embargo_lifted`
- `preprint_to_peer_reviewed_upgrade`
- `formal_version_verified`

Each event requires `occurred_at`, `source`, `source_field`, `url`, `precision` and `confidence`.

Date-only evidence on the cutoff calendar day is boundary-ambiguous and must be excluded. Search-engine freshness, metadata-only changes, issue assignment, correction publication or re-indexing are not qualifying events by themselves.

## 7. Phase D — classification and ranking

Apply `docs/research_taxonomy.md` and `config/scoring.yml`.

- Category assignment answers the research problem, not the venue.
- Every category has an independent Candidate Pool.
- Featured target is 5–8 per active category, but padding is forbidden.
- Preserve active LLM/Human–AI direction diversity before score-only ranking.
- Correspondence, protocols, editorials, retracted/flagged items and title-irrelevant results are excluded from ordinary ranking.

## 8. Phase E — evidence governance

Open a primary or authoritative page for every reported item. Record:

- research design and population
- claim text
- support state
- source URL and locator
- exact visible numbers
- sign, unit, direction, comparison group and semantic surface
- limitations and unresolved conflicts
- correction/retraction status

Allowed support states:

- `SUPPORTED`
- `PARTIAL`
- `CONFLICT`
- `UNVERIFIED`

`UNVERIFIED` claims may appear only in the candidate/audit section and must not appear as a report conclusion.

## 9. Source coverage and run status

Compute coverage before finalizing the report:

- `COMPLETE`: all required source targets and reported-item verification succeeded.
- `PARTIAL_SOURCE_COVERAGE`: the run produced useful results but one or more planned sources were not read.
- `SOURCE_ACCESS_GAP`: a required verification source could not be accessed for a reported candidate.
- `STATE_HISTORY_INCOMPLETE`: prior cross-run State was absent or invalid.
- `NO_QUALIFYING_ITEMS`: coverage was complete and no item passed the event gate.

Multiple limitations may be recorded, but the primary run status must never overstate completeness. `NO_QUALIFYING_ITEMS` is valid only with complete source coverage.

## 10. Required artifacts

Create all four artifacts in the current ChatGPT Work run:

1. `EvidenceRadar_Report.html`
2. `EvidenceRadar_State.json`
3. `EvidenceRadar_Evidence.json`
4. `EvidenceRadar_Run.json`

The three JSON artifacts must conform to `schemas/`. HTML is the primary user delivery and must agree with the JSON evidence and run status.

The State artifact advances only after the other artifacts validate. A partial or state-incomplete run may append observed identities but must preserve its limitation status and must not mark unverified events as notified.

## 11. Report contract

The report must include:

- generated time and exact window
- primary run status
- source-coverage matrix
- concise daily conclusions
- category sections
- event evidence for every reported paper
- claim support and caveats
- identifiers and direct links
- conflict/gap section
- explicit statement that the report is research triage, not individual medical advice

## 12. Stop conditions

Stop and deliver the validated artifacts when every reported item has event evidence and evidence-governance fields.

Do not:

- claim comprehensive coverage after a source gap
- use memory as current evidence
- fabricate a DOI, PMID, date, quote or locator
- silently drop sign, unit, comparator or direction
- update history after invalid artifact generation
- trigger TA/TP03 or image generation without a separate user request
