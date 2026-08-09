"""Cross-file delivery and GitHub Pages regression tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.build_pages_site import build_pages_site, github_pages_base_url
from tools.run_github_radar import Candidate, DiscoveryResult, event_record, execute
from tools.validate_delivery_bundle import validate_delivery_bundle


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Tokyo")


def create_bundle(directory: Path, *, protocol_commit: str = "a" * 40) -> tuple[Path, Path]:
    end_at = datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
    item = Candidate(
        title="Delivery contract fixture",
        stream="clinical_medicine",
        category="clinical_medicine",
        source="pubmed",
        publication_date="2026-08-08",
        venue="Fixture Journal",
        doi="10.1000/delivery.fixture",
        abstract="A controlled fixture summary with 12 participants.",
        landing_url="https://example.test/delivery",
        events=[
            event_record(
                "version_of_record_first_online",
                "2026-08-08",
                "pubmed",
                "ArticleDate",
                "https://example.test/delivery",
                "date",
                "provider_metadata",
            )
        ],
        score=90,
        triage_status="PRIORITY",
        triage_reasons=["fixture"],
        observed_streams=["clinical_medicine"],
        observed_sources=["pubmed"],
    )
    discovery_sources = {
        "pubmed",
        "europe_pmc",
        "openalex",
        "arxiv",
        "openreview",
        "acl_anthology",
        "pmlr",
    }

    def discoverer(*_args: object, **_kwargs: object) -> DiscoveryResult:
        source_access = [
            {
                "source_id": f"{source}-fixture",
                "provider": source,
                "url": f"https://example.test/{source}",
                "accessed_at": end_at.isoformat(),
                "status": "SUCCESS" if source == "pubmed" else "NO_RESULTS",
                "result_count": 1 if source == "pubmed" else 0,
            }
            for source in sorted(discovery_sources)
        ]
        return DiscoveryResult(
            all_candidates=[item],
            priority_candidates=[item],
            raw_candidate_count=1,
            queries=[
                {
                    "query_id": "fixture-query",
                    "category": "clinical_medicine",
                    "query": "delivery fixture",
                    "searched_at": end_at.isoformat(),
                    "source_ids": ["pubmed"],
                    "status": "SUCCESS",
                    "result_count": 1,
                }
            ],
            source_access=source_access,
            checked_sources=set(discovery_sources),
            searched_sources=set(discovery_sources),
            unavailable_sources=set(),
        )

    def publisher_probe(*_args: object, **_kwargs: object):
        access = {
            "source_id": "publisher-fixture",
            "provider": "publisher",
            "url": "https://doi.org/10.1000/delivery.fixture",
            "accessed_at": end_at.isoformat(),
            "status": "SUCCESS",
            "result_count": 1,
            "work_id": item.work_id,
            "candidate_title": item.title,
            "category": item.category,
            "http_status": 200,
        }
        return [(item, access)], [access], []

    bundle = directory / "bundle"
    canonical_state = directory / "state" / "EvidenceRadar_State.json"
    execute(
        root=ROOT,
        output_dir=bundle,
        state_path=canonical_state,
        end_at=end_at,
        run_id="github-actions-delivery-fixture",
        execution_lane="github_actions",
        protocol_commit=protocol_commit,
        discoverer=discoverer,
        publisher_probe=publisher_probe,
    )
    return bundle, canonical_state


class DeliveryBundleTests(unittest.TestCase):
    def test_valid_bundle_has_exact_html_and_run_candidate_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            errors, run = validate_delivery_bundle(
                ROOT,
                bundle,
                canonical_state=canonical,
                expected_lane="github_actions",
                expected_protocol_commit="a" * 40,
            )
            self.assertEqual([], errors)
            self.assertEqual(1, run["counts"]["deduplicated_candidates"])
            report = (bundle / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
            self.assertEqual(1, report.count("data-evidenceradar-work-id="))

    def test_html_candidate_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            report_path = bundle / "EvidenceRadar_Report.html"
            report = report_path.read_text(encoding="utf-8")
            report_path.write_text(
                report.replace("data-evidenceradar-work-id=", "data-omitted-work-id=", 1),
                encoding="utf-8",
            )
            errors, _run = validate_delivery_bundle(ROOT, bundle, canonical_state=canonical)
            self.assertTrue(any("candidate markers" in error for error in errors), errors)

    def test_schema_valid_files_cannot_hide_cross_file_provenance_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state_path = bundle / "EvidenceRadar_State.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["execution_lane"] = "chatgpt_work"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            errors, _run = validate_delivery_bundle(ROOT, bundle, canonical_state=canonical)
            self.assertTrue(any("State.execution_lane" in error for error in errors), errors)

    def test_pages_site_exposes_stable_and_immutable_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            bundle, canonical = create_bundle(temporary)
            output = temporary / "site"
            links = build_pages_site(
                root=ROOT,
                bundle=bundle,
                output_dir=output,
                repository="example-owner/EvidenceRadar",
                canonical_state=canonical,
                require_current_producer=False,
            )
            self.assertEqual(
                "https://example-owner.github.io/EvidenceRadar/",
                links["report_url"],
            )
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "links.json").is_file())
            self.assertTrue(
                (output / "runs" / "github-actions-delivery-fixture" / "index.html").is_file()
            )
            self.assertEqual(
                links,
                json.loads((output / "links.json").read_text(encoding="utf-8")),
            )

    def test_user_site_repository_uses_origin_without_project_suffix(self) -> None:
        self.assertEqual(
            "https://example-owner.github.io",
            github_pages_base_url("example-owner/example-owner.github.io"),
        )


if __name__ == "__main__":
    unittest.main()
