from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "legacy" / "github-actions" / "translation-stage-b.yml"
PROMPT = ROOT / "templates" / "work-stage-b-automation.md"


class WorkAutomationSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    @classmethod
    def _step_script(cls, name: str) -> str:
        marker = f"      - name: {name}\n"
        start = cls.workflow.index(marker)
        end = cls.workflow.find("\n      - name:", start + len(marker))
        block = cls.workflow[start : end if end >= 0 else None]
        run_marker = "        run: |\n"
        return textwrap.dedent(block.split(run_marker, 1)[1])

    @staticmethod
    def _write_fake_runner(root: Path) -> Path:
        runner = root / "tools" / "run_github_radar.py"
        runner.parent.mkdir(parents=True)
        runner.write_text(
            textwrap.dedent(
                """
                import json
                import os
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                if "--help" in args:
                    suffix = " [--profile PROFILE]" if os.environ.get("FAKE_SUPPORTS_PROFILE") == "true" else ""
                    print("usage: fake-runner" + suffix)
                    raise SystemExit(0)

                Path(os.environ["FAKE_ARGS_LOG"]).write_text(
                    json.dumps(args), encoding="utf-8"
                )
                output = Path(args[args.index("--output-dir") + 1])
                output.mkdir(parents=True, exist_ok=True)
                profile = os.environ.get("FAKE_OUTPUT_PROFILE", "")
                if profile == "__BOUND_ARGUMENT__":
                    profile = args[args.index("--profile") + 1]
                document = {"profile_id": profile} if profile else {}
                for filename in (
                    "EvidenceRadar_State.json",
                    "EvidenceRadar_Evidence.json",
                    "EvidenceRadar_Run.json",
                ):
                    (output / filename).write_text(
                        json.dumps(document), encoding="utf-8"
                    )
                print(json.dumps({"run_id": "stage-b-fixture", "run_status": "SUCCESS"}))
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return runner

    @staticmethod
    def _run_shell(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_submission_is_the_only_automatic_stage_b_trigger(self) -> None:
        pull_request = self.workflow[
            self.workflow.index("  pull_request:") : self.workflow.index("  push:")
        ]
        push = self.workflow[
            self.workflow.index("  push:") : self.workflow.index("\npermissions:")
        ]
        self.assertIn('".github/evidenceradar-translation-submission.json"', pull_request)
        self.assertIn('".github/evidenceradar-translation-submission.json"', push)
        for forbidden in ('"tools/**"', '"schemas/**"', '"artifacts/current/**"'):
            self.assertNotIn(forbidden, pull_request)
            self.assertNotIn(forbidden, push)

    def test_pr_validates_but_only_main_push_resumes(self) -> None:
        self.assertIn("if: github.event_name == 'pull_request'", self.workflow)
        self.assertIn("Stop after PR validation", self.workflow)
        for marker in (
            "Check out the exact Stage A producer",
            "Resume Stage B without rediscovery",
            "Validate and package the Stage B publication candidate",
            "Upload immutable Stage B publication candidate",
            "Advance queue entry to ready-to-publish",
        ):
            block = self.workflow[self.workflow.index(marker) - 120 : self.workflow.index(marker) + 160]
            self.assertIn("if: github.event_name == 'push'", block)

    def test_exact_request_producer_and_current_state_are_both_bound(self) -> None:
        for marker in (
            'git cat-file -e "$PRODUCER_COMMIT^{commit}"',
            'git merge-base --is-ancestor "$PRODUCER_COMMIT" HEAD',
            'git worktree add --detach "$RUNNER_TEMP/evidenceradar-producer"',
            '--protocol-commit "$PRODUCER_COMMIT"',
            '--translation-request "$RUNNER_TEMP/EvidenceRadar_TranslationRequest.json"',
            '--translation-response "$RUNNER_TEMP/EvidenceRadar_TranslationResponse.json"',
            '--state "$GITHUB_WORKSPACE/state/current/EvidenceRadar_State.json"',
            "--require-current-producer",
            "--require-semantic-contract-v3",
            "--reject-dirty",
        ):
            self.assertIn(marker, self.workflow)

    def test_profile_capability_routes_modern_and_legacy_producers(self) -> None:
        script = self._step_script("Resolve exact producer profile capability")
        cases = (
            ("modern-bound", "true", "owner_daily", 0, "supports_profile=true"),
            ("modern-unbound", "true", "", 1, "request-bound profile_id"),
            ("legacy-unbound", "false", "", 0, "supports_profile=false"),
            ("legacy-bound", "false", "owner_daily", 1, "legacy producer"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_fake_runner(root)
            for label, capability, profile, exit_code, marker in cases:
                with self.subTest(case=label):
                    github_output = root / f"{label}.output"
                    result = self._run_shell(
                        script,
                        {
                            "PRODUCER_ROOT": str(root),
                            "BOUND_PROFILE": profile,
                            "GITHUB_OUTPUT": str(github_output),
                            "FAKE_SUPPORTS_PROFILE": capability,
                        },
                    )
                    self.assertEqual(exit_code, result.returncode, result.stderr)
                    combined = result.stdout + result.stderr
                    if exit_code == 0:
                        combined += github_output.read_text(encoding="utf-8")
                    self.assertIn(marker, combined)

    def test_resume_passes_and_verifies_only_modern_profile_binding(self) -> None:
        script = self._step_script("Resume Stage B without rediscovery")
        cases = (
            ("modern", "true", "owner_daily", "__BOUND_ARGUMENT__", 0),
            ("legacy", "false", "", "", 0),
            ("modern-output-mismatch", "true", "owner_daily", "medicine_reader", 1),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_fake_runner(root)
            for label, capability, profile, output_profile, exit_code in cases:
                with self.subTest(case=label):
                    case_root = root / label
                    case_root.mkdir()
                    args_log = case_root / "args.json"
                    result = self._run_shell(
                        script,
                        {
                            "PRODUCER_ROOT": str(root),
                            "PRODUCER_COMMIT": "a" * 40,
                            "BOUND_PROFILE": profile,
                            "PRODUCER_SUPPORTS_PROFILE": capability,
                            "RADAR_OUTPUT": str(case_root / "output"),
                            "RUNNER_TEMP": str(case_root),
                            "GITHUB_WORKSPACE": str(case_root),
                            "GITHUB_OUTPUT": str(case_root / "github.output"),
                            "FAKE_ARGS_LOG": str(args_log),
                            "FAKE_OUTPUT_PROFILE": output_profile,
                        },
                    )
                    self.assertEqual(exit_code, result.returncode, result.stderr)
                    args = json.loads(args_log.read_text(encoding="utf-8"))
                    if capability == "true":
                        profile_index = args.index("--profile")
                        self.assertEqual(profile, args[profile_index + 1])
                    else:
                        self.assertNotIn("--profile", args)
                    if exit_code:
                        self.assertIn("Stage B output profile mismatch", result.stderr)

    def test_request_profile_output_rejects_unsafe_ids(self) -> None:
        self.assertIn(
            're.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", profile_id)',
            self.workflow,
        )

    def test_queue_issue_cannot_redirect_stage_b(self) -> None:
        for marker in (
            "evidenceradar-work-queue:v1",
            '"evidenceradar-handoff" not in labels',
            '"repository": os.environ["GITHUB_REPOSITORY"]',
            '"artifact_id": int(os.environ["ARTIFACT_ID"])',
            '"request_sha256": summary["request_sha256"]',
            '"status": "TRANSLATION_REQUIRED"',
        ):
            self.assertIn(marker, self.workflow)

    def test_runtime_remains_free_of_model_credentials(self) -> None:
        for forbidden in (
            "OPENAI_API_KEY",
            "EVIDENCERADAR_TRANSLATION_API_KEY",
            "copilot",
            "api.openai.com",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("actions: read", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)

    def test_publication_is_an_artifact_not_a_direct_state_write(self) -> None:
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            self.workflow,
        )
        self.assertIn("evidenceradar-publication-", self.workflow)
        self.assertNotIn("git add", self.workflow)
        self.assertNotIn("git push", self.workflow)
        self.assertNotIn("gh pr create", self.workflow)
        self.assertNotIn("artifacts/current/EvidenceRadar_Report.html", self.workflow)

    def test_durable_work_prompt_is_present(self) -> None:
        self.assertTrue(PROMPT.is_file())
        prompt = PROMPT.read_text(encoding="utf-8")
        for marker in (
            "evidenceradar-handoff",
            "evidenceradar-ready-to-publish",
            "work_translation_queue.py",
            "8 validated batches",
            "request_sha256",
            "auto-merge",
            "links.json",
        ):
            self.assertIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
