"""Executable mutation tests for the V3 semantic delivery contract."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_delivery_bundle import ROOT, create_bundle
from tools.run_github_radar import render_report_from_documents
from tools.validate_delivery_bundle import (
    _configured_streams_for_run,
    _crossref_journal_inventory_url_errors,
    _producer_requires_http_telemetry,
    _producer_requires_master_control,
    validate_delivery_bundle,
)


class SemanticContractV3Tests(unittest.TestCase):
    def test_complete_crossref_inventory_url_is_exact_and_non_narrowing(self) -> None:
        window = {
            "start": "2026-08-06T00:00:00+09:00",
            "end": "2026-08-09T00:00:00+09:00",
        }
        valid = (
            "https://api.crossref.org/journals/2041-1723/works?"
            "filter=from-online-pub-date%3A2026-08-06%2C"
            "until-online-pub-date%3A2026-08-09&rows=1000&cursor=%2A"
        )
        self.assertEqual(
            [],
            _crossref_journal_inventory_url_errors(
                valid,
                expected_issn="2041-1723",
                window=window,
                require_window_filter=True,
            ),
        )
        invalid_urls = {
            "nonstandard port": valid.replace("api.crossref.org", "api.crossref.org:444"),
            "missing cursor": valid.replace("&cursor=%2A", ""),
            "blank cursor": valid.replace("cursor=%2A", "cursor="),
            "narrow rows": valid.replace("rows=1000", "rows=1"),
            "extra query": f"{valid}&query=clinical",
            "extra filter": valid.replace(
                "%2Cuntil-online-pub-date",
                "%2Ctype%3Ajournal-article%2Cuntil-online-pub-date",
            ),
        }
        for label, invalid in invalid_urls.items():
            with self.subTest(label=label):
                self.assertTrue(
                    _crossref_journal_inventory_url_errors(
                        invalid,
                        expected_issn="2041-1723",
                        window=window,
                        require_window_filter=True,
                    )
                )

    def test_gitless_packaged_runner_preserves_modern_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "tools" / "run_github_radar.py"
            runner.parent.mkdir(parents=True)
            runner.write_bytes((ROOT / "tools" / "run_github_radar.py").read_bytes())
            run = {"protocol_commit": "f" * 40}
            self.assertTrue(_producer_requires_master_control(root, run))
            self.assertTrue(_producer_requires_http_telemetry(root, run))

    def test_gitless_work_pack_manifest_preserves_modern_capabilities_without_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = "f" * 40
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "format": "evidenceradar-work-pack",
                        "source_commit": commit,
                        "execution_lane": "chatgpt_work",
                        "capabilities": [
                            "MASTER_CONTROL_V1",
                            "EXECUTOR_HTTP_TELEMETRY_V1",
                            "TERMINAL_FOUR_ARTIFACT_DELIVERY_V1",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run = {"protocol_commit": commit}
            self.assertFalse((root / "tools" / "run_github_radar.py").exists())
            self.assertTrue(_producer_requires_master_control(root, run))
            self.assertTrue(_producer_requires_http_telemetry(root, run))

    """Mutations that must fail the cross-artifact V3 validator."""

    def _load(self, bundle: Path, name: str) -> dict:
        return json.loads((bundle / name).read_text(encoding="utf-8"))

    def _save(self, bundle: Path, name: str, value: dict) -> None:
        (bundle / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _validate(self, bundle: Path, canonical: Path) -> list[str]:
        errors, _run = validate_delivery_bundle(
            ROOT, bundle, canonical_state=canonical
        )
        return errors

    def _stable_id(self, prefix: str, *parts: object) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [str(part) for part in parts],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return f"{prefix}-{digest}"

    def _refresh_report(self, bundle: Path) -> None:
        """Re-render a deliberately changed JSON projection before validating it."""

        run = self._load(bundle, "EvidenceRadar_Run.json")
        evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
        report = render_report_from_documents(run, evidence)
        (bundle / "EvidenceRadar_Report.html").write_text(report, encoding="utf-8")
        run["report_sha256"] = hashlib.sha256(report.encode("utf-8")).hexdigest()
        self._save(bundle, "EvidenceRadar_Run.json", run)

    def _add_metadata_claim(self, bundle: Path, canonical: Path) -> tuple[str, str]:
        """Add a schema-valid metadata-only claim/binding fixture.

        The helper keeps the V3 renderer and report hash in sync.  Individual
        tests then mutate exactly one semantic field.
        """

        run = self._load(bundle, "EvidenceRadar_Run.json")
        state = self._load(bundle, "EvidenceRadar_State.json")
        evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
        source = state["source_registry"][0]
        source_id = str(source["source_id"])
        work_id = str(run["candidates"][0]["work_id"])
        claim_id = "claim-v3-fixture"
        binding_id = "binding-v3-fixture"
        evidence["claims"] = [
            {
                "claim_id": claim_id,
                "work_id": work_id,
                "status": "UNVERIFIED",
                "claim_text": "Fixture metadata claim awaiting review.",
                "measurement": None,
                "source_ids": [source_id],
                "source_url": source["canonical_url"],
                "locator": "metadata",
                "claim_kind": "BIBLIOGRAPHIC_FACT",
                "claim_origin": "METADATA_REPORTED",
                "citation_binding_ids": [binding_id],
                "support_reason": "Metadata locator only; substantive support is pending.",
            }
        ]
        evidence["citation_bindings"] = [
            {
                "binding_id": binding_id,
                "claim_id": claim_id,
                "source_id": source_id,
                "extraction_origin": "METADATA_REPORTED",
                "access_depth": "METADATA",
                "source_url": source["canonical_url"],
                "locator": "metadata",
                "support_scope": "CONTEXT_ONLY",
            }
        ]
        state["claim_registry"] = [
            {
                "claim_id": claim_id,
                "work_id": work_id,
                "claim_kind": "BIBLIOGRAPHIC_FACT",
                "claim_origin": "METADATA_REPORTED",
                "claim_text_sha256": hashlib.sha256(
                    evidence["claims"][0]["claim_text"].encode("utf-8")
                ).hexdigest(),
                "status": "UNVERIFIED",
                "source_ids": [source_id],
                "status_binding_ids": [binding_id],
                "first_seen_run": run["run_id"],
                "last_seen_run": run["run_id"],
                "last_status_change_run": run["run_id"],
            }
        ]
        run["counts"]["claims"] = 1
        self._save(bundle, "EvidenceRadar_State.json", state)
        canonical.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
        self._save(bundle, "EvidenceRadar_Run.json", run)
        self._refresh_report(bundle)
        return claim_id, binding_id

    def test_unbound_html_text_fails_canonical_byte_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            report_path = bundle / "EvidenceRadar_Report.html"
            report = report_path.read_text(encoding="utf-8")
            mutated = report.replace(
                "</body>",
                '<p data-unbound-fixture="true">arbitrary unbound HTML text</p></body>',
                1,
            )
            report_path.write_text(mutated, encoding="utf-8")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["report_sha256"] = hashlib.sha256(mutated.encode("utf-8")).hexdigest()
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("canonical byte-identical projection" in error for error in errors),
                errors,
            )

    def test_report_crlf_bytes_fail_canonical_byte_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            report_path = bundle / "EvidenceRadar_Report.html"
            report_path.write_bytes(report_path.read_bytes().replace(b"\n", b"\r\n"))

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.report_sha256 must bind the exact Report HTML bytes" in error
                    or "canonical byte-identical projection" in error
                    for error in errors
                ),
                errors,
            )

    def test_historical_relation_requires_known_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            digest = hashlib.sha256(
                json.dumps(
                    ["ghost-a", "ghost-b", "NEW_VERSION"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            relation_id = f"workrel-{digest}"
            state["work_relations"] = [
                {
                    "relation_id": relation_id,
                    "from_work_id": "ghost-a",
                    "to_work_id": "ghost-b",
                    "relation_type": "NEW_VERSION",
                    "comparison_basis": "Historical fixture with missing endpoints.",
                    "review_status": "AUTO_DETECTED",
                    "observed_run_id": "old-run",
                }
            ]
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("references unknown endpoints" in error for error in errors),
                errors,
            )

    def test_gap_requires_known_typed_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            gap_digest = hashlib.sha256(
                json.dumps(
                    ["SOURCE_UNAVAILABLE", "ghost-source"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            gap_id = f"gap-{gap_digest}"
            state["gaps"].append(
                {
                    "gap_id": gap_id,
                    "gap_type": "SOURCE_UNAVAILABLE",
                    "scope_type": "SOURCE",
                    "scope_id": "ghost-source",
                    "first_seen_run": "old-run",
                    "last_attempt_run": "old-run",
                    "attempt_count": 0,
                    "status": "OPEN",
                    "max_attempts": 3,
                    "resolution_criteria": "A real receipt succeeds.",
                    "receipt_ids": [],
                }
            )
            state["gaps"].sort(key=lambda item: item["gap_id"])
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("references unknown scope" in error for error in errors),
                errors,
            )

    def test_duplicate_stable_source_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            duplicate = copy.deepcopy(state["source_registry"][0])
            state["source_registry"].insert(1, duplicate)
            evidence["source_registry"] = copy.deepcopy(state["source_registry"])
            self._save(bundle, "EvidenceRadar_State.json", state)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "State.source_registry contains duplicate source_id" in error
                    for error in errors
                ),
                errors,
            )

    def test_duplicate_query_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["queries"].append(copy.deepcopy(run["queries"][0]))
            run["counts"]["queries"] = len(run["queries"])
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.queries contains duplicate query_id='fixture-query'" in error
                    for error in errors
                ),
                errors,
            )

    def test_orphan_retrieval_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            orphan = copy.deepcopy(run["retrieval_attempts"][0])
            orphan.pop("source_access_id", None)
            orphan.pop("query_id", None)
            orphan.pop("requested_query", None)
            orphan.pop("actual_query", None)
            orphan.pop("request_limit", None)
            orphan["source_id"] = "orphan-source"
            orphan["endpoint"] = "https://example.test/orphan"
            orphan["request_fingerprint"] = "0" * 64
            orphan["pagination"] = {"pages_requested": 1, "pages_received": 1}
            orphan["attempt_id"] = self._stable_id(
                "attempt", run["run_id"], "CHECK", orphan["source_id"]
            )
            run["retrieval_attempts"].append(orphan)
            run["retrieval_attempts"].sort(key=lambda item: item["attempt_id"])
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "receipt is not declared by a query, source_access, or source CHECK"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_retrieval_status_requires_results_and_received_pages(self) -> None:
        """Every retained/attempted status has a non-empty executor receipt."""

        mutations = (
            (
                "partial-result-count",
                "PARTIAL",
                0,
                1,
                "PARTIAL requires result_count>=1",
            ),
            (
                "partial-pages-received",
                "PARTIAL",
                1,
                0,
                "PARTIAL requires at least one successfully received page",
            ),
            (
                "no-results-pages-received",
                "NO_RESULTS",
                0,
                0,
                "NO_RESULTS requires at least one successfully received page",
            ),
            (
                "success-pages-received",
                "SUCCESS",
                1,
                0,
                "SUCCESS requires at least one successfully received page",
            ),
        )
        for label, status, result_count, pages_received, expected in mutations:
            with self.subTest(mutation=label), tempfile.TemporaryDirectory() as directory:
                bundle, canonical = create_bundle(Path(directory))
                run = self._load(bundle, "EvidenceRadar_Run.json")
                if status == "NO_RESULTS":
                    attempt = next(
                        item
                        for item in run["retrieval_attempts"]
                        if item.get("status") == "NO_RESULTS"
                    )
                else:
                    attempt = next(
                        item
                        for item in run["retrieval_attempts"]
                        if item.get("query_id") == "fixture-query"
                    )
                    query = run["queries"][0]
                    query["status"] = status
                    query["result_count"] = result_count
                attempt["status"] = status
                attempt["result_count"] = result_count
                attempt["pagination"]["pages_received"] = pages_received
                self._save(bundle, "EvidenceRadar_Run.json", run)

                errors = self._validate(bundle, canonical)
                self.assertTrue(
                    any(expected in error for error in errors),
                    f"{label}: {errors}",
                )

    def test_current_claim_registry_entry_requires_evidence_claim_and_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            claim_id, _binding_id = self._add_metadata_claim(bundle, canonical)
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence["claims"] = []
            evidence["citation_bindings"] = []
            run["counts"]["claims"] = 0
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    f"State.claim_registry[{claim_id}] last_seen_run is current but Evidence.claims omits it"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_citation_binding_url_must_match_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            _claim_id, binding_id = self._add_metadata_claim(bundle, canonical)
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            binding = evidence["citation_bindings"][0]
            self.assertEqual(binding_id, binding["binding_id"])
            binding["source_url"] = "https://example.test/registry-mismatch"
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    f"citation binding {binding_id!r} URL differs from source_registry"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_fulltext_observation_cannot_borrow_discovery_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, _canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            borrowed_attempt = next(
                item
                for item in run["retrieval_attempts"]
                if item["stage"] == "DISCOVERY" and item["status"] == "NO_RESULTS"
            )
            observation = state["source_observations"][0]
            observation["attempt_id"] = borrowed_attempt["attempt_id"]
            observation["observed_at"] = borrowed_attempt["attempted_at"]
            observation["access_depth"] = "FULL_TEXT"
            observation["access_outcome"] = "ACCESSIBLE"
            observation_digest = hashlib.sha256(
                json.dumps(
                    [
                        observation["source_id"],
                        observation["run_id"],
                        observation["attempt_id"],
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            observation["observation_id"] = f"obs-{observation_digest}"
            evidence["source_observations"] = copy.deepcopy(
                state["source_observations"]
            )
            self._save(bundle, "EvidenceRadar_State.json", state)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)

            errors, _run = validate_delivery_bundle(ROOT, bundle)
            self.assertTrue(
                any(
                    "FULL_TEXT ACCESSIBLE requires a successful direct-content receipt"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_receipt_endpoint_must_match_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            attempt = next(
                item for item in run["retrieval_attempts"] if item.get("query_id")
            )
            attempt["endpoint"] = "https://example.test/forged-endpoint"
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("URL disagrees with its receipt endpoint" in error for error in errors),
                errors,
            )

    def test_source_check_count_must_reconcile_with_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            check = next(
                item
                for item in run["source_coverage"]["checks"]
                if item["source_id"] == "pubmed"
            )
            check["result_count"] = 999
            evidence["coverage"]["checks"] = copy.deepcopy(
                run["source_coverage"]["checks"]
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "source CHECK 'pubmed' result_count disagrees with receipts"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_inventory_observation_fields_are_all_or_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            access = next(
                item for item in run["source_access"] if item["provider"] == "pubmed"
            )
            access["retrieval_complete"] = True
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("incomplete inventory observation" in error for error in errors),
                errors,
            )

    def test_inventory_retrieval_status_must_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            access = next(
                item for item in run["source_access"] if item["provider"] == "pubmed"
            )
            access.update(
                {
                    "retrieval_complete": False,
                    "retrieval_backend": "rss_atom",
                    "feed_entry_count": 1,
                    "registry_record_count": 0,
                    "window_record_count": 1,
                    "inventory_url": "https://example.test/pubmed/feed.xml",
                }
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "incomplete inventory retrieval must be FAILED or PARTIAL" in error
                    for error in errors
                ),
                errors,
            )

    def test_inventory_observation_must_agree_across_provider_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            pubmed = next(
                item for item in run["source_access"] if item["provider"] == "pubmed"
            )
            second = next(
                item
                for item in run["source_access"]
                if item["provider"] == "europe_pmc"
            )
            common = {
                "retrieval_complete": True,
                "retrieval_backend": "rss_atom",
                "feed_entry_count": 2,
                "registry_record_count": 0,
                "window_record_count": 1,
                "inventory_url": "https://example.test/pubmed/feed.xml",
            }
            pubmed.update(common)
            second.update(common)
            second["provider"] = "pubmed"
            second["window_record_count"] = 2
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "inventory observation disagrees across 'pubmed' queries" in error
                    for error in errors
                ),
                errors,
            )

    def test_nonzero_inventory_can_have_zero_query_matches_and_no_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            access = next(
                item
                for item in run["source_access"]
                if item["provider"] == "europe_pmc"
            )
            access.update(
                {
                    "retrieval_complete": True,
                    "retrieval_backend": "rss_atom",
                    "feed_entry_count": 8,
                    "registry_record_count": 0,
                    "window_record_count": 8,
                    "inventory_url": "https://example.test/europe-pmc/feed.xml",
                }
            )
            check = next(
                item
                for item in run["source_coverage"]["checks"]
                if item["source_id"] == "europe_pmc"
            )
            self.assertEqual("NO_RESULTS", check["status"])
            self.assertEqual(0, check["result_count"])
            check["url"] = access["inventory_url"]
            evidence["coverage"]["checks"] = copy.deepcopy(
                run["source_coverage"]["checks"]
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_inventory_window_count_does_not_replace_aggregate_query_matches(self) -> None:
        """Repeated query matches remain separate from one cached provider inventory."""

        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            first_query = run["queries"][0]
            second_query = copy.deepcopy(first_query)
            second_query["query_id"] = "fixture-query-2"
            run["queries"].append(second_query)
            run["counts"]["queries"] = 2
            run["counts"]["raw_candidates"] = 2

            first_access = next(
                item for item in run["source_access"] if item["provider"] == "pubmed"
            )
            inventory = {
                "retrieval_complete": True,
                "retrieval_backend": "rss_atom",
                "feed_entry_count": 1,
                "registry_record_count": 0,
                "window_record_count": 1,
                "inventory_url": "https://example.test/pubmed/feed.xml",
            }
            first_access.update(inventory)
            second_access = copy.deepcopy(first_access)
            second_access["source_id"] = "pubmed-fixture-2"
            run["source_access"].append(second_access)
            run["source_access"].sort(key=lambda item: item["source_id"])

            first_attempt = next(
                item
                for item in run["retrieval_attempts"]
                if item.get("query_id") == "fixture-query"
            )
            second_attempt = copy.deepcopy(first_attempt)
            second_attempt["query_id"] = second_query["query_id"]
            second_attempt["source_access_id"] = second_access["source_id"]
            second_attempt["attempt_id"] = self._stable_id(
                "attempt",
                run["run_id"],
                second_attempt["stage"],
                second_query["query_id"],
                "pubmed",
            )
            run["retrieval_attempts"].append(second_attempt)
            run["retrieval_attempts"].sort(key=lambda item: item["attempt_id"])
            pubmed_check = next(
                item
                for item in run["source_coverage"]["checks"]
                if item["source_id"] == "pubmed"
            )
            pubmed_check["result_count"] = 2
            pubmed_check["url"] = inventory["inventory_url"]
            evidence["coverage"]["checks"] = copy.deepcopy(
                run["source_coverage"]["checks"]
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertEqual([], errors)

    def test_requested_sources_must_equal_configured_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            coverage = run["source_coverage"]
            replaced = coverage["requested"][0]
            for field in ("requested", "checked", "searched", "unavailable"):
                coverage[field] = [
                    "ghost-source" if value == replaced else value
                    for value in coverage[field]
                ]
                coverage[field].sort()
            for check in coverage["checks"]:
                if check["source_id"] == replaced:
                    check["source_id"] = "ghost-source"
            coverage["checks"].sort(key=lambda item: item["source_id"])
            evidence["coverage"].update(copy.deepcopy(coverage))
            evidence["coverage"]["requested_sources"] = list(coverage["requested"])
            evidence["coverage"]["searched_sources"] = list(coverage["searched"])
            evidence["coverage"]["unavailable_sources"] = list(
                coverage["unavailable"]
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.source_coverage.requested must exactly equal configured stream sources"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_evidence_fulltext_location_requires_stable_observed_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            source = evidence["sources"][0]
            source.update(
                {
                    "access_status": "FULL_TEXT",
                    "access_probe_status": "ACCESSIBLE",
                    "fulltext_kind": "HTML",
                    "download_urls": ["https://example.test/unobserved-fulltext"],
                    "fulltext_locations": [
                        {
                            "url": "https://example.test/unobserved-fulltext",
                            "kind": "HTML",
                            "host_type": "PUBLISHER",
                            "access_status": "ACCESSIBLE",
                            "reason": "Deliberately missing from source registry and receipts.",
                        }
                    ],
                }
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "fulltext location must have its own stable source entry" in error
                    for error in errors
                ),
                errors,
            )
            self.assertTrue(
                any(
                    "full-text access lacks an accessible FULL_TEXT observation"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_candidate_fulltext_status_requires_stable_current_direct_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            direct_url = "https://unobserved.example/fulltext.pdf"
            location = {
                "url": direct_url,
                "kind": "PDF",
                "host_type": "PUBLISHER",
                "access_status": "ACCESSIBLE",
                "reason": "Deliberately lacks an executor receipt.",
            }
            for item in (run["candidates"][0], state["works"][0]):
                item.update(
                    {
                        "access_status": "ACCESSIBLE",
                        "access_depth": "FULL_TEXT",
                        "access_outcome": "ACCESSIBLE",
                        "fulltext_kind": "PDF",
                        "download_urls": [direct_url],
                        "fulltext_locations": [copy.deepcopy(location)],
                    }
                )
                item["source_urls"] = sorted(
                    set(item.get("source_urls", [])) | {direct_url}
                )
            evidence["works"][0].update(
                {
                    "access_status": "ACCESSIBLE",
                    "access_depth": "FULL_TEXT",
                    "access_outcome": "ACCESSIBLE",
                    "fulltext_kind": "PDF",
                    "download_urls": [direct_url],
                    "fulltext_locations": [copy.deepcopy(location)],
                }
            )
            run["counts"]["fulltext_accessible"] = 1
            run["counts"]["fulltext_not_checked"] = 0
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "full-text URLs are absent from the stable source registry" in error
                    or "FULL_TEXT/ACCESSIBLE status lacks a current direct receipt" in error
                    for error in errors
                ),
                errors,
            )

    def test_candidate_blocked_status_requires_current_direct_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            for item in (run["candidates"][0], state["works"][0]):
                item.update(
                    {
                        "access_status": "BLOCKED",
                        "access_depth": "METADATA",
                        "access_outcome": "BLOCKED",
                        "fulltext_access_status": "BLOCKED",
                        "fulltext_kind": "ABSTRACT_ONLY",
                        "download_urls": [],
                        "fulltext_locations": [],
                    }
                )
            evidence["works"][0].update(
                {
                    "access_status": "BLOCKED",
                    "access_depth": "METADATA",
                    "access_outcome": "BLOCKED",
                    "fulltext_kind": "ABSTRACT_ONLY",
                    "download_urls": [],
                    "fulltext_locations": [],
                }
            )
            run["counts"]["fulltext_blocked"] = 1
            run["counts"]["fulltext_not_checked"] = 0
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "BLOCKED status lacks a current direct receipt" in error
                    for error in errors
                ),
                errors,
            )

    def test_supported_bibliographic_metadata_claim_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            claim_id, _binding_id = self._add_metadata_claim(bundle, canonical)
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            evidence["claims"][0]["status"] = "SUPPORTED"
            state["claim_registry"][0]["status"] = "SUPPORTED"
            run["candidates"][0]["review_status"] = "VERIFIED"
            run["counts"]["verified_works"] = 1
            run["counts"]["review_pending"] = 0
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertEqual([], errors, f"metadata claim {claim_id}: {errors}")

    def test_empty_work_pack_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            bundle, canonical = create_bundle(temporary)
            run = self._load(bundle, "EvidenceRadar_Run.json")
            manifest = temporary / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format": "evidenceradar-work-pack",
                        "source_commit": run["protocol_commit"],
                        "git_dirty": False,
                        "files": [],
                        "file_count": 0,
                    }
                ),
                encoding="utf-8",
            )

            errors, _run = validate_delivery_bundle(
                ROOT,
                bundle,
                canonical_state=canonical,
                manifest=manifest,
                reject_dirty=True,
            )
            self.assertTrue(
                any("manifest.files must not be empty" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("omits required execution capabilities" in error for error in errors),
                errors,
            )

    def test_followup_without_preexisting_gap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            attempt = run["retrieval_attempts"][0]
            attempt_id = attempt["attempt_id"]
            resolved = attempt["status"] in {"SUCCESS", "NO_RESULTS"}
            gap_id = "gap-does-not-exist"
            followup_digest = hashlib.sha256(
                json.dumps(
                    [gap_id, attempt_id],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            run["followup_attempts"] = [
                {
                    "followup_id": f"followup-{followup_digest}",
                    "gap_id": gap_id,
                    "trigger": "PRIMARY_SOURCE_MISSING",
                    "scope_type": "SOURCE",
                    "scope_id": "fixture-source",
                    "attempt_id": attempt_id,
                    "query": str(
                        attempt.get("actual_query")
                        or attempt.get("requested_query")
                        or attempt.get("endpoint")
                    ),
                    "source_backend": attempt["source_id"],
                    "attempted_at": attempt["attempted_at"],
                    "result": attempt["status"],
                    "resolved_gap_ids": [gap_id] if resolved else [],
                    "outcome": "RESOLVED" if resolved else "STILL_OPEN",
                }
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.followup_attempts[0] must reference a pre-existing gap"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_resolved_gap_requires_resolution_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state["gaps"] = [
                {
                    "gap_id": "gap-resolved-without-receipt",
                    "gap_type": "SOURCE_UNAVAILABLE",
                    "scope_type": "SOURCE",
                    "scope_id": "fixture-source",
                    "first_seen_run": "prior-run",
                    "last_attempt_run": run["run_id"],
                    "attempt_count": 1,
                    "status": "RESOLVED",
                    "max_attempts": 3,
                    "resolution_criteria": "A successful executor receipt is required.",
                    "receipt_ids": [],
                }
            ]
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "State.gaps[gap-resolved-without-receipt] RESOLVED requires a resolution receipt"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_model_inference_is_forbidden_as_citation_binding_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            _claim_id, binding_id = self._add_metadata_claim(bundle, canonical)
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            evidence["citation_bindings"][0]["extraction_origin"] = "MODEL_INFERENCE"
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    f"citation binding {binding_id!r} cannot use MODEL_INFERENCE" in error
                    for error in errors
                ),
                errors,
            )

    def test_topic_alignment_alone_never_supports_a_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            _claim_id, _binding_id = self._add_metadata_claim(bundle, canonical)
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            self.assertTrue(run["candidates"][0]["topic_alignments"])
            evidence["claims"][0]["status"] = "SUPPORTED"
            evidence["claims"][0]["claim_kind"] = "SCIENTIFIC_FINDING"
            evidence["claims"][0]["support_reason"] = (
                "Topic alignment is not substantive source support."
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "SUPPORTED requires compatible full-text extraction" in error
                    for error in errors
                ),
                errors,
            )

    def test_visible_numeric_claim_requires_structured_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            claim_id, _binding_id = self._add_metadata_claim(bundle, canonical)
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            claim = evidence["claims"][0]
            claim["claim_text"] = "This fixture reports 12 participants."
            state["claim_registry"][0]["claim_text_sha256"] = hashlib.sha256(
                claim["claim_text"].encode("utf-8")
            ).hexdigest()
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    f"Evidence.claims[{claim_id}] visible numeric content requires structured measurement"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_resolved_conflict_requires_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            self._add_metadata_claim(bundle, canonical)
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            second_claim = copy.deepcopy(evidence["claims"][0])
            second_claim["claim_id"] = "claim-v3-second"
            second_claim["claim_text"] = "Second metadata statement awaiting review."
            second_claim["citation_binding_ids"] = ["binding-v3-second"]
            second_binding = copy.deepcopy(evidence["citation_bindings"][0])
            second_binding["binding_id"] = "binding-v3-second"
            second_binding["claim_id"] = "claim-v3-second"
            second_registry = copy.deepcopy(state["claim_registry"][0])
            second_registry["claim_id"] = "claim-v3-second"
            second_registry["claim_text_sha256"] = hashlib.sha256(
                second_claim["claim_text"].encode("utf-8")
            ).hexdigest()
            second_registry["status_binding_ids"] = ["binding-v3-second"]
            evidence["claims"].append(second_claim)
            evidence["claims"].sort(key=lambda item: item["claim_id"])
            evidence["citation_bindings"].append(second_binding)
            evidence["citation_bindings"].sort(key=lambda item: item["binding_id"])
            evidence["conflict_groups"] = [
                {
                    "conflict_id": "conflict-v3-no-resolution",
                    "claim_ids": ["claim-v3-fixture", "claim-v3-second"],
                    "dimensions": ["POPULATION"],
                    "status": "RESOLVED",
                }
            ]
            state["claim_registry"].append(second_registry)
            state["claim_registry"].sort(key=lambda item: item["claim_id"])
            run["counts"]["claims"] = 2
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "conflict group 'conflict-v3-no-resolution' RESOLVED requires a resolution"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_master_bindings_are_required_in_all_three_json_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            evidence.pop("profile_id")
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Evidence has incomplete master-control bindings" in error
                    for error in errors
                ),
                errors,
            )

    def test_master_bindings_must_be_identical_across_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            evidence["runtime_request_sha256"] = "0" * 64
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Evidence.runtime_request_sha256 must be JSON-identical to Run.runtime_request_sha256"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_chatbot_translation_requires_request_digest_in_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            documents = {
                name: self._load(bundle, f"EvidenceRadar_{name}.json")
                for name in ("Run", "State", "Evidence")
            }
            documents["Run"]["notes"].append("CHATBOT_TRANSLATION_HANDOFF_V1")
            for document in documents.values():
                document["runtime_request_sha256"] = None
            for name, document in documents.items():
                self._save(bundle, f"EvidenceRadar_{name}.json", document)
            canonical.write_text(
                json.dumps(documents["State"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            errors = self._validate(bundle, canonical)
            for label in ("Run", "State", "Evidence"):
                self.assertTrue(
                    any(
                        f"{label}.runtime_request_sha256 must be a lowercase 64-hex digest"
                        in error
                        for error in errors
                    ),
                    errors,
                )

    def test_translation_request_binding_cannot_drop_handoff_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            documents = {
                name: self._load(bundle, f"EvidenceRadar_{name}.json")
                for name in ("Run", "State", "Evidence")
            }
            for document in documents.values():
                document["runtime_request_sha256"] = "1" * 64
            documents["Run"]["notes"] = [
                note
                for note in documents["Run"]["notes"]
                if note != "CHATBOT_TRANSLATION_HANDOFF_V1"
            ]
            for name, document in documents.items():
                self._save(bundle, f"EvidenceRadar_{name}.json", document)
            canonical.write_text(
                json.dumps(documents["State"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "runtime request or chatbot summary evidence requires exactly one "
                    "CHATBOT_TRANSLATION_HANDOFF_V1 marker"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_state_study_classification_values_must_equal_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state = self._load(bundle, "EvidenceRadar_State.json")
            state["works"][0]["study_designs"] = ["review"]
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    ".study_designs must equal Run.candidates[0].study_designs"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_state_preprint_flag_cannot_drift_from_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            for item in (run["candidates"][0], state["works"][0]):
                item["document_type"] = "preprint"
                item["document_type_basis"] = "SOURCE_CLASS"
                item["provider_publication_types"] = ["preprint"]
            run["candidates"][0]["is_preprint"] = True
            state["works"][0]["is_preprint"] = False
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    ".is_preprint must equal or monotonically strengthen Run.candidates"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_chatbot_title_must_match_state_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            state = self._load(bundle, "EvidenceRadar_State.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            run["notes"].append("CHATBOT_TRANSLATION_HANDOFF_V1")
            run["runtime_request_sha256"] = "4" * 64
            state["runtime_request_sha256"] = "4" * 64
            evidence["runtime_request_sha256"] = "4" * 64
            run["candidates"][0]["title_zh_tw"] = "一致性測試標題"
            run["candidates"][0]["summary_basis"] = "CHATBOT_TITLE_ZH_TW"
            state["works"][0]["title_zh_tw"] = "遭竄改的不同標題"
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_State.json", state)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            canonical.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    ".title_zh_tw must equal Run.candidates[0].title_zh_tw" in error
                    for error in errors
                ),
                errors,
            )

    def test_master_note_markers_must_match_run_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            marker_index = next(
                index
                for index, note in enumerate(run["notes"])
                if note.startswith("RADAR_SOURCES_JSON:")
            )
            run["notes"][marker_index] = "RADAR_SOURCES_JSON:[\"pubmed\"]"
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.resolved_source_ids must equal RADAR_SOURCES_JSON" in error
                    for error in errors
                ),
                errors,
            )

    def test_master_note_markers_are_all_required(self) -> None:
        for prefix in (
            "RADAR_PROFILE:",
            "RADAR_STREAMS_JSON:",
            "RADAR_SOURCES_JSON:",
        ):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                bundle, canonical = create_bundle(Path(directory))
                run = self._load(bundle, "EvidenceRadar_Run.json")
                run["notes"] = [
                    note for note in run["notes"] if not note.startswith(prefix)
                ]
                self._save(bundle, "EvidenceRadar_Run.json", run)

                errors = self._validate(bundle, canonical)
                self.assertTrue(
                    any(
                        f"exactly one non-empty {prefix[:-1]}" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_master_resolution_cannot_be_self_consistently_narrowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            documents = {
                name: self._load(bundle, f"EvidenceRadar_{name}.json")
                for name in ("Run", "State", "Evidence")
            }
            removed_source = documents["Run"]["resolved_source_ids"][0]
            narrowed = [
                source_id
                for source_id in documents["Run"]["resolved_source_ids"]
                if source_id != removed_source
            ]
            for document in documents.values():
                document["resolved_source_ids"] = list(narrowed)
            documents["Run"]["notes"] = [
                (
                    "RADAR_SOURCES_JSON:"
                    + json.dumps(narrowed, separators=(",", ":"))
                    if note.startswith("RADAR_SOURCES_JSON:")
                    else note
                )
                for note in documents["Run"]["notes"]
            ]
            for name, document in documents.items():
                self._save(bundle, f"EvidenceRadar_{name}.json", document)
            canonical.write_text(
                json.dumps(documents["State"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "Run.resolved_source_ids must exactly equal load_master_runtime resolution"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_legacy_v3_without_master_markers_uses_legacy_catalog(self) -> None:
        streams, errors = _configured_streams_for_run(
            ROOT,
            {"notes": ["SEMANTIC_CONTRACT_V3"]},
            {},
            {},
        )
        self.assertEqual([], errors)
        self.assertIsNotNone(streams)
        self.assertEqual(
            sorted((streams or {}).get("source_catalog", {})),
            [
                "acl_anthology",
                "arxiv",
                "europe_pmc",
                "formal_proceedings_or_publisher",
                "openalex",
                "openreview",
                "pmlr",
                "publisher",
                "pubmed",
            ],
        )

    def test_master_capable_producer_cannot_strip_every_master_binding(self) -> None:
        with mock.patch(
            "tools.validate_delivery_bundle._producer_requires_master_control",
            return_value=True,
        ):
            streams, errors = _configured_streams_for_run(
                ROOT,
                {
                    "notes": ["SEMANTIC_CONTRACT_V3"],
                    "protocol_commit": "f" * 40,
                },
                {},
                {},
            )
        self.assertIsNone(streams)
        self.assertTrue(
            any("cannot omit all master bindings" in error for error in errors),
            errors,
        )

    def test_http_telemetry_capable_producer_rejects_strip_all_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["notes"].append("EXECUTOR_HTTP_TELEMETRY_V1")
            attempt = next(
                item for item in run["retrieval_attempts"] if item.get("query_id")
            )
            access = next(
                item
                for item in run["source_access"]
                if item["source_id"] == attempt["source_access_id"]
            )
            access.update(
                {
                    "http_requests_attempted": 3,
                    "http_responses_received": 3,
                    "cache_reused": False,
                }
            )
            attempt["pagination"] = {"pages_requested": 3, "pages_received": 3}
            for field in (
                "http_requests_attempted",
                "http_responses_received",
                "cache_reused",
            ):
                access.pop(field)
            attempt["pagination"] = {"pages_requested": 1, "pages_received": 1}
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._refresh_report(bundle)

            with mock.patch(
                "tools.validate_delivery_bundle._producer_requires_http_telemetry",
                return_value=True,
            ):
                errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "modern query receipt requires HTTP telemetry" in error
                    or "modern executor receipt requires HTTP telemetry" in error
                    for error in errors
                ),
                errors,
            )

    def test_modern_inventory_cannot_strip_unusable_and_pagination_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            run["notes"].append("EXECUTOR_HTTP_TELEMETRY_V1")
            provider = "jama_network_open"
            access_id = "jama-inventory-fixture"
            endpoint = "https://api.crossref.org/journals/2574-3805/works"
            accessed_at = run["finished_at"]
            run["source_access"].append(
                {
                    "source_id": access_id,
                    "provider": provider,
                    "url": endpoint,
                    "accessed_at": accessed_at,
                    "status": "NO_RESULTS",
                    "result_count": 0,
                    "retrieval_complete": True,
                    "retrieval_backend": "rss_atom+crossref_journal_window",
                    "feed_entry_count": 0,
                    "registry_record_count": 0,
                    "window_record_count": 0,
                    "inventory_url": endpoint,
                }
            )
            run["source_access"].sort(key=lambda item: item["source_id"])
            def canonical_value(value):
                return json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            run["retrieval_attempts"].append(
                {
                    "attempt_id": self._stable_id(
                        "attempt", run["run_id"], "DISCOVERY", access_id
                    ),
                    "stage": "DISCOVERY",
                    "source_id": provider,
                    "source_access_id": access_id,
                    "attempted_at": accessed_at,
                    "status": "NO_RESULTS",
                    "endpoint": endpoint,
                    "request_fingerprint": hashlib.sha256(
                        canonical_value(
                            {"url": endpoint, "provider": provider, "work_id": ""}
                        ).encode("utf-8")
                    ).hexdigest(),
                    "receipt_origin": "EXECUTOR",
                    "result_count": 0,
                    "result_ids_sha256": hashlib.sha256(
                        canonical_value([]).encode("utf-8")
                    ).hexdigest(),
                    "pagination": {"pages_requested": 1, "pages_received": 1},
                    "limit_reached": False,
                }
            )
            run["retrieval_attempts"].sort(key=lambda item: item["attempt_id"])
            check = next(
                item
                for item in run["source_coverage"]["checks"]
                if item["source_id"] == provider
            )
            check["url"] = endpoint
            evidence["coverage"]["checks"] = copy.deepcopy(
                run["source_coverage"]["checks"]
            )
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            self._refresh_report(bundle)

            with mock.patch(
                "tools.validate_delivery_bundle._producer_requires_http_telemetry",
                return_value=True,
            ):
                errors = self._validate(bundle, canonical)
            self.assertTrue(
                any("modern inventory telemetry is incomplete" in error for error in errors),
                errors,
            )

    def test_catalog_hybrid_sources_reject_rss_only_inventory_receipts(self) -> None:
        for provider, feed_url in (
            ("jama_network_open", "https://jamanetwork.com/rss/site_214/187.xml"),
            ("nature_communications", "https://www.nature.com/ncomms.rss"),
        ):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                bundle, canonical = create_bundle(Path(directory))
                run = self._load(bundle, "EvidenceRadar_Run.json")
                run["source_access"].append(
                    {
                        "source_id": f"{provider}-rss-only-fixture",
                        "provider": provider,
                        "url": feed_url,
                        "accessed_at": run["finished_at"],
                        "status": "NO_RESULTS",
                        "result_count": 0,
                        "retrieval_complete": True,
                        "retrieval_backend": "rss_atom",
                        "feed_entry_count": 8,
                        "registry_record_count": 0,
                        "unusable_record_count": 0,
                        "window_record_count": 8,
                        "inventory_url": feed_url,
                        "inventory_pages_requested": 1,
                        "inventory_pages_received": 1,
                        "http_requests_attempted": 1,
                        "http_responses_received": 1,
                        "cache_reused": False,
                    }
                )
                run["source_access"].sort(key=lambda item: item["source_id"])
                self._save(bundle, "EvidenceRadar_Run.json", run)
                self._refresh_report(bundle)

                errors = self._validate(bundle, canonical)
                self.assertTrue(
                    any(
                        f"{provider!r} requires retrieval_backend=" in error
                        and "rss_atom+crossref_journal_window" in error
                        for error in errors
                    ),
                    errors,
                )
                self.assertTrue(
                    any(
                        "inventory_url must use the catalog-bound Crossref journal works endpoint"
                        in error
                        for error in errors
                    ),
                    errors,
                )

    def test_complete_hybrid_inventory_requires_feed_and_crossref_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            inventory_url = (
                "https://api.crossref.org/journals/2041-1723/works?"
                "filter=from-online-pub-date%3A2026-08-06%2C"
                "until-online-pub-date%3A2026-08-09&rows=1000&cursor=%2A"
            )
            run["source_access"].append(
                {
                    "source_id": "nature-one-page-hybrid-fixture",
                    "provider": "nature_communications",
                    "url": inventory_url,
                    "accessed_at": run["finished_at"],
                    "status": "NO_RESULTS",
                    "result_count": 0,
                    "retrieval_complete": True,
                    "retrieval_backend": "rss_atom+crossref_journal_window",
                    "feed_entry_count": 8,
                    "registry_record_count": 116,
                    "unusable_record_count": 0,
                    "window_record_count": 8,
                    "inventory_url": inventory_url,
                    "inventory_pages_requested": 1,
                    "inventory_pages_received": 1,
                    "http_requests_attempted": 1,
                    "http_responses_received": 1,
                    "cache_reused": False,
                }
            )
            run["source_access"].sort(key=lambda item: item["source_id"])
            self._save(bundle, "EvidenceRadar_Run.json", run)
            self._refresh_report(bundle)

            errors = self._validate(bundle, canonical)
            self.assertTrue(
                any(
                    "complete hybrid inventory requires at least 2 requested pages"
                    in error
                    for error in errors
                ),
                errors,
            )

    def test_v2_fixture_remains_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            run["notes"] = [
                note for note in run.get("notes", []) if note != "SEMANTIC_CONTRACT_V3"
            ]
            self._save(bundle, "EvidenceRadar_Run.json", run)

            errors = self._validate(bundle, canonical)
            self.assertEqual([], errors)

            public_errors, _run = validate_delivery_bundle(
                ROOT,
                bundle,
                canonical_state=canonical,
                require_semantic_contract_v3=True,
            )
            self.assertIn(
                "SEMANTIC_CONTRACT_V3 is required for this delivery",
                public_errors,
            )


if __name__ == "__main__":
    unittest.main()
