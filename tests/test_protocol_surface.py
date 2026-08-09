from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProtocolSurfaceTests(unittest.TestCase):
    def test_active_runtime_has_two_explicit_lanes(self) -> None:
        output = (ROOT / "config/output.yml").read_text(encoding="utf-8")
        self.assertIn("owners: [chatgpt_work, github_actions]", output)
        self.assertIn("chatgpt_work:", output)
        self.assertIn("github_actions:", output)
        self.assertIn("github_actions: true", output)
        self.assertIn("mcp: false", output)
        self.assertIn("external_server: false", output)
        self.assertIn("codex: false", output)

    def test_github_execution_has_daily_and_manual_triggers(self) -> None:
        self.assertFalse((ROOT / ".manual-run").exists())
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
        self.assertEqual(["daily-radar.yml", "public-release.yml"], sorted(path.name for path in workflows))

        maintenance = (workflow_dir / "public-release.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/validate_public_release.py", maintenance)
        self.assertIn("contents: read", maintenance)
        self.assertNotIn("schedule:", maintenance)
        self.assertNotIn("workflow_dispatch:", maintenance)
        self.assertNotIn("python src/run.py", maintenance)
        self.assertNotIn("contents: write", maintenance)

        daily = (workflow_dir / "daily-radar.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", daily)
        self.assertIn("workflow_dispatch:", daily)
        self.assertIn("contents: write", daily)
        self.assertIn("python tools/run_github_radar.py", daily)

    def test_new_runner_is_separate_from_archived_runtime(self) -> None:
        self.assertFalse((ROOT / "src").exists())
        self.assertTrue((ROOT / "requirements.txt").exists())
        self.assertTrue((ROOT / "tools" / "run_github_radar.py").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "src" / "run.py").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "requirements.txt").exists())

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
        runner = (ROOT / "tools" / "run_github_radar.py").read_text(encoding="utf-8")
        self.assertIn("candidate_display_capped_by_publisher_budget: false", output)
        self.assertIn("display_all_deduplicated: true", output)
        self.assertIn("group_by_category: true", output)
        self.assertIn("displays every deduplicated candidate", protocol)
        self.assertIn("publisher access success is never a display gate", protocol)
        self.assertIn('"candidates": candidate_records', runner)
        self.assertIn("select_display_candidates", runner)

    def test_report_requires_readable_summaries_and_interactive_filters(self) -> None:
        output = (ROOT / "config" / "output.yml").read_text(encoding="utf-8")
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        runner = (ROOT / "tools" / "run_github_radar.py").read_text(encoding="utf-8")
        self.assertIn("include_candidate_content_summary: true", output)
        self.assertIn("interactive_candidate_filters: true", output)
        self.assertIn("candidate_summary_max_chars: 320", output)
        self.assertIn("candidate_summary_language: zh-TW", output)
        self.assertIn("EVIDENCERADAR_TRANSLATION_API_KEY", output)
        self.assertIn("TRANSLATED_ABSTRACT_EXCERPT_ZH_TW", protocol)
        self.assertIn("ZH_TW_METADATA_TEMPLATE", protocol)
        self.assertIn("summary_language: zh-TW", protocol)
        self.assertIn('id="candidate-search"', runner)
        self.assertIn('id="category-filter"', runner)
        self.assertIn('id="triage-filter"', runner)
        self.assertIn('id="source-filter"', runner)
        self.assertIn('"summary_language": "zh-TW"', runner)

    def test_state_merge_and_work_pack_are_public_surfaces(self) -> None:
        self.assertTrue((ROOT / "tools" / "merge_radar_state.py").exists())
        self.assertTrue((ROOT / "tools" / "build_work_pack.py").exists())
        self.assertTrue((ROOT / "release" / "work-pack-manifest.json").exists())
        self.assertTrue((ROOT / "docs" / "GITHUB_DEPLOYMENT.md").exists())
        self.assertTrue((ROOT / "docs" / "WORK_SETUP.md").exists())
        self.assertTrue((ROOT / "docs" / "MIGRATION_DUAL_LANE_1.0.md").exists())

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

    def test_historical_outputs_remain_present(self) -> None:
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.md").exists())
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.html").exists())
        self.assertTrue((ROOT / "state" / "literature_registry.json").exists())
        self.assertTrue((ROOT / "state" / "run_history.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
