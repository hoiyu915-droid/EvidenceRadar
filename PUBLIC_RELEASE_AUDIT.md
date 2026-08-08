# Public release audit

Baseline audit: 2026-08-08 (Asia/Tokyo)

Dual-lane public-deployment delta: 2026-08-09 (Asia/Tokyo)

## Coverage

- Public `main` baseline and the dual-lane change set were inspected.
- 126 unique commits returned by repository-history searches were inspected from repository initialization through the current GPT Work refactor.
- 104 unique historical paths were classified.
- Current ignore rules were reviewed.
- The GitHub workflow, root runtime dependencies, Work Pack allow-list,
  generated-artifact paths and state merge surface were reviewed.

## Results

- No private-key blocks or recognizable GitHub, OpenAI, AWS, Google, Slack, or credential-bearing URL patterns found.
- No committed `.env`, credential store, private-key file, publisher PDF, JATS/XML dump, EPUB, `data/raw`, or raw/full-text cache path found.
- Historical outputs consist of repository-authored reports and structured bibliographic or event records rather than redistributed article files.
- Protected environment variables appeared only as secret references in the historical GitHub Actions workflow, not as credential values.
- The active workflow reads optional/required provider values through GitHub
  Secrets references; no value is checked in or included in the Work Pack.
- Work Pack generation is allow-list based, rejects symlinks/path traversal and
  secret-like content, and emits a SHA-256 sidecar plus per-file manifest.
- The GitHub runner stores metadata and links, not publisher full text, and
  leaves its scientific claims ledger empty pending direct review.

## Required permanent boundaries

- Never commit credentials, private user artifacts, publisher PDFs, paywalled full text, or raw article dumps.
- License only repository-authored summaries, annotations, taxonomy, selection, and arrangement; do not claim ownership of third-party bibliographic or quoted material.
- Treat historical `daily/`, historical top-level `state/`, and
  `legacy/python-runtime/` as public provenance.
- Keep new canonical State under `state/current/` and generated run bundles
  free of credentials, raw article bodies and private Work artifacts.
- Run the checked-in hygiene validator and review repository settings before
  every release or material workflow-permission change.

## Decision

The inspected delta is suitable for review and merge in the public repository.
This file documents repository-content hygiene; passing it does not prove live
source access, claim truth, or a successful scheduled host run.
