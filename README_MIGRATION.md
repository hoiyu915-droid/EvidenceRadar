# EvidenceRadar master control migration

EvidenceRadar now separates the global source catalog from the sources selected for one reader or run. A source can be known to the system without being active in every profile.

## Authoritative control

`config/radar_master.json` owns source identities and capabilities, reusable source groups, the scholarly taxonomy, routing categories, stream-to-source routing, profiles and runtime limits. Existing query text and non-category scoring policy remain compatibility inputs during this migration. Adapter implementation remains code; JSON selects which implemented adapter a source uses.

`tools/radar_control.py` validates and compiles that control plane into the runtime source-check contract, stream set, scoring categories, category thresholds, source endpoints, adapter mapping and effective limits. `tools/generate_legacy_config.py` can still project `streams.yml` and `scoring.yml` while downstream consumers migrate.

## OA-biased source catalog

Publisher-native and repository/index sources are catalogued separately from profile activation. The first verified active OA journal feeds are Nature Communications, Communications Physics, Communications Chemistry, Scientific Reports and JAMA Network Open. Additional OA journals and OA backbones may be recorded as `planned` / disabled until their endpoint and adapter semantics are verified; planned sources are never silently searched.

Source groups such as `oa_prestige_general`, `oa_prestige_biomed`, `oa_prestige_physics`, `oa_prestige_chemistry` and `oa_prestige_llm` let profiles select only the literature relevant to that reader. The catalog is global; the active source set is profile-derived.

## Profiles

- `current_focus`: compatibility profile for the existing clinical, sport, LLM and human-AI streams.
- `general_research`: broad Nature subject discovery plus the verified OA general/physics/chemistry layer.
- `current_plus_general`: formal daily production union.
- `medicine_reader`, `llm_reader`, `physics_reader`, `chemistry_reader`: narrower examples that demonstrate reader-specific source selection.
- `oa_general_reader`: only the verified broad OA journal layer.

## Limits

`radar_master.json.limits` is the source of truth for discovery, featured-selection and publisher-verification budgets. Profiles may override individual limit fields without changing global defaults.

The default effective limits are 40 results per query, 5 featured items per category with a hard maximum of 8, and publisher verification target/hard/per-domain budgets of 10/15/2. `max_per_source` and `max_per_category` are intentionally `null`: complete-ledger semantics for those truncation modes are not implemented, so non-null values fail closed rather than pretending to cap the pool. The old `streams.yml` `hard_max_per_category` value is therefore not projected into runtime configuration.

`tools/apply_master_runtime_config.py` materializes profile-derived output/deployment limits into a disposable runtime checkout. Scheduled production uses the master defaults; explicit workflow-dispatch publisher limits remain manual run overrides.

## Runtime migration bridge

`tools/apply_master_control_runtime.py` remains the fail-closed, versioned bridge for the existing monolithic runner. The daily workflow first materializes profile limits, then applies the source-routing bridge before Stage A. Exact replacement-point drift aborts instead of silently falling back to legacy hard-coded behavior.

## Delivery naming

Canonical validator inputs remain:

- `EvidenceRadar_Report.html`
- `EvidenceRadar_State.json`
- `EvidenceRadar_Evidence.json`
- `EvidenceRadar_Run.json`

`tools/package_work_delivery.py` additionally creates byte-identical direct-delivery siblings using the run timestamp in Asia/Tokyo:

`YYYYMMDD_HHMMSS__<canonical filename>`

The canonical bundle/ZIP retains the stable filenames for validators, State handoff and reproducibility. User-facing direct attachments use the timestamped aliases, and packaging refuses to overwrite an existing alias or run bundle.

## Migration rule

Edit sources, source groups, taxonomy, routing, profiles and limits in `radar_master.json`. `config/streams.yml` remains the temporary query catalog and `config/scoring.yml` remains the temporary non-category scoring-policy input; neither should be treated as authority for source selection or runtime limits.
