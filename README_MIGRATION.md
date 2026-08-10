# EvidenceRadar master control migration

This migration replaces the hidden source/category world-view with one JSON control plane while preserving the existing focused profile as a compatibility mode.

## Authoritative control

`config/radar_master.json` owns source identities and capabilities, reusable source groups, the broad scholarly taxonomy, routing categories, stream-to-category/source routing and profiles. Existing query text and non-category scoring policy remain compatibility inputs during this migration. Adapter implementation remains code; JSON selects which implemented adapter a source uses.

`tools/radar_control.py` validates and compiles that control plane into the runtime source-check contract, stream set, scoring categories, category thresholds, source endpoints and adapter mapping. `tools/generate_legacy_config.py` can still project `streams.yml` and `scoring.yml` while downstream consumers migrate.

`tools/publisher_feed.py` adds a generic RSS 1.0/RDF, RSS 2.0 and Atom reader with date-window filtering, query filtering, DOI/venue/date extraction and per-run feed caching. The first active publisher-native family is Nature Portfolio; Science/AAAS and Cell Press are represented as planned sources, not falsely reported as active adapters.

## Profiles

- `current_focus`: preserves the existing clinical, sport, LLM and human-AI streams.
- `general_research`: adds publisher-native interdisciplinary, physics, chemistry, engineering, computer-science, earth/environment and social-science discovery.
- `current_plus_general`: union of both and the formal daily producer profile.

The taxonomy is global; a profile only selects the streams active for a run.

## Runtime migration bridge

`tools/apply_master_control_runtime.py` is a fail-closed, versioned bridge for the existing monolithic runner. The daily workflow applies it before Stage A, then executes `tools/run_github_radar.py --profile current_plus_general`. Every source-routing replacement is count-checked so runner drift aborts instead of silently falling back to legacy hard-coded behavior.

The bridge deliberately leaves the canonical Run/Evidence/State semantics and pure HTML projection unchanged. This keeps historical bundle reproduction stable while the source-control seam is migrated. A later cleanup can fold the bridge directly into the runner once the monolith is split into adapter/renderer modules.

## Migration rule

`radar_master.json` is the hand-edited source of truth for sources, taxonomy, routing categories, stream routing and profiles. `config/streams.yml` remains the temporary query catalog and `config/scoring.yml` remains the temporary non-category scoring-policy input; source/category edits belong only in the master JSON.
