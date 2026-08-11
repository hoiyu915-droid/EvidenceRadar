from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.run_github_radar import (
    Candidate,
    RadarRuntimeError,
    build_candidate_ledger,
    build_state,
    classify_publication,
    deduplicate,
    event_class,
    event_record,
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

    def test_dedup_bridges_complementary_identifiers_by_exact_title(self) -> None:
        doi = Candidate(
            title="One stable research title",
            stream="clinical",
            category="clinical_medicine",
            source="Publisher",
            publication_date="2026-08-10",
            doi="10.1000/bridge",
            authors=["A. Researcher"],
            provider_publication_types=["Journal Article"],
        )
        pmid = Candidate(
            title="One stable research title",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            pmid="12345678",
            authors=["A. Researcher"],
        )
        forward = deduplicate([doi, pmid])
        reverse = deduplicate([pmid, doi])
        self.assertEqual(1, len(forward))
        self.assertEqual("doi:10.1000/bridge", forward[0].work_id)
        self.assertEqual("12345678", forward[0].pmid)
        self.assertEqual(forward[0].work_id, reverse[0].work_id)
        self.assertEqual(forward[0].observed_sources, reverse[0].observed_sources)

    def test_dedup_does_not_bridge_generic_title_with_complementary_ids(self) -> None:
        doi = Candidate(
            title="Editorial",
            stream="clinical",
            category="clinical_medicine",
            source="Publisher",
            publication_date="2026-08-10",
            doi="10.1000/editorial-a",
            authors=["A. Editor"],
        )
        pmid = Candidate(
            title="Editorial",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            pmid="99887766",
            authors=["A. Editor"],
        )
        self.assertEqual(2, len(deduplicate([doi, pmid])))

    def test_dedup_requires_positive_metadata_for_title_bridge(self) -> None:
        doi = Candidate(
            title="A sufficiently specific shared research title",
            stream="clinical",
            category="clinical_medicine",
            source="Publisher",
            publication_date="2026-08-10",
            doi="10.1000/specific-a",
            authors=["A. Researcher"],
        )
        pmid = Candidate(
            title="A sufficiently specific shared research title",
            stream="clinical",
            category="clinical_medicine",
            source="PubMed",
            publication_date="2026-08-10",
            pmid="11223344",
            authors=["B. Researcher"],
        )
        self.assertEqual(2, len(deduplicate([doi, pmid])))

    def test_dedup_keeps_same_title_with_conflicting_dois_separate(self) -> None:
        first = Candidate(
            title="Shared short title",
            stream="clinical",
            category="clinical_medicine",
            source="Publisher A",
            publication_date="2026-08-10",
            doi="10.1000/a",
        )
        second = Candidate(
            title="Shared short title",
            stream="clinical",
            category="clinical_medicine",
            source="Publisher B",
            publication_date="2026-08-10",
            doi="10.1000/b",
        )
        self.assertEqual(2, len(deduplicate([first, second])))

    def test_formal_observation_wins_over_preprint_classification(self) -> None:
        preprint = Candidate(
            title="Versioned research work",
            stream="llm",
            category="llm_research",
            source="arXiv",
            publication_date="2025-01-15",
            doi="10.1000/versioned",
            arxiv_id="2608.11111",
            is_preprint=True,
            provider_publication_types=["preprint"],
            landing_url="https://arxiv.org/abs/2608.11111",
            abstract="Preprint abstract retained as supplemental content.",
        )
        journal = Candidate(
            title="Versioned research work",
            stream="llm",
            category="llm_research",
            source="PubMed",
            publication_date="2026-08-10",
            doi="10.1000/versioned",
            pmid="87654321",
            provider_publication_types=["Journal Article"],
            landing_url="https://pubmed.ncbi.nlm.nih.gov/87654321/",
            events=[
                event_record(
                    "first_formal_indexing",
                    "2026-08-10",
                    "PubMed",
                    "pubmed:edat",
                    "https://pubmed.ncbi.nlm.nih.gov/87654321/",
                    "date",
                    "provider_metadata",
                )
            ],
        )
        [merged] = deduplicate([preprint, journal])
        self.assertFalse(merged.is_preprint)
        self.assertEqual("journal_article", merged.document_type)
        self.assertIn("preprint", [value.casefold() for value in merged.provider_publication_types])
        self.assertIn("Journal Article", merged.provider_publication_types)
        self.assertEqual("PubMed", merged.source)
        self.assertEqual("2026-08-10", merged.publication_date)
        self.assertEqual(
            "https://pubmed.ncbi.nlm.nih.gov/87654321/", merged.landing_url
        )
        self.assertEqual(
            "Preprint abstract retained as supplemental content.", merged.abstract
        )
        self.assertNotEqual(
            "BACKFILL_INDEXING",
            event_class(
                merged,
                merged.events[0],
                datetime(2026, 8, 8, tzinfo=ZoneInfo("Asia/Tokyo")),
            ),
        )

    def test_formal_observation_clears_preprint_across_state_runs(self) -> None:
        observed_at = datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
        preprint = Candidate(
            title="Versioned state work",
            stream="llm",
            category="llm_research",
            source="arXiv",
            publication_date="2025-01-01",
            doi="10.1000/state-versioned",
            is_preprint=True,
            provider_publication_types=["preprint"],
        )
        first = build_state(
            None,
            [preprint],
            [],
            generated_at=observed_at,
            run_id="run-preprint",
            execution_lane="github_actions",
            protocol_commit="preprint-commit",
            base_state_sha256="0" * 64,
        )
        formal = Candidate(
            title="Versioned state work",
            stream="llm",
            category="llm_research",
            source="PubMed",
            publication_date="2026-08-10",
            doi="10.1000/state-versioned",
            provider_publication_types=["Journal Article"],
        )
        second = build_state(
            first,
            [formal],
            [],
            generated_at=observed_at,
            run_id="run-formal",
            execution_lane="github_actions",
            protocol_commit="formal-commit",
            base_state_sha256="1" * 64,
        )
        [work] = second["works"]
        self.assertFalse(work["is_preprint"])
        self.assertEqual("journal_article", work["document_type"])

    def test_strong_identifiers_are_case_insensitive_during_component_merge(self) -> None:
        upper = Candidate(
            title="Repository identity observation",
            stream="clinical",
            category="clinical_medicine",
            source="Europe PMC",
            publication_date="2026-08-10",
            pmcid="PMC123ABC",
        )
        lower = Candidate(
            title="Repository identity observation from another provider",
            stream="clinical",
            category="clinical_medicine",
            source="OpenAlex",
            publication_date="2026-08-10",
            pmcid="pmc123abc",
        )
        forward = deduplicate([upper, lower])
        reverse = deduplicate([lower, upper])
        self.assertEqual(1, len(forward))
        self.assertEqual(forward[0].work_id, reverse[0].work_id)

    def test_transitive_identifier_conflict_fails_closed(self) -> None:
        values = [
            Candidate(
                title="Bridge A",
                stream="clinical",
                category="clinical_medicine",
                source="one",
                publication_date="2026-08-10",
                doi="10.1000/a",
                pmid="1",
            ),
            Candidate(
                title="Bridge B",
                stream="clinical",
                category="clinical_medicine",
                source="two",
                publication_date="2026-08-10",
                pmid="1",
                pmcid="PMC1",
            ),
            Candidate(
                title="Bridge C",
                stream="clinical",
                category="clinical_medicine",
                source="three",
                publication_date="2026-08-10",
                doi="10.1000/b",
                pmcid="PMC1",
            ),
        ]
        with self.assertRaisesRegex(RadarRuntimeError, "conflicting candidate identifiers"):
            deduplicate(values)

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
