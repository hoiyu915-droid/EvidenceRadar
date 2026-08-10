# EvidenceRadar immutable Runtime Release

This directory defines the contract for the versioned local Runtime package.
The Runtime ZIP is a release artifact, not a mutable working copy of the
repository.

## Contract

- Runtime source files are immutable after a release is built.
- `EvidenceRadar_State.json` is external mutable input/output and is never
  bundled into the Runtime ZIP.
- `EvidenceRadar_Run.json`, `EvidenceRadar_Evidence.json`,
  `EvidenceRadar_State.json`, and `EvidenceRadar_Report.html` are written only
  to an external output directory.
- History directories, credentials, Git metadata, CI files, generated reports,
  and legacy crawlers are excluded from the Runtime ZIP.
- Every release ZIP contains `RUNTIME_MANIFEST.json`, which binds the package
  contents to an exact clean Git commit and records SHA-256 for every packaged
  file.
- The ZIP itself is accompanied by a `.sha256` sidecar. The archive hash is not
  embedded inside `RUNTIME_MANIFEST.json` because doing so would create a
  self-referential hash.
- A local Runtime execution uses the same automated discovery capability as the
  GitHub Actions lane, so canonical artifacts continue to record
  `execution_lane: github_actions`. `EvidenceRadar_RuntimeReceipt.json`
  separately records `execution_host: local_runtime` and the Runtime version.

## User flow

1. Download `EvidenceRadar-Runtime-v<VERSION>.zip` and its matching
   `.zip.sha256` from the GitHub Release.
2. Verify the archive before extraction:

   ```sh
   python3 tools/verify_runtime_release.py \
     --archive EvidenceRadar-Runtime-v<VERSION>.zip \
     --checksum EvidenceRadar-Runtime-v<VERSION>.zip.sha256
   ```

   If the verifier is not yet available locally, verify the SHA-256 sidecar
   with the operating system first, extract the ZIP, then run the packaged
   verifier with `--root`.
3. Extract into a dedicated Runtime directory and install the pinned runtime
   requirements into the chosen Python environment:

   ```sh
   python3 -m pip install -r requirements.txt
   ```

4. Keep State and outputs outside the Runtime directory, for example:

   ```text
   workspace/
     runtime/   # extracted release; treat as read-only
     input/EvidenceRadar_State.json
     output/
     runs/
   ```

5. Execute:

   ```sh
   python3 runtime/tools/run_local_runtime.py \
     --state input/EvidenceRadar_State.json \
     --output-dir output \
     --runs-dir runs \
     --translation-request input/EvidenceRadar_TranslationRequest.json
   ```

   This Stage A command ends normally with `TRANSLATION_REQUIRED`; State is
   unchanged. Upload the request JSON to an ordinary ChatGPT chatbot and save
   its JSON-only answer as `input/EvidenceRadar_TranslationResponse.json`.
   Resume Stage B with the same request:

   ```sh
   python3 runtime/tools/run_local_runtime.py \
     --state input/EvidenceRadar_State.json \
     --output-dir output \
     --runs-dir runs \
     --translation-request input/EvidenceRadar_TranslationRequest.json \
     --translation-response input/EvidenceRadar_TranslationResponse.json
   ```

No model API key, GitHub token, Codex, Copilot, or ChatGPT Work is required.
The local runner verifies the extracted Runtime before execution, passes the
manifest's exact source commit to the canonical producer, validates the
resulting four-file bundle, verifies the Runtime again after execution, and
writes an external `EvidenceRadar_RuntimeReceipt.json` audit receipt.

For maintainer build/release instructions, see
[`docs/RUNTIME_RELEASE.md`](../docs/RUNTIME_RELEASE.md).
