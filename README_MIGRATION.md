# EvidenceRadar master control migration

EvidenceRadar now separates the global source catalog from the sources selected for one reader or run. A source can be known to the system without being active in every profile.

## Authoritative control

`config/radar_master.json` owns source identities and capabilities, reusable source groups, the scholarly taxonomy, routing categories, stream-to-source routing, profiles and runtime limits. Existing query text and non-category scoring policy remain compatibility inputs during this migration. Adapter implementation remains code; JSON selects which implemented adapter a source uses.

`tools/radar_control.py` validates and compiles source/profile routing into the runtime source-check contract, stream set, scoring categories, category thresholds, source endpoints, adapter mapping and effective limits. The canonical runner consumes that compiled result in memory. `tools/apply_master_runtime_config.py` remains only as a legacy inspection/projection utility; formal execution never mutates producer or config bytes.

## OA-biased source catalog

Publisher-native and repository/index sources are catalogued separately from profile activation. The first verified active OA journal feeds are Nature Communications, Communications Physics, Communications Chemistry, Scientific Reports and JAMA Network Open. Additional OA journals and OA backbones may be recorded as `planned` / disabled until their endpoint and adapter semantics are verified; planned sources are never silently searched.

Source groups such as `oa_prestige_general`, `oa_prestige_biomed`, `oa_prestige_physics`, `oa_prestige_chemistry` and `oa_prestige_llm` let profiles select only the literature relevant to that reader. The catalog is global; the active source set is profile-derived.

## Profiles

- `current_focus`: compatibility profile for the existing clinical, sport, LLM and human-AI streams.
- `general_research`: broad Nature subject discovery plus the verified OA general/physics/chemistry layer.
- `owner_daily`: scheduled reader-scoped production profile for the owner’s clinical, sport, LLM and human-AI interests, with JAMA Network Open and topic-routed Nature Communications OA discovery.
- `current_plus_general`: broad integration/stress profile; not the scheduled daily default.
- `medicine_reader`, `llm_reader`, `physics_reader`, `chemistry_reader`: narrower examples that demonstrate reader-specific source selection.
- `oa_general_reader`: only the verified broad OA journal layer.


## LLM / OA prestige catalog

The catalog may know about strong OA/public venues without activating them for every run. TMLR, JMLR, TACL, COLM, ICLR and NeurIPS are catalogued as LLM/ML prestige candidates. Venue-specific entries that still need accepted-only, venue-aware or first-party adapter semantics remain disabled and carry an explicit `activation_blocker`; the active LLM discovery containers remain OpenReview, ACL Anthology and PMLR until those semantics are implemented.

`owner_daily` intentionally excludes broad Nature subject feeds, Scientific Reports and unrelated physics/chemistry sources. The global catalog remains reusable by `general_research`, discipline readers and the broad `current_plus_general` integration profile.

## Limits

`radar_master.json.limits` is the source of truth for discovery, featured-selection and publisher-verification budgets. Profiles may override individual limit fields without changing global defaults.

The default effective discovery limit is 40 results per query. There is deliberately **no global candidate hard cap**: every deduplicated discovery candidate remains in the Run/State ledger. `max_per_source`, discovery `max_per_category`, and `global_candidate_hard_max` are reserved as `null`; setting them non-null fails closed rather than silently discarding candidates.

Featured selection has a separate ranking pool (default 30 candidates per category) that limits only which candidates compete for digest slots; it never mutates or truncates the complete ledger. Global featured defaults remain target 5 / hard maximum 8 per category. Profiles may override category-specific target/hard values and may add a final-digest target/hard maximum. `owner_daily` uses clinical 4/6, sport science 3/5, sport nutrition/fitness 3/5, LLM 6/10, and human-AI 4/6, with a final digest target/hard maximum of 20/32.

Publisher verification remains an independent network budget: target/hard/per-domain 10/15/2 by default. The old `streams.yml` `hard_max_per_category` value is not projected into runtime configuration.

The runner projects profile-derived discovery, ranking, featured and publisher limits in memory. Scheduled production resolves `owner_daily`; explicit workflow-dispatch publisher limits remain manual run overrides.

## Runtime migration bridge

Master-control support is checked into the monolithic runner, so Stage A, Stage B, Work Pack and Runtime execute identical producer bytes. `tools/apply_master_control_runtime.py --check` is a validator-only, fail-closed integration guard; its historical write/upgrade mode is retired because it could create a partially integrated producer. Missing `radar_master.json` aborts; there is no implicit legacy nine-source fallback.

## Delivery naming

Canonical validator inputs remain:

- `EvidenceRadar_Report.html`
- `EvidenceRadar_State.json`
- `EvidenceRadar_Evidence.json`
- `EvidenceRadar_Run.json`

`tools/materialize_delivery_aliases.py` creates byte-identical direct-delivery siblings after the canonical bundle has validated and packaged, using the run timestamp in Asia/Tokyo:

`YYYYMMDD_HHMMSS__<canonical filename>`

The canonical bundle/ZIP retains the stable filenames for validators, State handoff and reproducibility. User-facing direct attachments use the timestamped aliases; the package and alias materializer each refuse to overwrite their existing targets.

## Migration rule

Edit sources, source groups, taxonomy, routing, profiles and limits in `radar_master.json`. `config/streams.yml` remains the temporary query catalog and `config/scoring.yml` remains the temporary non-category scoring-policy input; neither should be treated as authority for source selection or runtime limits.
