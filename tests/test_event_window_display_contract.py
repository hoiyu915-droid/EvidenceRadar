from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.run_github_radar import (
    Candidate,
    annotate_candidate_event_classes,
    build_candidate_ledger,
    event_record,
    qualifying_event,
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

    def test_preprint_never_qualifies_even_with_in_window_formal_or_oa_event(self) -> None:
        for event_type in ("oa_fulltext_first_available", "formal_version_verified"):
            with self.subTest(event_type=event_type):
                candidate = Candidate(
                    title=f"Preprint gate fixture: {event_type}",
                    stream="llm_research",
                    category="llm_research",
                    source="preprint fixture",
                    publication_date="2026-08-09",
                    is_preprint=True,
                    score=90,
                    events=[
                        event_record(
                            event_type,
                            "2026-08-09T12:00:00+09:00",
                            "preprint fixture",
                            "fixture_event_date",
                            "https://fixture.example/preprint",
                            "instant",
                            "provider_metadata",
                        )
                    ],
                )
                candidate.triage_status = "PRIORITY"
                candidate.triage_reasons = ["MEETS_CATEGORY_ROUTING_THRESHOLD"]
                self.assertEqual("timestamp", candidate.events[0]["precision"])

                self.assertIsNone(
                    qualifying_event(candidate, self.start, self.end, self.timezone)
                )
                annotate_candidate_event_classes(
                    [candidate], start=self.start, end=self.end, timezone=self.timezone
                )
                self.assertEqual("LOWER_PRIORITY", candidate.triage_status)
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
                self.assertEqual("NO_QUALIFYING_EVENT", record["event_status"])
                self.assertNotIn(
                    candidate.work_id,
                    select_featured_work_ids(
                        [record],
                        target_per_category=1,
                        hard_max_per_category=1,
                        excluded_event_classes=set(),
                    ),
                )

    def test_non_preprint_peer_reviewed_upgrade_is_qualifying(self) -> None:
        candidate = Candidate(
            title="Peer-reviewed upgrade fixture",
            stream="llm_research",
            category="llm_research",
            source="formal publisher",
            publication_date="2026-08-09",
            provider_publication_types=["journal article"],
            score=90,
            events=[
                event_record(
                    "preprint_to_peer_reviewed_upgrade",
                    "2026-08-09T12:00:00+09:00",
                    "formal publisher",
                    "version_of_record_date",
                    "https://doi.org/10.1000/upgrade",
                    "instant",
                    "provider_metadata",
                )
            ],
        )
        candidate.triage_status = "PRIORITY"
        candidate.triage_reasons = ["MEETS_CATEGORY_ROUTING_THRESHOLD"]

        event = qualifying_event(candidate, self.start, self.end, self.timezone)
        self.assertIsNotNone(event)
        self.assertEqual("preprint_to_peer_reviewed_upgrade", event["event_type"])

        annotate_candidate_event_classes(
            [candidate], start=self.start, end=self.end, timezone=self.timezone
        )
        self.assertEqual("PRIORITY", candidate.triage_status)
        self.assertNotIn("MISSING_QUALIFYING_EVENT", candidate.triage_reasons)

        record = build_candidate_ledger(
            [candidate],
            start=self.start,
            end=self.end,
            timezone=self.timezone,
            notified_event_ids=set(),
            publisher_access=[],
            displayed_work_ids={candidate.work_id},
        )[0]
        self.assertEqual("QUALIFYING", record["event_status"])


if __name__ == "__main__":
    unittest.main()
