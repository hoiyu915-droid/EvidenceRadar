from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_IMPL = ROOT / "tools" / "run_github_radar_core.py"


class ProtocolSurfaceTests(unittest.TestCase):
    def test_user_runtime_enables_only_chatgpt_work(self) -> None:
        output = (ROOT / "config/output.yml").read_text(encoding="utf-8")
        deployment = (ROOT / "config/deployment.yml").read_text(encoding="utf-8")
        self.assertIn("owners: [chatgpt_work]", output)
        self.assertIn("chatgpt_work:", output)
        self.assertIn("github_actions:", output)
        self.assertIn("github_actions: false", output)
        self.assertIn("active_radar_execution: false", deployment)
        self.assertIn("archived_workflow_directory: legacy/github-actions", deployment)
        self.assertIn("mcp: false", output)
        self.assertIn("external_server: false", output)
        self.assertIn("codex: false", output)

    def test_active_workflows_do_not_execute_radar(self) -> None:
        self.assertFalse((ROOT / ".manual-run").exists())
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
        self.assertEqual(
            [
                "pages.yml",
                "public-release.yml",
                "runtime-release.yml",
                "work-pack-release.yml",
            ],
            sorted(path.name for path in workflows),
        )
        for workflow_path in workflows:
            active = workflow_path.read_text(encoding="utf-8")
            self.assertNotIn("Resume Stage B without rediscovery", active, workflow_path.name)
            self.assertNotIn("TRANSLATION_REQUIRED", active, workflow_path.name)
            self.assertNotIn("evidenceradar-handoff", active, workflow_path.name)

        maintenance = (workflow_dir / "public-release.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/validate_public_release.py", maintenance)
        self.assertIn("contents: read", maintenance)
        self.assertNotIn("schedule:", maintenance)
        self.assertIn("workflow_dispatch:", maintenance)
        self.assertNotIn("python src/run.py", maintenance)
        self.assertNotIn("contents: write", maintenance)

        self.assertFalse((workflow_dir / "daily-radar.yml").exists())
        self.assertFalse((workflow_dir / "translation-stage-b.yml").exists())
        archived_workflows = ROOT / "legacy" / "github-actions"
        daily = (archived_workflows / "daily-radar.yml").read_text(encoding="utf-8")
        self.assertIn("ARCHIVED", daily)
        self.assertIn("schedule:", daily)
        self.assertIn("workflow_dispatch:", daily)
        self.assertIn("contents: read", daily)
        self.assertNotIn("contents: write", daily)
        self.assertIn("TRANSLATION_REQUIRED", daily)
        self.assertIn("python tools/run_github_radar.py", daily)

        stage_b = (archived_workflows / "translation-stage-b.yml").read_text(encoding="utf-8")
        self.assertIn("ARCHIVED", stage_b)
        self.assertIn("python tools/work_translation_queue.py validate-submission", stage_b)
        self.assertIn("Resume Stage B without rediscovery", stage_b)
        self.assertIn("--translation-response", stage_b)
        self.assertIn("evidenceradar-ready-to-publish", stage_b)

        pages = (workflow_dir / "pages.yml").read_text(encoding="utf-8")
        self.assertIn(
            "actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128 # v5.0.0",
            pages,
        )
        self.assertIn("python tools/build_pages_site.py", pages)

        runtime_release = (workflow_dir / "runtime-release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", runtime_release)
        self.assertNotIn("schedule:", runtime_release)
        self.assertIn("contents: write", runtime_release)
        self.assertIn("python tools/build_runtime_release.py", runtime_release)
        self.assertIn("python tools/verify_runtime_release.py", runtime_release)
        self.assertIn("immutable Runtime tag already exists", runtime_release)
        self.assertIn("gh release create", runtime_release)

        work_pack_release = (workflow_dir / "work-pack-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", work_pack_release)
        self.assertNotIn("schedule:", work_pack_release)
        self.assertIn("python tools/build_work_pack.py", work_pack_release)
        self.assertIn("python tools/verify_work_pack.py", work_pack_release)
        self.assertIn("EvidenceRadar-WorkPack-current.zip", work_pack_release)

    def test_new_runner_is_separate_from_archived_runtime(self) -> None:
        self.assertFalse((ROOT / "src").exists())
        self.assertTrue((ROOT / "requirements.txt").exists())
        self.assertTrue((ROOT / "requirements.lock").exists())
        self.assertTrue((ROOT / "tools" / "run_github_radar.py").exists())
        self.assertTrue(RUNNER_IMPL.exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "src" / "run.py").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "requirements.txt").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "requirements.lock").exists())

        runner = (ROOT / "tools" / "run_github_radar.py").read_text(encoding="utf-8")
        self.assertNotIn("legacy/python-runtime", runner)

    def test_public_deployment_settings_pin_publisher_budget(self) -> None:
        deployment = (ROOT / "config" / "deployment.yml").read_text(encoding="utf-8")
        self.assertIn("target_min_per_run: 10", deployment)
        self.assertIn("hard_max_per_run: 15", deployment)
        self.assertIn("per_domain_hard_max: 2", deployment)
        self.assertIn("stop_domain_on_http_status: [401, 403, 429]", deployment)
        self.assertIn("padding_forbidden: true", deployment)
        self.assertIn("budget_scope: publisher_network_access_only", deployment)
        self.assertIn("candidate_display_capped_by_this_budget: false", deployment)
        self.assertIn("OPENALEX_API_KEY:", deployment)
        self.assertIn("missing_behavior: record_source_gap", deployment)

    def test_candidate_visibility_is_independent_of_publisher_access_budget(self) -> None:
        output = (ROOT / "config/output.yml").read_text(encoding="utf-8")
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        runner = RUNNER_IMPL.read_text(encoding="utf-8")
        self.assertIn("candidate_display_capped_by_publisher_budget: false", output)
        self.assertIn("display_all_deduplicated: true", output)
        self.assertIn("group_by_category: true", output)
        self.assertIn("displays every deduplicated candidate", protocol)
        self.assertIn("publisher access success is never a display gate", protocol)
        self.assertIn('"candidates": candidate_records', runner)
        self.assertIn("select_display_candidates", runner)

    def test_report_requires_readable_summaries_and_interactive_filters(self) -> None:
        output = (ROOT / "config/output.yml").read_text(encoding="utf-8")
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        runner = RUNNER_IMPL.read_text(encoding="utf-8")
        self.assertIn("include_candidate_content_summary: true", output)
        self.assertIn("interactive_candidate_filters: true", output)
        self.assertIn("candidate_summary_max_chars: 320", output)
        self.assertIn("candidate_summary_language: zh-TW", output)
        self.assertIn("chatgpt_work: native_chatgpt_work", output)
        self.assertIn("ordinary_chatgpt_manual_handoff", output)
        self.assertIn("EvidenceRadar_TranslationRequest.json", output)
        self.assertIn("CHATBOT_TITLE_AND_ABSTRACT_ZH_TW", protocol)
        self.assertIn("CHATBOT_TITLE_ZH_TW", protocol)
        self.assertIn("summary_language: zh-TW", protocol)
        self.assertIn('id="candidate-search"', runner)
        self.assertIn('id="category-filter"', runner)
        self.assertIn('id="triage-filter"', runner)
        self.assertIn('id="source-filter"', runner)
        self.assertIn('"summary_language": "zh-TW"', runner)

    def test_state_merge_and_work_pack_are_public_surfaces(self) -> None:
        self.assertTrue((ROOT / "tools" / "merge_radar_state.py").exists())
        self.assertTrue((ROOT / "tools" / "build_work_pack.py").exists())
        self.assertTrue((ROOT / "tools" / "validate_delivery_bundle.py").exists())
        self.assertTrue((ROOT / "tools" / "render_report_from_artifacts.py").exists())
        self.assertTrue((ROOT / "tools" / "build_pages_site.py").exists())
        self.assertTrue((ROOT / "release" / "work-pack-manifest.json").exists())
        self.assertTrue((ROOT / "docs" / "GITHUB_DEPLOYMENT.md").exists())
        self.assertTrue((ROOT / "docs" / "WORK_SETUP.md").exists())
        self.assertTrue((ROOT / "docs" / "MIGRATION_DUAL_LANE_1.0.md").exists())
        self.assertTrue((ROOT / "docs" / "SEMANTIC_CONTRACT_V3.md").exists())

    def test_v3_semantic_contract_is_executable_public_surface(self) -> None:
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        semantic = (ROOT / "docs" / "SEMANTIC_CONTRACT_V3.md").read_text(encoding="utf-8")
        instructions = (ROOT / "templates" / "gpt-work-instructions.md").read_text(encoding="utf-8")
        validator = (ROOT / "tools" / "validate_delivery_bundle.py").read_text(encoding="utf-8")
        runner = RUNNER_IMPL.read_text(encoding="utf-8")
        for marker in (
            "SEMANTIC_CONTRACT_V3",
            "retrieval_attempts",
            "source_registry",
            "source_observations",
            "claim_origin",
            "citation_bindings",
            "effect_estimates",
            "conflict_groups",
            "followup_attempts",
            "MODEL_INFERENCE",
            "topic_alignments",
            "render_report_from_artifacts.py",
        ):
            self.assertTrue(
                any(marker in document for document in (protocol, semantic, instructions, validator, runner)),
                marker,
            )
        self.assertIn("byte-identical", validator)
        self.assertIn("navigation_summary", runner)
        self.assertIn("substantive_claim", runner)

    def test_protocol_declares_required_artifacts_and_statuses(self) -> None:
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        for artifact in (
            "EvidenceRadar_Report.html",
            "EvidenceRadar_State.json",
            "EvidenceRadar_Evidence.json",
            "EvidenceRadar_Run.json",
        ):
            self.assertIn(artifact, protocol)
        for status in (
            "COMPLETE",
            "PARTIAL_SOURCE_COVERAGE",
            "SOURCE_ACCESS_GAP",
            "STATE_HISTORY_INCOMPLETE",
            "NO_QUALIFYING_ITEMS",
        ):
            self.assertIn(status, protocol)
        for provenance in (
            "execution_lane",
            "protocol_commit",
            "base_state_sha256",
            "parent_run_ids",
        ):
            self.assertIn(provenance, protocol)
        self.assertIn("data-evidenceradar-work-id", protocol)
        self.assertIn("links.json", protocol)

    def test_historical_outputs_remain_present(self) -> None:
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.md").exists())
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.html").exists())
        self.assertTrue((ROOT / "state" / "literature_registry.json").exists())
        self.assertTrue((ROOT / "state" / "run_history.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
