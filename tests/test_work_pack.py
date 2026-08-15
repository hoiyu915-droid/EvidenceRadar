from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import work_pack_contract_cases as _cases  # noqa: E402, I001


class WorkPackTests(_cases.WorkPackTests):
    """Run the established Work Pack cases with the current user-entry wording."""

    def test_user_entry_is_one_download_then_terminal_four_file_delivery(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        instructions = (ROOT / "templates" / "gpt-work-instructions.md").read_text(
            encoding="utf-8"
        )
        skill = (
            ROOT / ".agents" / "skills" / "evidence-radar" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("## 最快的試用方式", readme)
        self.assertIn("EvidenceRadar-WorkPack-current.zip", readme)
        self.assertIn(
            "GitHub 在這條一般用家路徑主要負責原始碼、版本化設定與 immutable Work Pack 儲存",
            readme,
        )
        self.assertIn("user-launched terminal flow", readme)
        self.assertIn("TRANSLATION_REQUIRED", instructions)
        self.assertIn("Work run is complete only after", instructions)
        self.assertIn("tools/run_work_radar.py", skill)
        self.assertIn("status: COMPLETE", skill)
        self.assertNotIn("Stage A and waits", instructions)
