# EvidenceRadar Runtime Release

EvidenceRadar supports an immutable, versioned Runtime ZIP for local execution.
This is a packaging/deployment mode for the existing automated discovery
producer; it is not a third evidence-review lane and does not change the four
canonical artifact contracts.

## Separation of responsibilities

```text
Git repository            development and review of Runtime source
GitHub Runtime Release    immutable executable source package
External State            mutable cross-run history
External output           canonical Run / State / Evidence / HTML
External runs directory   append-only immutable run snapshots
```

A normal local run must not edit any file extracted from the Runtime ZIP. Code
changes are made in the repository, reviewed and tested, then released as a new
Runtime version.

## Release contents

A release publishes exactly two assets:

```text
EvidenceRadar-Runtime-v<VERSION>.zip
EvidenceRadar-Runtime-v<VERSION>.zip.sha256
```

The ZIP contains the same portable protocol/config/schema/renderer/runner
surface selected by the existing Work Pack allow-list, plus the Runtime
contract files and local Runtime verifier/runner. It intentionally excludes:

- `.git/` and `.github/`
- credentials and `.env` files
- `state/`
- `artifacts/`
- `runs/` and `daily/`
- `legacy/`
- tests and release-build tooling

`RUNTIME_MANIFEST.json` binds every packaged file to SHA-256 and an exact clean
40-character source commit. The manifest records the expected Python version,
semantic contract, Runtime version, package file list, and the distinction
between the canonical capability lane (`github_actions`) and local execution
host (`local_runtime`).

The archive SHA-256 is stored in the sidecar rather than inside the ZIP manifest
to avoid a self-referential archive hash.

## Build from source

A Runtime release is buildable only from a clean Git checkout. From repository
root:

```sh
python3 tools/build_runtime_release.py --output-dir dist
python3 tools/verify_runtime_release.py \
  --archive dist/EvidenceRadar-Runtime-v$(cat runtime/VERSION).zip \
  --checksum dist/EvidenceRadar-Runtime-v$(cat runtime/VERSION).zip.sha256
```

The builder reuses `release/work-pack-manifest.json` as the portable source
allow-list and adds only the Runtime-specific contract and entrypoint files. It
does not maintain a second copy of the protocol/config/schema package list.

With the same source commit and `SOURCE_DATE_EPOCH`, builds are byte
reproducible.

## GitHub Release publication

The `Runtime release` workflow is the authoritative publication path. It is
manual by design because a Runtime version is immutable.

Before publishing, the workflow:

1. requires the workflow to run from `main`;
2. checks out full Git history and tags;
3. installs active runtime requirements;
4. runs public-release validation and the full unittest suite;
5. builds the deterministic Runtime ZIP;
6. verifies the ZIP and checksum;
7. extracts it into a fresh directory and verifies the extracted tree;
8. refuses to reuse an existing `runtime-v<VERSION>` tag or GitHub Release;
9. creates the GitHub Release and uploads the ZIP plus checksum.

To change Runtime code after publication, bump `runtime/VERSION` and publish a
new version. Do not replace or mutate an existing release asset.

## Local user workflow

Keep runtime, State, and output separate:

```text
workspace/
  runtime/                       # extracted ZIP; immutable
  input/
    EvidenceRadar_State.json     # latest State downloaded from repository
  output/                        # current four-file bundle
  runs/                          # optional immutable snapshots
```

Install requirements into the chosen environment:

```sh
python3 -m pip install -r workspace/runtime/requirements.txt
```

Then run:

```sh
python3 workspace/runtime/tools/run_local_runtime.py \
  --state workspace/input/EvidenceRadar_State.json \
  --output-dir workspace/output \
  --runs-dir workspace/runs
```

The wrapper performs these gates in order:

1. verify every declared Runtime source file against `RUNTIME_MANIFEST.json`;
2. reject State/output/runs paths that live inside the Runtime tree;
3. check the required runtime dependencies;
4. invoke the canonical `tools/run_github_radar.py` producer using the exact
   manifest `source_commit` as `--protocol-commit`;
5. validate the resulting four-file bundle with
   `tools/validate_delivery_bundle.py` against that exact producer commit;
6. verify the Runtime tree again after execution;
7. write `EvidenceRadar_RuntimeReceipt.json` outside the Runtime tree.

The receipt records the Runtime version, exact source commit, manifest SHA-256,
execution host, canonical lane, run ID and SHA-256 of the four output artifacts.
It is an audit receipt, not a fifth canonical EvidenceRadar artifact.

## Why canonical artifacts still say `github_actions`

`execution_lane` describes the producer/capability contract, not whether the
process happened on a GitHub-hosted VM. The local Runtime package executes the
same automated discovery producer and the same source-access limitations as the
GitHub Actions lane. Changing the host therefore must not imply ChatGPT Work
claim-review capability.

The separate Runtime receipt records `execution_host: local_runtime`, so local
execution remains auditable without widening the semantic-contract lane enum or
forking the producer implementation.

## State rules

The Runtime ZIP never contains mutable State. For an existing Radar deployment,
always provide the latest canonical `EvidenceRadar_State.json`; otherwise
cross-run deduplication history is incomplete. A run writes the updated State to
the external `--state` path only after the canonical bundle has validated.

The updated State and output bundle may then be committed/uploaded by the caller
using the repository's existing writeback process. Runtime source itself is not
written back.
