from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.run_github_radar import (
    Candidate,
    annotate_candidate_event_classes,
    build_candidate_ledger,
    event_record,
    render_report,
    select_featured_work_ids,
)


class EventWindowDisplayContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timezone = ZoneInfo("Asia/Tokyo")
        self.end = datetime(2026, 8, 10, 7, 0, tzinfo=self.timezone)
        self.start = self.end - timedelta(hours=72)

    def _render(self, record: dict[str, object]) -> str:
        return render_report(
            [record],
            run_id="event-window-contract-test",
            execution_lane="github_actions",
            protocol_commit="test-commit",
            generated_at=self.end,
            start=self.start,
            end=self.end,
            run_status="COMPLETE",
            coverage_status="COMPLETE",
            warnings=[],
            publisher_min=0,
            publisher_max=15,
            publisher_attempted=0,
            publisher_accessible=0,
            source_coverage={"requested": [], "checked": [], "checks": []},
            evidence={},
            featured_target_per_category=1,
            featured_hard_max_per_category=1,
            featured_excluded_event_classes=set(),
        )

    def test_missing_qualifying_event_is_demoted_and_never_featured(self) -> None:
        candidate = Candidate(
            title="Foot and Ankle Fractures in the Elderly: A Review on Osteoporosis, Biomechanics, and Rehabilitation.",
            stream="sport_science",
            category="sport_science",
            source="Europe PMC",
            publication_date="2026-02-26",
            authors=["Example A"],
            venue="Example Journal",
            score=69,
        )
        candidate.triage_status = "PRIORITY"
        candidate.triage_reasons = ["MEETS_CATEGORY_ROUTING_THRESHOLD"]

        annotate_candidate_event_classes(
            [candidate], start=self.start, end=self.end, timezone=self.timezone
        )

        self.assertEqual(candidate.triage_status, "LOWER_PRIORITY")
        self.assertIn("MISSING_QUALIFYING_EVENT", candidate.triage_reasons)

        record = build_candidate_ledger(
            [candidate],
            start=self.start,
            end=self.end,
            timezone=self.timezone,
            notified_event_ids=set(),
            publisher_access=[],
            displayed_work_ids={candidate.work_id},
        )[0]
        self.assertEqual(record["event_status"], "NO_QUALIFYING_EVENT")
        self.assertNotIn(
            candidate.work_id,
            select_featured_work_ids(
                [record],
                target_per_category=1,
                hard_max_per_category=1,
                excluded_event_classes=set(),
            ),
        )

        report = self._render(record)
        self.assertIn("72 小時納入理由", report)
        self.assertIn(
            "未納入：沒有合格事件落在本輪 72 小時觀測窗；此項僅因完整候選池保留政策而顯示。",
            report,
        )
        self.assertIn("無 72h 新事件", report)
        self.assertNotIn("今日精選 1 / 完整候選池 1 項", report)

    def test_qualifying_event_explains_why_item_is_inside_window(self) -> None:
        candidate = Candidate(
            title="Recent biomechanics study",
            stream="sport_science",
            category="sport_science",
            source="Europe PMC",
            publication_date="2026-08-09",
            authors=["Example B"],
            venue="Example Journal",
            score=80,
            events=[
                event_record(
                    "formal_version_verified",
                    "2026-08-09T12:00:00+09:00",
                    "Europe PMC",
                    "firstPublicationDate",
                    "https://europepmc.org/article/MED/12345678",
                    "instant",
                    "provider_metadata",
                )
            ],
        )
        candidate.triage_status = "PRIORITY"
        candidate.triage_reasons = ["MEETS_CATEGORY_ROUTING_THRESHOLD"]
        annotate_candidate_event_classes(
            [candidate], start=self.start, end=self.end, timezone=self.timezone
        )

        record = build_candidate_ledger(
            [candidate],
            start=self.start,
            end=self.end,
            timezone=self.timezone,
            notified_event_ids=set(),
            publisher_access=[],
            displayed_work_ids={candidate.work_id},
        )[0]
        self.assertEqual(record["event_status"], "QUALIFYING")

        report = self._render(record)
        self.assertIn("72 小時納入理由", report)
        self.assertIn(
            "納入：正式版本事件已驗證於 2026-08-09T12:00:00+09:00；Europe PMC / firstPublicationDate 落在本輪 72 小時觀測窗。",
            report,
        )


if __name__ == "__main__":
    unittest.main()
