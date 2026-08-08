# Legacy Python Runtime

The Python crawler, renderer and GitHub Actions workflow were the EvidenceRadar runtime through 2026-08-08.

They are no longer part of the active architecture. The historical implementation is retained under `legacy/python-runtime/` only to preserve provenance and prior tests. It must not be presented as the current execution path, scheduled, or used to write `daily/` and `state/`.

The active runtime is the ChatGPT Work protocol defined in `EVIDENCE_RADAR_PROTOCOL.md`.

Historical contents retained without semantic modification:

- `legacy/python-runtime/src/`
- `legacy/python-runtime/tests/`
- `legacy/python-runtime/requirements.txt`
- repository `daily/`
- repository `state/`

The known production history, including previous API-rate-limit and retry behavior, remains historical evidence rather than an active service guarantee.
