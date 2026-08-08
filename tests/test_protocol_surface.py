from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProtocolSurfaceTests(unittest.TestCase):
    def test_active_runtime_is_chatgpt_work_only(self) -> None:
        output = (ROOT / "config/output.yml").read_text(encoding="utf-8")
        self.assertIn("owner: chatgpt_work", output)
        self.assertIn("mcp: false", output)
        self.assertIn("external_server: false", output)
        self.assertIn("codex: false", output)
        self.assertIn("github_actions: false", output)
        self.assertIn("repository_writeback: false", output)

    def test_github_execution_triggers_are_absent(self) -> None:
        self.assertFalse((ROOT / ".manual-run").exists())
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
        self.assertEqual(["public-release.yml"], sorted(path.name for path in workflows))

        maintenance = workflows[0].read_text(encoding="utf-8")
        self.assertIn("python tools/validate_public_release.py", maintenance)
        self.assertIn("contents: read", maintenance)
        self.assertNotIn("schedule:", maintenance)
        self.assertNotIn("workflow_dispatch:", maintenance)
        self.assertNotIn("python src/run.py", maintenance)
        self.assertNotIn("contents: write", maintenance)

    def test_legacy_runtime_is_separated_from_active_surface(self) -> None:
        self.assertFalse((ROOT / "src").exists())
        self.assertFalse((ROOT / "requirements.txt").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "src" / "run.py").exists())
        self.assertTrue((ROOT / "legacy" / "python-runtime" / "requirements.txt").exists())

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

    def test_historical_outputs_remain_present(self) -> None:
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.md").exists())
        self.assertTrue((ROOT / "daily" / "20260808 1013.Rader.html").exists())
        self.assertTrue((ROOT / "state" / "literature_registry.json").exists())
        self.assertTrue((ROOT / "state" / "run_history.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
