# Lancet named-source batch — 2026-08-13

## Outcome

EvidenceRadar activates the first bounded batch of eight Lancet-family journals
as separate named discovery sources. They reuse `rss_atom`; there is no
publisher-branded adapter. Each source binds one first-party ScienceDirect RSS
endpoint and one ISSN-specific Crossref journal-window fallback.

| source ID | ISSN | first-party RSS |
|---|---|---|
| `lancet` | `0140-6736` | `https://rss.sciencedirect.com/publication/science/01406736` |
| `lancet_digital_health` | `2589-7500` | `https://rss.sciencedirect.com/publication/science/25897500` |
| `lancet_healthy_longevity` | `2666-7568` | `https://rss.sciencedirect.com/publication/science/26667568` |
| `lancet_global_health` | `2214-109X` | `https://rss.sciencedirect.com/publication/science/2214109X` |
| `lancet_regional_health_western_pacific` | `2666-6065` | `https://rss.sciencedirect.com/publication/science/26666065` |
| `lancet_regional_health_europe` | `2666-7762` | `https://rss.sciencedirect.com/publication/science/26667762` |
| `lancet_regional_health_americas` | `2667-193X` | `https://rss.sciencedirect.com/publication/science/2667193X` |
| `eclinicalmedicine` | `2589-5370` | `https://rss.sciencedirect.com/publication/science/25895370` |

## Owner routing

The batch uses four family-specific `owner_daily` streams instead of adding all
feeds to `biomedical_core` or every LLM L1–L9 stream:

- broad clinical routing for six general Lancet titles;
- focused LLM and Human–AI queries for The Lancet Digital Health;
- focused exercise, nutrition and healthy-ageing queries for The Lancet
  Healthy Longevity.

Discovery remains globally uncapped. Source-query matches may be ranked later,
but every deduplicated candidate remains in the ledger.

## Live 72-hour smoke check

At 2026-08-13, all eight configured RSS URLs returned HTTP 200
`application/rss+xml` and parsed with the production adapter. For the inclusive
2026-08-10 through 2026-08-13 journal window, the parser retained 14 exact
`Available online` events:

| source ID | exact in-window records |
|---|---:|
| `lancet` | 1 |
| `lancet_digital_health` | 4 |
| `lancet_healthy_longevity` | 2 |
| `lancet_global_health` | 7 |
| `lancet_regional_health_western_pacific` | 0 |
| `lancet_regional_health_europe` | 0 |
| `lancet_regional_health_americas` | 0 |
| `eclinicalmedicine` | 0 |

Each source also completed one ISSN-bound Crossref request, so the observed
backend was `rss_atom+crossref_journal_window` with two requested and two
received inventory pages. Crossref returned zero `published-online` records in
that window. ScienceDirect feeds also contained month-only issue entries; these
were deliberately excluded from the 72-hour event set and surfaced as unusable
date records. The corresponding source CHECK therefore remains fail-closed
where inventory completeness cannot be proven.

A production `owner_daily` smoke over the clinical, JAMA, Nature Communications
and four new Lancet streams then emitted CHECKs for all 12 selected discovery
sources. JAMA Network Open, Nature Communications and PubMed returned
`SUCCESS`; Europe PMC returned `NO_RESULTS`; all eight Lancet sources retained
their visible incomplete-date `FAILED` CHECKs, including the two sources that
still contributed eight query-matched candidates. The run observed 125 raw
candidates and 108 candidates after identity deduplication. This proves the new
sources do not suppress existing JAMA, Nature or PubMed discovery and that
fallback results cannot turn an incomplete first-party CHECK into success.

## Evidence boundary

- RSS labels and descriptions are discovery and bibliographic metadata, not
  scientific claim evidence.
- Crossref fallback does not become a first-party feed success.
- `oa_mode: verify_per_work` keeps OA independent from observed full-text
  access.
- A ScienceDirect 401, 403 or 429 remains a `BLOCKED` access outcome, stops the
  domain circuit and never increments publisher-accessible counts.
- DOI, repository, author-manuscript and preprint alternatives retain their
  own version identity; none is relabelled as the publisher version of record.
