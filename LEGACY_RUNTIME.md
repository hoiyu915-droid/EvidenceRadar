# Legacy Python Runtime

The Python crawler, renderer and GitHub Actions workflow were the EvidenceRadar runtime through 2026-08-08.

They are no longer part of the active architecture. The historical implementation is retained under `legacy/python-runtime/` only to preserve provenance and prior tests. It must not be presented as the current execution path, scheduled, or used to write new artifacts or canonical State.

The active protocol now supports two cleanly separated lanes: ChatGPT Work and the new root-level GitHub Actions runner in `tools/run_github_radar.py`. The new runner shares artifact schemas with Work but does not import or invoke the archived crawler.

Historical contents retained without semantic modification:

- `legacy/python-runtime/src/`
- `legacy/python-runtime/tests/`
- `legacy/python-runtime/requirements.txt`
- repository `daily/`
- repository `state/`

The historical files immediately under `state/` remain snapshots. New GitHub lane state uses the explicitly separate `state/current/` path.

The known production history, including previous API-rate-limit and retry behavior, remains historical evidence rather than an active service guarantee.
