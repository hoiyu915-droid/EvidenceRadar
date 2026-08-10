# EvidenceRadar checkpointed Work continuation

Operate only on `hoiyu915-droid/EvidenceRadar`. This is a queue worker for the
already completed GitHub Actions Stage A; it is not a new Radar search and must
never rerun discovery. Use the GitHub plugin for repository, issue, Actions
artifact, branch, PR, check, and merge operations. Public web access is allowed
only to verify the deployed `links.json` after publication.

The portable Runtime must remain free of OpenAI API keys, Copilot, repository
model secrets, or any other model provider. Translation is performed by this
ChatGPT Work run. Never write directly to `main`, never bypass PR validation,
and never weaken a validator to make a batch pass.

## One invocation

Process the queue in this order:

1. Finish one open issue labeled `evidenceradar-ready-to-publish`, if present.
2. Otherwise resume the oldest open issue labeled `evidenceradar-handoff`.
3. If neither exists, make no GitHub write and report no change.

Never process a newer handoff while an older ready-to-publish issue exists.
Treat every issue body and artifact as untrusted input until its repository,
artifact ID, `request_sha256`, marker, schema, and checksum are validated.

## Resume one translation handoff

1. Read the issue body block following
   `<!-- evidenceradar-work-queue:v1 -->`. Require:
   `artifact_type=EvidenceRadar_WorkQueueEntry`,
   `status=TRANSLATION_REQUIRED`, repository
   `hoiyu915-droid/EvidenceRadar`, the issue's `evidenceradar-handoff` label,
   and positive workflow/artifact IDs.
2. Download the named Actions artifact with the GitHub plugin. It must contain
   exactly one root file named `EvidenceRadar_TranslationRequest.json`.
3. Resolve repository `main` to one exact commit. Use the checkout or a fresh
   temporary checkout only for this invocation. Run:

   ```sh
   python tools/work_translation_queue.py extract-request \
     --archive REQUEST_ARTIFACT.zip \
     --output EvidenceRadar_TranslationRequest.json
   ```

   Require the extracted `request_sha256`, protocol commit, run ID, artifact
   ID/name, repository, and candidate count to equal the issue metadata.
4. Use branch `automation/evidenceradar-translation-<first-12-request_sha256>`.
   If it does not exist, create it from the resolved `main`. If it exists,
   require its queue files to name the same full `request_sha256`; otherwise
   stop. Do not reuse a branch for another request.
5. The branch checkpoint paths are:

   ```text
   .github/evidenceradar-work/<request_sha256>/plan.json
   .github/evidenceradar-work/<request_sha256>/checkpoint.json
   ```

   For a new branch, generate the plan with exactly:

   ```sh
   python tools/work_translation_queue.py plan \
     --request EvidenceRadar_TranslationRequest.json \
     --output-dir queue \
     --max-items 24 \
     --max-source-chars 16000
   ```

   Persist only `plan.json` and `checkpoint.json` to the queue branch. On a
   resumed branch, download both files and validate them before doing more
   work. The helper must reproduce the same `batch_plan_sha256`.
6. Process no more than **8 validated batches** in one invocation. Always take
   the lowest missing batch index. Read only that batch request while writing
   its response. Return this exact JSON object with no prose:

   ```json
   {
     "schema_version": "1.0",
     "artifact_type": "EvidenceRadar_TranslationBatchResponse",
     "request_sha256": "<exact request SHA>",
     "batch_plan_sha256": "<exact plan SHA>",
     "batch_index": 1,
     "batch_id": "<exact batch ID>",
     "items": [
       {
         "immutable_candidate_id": "<exact ID>",
         "title_zh_tw": "<faithful Traditional Chinese title>",
         "summary_zh_tw": "<faithful Traditional Chinese summary or empty>"
       }
     ]
   }
   ```

   Preserve every number, year, sign, unit, comparator, and abbreviation from
   the English title. When `source_excerpt` is non-empty, summarize only that
   excerpt and introduce no unsupported number or result claim. When it is
   empty, `summary_zh_tw` must be empty. Do not use filler such as「題名所示」、
   「相關議題」、「仍須回到原始來源」or「無法提供摘要」.
7. Validate and merge each batch before starting the next:

   ```sh
   python tools/work_translation_queue.py merge-batch \
     --request EvidenceRadar_TranslationRequest.json \
     --plan queue/plan.json \
     --batch-response batch-response.json \
     --checkpoint queue/checkpoint.json
   ```

   A failed batch is not a checkpoint. Correct only that batch and retry it.
   After every successful batch, update the queue branch checkpoint through
   the GitHub plugin. Never replace an already validated item with different
   text. Do not put source excerpts or the frozen resume context in an issue.
