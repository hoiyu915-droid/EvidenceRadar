# Governance

## Maintainer

`hoiyu915-droid` is the primary maintainer and has final responsibility for repository policy, release decisions, security response, and merge authorization.

## Change process

- Ordinary fixes and documentation changes use focused pull requests.
- Changes to the event gate, evidence states, schemas, source-coverage rules, or runtime authority require tests and an explicit migration note.
- Historical reports and state are provenance records; corrections should be additive and explain what changed.
- Automated or AI-assisted output never merges without maintainer review.

## Releases

The `main` branch is the current maintained line. Tagged releases should identify protocol and schema compatibility. Deprecated behavior remains documented until downstream migration is practical.

## Community

Questions, integrations, and proposals may be opened as GitHub issues. Public adoption is recorded only from verifiable repositories, issues, releases, citations, or user reports; metrics are not inferred or fabricated.
