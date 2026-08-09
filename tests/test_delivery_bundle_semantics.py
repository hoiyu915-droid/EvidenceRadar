"""Semantic (cross-artifact) delivery regressions.

JSON Schema accepts individually plausible fields.  These fixtures exercise
the fail-closed invariants that caught the Work runtime's stale semantic
counts: OA truth is independent from probe access, discovery landing pages do
not verify claims, and PARTIAL claims never become VERIFIED works.
"""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.test_delivery_bundle import ROOT, create_bundle
from tools.validate_delivery_bundle import (
    _canonical_artifact_path_errors,
    _manifest_errors,
    validate_delivery_bundle,
)


class DeliveryBundleSemanticTests(unittest.TestCase):
    def _load(self, bundle: Path, name: str) -> dict:
        path = bundle / name
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, bundle: Path, name: str, value: dict) -> None:
        (bundle / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _validate(self, bundle: Path, canonical: Path):
        return validate_delivery_bundle(ROOT, bundle, canonical_state=canonical)

    def _manifest(self, root: Path, relative: str) -> Path:
        payload = b"manifest path fixture\n"
        target = root / "safe.txt"
        target.write_bytes(payload)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "source_commit": "fixture-commit",
                    "files": [
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size": len(payload),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_manifest_rejects_absolute_dot_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (str(root / "safe.txt"), ".", "../safe.txt", "nested/../safe.txt"):
                manifest = self._manifest(root, relative)
                errors = _manifest_errors(
                    root, manifest, protocol_commit="fixture-commit", reject_dirty=False
                )
                self.assertTrue(any("unsafe Work Pack manifest path" in error for error in errors), relative)

    def test_manifest_rejects_symlink_and_resolved_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(outside)
                manifest = self._manifest(root, "link.txt")
                errors = _manifest_errors(
                    root, manifest, protocol_commit="fixture-commit", reject_dirty=False
                )
                self.assertTrue(any("symlink" in error or "escapes root" in error for error in errors), errors)
            finally:
                outside.unlink(missing_ok=True)

    def test_canonical_bundle_artifact_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            target = bundle / "EvidenceRadar_Run.json"
            backup = bundle / "run-target.json"
            target.rename(backup)
            target.symlink_to(backup)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("must not be a symlink" in error for error in errors), errors)

    def test_canonical_bundle_artifact_rejects_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, _canonical = create_bundle(Path(directory))
            for name in ("/tmp/EvidenceRadar_Run.json", ".", "../EvidenceRadar_Run.json"):
                errors = _canonical_artifact_path_errors(bundle, name)
                self.assertTrue(any("unsafe canonical artifact path" in error for error in errors), name)

    def test_state_run_oa_access_download_and_event_fields_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["candidates"][0]["oa_status"] = "YES"
            run["candidates"][0]["oa_evidence"] = [
                {
                    "source": "repository",
                    "evidence_type": "repository_identifier",
                    "value": "PMC987654",
                    "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC987654/",
                }
            ]
            run["counts"].update({"oa_yes": 1, "oa_unknown": 0})
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("State.works" in error and "oa_status" in error for error in errors), errors)

    def test_oa_evidence_must_be_unique_and_deterministically_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            candidate = run["candidates"][0]
            candidate["oa_status"] = "YES"
            candidate["oa_evidence"] = [
                {"source": "z-provider", "evidence_type": "identifier", "value": "true"},
                {"source": "a-repository", "evidence_type": "identifier", "value": "PMC1"},
            ]
            run["counts"].update({"oa_yes": 1, "oa_unknown": 0})
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("deterministic order" in error for error in errors), errors)

    def test_state_historical_blocked_access_survives_current_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            state["works"][0]["access_status"] = "BLOCKED"
            state["works"][0]["fulltext_access_status"] = "BLOCKED"
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            errors, _run = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_featured_html_meta_and_data_markers_must_match_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            report_path = bundle / "EvidenceRadar_Report.html"
            report = report_path.read_text(encoding="utf-8")
            featured_count = run["counts"]["featured_candidates"]
            self.assertIn(f'data-featured="true"', report)
            report_path.write_text(
                report.replace('data-featured="true"', 'data-featured="false"', 1),
                encoding="utf-8",
            )
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("featured_candidates" in error for error in errors),
                f"expected featured count {featured_count}, errors={errors}",
            )

    def test_oa_yes_and_blocked_fulltext_is_valid(self) -> None:
        """A blocked probe must not erase affirmative OA evidence."""

        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            candidate = run["candidates"][0]
            semantic_fields = {
                "oa_status": "YES",
                "oa_evidence": [
                    {
                        "source": "repository",
                        "evidence_type": "repository_identifier",
                        "value": "PMC123456",
                        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/",
                    }
                ],
                "access_status": "BLOCKED",
                "fulltext_kind": "REPOSITORY",
                "download_urls": [
                    "https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/pdf"
                ],
            }
            candidate.update(semantic_fields)
            state["works"][0].update(semantic_fields)
            run["counts"].update(
                {"oa_yes": 1, "oa_unknown": 0, "fulltext_blocked": 1, "fulltext_not_checked": 0}
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            errors, _run = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_semantic_contract_marker_requires_every_candidate_access_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            del run["candidates"][0]["access_status"]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("missing semantic contract field 'access_status'" in error for error in errors),
                errors,
            )

    def test_publisher_counts_are_derived_from_candidate_access_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["counts"]["publisher_accessible"] = 0
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("publisher_accessible" in error for error in errors), errors)

    def test_publisher_success_rejects_blocked_http_status_and_bad_result_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            access = next(item for item in run["source_access"] if item.get("provider") == "publisher")
            access["http_status"] = 403
            access["result_count"] = 0
            candidate = run["candidates"][0]
            candidate["publisher_http_status"] = 403
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("SUCCESS cannot carry HTTP 403" in error for error in errors), errors)
            self.assertTrue(any("SUCCESS requires result_count" in error for error in errors), errors)

    def test_publisher_source_access_rejects_discovery_status_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            access = next(item for item in run["source_access"] if item.get("provider") == "publisher")
            access["status"] = "NO_RESULTS"
            candidate = run["candidates"][0]
            candidate["publisher_access_status"] = "NOT_ATTEMPTED"
            candidate.pop("publisher_access_id", None)
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("publisher status must be SUCCESS, FAILED, or NOT_ATTEMPTED" in error for error in errors),
                errors,
            )

    def test_publisher_success_cannot_be_an_openalex_landing_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            access = next(item for item in run["source_access"] if item.get("provider") == "publisher")
            access["url"] = "https://openalex.org/W123456789"
            self._save(bundle, "EvidenceRadar_Run.json", run)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("discovery landing cannot be publisher/formal verification" in error for error in errors),
                errors,
            )

    def test_arxiv_abstract_page_cannot_verify_supported_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://arxiv.org/abs/2608.12345",
                    "source_type": "arxiv",
                    "access_status": "FULL_TEXT",
                    "access_probe_status": "ACCESSIBLE",
                    "fulltext_kind": "PDF",
                    "download_urls": ["https://arxiv.org/abs/2608.12345"],
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-arxiv-landing",
                    "work_id": work_id,
                    "status": "SUPPORTED",
                    "claim_text": "A claim copied from an abstract landing page.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "abstract",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("no substantive full-text source" in error for error in errors), errors
            )

    def test_openalex_landing_cannot_verify_supported_claim_even_if_probe_says_accessible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://openalex.org/W123456789",
                    "source_type": "other",
                    "access_status": "FULL_TEXT",
                    "access_probe_status": "ACCESSIBLE",
                    "fulltext_kind": "HTML",
                    "download_urls": ["https://openalex.org/W123456789"],
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-openalex-landing",
                    "work_id": work_id,
                    "status": "SUPPORTED",
                    "claim_text": "A claim copied from an OpenAlex landing record.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "metadata",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("no substantive full-text source" in error for error in errors), errors)

    def test_partial_claim_never_counts_as_verified_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://publisher.example/article/fulltext.html",
                    "source_type": "publisher",
                    "access_status": "FULL_TEXT",
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-partial",
                    "work_id": work_id,
                    "status": "PARTIAL",
                    "claim_text": "A partially supported claim.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "results",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("verified_works" in error for error in errors), errors)
            self.assertTrue(any("review_pending" in error for error in errors), errors)

    def test_supported_claim_requires_explicit_fulltext_probe_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://publisher.example/article/fulltext.html",
                    "source_type": "publisher",
                    "access_status": "FULL_TEXT",
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-unprobed-fulltext",
                    "work_id": work_id,
                    "status": "SUPPORTED",
                    "claim_text": "A claim from a page merely labelled full text.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "results",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(any("no substantive full-text source" in error for error in errors), errors)

    def test_supported_claim_accepts_explicit_accessible_fulltext_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://publisher.example/article/fulltext.html",
                    "source_type": "publisher",
                    "access_status": "FULL_TEXT",
                    "access_probe_status": "ACCESSIBLE",
                    "fulltext_kind": "HTML",
                    "download_urls": ["https://publisher.example/article/fulltext.html"],
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-probed-fulltext",
                    "work_id": work_id,
                    "status": "SUPPORTED",
                    "claim_text": "A claim from an explicitly probed full-text page.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "results",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_europe_pmc_direct_pmc_fulltext_path_supports_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC987654/pdf",
                    "source_type": "europe_pmc",
                    "access_status": "FULL_TEXT",
                    "access_probe_status": "ACCESSIBLE",
                    "fulltext_kind": "PDF",
                    "download_urls": [
                        "https://pmc.ncbi.nlm.nih.gov/articles/PMC987654/pdf"
                    ],
                }
            )
            work_id = run["candidates"][0]["work_id"]
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"].update({"claims": 1, "verified_works": 1, "review_pending": 0})
            evidence["claims"] = [
                {
                    "claim_id": "claim-europe-pmc-fulltext",
                    "work_id": work_id,
                    "status": "SUPPORTED",
                    "claim_text": "A claim from direct PMC full text.",
                    "measurement": None,
                    "source_ids": [source["source_id"]],
                    "source_url": source["url"],
                    "locator": "full text",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            errors, _run = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_candidate_cannot_be_verified_without_supported_fulltext_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            candidate = run["candidates"][0]
            candidate["review_status"] = "VERIFIED"
            # Keep the presentation counts honest so this mutation exercises
            # the candidate-level review invariant rather than only count
            # parity.
            run["counts"].update({"verified_works": 0, "review_pending": 1})
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors, _run = self._validate(bundle, canonical)
            self.assertTrue(
                any("review_status=VERIFIED" in error for error in errors),
                errors,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