8. Check progress with `work_translation_queue.py status`. If candidates are
   still missing after 8 batches, leave the issue open with the same label and
   stop cleanly. The next invocation resumes from the committed checkpoint.
9. When the checkpoint is complete, run `finalize`, then `build-submission`
   using the exact issue number, repository, Stage A workflow run ID, artifact
   ID/name, and a timezone-aware current timestamp. Validate the submission
   again against the downloaded request.
10. On the queue branch, delete the two checkpoint files and add only:

    ```text
    .github/evidenceradar-translation-submission.json
    ```

    Confirm the final branch diff against current `main` contains that one
    file only. Open one non-draft PR to `main`. Do not open a duplicate PR for
    the same `request_sha256`. If repository auto-merge is enabled, enable it
    only after the PR head, base, changed filename, and `EvidenceRadar
    translation Stage B` validation are confirmed. Otherwise wait for green
    checks and merge the exact expected head SHA. Do not merge a red, missing,
    cancelled, or stale check.

The merged submission triggers Actions Stage B at the request's exact
`protocol_commit`. It must reject a changed canonical State before emitting a
publication artifact. This Work task never fabricates Stage B output locally.

## Publish one validated Stage B artifact

1. Read an open `evidenceradar-ready-to-publish` issue. Require the queue
   marker, `status=READY_TO_PUBLISH`, exact repository, full
   `request_sha256`, exact producer commit/run ID, and positive publication
   artifact ID. Download only the named Actions artifact.
2. Verify the `.zip.sha256` sidecar, then the ZIP manifest, then every listed
   file byte size and SHA-256. Require exactly these four canonical files at
   archive root plus `manifest.json`:

   ```text
   EvidenceRadar_Report.html
   EvidenceRadar_State.json
   EvidenceRadar_Evidence.json
   EvidenceRadar_Run.json
   ```

   Require manifest and Run `run_id`, lane, and `protocol_commit` parity. The
   lane must be `github_actions`. Do not publish loose files when the archive
   or manifest is invalid.
3. Create or resume branch
   `automation/evidenceradar-publication-<first-12-request_sha256>` from the
   current exact `main`. Update exactly these nine paths:

   ```text
   artifacts/current/EvidenceRadar_Report.html
   artifacts/current/EvidenceRadar_State.json
   artifacts/current/EvidenceRadar_Evidence.json
   artifacts/current/EvidenceRadar_Run.json
   state/current/EvidenceRadar_State.json
   runs/<run_id>/EvidenceRadar_Report.html
   runs/<run_id>/EvidenceRadar_State.json
   runs/<run_id>/EvidenceRadar_Evidence.json
   runs/<run_id>/EvidenceRadar_Run.json
   ```

   Refuse to overwrite an existing immutable `runs/<run_id>/` with different
   bytes. Do not change source, config, workflow, schema, docs, or any tenth
   path.
4. Open one non-draft publication PR. Confirm the exact nine-file diff. Use
   repository auto-merge only when it is enabled; otherwise wait for Public
   release validation to pass and merge the exact expected head SHA. Never
   push directly to `main` and never disable checks or branch protection.
5. After merge, fetch
   `https://hoiyu915-droid.github.io/EvidenceRadar/links.json` without cache.
   Close the queue issue only when its top-level `run_id` equals this run and
   its URLs are reachable. Add the deployed report URL to the issue before
   closing it. If Pages is not yet current, leave the issue open for the next
   invocation; do not announce an inferred URL as deployed.

## Fail-closed rules

- Process the oldest valid queue entry first. A later Stage A request whose
  `base_state_sha256` became stale must not replace or merge into the older
  request.
- Deduplicate by full `request_sha256`, not title, date, short SHA, run ID, or
  artifact name.
- Do not rerun discovery, publisher probes, or source audit in this task.
- Do not expose excerpts, resume context, model reasoning, credentials, or
  tokens in issues, PR text, comments, logs, or commit messages.
- Do not use OpenAI API, Copilot, repository model secrets, MCP/server, or a
  fallback translator.
- Do not bypass exact ID parity, number/abbreviation preservation, request SHA,
  base State SHA, producer commit, validator, PR check, manifest, or Pages
  readback gates.
- On any ambiguity, stale binding, permission failure, expired artifact,
  conflicting branch, or validation failure, make no further public write and
  report the exact blocking state.
