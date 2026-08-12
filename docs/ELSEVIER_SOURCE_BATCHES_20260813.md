# Curated Elsevier named-source batches — 2026-08-13

## Outcome

EvidenceRadar activates 30 curated Elsevier journals as separate named
discovery sources, in addition to the eight Lancet-family sources. The sources
reuse the generic `rss_atom` adapter. Every source binds an ISSN-specific,
first-party ScienceDirect RSS URL and an ISSN-bound Crossref journal-window
fallback; no publisher-branded adapter or all-ScienceDirect crawl was added.

Activation stays bounded to at most eight sources per batch:

| batch | named sources |
|---|---|
| `2026-08-13-elsevier-02` | `elsevier_jsams`, `elsevier_jshs`, `elsevier_smhs`, `elsevier_jesf`, `elsevier_clinical_nutrition`, `elsevier_clinical_nutrition_espen`, `elsevier_nutrition`, `elsevier_journal_clinical_epidemiology` |
| `2026-08-13-elsevier-03` | `elsevier_artificial_intelligence`, `elsevier_neural_networks`, `elsevier_information_processing_management`, `elsevier_natural_language_processing_journal`, `elsevier_computers_human_behavior`, `elsevier_computers_human_behavior_reports`, `elsevier_computers_human_behavior_artificial_humans`, `elsevier_international_journal_human_computer_studies` |
| `2026-08-13-elsevier-04` | `elsevier_physical_therapy_sport`, `elsevier_psychology_sport_exercise`, `elsevier_human_movement_science`, `elsevier_clinical_nutrition_open_science`, `elsevier_nutrition_research`, `elsevier_journal_nutritional_biochemistry`, `elsevier_artificial_intelligence_medicine`, `elsevier_journal_biomedical_informatics` |
| `2026-08-13-elsevier-05` | `elsevier_gait_posture`, `elsevier_clinical_biomechanics`, `elsevier_expert_systems_applications`, `elsevier_knowledge_based_systems`, `elsevier_computer_speech_language`, `elsevier_computers_education_ai` |

The exact journal title, ISSN and RSS endpoint for every source live in
`config/radar_master.json`, which remains the authoritative source registry.

## Topic routing

Five focused `owner_daily` streams select the sources through curated groups:

| stream | category | group size |
|---|---|---:|
| `owner_elsevier_sport` | sport science | 9 |
| `owner_elsevier_nutrition` | sport nutrition and fitness | 6 |
| `owner_elsevier_llm` | LLM research | 9 |
| `owner_elsevier_human_ai` | Human–AI / HCI | 5 |
| `owner_elsevier_clinical_ai` | clinical methods and medical AI | 3 |

These groups are not added to `biomedical_core` or the general LLM L1–L9
streams. This avoids applying PubMed syntax to RSS text and avoids repeatedly
querying every journal from unrelated streams.

## Live 72-hour audit

The production parser was run against all 30 configured sources for the
inclusive 2026-08-10 through 2026-08-13 window:

- all 30 first-party RSS requests and all 30 ISSN-bound Crossref requests
  returned a response; no probe raised an exception;
- 60 of 60 requested inventory pages were received;
- the RSS feeds supplied 1,859 entries and 39 exact `Available online`
  in-window records; Crossref supplied zero `published-online` records in the
  same journal windows;
- 1,613 entries had only issue/month metadata or otherwise lacked a trustworthy
  publication date. They were not promoted into the 72-hour event set.

The five focused production streams then emitted 32 query/source CHECKs and
covered all 30 named sources. The run retained 20 query-matched candidates
after identity deduplication. Because every feed also contained incomplete-date
entries, the 25 no-match CHECKs remained `FAILED` and the seven matching CHECKs
remained `PARTIAL`; all 30 sources were therefore visible in the unavailable
source set. This is the intended fail-closed result: usable first-party
candidates survive, while Crossref fallback or partial RSS metadata cannot
manufacture a successful source CHECK.

## Evidence boundary

- RSS titles, descriptions and publication labels are discovery metadata, not
  scientific claim evidence.
- Crossref is a journal-window fallback and never counts as first-party RSS
  success.
- `oa_mode: verify_per_work` keeps OA status independent of direct access.
- A ScienceDirect 401, 403 or 429 remains `BLOCKED`, stops the domain circuit
  and cannot count as publisher-accessible full text.
- DOI, repository, author manuscript and preprint alternatives retain their
  own version identity and are never relabelled as the version of record.
