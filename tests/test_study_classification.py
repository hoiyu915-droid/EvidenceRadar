from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.run_github_radar import (
    Candidate,
    build_candidate_ledger,
    classify_publication,
    deduplicate,
    render_report,
)
from tools.validate_delivery_bundle import _study_classification_errors


class StudyClassificationTests(unittest.TestCase):
    def test_title_explicit_systematic_review_meta_analysis(self) -> None:
        value = classify_publication(
            title="Efficacy of X: a systematic review and meta-analysis",
            source="PubMed",
            is_preprint=False,
            provider_publication_types=[],
        )
        self.assertEqual(value["document_type"], "journal_article")
        self.assertEqual(value["document_type_basis"], "SOURCE_CLASS")
        self.assertEqual(value["study_designs"], ["meta_analysis", "systematic_review"])
        self.assertEqual(value["study_design_basis"], "TITLE_EXPLICIT")

    def test_provider_metadata_identifies_rct_without_title_hint(self) -> None:
        candidate = Candidate(
            title="Effects of intervention X on outcome Y",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            provider_publication_types=["Journal Article", "Randomized Controlled Trial"],
        )
        self.assertEqual(candidate.document_type, "journal_article")
        self.assertEqual(candidate.document_type_basis, "PROVIDER_METADATA")
        self.assertEqual(candidate.study_designs, ["randomized_controlled_trial"])
        self.assertEqual(candidate.study_design_basis, "PROVIDER_METADATA")

    def test_preprint_without_design_signal_stays_unknown(self) -> None:
        candidate = Candidate(
            title="A new inference-time scheduling method",
            stream="llm",
            category="llm_research",
            source="arXiv",
            publication_date="2026-08-10",
            arxiv_id="2608.12345",
            is_preprint=True,
        )
        self.assertEqual(candidate.document_type, "preprint")
        self.assertEqual(candidate.document_type_basis, "SOURCE_CLASS")
        self.assertEqual(candidate.study_designs, [])
        self.assertEqual(candidate.study_design_basis, "UNKNOWN")

    def test_dedup_preserves_provider_design_metadata(self) -> None:
        openalex = Candidate(
            title="Effects of intervention X on outcome Y",
            stream="clinical",
            category="clinical_medicine",
            source="OpenAlex",
            publication_date="2026-08-10",
            doi="10.1000/test",
        )
        openalex.score = 80
        pubmed = Candidate(
            title="Effects of intervention X on outcome Y",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            doi="10.1000/test",
            provider_publication_types=["Journal Article", "Randomized Controlled Trial"],
        )
        pubmed.score = 70
        [merged] = deduplicate([openalex, pubmed])
        self.assertIn("Randomized Controlled Trial", merged.provider_publication_types)
        self.assertEqual(merged.study_designs, ["randomized_controlled_trial"])

    def _ledger_record(self) -> dict[str, object]:
        candidate = Candidate(
            title="Efficacy of X: a systematic review and meta-analysis",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            pmid="12345678",
            provider_publication_types=["Journal Article", "Systematic Review", "Meta-Analysis"],
        )
        candidate.score = 87
        candidate.triage_status = "PRIORITY"
        candidate.triage_reasons = ["test"]
        candidate.query_ids = ["q-1"]
        candidate.observed_streams = ["clinical"]
        candidate.observed_sources = ["PubMed"]
        start = datetime(2026, 8, 9, tzinfo=ZoneInfo("Asia/Tokyo"))
        end = datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
        [record] = build_candidate_ledger(
            [candidate],
            start=start,
            end=end,
            timezone=ZoneInfo("Asia/Tokyo"),
            notified_event_ids=set(),
            publisher_access=[],
            displayed_work_ids={candidate.work_id},
        )
        return record

    def test_ledger_projects_two_axis_classification(self) -> None:
        record = self._ledger_record()
        self.assertEqual(record["document_type"], "journal_article")
        self.assertEqual(record["study_designs"], ["meta_analysis", "systematic_review"])
        self.assertEqual(record["study_design_basis"], "PROVIDER_METADATA_AND_TITLE")
        self.assertIn("Systematic Review", record["provider_publication_types"])

    def test_renderer_exposes_visible_badges_filter_and_binding_attributes(self) -> None:
        record = self._ledger_record()
        report = render_report(
            [record],
            run_id="test-run",
            execution_lane="github_actions",
            protocol_commit="deadbeef",
            generated_at=datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Tokyo")),
            start=datetime(2026, 8, 9, tzinfo=ZoneInfo("Asia/Tokyo")),
            end=datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Tokyo")),
            run_status="COMPLETE",
            coverage_status="COMPLETE",
            warnings=[],
            publisher_min=0,
            publisher_max=15,
            publisher_attempted=0,
            publisher_accessible=0,
            source_coverage={"requested": [], "checked": [], "checks": []},
            evidence={"claims": []},
        )
        self.assertIn("系統性回顧", report)
        self.assertIn("Meta-analysis", report)
        self.assertIn('id="study-type-filter"', report)
        self.assertIn('data-document-type="journal_article"', report)
        self.assertIn('data-study-designs="meta_analysis|systematic_review"', report)

    def test_fail_closed_semantic_validation(self) -> None:
        valid = {
            "work_id": "pmid:1",
            "is_preprint": False,
            "provider_publication_types": ["Journal Article"],
            "document_type": "journal_article",
            "document_type_basis": "PROVIDER_METADATA",
            "study_designs": ["systematic_review"],
            "study_design_basis": "PROVIDER_METADATA",
        }
        self.assertEqual(_study_classification_errors(valid, 0), [])
        invalid = dict(valid)
        invalid["study_designs"] = []
        invalid["study_design_basis"] = "PROVIDER_METADATA"
        self.assertTrue(_study_classification_errors(invalid, 0))
        invalid_preprint = dict(valid)
        invalid_preprint["is_preprint"] = True
        self.assertTrue(_study_classification_errors(invalid_preprint, 0))


if __name__ == "__main__":
    unittest.main()
