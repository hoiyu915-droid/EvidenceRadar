"""Tests for deterministic, concurrent-safe EvidenceRadar state merging."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_delivery_bundle import create_bundle
from tools.merge_radar_state import (
    StateMergeError,
    merge_states,
    state_sha256,
    write_json_atomic,
)
from tools.validate_gpt_work_artifacts import load_json, validate_document

ROOT = Path(__file__).resolve().parents[1]


def _work(
    work_id: str,
    *,
    title: str = "Shared title",
    normalized_title: str = "shared title",
    identifiers: dict[str, str] | None = None,
    first: str = "2026-08-01T00:00:00+00:00",
    last: str = "2026-08-01T01:00:00+00:00",
    count: int = 1,
    event_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
    streams: list[str] | None = None,
    notes: list[str] | None = None,
    oa_status: str | None = None,
    oa_evidence: list[dict] | None = None,
    access_status: str | None = None,
    fulltext_kind: str | None = None,
    download_urls: list[str] | None = None,
    fulltext_locations: list[dict] | None = None,
    event_class: str | None = None,
    provider_publication_types: list[str] | None = None,
    study_designs: list[str] | None = None,
) -> dict:
    return {
        "work_id": work_id,
        "title": title,
        "normalized_title": normalized_title,
        "identifiers": identifiers or {},
        "first_seen_at": first,
        "last_seen_at": last,
        "seen_count": count,
        "notified_event_ids": event_ids or [],
        **({"source_urls": source_urls} if source_urls is not None else {}),
        **({"streams": streams} if streams is not None else {}),
        **({"notes": notes} if notes is not None else {}),
        **({"oa_status": oa_status} if oa_status is not None else {}),
        **({"oa_evidence": oa_evidence} if oa_evidence is not None else {}),
        **({"access_status": access_status} if access_status is not None else {}),
        **({"fulltext_kind": fulltext_kind} if fulltext_kind is not None else {}),
        **({"download_urls": download_urls} if download_urls is not None else {}),
        **({"fulltext_locations": fulltext_locations} if fulltext_locations is not None else {}),
        **({"event_class": event_class} if event_class is not None else {}),
        **(
            {"provider_publication_types": provider_publication_types}
            if provider_publication_types is not None
            else {}
        ),
        **({"study_designs": study_designs} if study_designs is not None else {}),
    }


def _event(event_id: str, work_id: str, *, notified_at: str = "2026-08-01T01:00:00+00:00") -> dict:
    return {
        "event_id": event_id,
        "work_id": work_id,
        "event_type": "version_of_record_first_online",
        "occurred_at": "2026-08-01",
        "notified_at": notified_at,
        "source": "Example Publisher",
        "source_url": "https://example.org/article",
        "source_field": "publisher_online_date",
        "precision": "date",
        "confidence": "publisher_verified",
    }


def _state(*, generated: str, run_id: str, works: list[dict], events: list[dict], **extra: object) -> dict:
    return {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_State",
        "generated_at": generated,
        "timezone": "UTC",
        "history_status": "COMPLETE",
        "last_run_id": run_id,
        "dedupe_priority": ["doi", "pmid", "normalized_title"],
        "works": works,
        "notified_events": events,
        **extra,
    }


class StateMergeTests(unittest.TestCase):
    def test_v3_cli_loads_schema_validator_from_pack_root_outside_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            bundle, _canonical = create_bundle(temporary / "fixture")
            state_path = bundle / "EvidenceRadar_State.json"
            output_path = temporary / "merged" / "EvidenceRadar_State.json"
            outside_cwd = temporary / "outside-cwd"
            outside_cwd.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "merge_radar_state.py"),
                    str(state_path),
                    str(state_path),
                    "--output",
                    str(output_path),
                ],
                cwd=outside_cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            schema = load_json(ROOT / "schemas" / "evidence-radar-state.schema.json")
            self.assertEqual([], validate_document(load_json(output_path), schema))

    def test_v3_relation_merge_is_order_independent_and_preserves_review(self) -> None:
        def relation_id(prefix: str, left: str, right: str, kind: str) -> str:
            digest = hashlib.sha256(
                json.dumps(
                    [left, right, kind],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            return f"{prefix}-{digest}"

        left = "doi:10.1000/left"
        right = "doi:10.1000/right"
        relation = relation_id("workrel", left, right, "NEW_VERSION")

        def state(run_id: str, status: str, basis: str) -> dict:
            return _state(
                generated=f"2026-08-0{1 if run_id == 'run-a' else 2}T00:00:00+00:00",
                run_id=run_id,
                works=[
                    _work(
                        left,
                        title="Left version",
                        normalized_title="left version",
                        identifiers={"doi": "10.1000/left"},
                    ),
                    _work(
                        right,
                        title="Right version",
                        normalized_title="right version",
                        identifiers={"doi": "10.1000/right"},
                    ),
                ],
                events=[],
                work_relations=[
                    {
                        "relation_id": relation,
                        "from_work_id": left,
                        "to_work_id": right,
                        "relation_type": "NEW_VERSION",
                        "comparison_basis": basis,
                        "review_status": status,
                        "observed_run_id": run_id,
                    }
                ],
            )

        base = state("run-a", "AUTO_DETECTED", "title continuity")
        incoming = state("run-b", "REVIEWED", "identifier review")
        forward = merge_states(base, incoming)
        reverse = merge_states(incoming, base)
        self.assertEqual(forward["work_relations"], reverse["work_relations"])
        self.assertEqual("REVIEWED", forward["work_relations"][0]["review_status"])
        self.assertEqual(
            "identifier review | title continuity",
            forward["work_relations"][0]["comparison_basis"],
        )
        self.assertEqual("run-b", forward["work_relations"][0]["observed_run_id"])

    def test_v3_gap_merge_uses_latest_status_and_counts_divergent_attempts(self) -> None:
        gap_id = "gap-" + hashlib.sha256(
            json.dumps(
                ["SOURCE_UNAVAILABLE", "pubmed"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]

        def state(
            *, generated: str, run_id: str, status: str, receipt: str,
            resolution: str | None = None,
        ) -> dict:
            gap = {
                "gap_id": gap_id,
                "gap_type": "SOURCE_UNAVAILABLE",
                "scope_type": "SOURCE_SYSTEM",
                "scope_id": "pubmed",
                "first_seen_run": "run-old",
                "last_attempt_run": run_id,
                "attempt_count": 1,
                "status": status,
                "max_attempts": 3,
                "resolution_criteria": "A successful executor receipt.",
                "receipt_ids": [receipt],
            }
            if resolution is not None:
                gap["resolution_receipt_id"] = resolution
            return _state(
                generated=generated,
                run_id=run_id,
                works=[_work("pmid:gap", identifiers={"pmid": "gap"})],
                events=[],
                source_registry=[],
                source_observations=[],
                gaps=[gap],
                work_relations=[],
                claim_relations=[],
                claim_registry=[],
            )

        older = state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-old",
            status="RESOLVED",
            receipt="attempt-ok",
            resolution="attempt-ok",
        )
        newer = state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-new",
            status="OPEN",
            receipt="attempt-fail",
        )
        forward = merge_states(older, newer)
        reverse = merge_states(newer, older)
        self.assertEqual(forward["gaps"], reverse["gaps"])
        self.assertEqual("OPEN", forward["gaps"][0]["status"])
        self.assertEqual(2, forward["gaps"][0]["attempt_count"])
        self.assertEqual(
            ["attempt-fail", "attempt-ok"], forward["gaps"][0]["receipt_ids"]
        )
        self.assertNotIn("resolution_receipt_id", forward["gaps"][0])

    def test_v3_relation_merge_rejects_self_relation(self) -> None:
        state = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-self",
            works=[_work("pmid:self", identifiers={"pmid": "self"})],
            events=[],
            source_registry=[],
            source_observations=[],
            gaps=[],
            work_relations=[
                {
                    "relation_id": "workrel-self",
                    "from_work_id": "pmid:self",
                    "to_work_id": "pmid:self",
                    "relation_type": "NEW_VERSION",
                    "comparison_basis": "invalid self edge",
                    "review_status": "AUTO_DETECTED",
                    "observed_run_id": "run-self",
                }
            ],
            claim_relations=[],
            claim_registry=[],
        )
        with self.assertRaisesRegex(StateMergeError, "self-referential"):
            merge_states(state, copy.deepcopy(state))

    def test_v3_state_merge_rejects_schema_invalid_registry_enum(self) -> None:
        state = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-invalid-source",
            works=[_work("pmid:invalid", identifiers={"pmid": "invalid"})],
            events=[],
            source_registry=[
                {
                    "source_id": "src-invalid",
                    "work_id": "pmid:invalid",
                    "canonical_url": "https://example.test/invalid",
                    "source_type": "publisher",
                    "source_role": "INVALID_ROLE",
                    "identifiers": {"pmid": "invalid"},
                    "first_seen_run": "run-invalid-source",
                    "last_seen_run": "run-invalid-source",
                }
            ],
            source_observations=[],
            gaps=[],
            work_relations=[],
            claim_relations=[],
            claim_registry=[],
        )
        with self.assertRaisesRegex(StateMergeError, "fails the State schema"):
            merge_states(state, copy.deepcopy(state))

    def test_v3_state_merge_rejects_unstable_source_id(self) -> None:
        state = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-unstable-source",
            works=[_work("pmid:unstable", identifiers={"pmid": "unstable"})],
            events=[],
            source_registry=[
                {
                    "source_id": "src-not-stable",
                    "work_id": "pmid:unstable",
                    "canonical_url": "https://example.test/source",
                    "source_type": "publisher",
                    "source_role": "FORMAL_PUBLICATION",
                    "identifiers": {"pmid": "unstable"},
                    "first_seen_run": "run-unstable-source",
                    "last_seen_run": "run-unstable-source",
                }
            ],
            source_observations=[],
            gaps=[],
            work_relations=[],
            claim_relations=[],
            claim_registry=[],
        )
        with self.assertRaisesRegex(StateMergeError, "source_id is not stable"):
            merge_states(state, copy.deepcopy(state))

    def test_v3_first_and_last_run_follow_snapshot_time_not_lexical_id(self) -> None:
        source_url = "https://example.org/chronology"
        source_digest = hashlib.sha256(
            json.dumps(
                [source_url],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        source_id = f"src-{source_digest}"

        def state(generated: str, run_id: str) -> dict:
            return _state(
                generated=generated,
                run_id=run_id,
                works=[
                    _work(
                        "pmid:chronology",
                        identifiers={"pmid": "chronology"},
                    )
                ],
                events=[],
                source_registry=[
                    {
                        "source_id": source_id,
                        "work_id": "pmid:chronology",
                        "canonical_url": source_url,
                        "source_type": "publisher",
                        "source_role": "FORMAL_PUBLICATION",
                        "identifiers": {"pmid": "chronology"},
                        "first_seen_run": run_id,
                        "last_seen_run": run_id,
                    }
                ],
                source_observations=[],
                gaps=[],
                work_relations=[],
                claim_relations=[],
                claim_registry=[],
            )

        older = state("2026-08-01T00:00:00+00:00", "run-9")
        newer = state("2026-08-02T00:00:00+00:00", "run-10")
        forward = merge_states(older, newer)
        reverse = merge_states(newer, older)
        self.assertEqual(forward["source_registry"], reverse["source_registry"])
        self.assertEqual("run-9", forward["source_registry"][0]["first_seen_run"])
        self.assertEqual("run-10", forward["source_registry"][0]["last_seen_run"])

    def test_v3_registry_gap_and_claim_state_follow_canonical_work_identity(self) -> None:
        def stable_id(prefix: str, *parts: object) -> str:
            digest = hashlib.sha256(
                json.dumps(
                    [str(part) for part in parts],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            return f"{prefix}-{digest}"

        source_url = "https://example.org/stable-source"
        source_id = stable_id("src", source_url)
        claim_id = "claim-stable"
        base_gap_id = stable_id("gap", "CONTENT_INACCESSIBLE", "legacy-alias")
        incoming_gap_id = stable_id("gap", "CONTENT_INACCESSIBLE", "pmid:2")

        def v3_extra(work_id: str, run_id: str, gap_id: str, status: str) -> dict:
            return {
                "source_registry": [
                    {
                        "source_id": source_id,
                        "work_id": work_id,
                        "canonical_url": source_url,
                        "source_type": "publisher",
                        "source_role": "FORMAL_PUBLICATION",
                        "identifiers": {"pmid": "2"},
                        "first_seen_run": run_id,
                        "last_seen_run": run_id,
                    }
                ],
                "source_observations": [],
                "gaps": [
                    {
                        "gap_id": gap_id,
                        "gap_type": "CONTENT_INACCESSIBLE",
                        "scope_type": "WORK",
                        "scope_id": work_id,
                        "first_seen_run": run_id,
                        "last_attempt_run": run_id,
                        "attempt_count": 1,
                        "status": "OPEN",
                        "max_attempts": 3,
                        "resolution_criteria": "A direct content receipt succeeds.",
                        "receipt_ids": [f"attempt-{run_id}"],
                    }
                ],
                "work_relations": [],
                "claim_relations": [],
                "claim_registry": [
                    {
                        "claim_id": claim_id,
                        "work_id": work_id,
                        "claim_kind": "BIBLIOGRAPHIC_FACT",
                        "claim_origin": "METADATA_REPORTED",
                        "claim_text_sha256": "a" * 64,
                        "status": status,
                        "source_ids": [source_id],
                        "status_binding_ids": [f"binding-{run_id}"],
                        "first_seen_run": run_id,
                        "last_seen_run": run_id,
                        "last_status_change_run": run_id,
                    }
                ],
            }

        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[_work("legacy-alias", identifiers={"pmid": "2"})],
            events=[],
            **v3_extra("legacy-alias", "run-a", base_gap_id, "SUPPORTED"),
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[_work("pmid:2", identifiers={"pmid": "2"})],
            events=[],
            **v3_extra("pmid:2", "run-b", incoming_gap_id, "UNVERIFIED"),
        )

        merged = merge_states(base, incoming)
        self.assertEqual("pmid:2", merged["works"][0]["work_id"])
        self.assertEqual("pmid:2", merged["source_registry"][0]["work_id"])
        self.assertEqual("run-a", merged["source_registry"][0]["first_seen_run"])
        self.assertEqual("run-b", merged["source_registry"][0]["last_seen_run"])
        self.assertEqual(1, len(merged["gaps"]))
        self.assertEqual(
            stable_id("gap", "CONTENT_INACCESSIBLE", "pmid:2"),
            merged["gaps"][0]["gap_id"],
        )
        self.assertEqual(["attempt-run-a", "attempt-run-b"], merged["gaps"][0]["receipt_ids"])
        self.assertEqual("UNVERIFIED", merged["claim_registry"][0]["status"])
        self.assertEqual("pmid:2", merged["claim_registry"][0]["work_id"])

        schema = load_json(ROOT / "schemas" / "evidence-radar-state.schema.json")
        self.assertEqual([], validate_document(merged, schema))

    def test_stale_union_preserves_bounds_counts_lists_and_is_idempotent(self) -> None:
        base = _state(
            generated="2026-08-01T01:00:00+00:00",
            run_id="run-base",
            works=[
                _work(
                    "work-base",
                    identifiers={"doi": "10.1234/Example"},
                    first="2026-07-31T23:00:00+00:00",
                    last="2026-08-01T01:00:00+00:00",
                    count=4,
                    event_ids=["event-1"],
                    source_urls=["https://source.example/base"],
                    streams=["clinical_medicine"],
                    notes=["base note"],
                )
            ],
            events=[_event("event-1", "work-base")],
        )
        incoming = _state(
            generated="2026-08-01T00:30:00+00:00",  # deliberately stale
            run_id="run-incoming",
            works=[
                _work(
                    "work-incoming",
                    identifiers={"doi": "doi:10.1234/example"},
                    first="2026-08-01T00:00:00+00:00",
                    last="2026-08-01T02:00:00+00:00",
                    count=2,
                    event_ids=["event-1", "event-2"],
                    source_urls=["https://source.example/incoming"],
                    streams=["llm_research"],
                    notes=["incoming note"],
                )
            ],
            events=[_event("event-1", "work-incoming"), _event("event-2", "work-incoming")],
        )

        merged = merge_states(base, incoming, execution_lane="chatgpt_work", protocol_commit="abc123")
        self.assertEqual(len(merged["works"]), 1)
        work = merged["works"][0]
        self.assertEqual(work["first_seen_at"], "2026-07-31T23:00:00+00:00")
        self.assertEqual(work["last_seen_at"], "2026-08-01T02:00:00+00:00")
        self.assertEqual(work["seen_count"], 4)
        self.assertEqual(work["source_urls"], ["https://source.example/base", "https://source.example/incoming"])
        self.assertEqual(work["streams"], ["clinical_medicine", "llm_research"])
        self.assertEqual(work["notes"], ["base note", "incoming note"])
        self.assertEqual([event["event_id"] for event in merged["notified_events"]], ["event-1", "event-2"])
        self.assertTrue(all(event["work_id"] == work["work_id"] for event in merged["notified_events"]))
        self.assertEqual(merged["base_state_sha256"], state_sha256(base))
        self.assertEqual(merged["parent_run_ids"], ["run-incoming"])

        # Applying the same stale branch again is a no-op, including byte
        # ordering and provenance, which is the key concurrency guarantee.
        repeated = merge_states(merged, incoming)
        # The audit hash is intentionally directional: it identifies the
        # caller's base snapshot.  The merged state itself stays identical.
        self.assertEqual(repeated["works"], merged["works"])
        self.assertEqual(repeated["notified_events"], merged["notified_events"])
        self.assertEqual(repeated["parent_run_ids"], merged["parent_run_ids"])

    def test_default_provenance_follows_the_state_that_supplies_last_run(self) -> None:
        base = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-newer",
            works=[],
            events=[],
            execution_lane="github_actions",
            protocol_commit="newer-commit",
        )
        incoming = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-stale",
            works=[],
            events=[],
            execution_lane="chatgpt_work",
            protocol_commit="stale-commit",
        )
        merged = merge_states(base, incoming)
        self.assertEqual("run-newer", merged["last_run_id"])
        self.assertEqual("github_actions", merged["execution_lane"])
        self.assertEqual("newer-commit", merged["protocol_commit"])

    def test_formal_state_observation_clears_historical_preprint_flag(self) -> None:
        preprint = _work(
            "doi:10.1000/versioned",
            identifiers={"doi": "10.1000/versioned", "arxiv_id": "2608.1"},
            provider_publication_types=["preprint"],
            first="2025-01-01T00:00:00+00:00",
            last="2025-01-01T00:00:00+00:00",
        )
        preprint.update(
            {
                "is_preprint": True,
                "document_type": "preprint",
                "document_type_basis": "SOURCE_CLASS",
                "study_design_basis": "UNKNOWN",
            }
        )
        formal = _work(
            "doi:10.1000/versioned",
            identifiers={"doi": "10.1000/versioned", "pmid": "123"},
            provider_publication_types=["Journal Article"],
            first="2026-08-10T00:00:00+00:00",
            last="2026-08-10T00:00:00+00:00",
        )
        formal.update(
            {
                "is_preprint": False,
                "document_type": "journal_article",
                "document_type_basis": "PROVIDER_METADATA",
                "study_design_basis": "UNKNOWN",
            }
        )
        merged = merge_states(
            _state(
                generated="2025-01-01T00:00:00+00:00",
                run_id="run-preprint",
                works=[preprint],
                events=[],
            ),
            _state(
                generated="2026-08-10T00:00:00+00:00",
                run_id="run-formal",
                works=[formal],
                events=[],
            ),
        )
        self.assertFalse(merged["works"][0]["is_preprint"])
        self.assertEqual("journal_article", merged["works"][0]["document_type"])

    def test_latest_event_class_and_empty_classification_lists_survive_merge(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-base",
            works=[
                _work(
                    "pmid:classification",
                    identifiers={"pmid": "classification"},
                    last="2026-08-01T01:00:00+00:00",
                    event_class="BACKFILL_INDEXING",
                    provider_publication_types=[],
                    study_designs=[],
                )
            ],
            events=[],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-incoming",
            works=[
                _work(
                    "pmid:classification",
                    identifiers={"pmid": "classification"},
                    last="2026-08-02T01:00:00+00:00",
                    event_class="NEW_PUBLICATION",
                    provider_publication_types=[],
                    study_designs=[],
                )
            ],
            events=[],
        )

        work = merge_states(base, incoming)["works"][0]
        self.assertEqual("NEW_PUBLICATION", work["event_class"])
        self.assertEqual([], work["provider_publication_types"])
        self.assertEqual([], work["study_designs"])

    def test_latest_complete_master_control_bindings_are_preserved(self) -> None:
        older_bindings = {
            "profile_id": "medicine_reader",
            "resolved_stream_ids": ["clinical_medicine"],
            "resolved_source_ids": ["pubmed"],
            "master_control_sha256": "1" * 64,
            "runtime_request_sha256": None,
        }
        newer_bindings = {
            "profile_id": "owner_daily",
            "resolved_stream_ids": ["clinical_medicine", "sport_science"],
            "resolved_source_ids": ["pubmed", "publisher"],
            "master_control_sha256": "2" * 64,
            "runtime_request_sha256": "3" * 64,
        }
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-base",
            works=[],
            events=[],
            **older_bindings,
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-incoming",
            works=[],
            events=[],
            **newer_bindings,
        )

        merged = merge_states(base, incoming)
        for field, expected in newer_bindings.items():
            self.assertEqual(expected, merged[field])
        self.assertNotIn("run-incoming", merged["parent_run_ids"])
        self.assertEqual(["run-base"], merged["parent_run_ids"])

    def test_newer_legacy_state_does_not_inherit_stale_master_bindings(self) -> None:
        bindings = {
            "profile_id": "owner_daily",
            "resolved_stream_ids": ["clinical_medicine"],
            "resolved_source_ids": ["pubmed"],
            "master_control_sha256": "2" * 64,
            "runtime_request_sha256": "3" * 64,
        }
        modern = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-modern",
            works=[],
            events=[],
            execution_lane="chatgpt_work",
            protocol_commit="modern-commit",
            **bindings,
        )
        legacy = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-legacy",
            works=[],
            events=[],
            execution_lane="github_actions",
            protocol_commit="legacy-commit",
        )

        merged = merge_states(modern, legacy)
        self.assertEqual("run-legacy", merged["last_run_id"])
        self.assertEqual("github_actions", merged["execution_lane"])
        self.assertEqual("legacy-commit", merged["protocol_commit"])
        for field in bindings:
            self.assertNotIn(field, merged)

    def test_incomplete_master_control_bindings_fail_closed(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-base",
            works=[],
            events=[],
            profile_id="owner_daily",
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-incoming",
            works=[],
            events=[],
        )
        with self.assertRaisesRegex(StateMergeError, "incomplete master-control"):
            merge_states(base, incoming)

    def test_disjoint_work_and_notification_union_never_drops_an_input(self) -> None:
        shared = _work("pmid:shared", identifiers={"pmid": "shared"})
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-base",
            works=[shared, _work("pmid:base", identifiers={"pmid": "base"})],
            events=[
                _event("event-shared", "pmid:shared"),
                _event("event-base", "pmid:base"),
            ],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-incoming",
            works=[shared, _work("pmid:incoming", identifiers={"pmid": "incoming"})],
            events=[
                _event("event-shared", "pmid:shared"),
                _event("event-incoming", "pmid:incoming"),
            ],
        )

        merged = merge_states(base, incoming)
        self.assertEqual(
            {"pmid:base", "pmid:incoming", "pmid:shared"},
            {work["work_id"] for work in merged["works"]},
        )
        self.assertEqual(
            {"event-base", "event-incoming", "event-shared"},
            {event["event_id"] for event in merged["notified_events"]},
        )

    def test_title_only_records_never_bridge_distinct_work_ids(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[_work("doi-work", identifiers={"doi": "10.1/a"})],
            events=[],
        )
        title_only = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-b",
            works=[_work("title-only", identifiers={})],
            events=[],
        )
        merged = merge_states(base, title_only)
        self.assertEqual(len(merged["works"]), 2)

        conflicting = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-c",
            works=[_work("doi-other", identifiers={"doi": "10.1/b"})],
            events=[],
        )
        self.assertEqual(len(merge_states(base, conflicting)["works"]), 2)

        generic_a = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-editorial-a",
            works=[
                _work(
                    "editorial-a",
                    title="Editorial",
                    normalized_title="editorial",
                    identifiers={},
                )
            ],
            events=[],
        )
        generic_b = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-editorial-b",
            works=[
                _work(
                    "editorial-b",
                    title="Editorial",
                    normalized_title="editorial",
                    identifiers={},
                )
            ],
            events=[],
        )
        self.assertEqual(2, len(merge_states(generic_a, generic_b)["works"]))

    def test_conflicting_identifier_bridge_fails_closed(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[
                _work("a", identifiers={"doi": "10.1/x", "pmid": "1"}),
                _work("b", identifiers={"doi": "10.1/x", "pmid": "2"}),
            ],
            events=[],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[_work("c", identifiers={"doi": "10.1/y", "pmid": "2"})],
            events=[],
        )
        with self.assertRaisesRegex(StateMergeError, "conflicting strong identifiers"):
            merge_states(base, incoming)

    def test_event_id_collision_fails_closed(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[_work("work", identifiers={"doi": "10.1/x"}, event_ids=["event-1"])],
            events=[_event("event-1", "work")],
        )
        conflicting_event = _event("event-1", "work")
        conflicting_event["occurred_at"] = "2026-08-02"
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[_work("work", identifiers={"doi": "10.1/x"}, event_ids=["event-1"])],
            events=[conflicting_event],
        )
        with self.assertRaisesRegex(StateMergeError, "event_id collision"):
            merge_states(base, incoming)

    def test_event_id_collision_rejects_provenance_drift(self) -> None:
        base_event = _event("event-1", "work")
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[_work("work", identifiers={"doi": "10.1/x"})],
            events=[base_event],
        )
        for field, value in (
            ("source_url", "https://other.example/article"),
            ("precision", "datetime"),
            ("confidence", "provider_metadata"),
        ):
            with self.subTest(field=field):
                incoming_event = copy.deepcopy(base_event)
                incoming_event[field] = value
                incoming = _state(
                    generated="2026-08-02T00:00:00+00:00",
                    run_id=f"run-{field}",
                    works=[_work("work", identifiers={"doi": "10.1/x"})],
                    events=[incoming_event],
                )
                with self.assertRaisesRegex(StateMergeError, "event_id collision"):
                    merge_states(base, incoming)

    def test_oa_union_is_independent_from_latest_access_observation(self) -> None:
        pmc_location = {
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
            "kind": "REPOSITORY",
            "host_type": "REPOSITORY",
            "access_status": "NOT_CHECKED",
        }
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[
                _work(
                    "work",
                    identifiers={"pmcid": "PMC123"},
                    last="2026-08-01T01:00:00+00:00",
                    oa_status="YES",
                    oa_evidence=[
                        {
                            "source": "pubmed",
                            "evidence_type": "PMCID",
                            "value": "PMC123",
                            "url": pmc_location["url"],
                        }
                    ],
                    access_status="NOT_CHECKED",
                    fulltext_kind="REPOSITORY",
                    download_urls=[pmc_location["url"]],
                    fulltext_locations=[pmc_location],
                )
            ],
            events=[],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[
                _work(
                    "work",
                    identifiers={"pmcid": "PMC123"},
                    last="2026-08-02T01:00:00+00:00",
                    oa_status="UNKNOWN",
                    oa_evidence=[
                        {
                            "source": "publisher",
                            "evidence_type": "LICENSE_NOT_OBSERVED",
                            "value": "unknown",
                        }
                    ],
                    access_status="BLOCKED",
                    fulltext_kind="HTML",
                    download_urls=["https://publisher.example/article"],
                )
            ],
            events=[],
        )

        work = merge_states(base, incoming)["works"][0]
        self.assertEqual("YES", work["oa_status"])
        self.assertEqual("BLOCKED", work["access_status"])
        self.assertEqual("REPOSITORY", work["fulltext_kind"])
        self.assertEqual("BLOCKED", work["fulltext_access_status"])
        self.assertEqual(
            [
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
                "https://publisher.example/article",
            ],
            work["download_urls"],
        )
        self.assertEqual(2, len(work["oa_evidence"]))
        self.assertEqual([pmc_location], work["fulltext_locations"])

    def test_fulltext_location_merge_preserves_probe_and_derives_mixed(self) -> None:
        repository_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"
        publisher_url = "https://publisher.example/fulltext"
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[
                _work(
                    "work",
                    identifiers={"pmcid": "PMC123"},
                    last="2026-08-01T01:00:00+00:00",
                    access_status="ACCESSIBLE",
                    fulltext_kind="REPOSITORY",
                    fulltext_locations=[
                        {
                            "url": repository_url,
                            "kind": "REPOSITORY",
                            "host_type": "REPOSITORY",
                            "access_status": "ACCESSIBLE",
                        }
                    ],
                )
            ],
            events=[],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[
                _work(
                    "work",
                    identifiers={"pmcid": "PMC123"},
                    last="2026-08-02T01:00:00+00:00",
                    access_status="BLOCKED",
                    fulltext_kind="HTML",
                    fulltext_locations=[
                        {
                            "url": repository_url,
                            "kind": "REPOSITORY",
                            "host_type": "REPOSITORY",
                            "access_status": "NOT_CHECKED",
                        },
                        {
                            "url": publisher_url,
                            "kind": "HTML",
                            "host_type": "PUBLISHER",
                            "access_status": "BLOCKED",
                        },
                    ],
                )
            ],
            events=[],
        )

        work = merge_states(base, incoming)["works"][0]
        by_url = {item["url"]: item for item in work["fulltext_locations"]}
        self.assertEqual("ACCESSIBLE", by_url[repository_url]["access_status"])
        self.assertEqual("BLOCKED", by_url[publisher_url]["access_status"])
        self.assertEqual("MIXED", work["fulltext_access_status"])
        self.assertEqual("BLOCKED", work["access_status"])
        self.assertEqual("REPOSITORY", work["fulltext_kind"])

    def test_existing_canonical_work_id_wins_over_lexical_alias(self) -> None:
        base = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-a",
            works=[_work("pmid:2", identifiers={"pmid": "2"})],
            events=[],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-b",
            works=[_work("aaa-alias", identifiers={"pmid": "2"})],
            events=[],
        )
        self.assertEqual("pmid:2", merge_states(base, incoming)["works"][0]["work_id"])

    def test_reversed_inputs_have_deterministic_work_and_event_order(self) -> None:
        base = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-z",
            works=[_work("z-id", identifiers={"pmid": "2"}), _work("a-id", identifiers={"pmid": "1"})],
            events=[_event("event-z", "z-id"), _event("event-a", "a-id")],
        )
        incoming = _state(
            generated="2026-08-02T00:00:00+00:00",
            run_id="run-a",
            works=[_work("new-id", identifiers={"doi": "10.1/new"})],
            events=[],
        )
        left = merge_states(base, incoming)
        right = merge_states(incoming, base)
        # Provenance records the directional base snapshot; all union content
        # and serialization ordering remain deterministic regardless of lane.
        self.assertEqual(left["works"], right["works"])
        self.assertEqual(left["notified_events"], right["notified_events"])
        self.assertEqual(left["dedupe_priority"], right["dedupe_priority"])
        self.assertEqual(left["last_run_id"], right["last_run_id"])

    def test_provenance_direct_fields_are_schema_valid_and_unknown_fields_fail_closed(self) -> None:
        state = load_json(ROOT / "examples" / "EvidenceRadar_State.json")
        state.update(
            {
                "execution_lane": "github_actions",
                "protocol_commit": "b05d565",
                "base_state_sha256": "0" * 64,
                "parent_run_ids": ["run-parent"],
                "profile_id": "owner_daily",
                "resolved_stream_ids": ["clinical_medicine"],
                "resolved_source_ids": ["pubmed"],
                "master_control_sha256": "1" * 64,
                "runtime_request_sha256": None,
            }
        )
        schema = load_json(ROOT / "schemas" / "evidence-radar-state.schema.json")
        self.assertEqual(validate_document(state, schema), [])
        bad = copy.deepcopy(state)
        bad["execution_lane"] = "unknown"
        self.assertTrue(validate_document(bad, schema))
        bad = copy.deepcopy(state)
        bad["unlisted"] = True
        self.assertTrue(validate_document(bad, schema))

    def test_atomic_writer_and_invalid_timezone(self) -> None:
        base = _state(generated="2026-08-01T00:00:00+00:00", run_id="run-a", works=[], events=[])
        incoming = copy.deepcopy(base)
        incoming["timezone"] = "Asia/Tokyo"
        with self.assertRaises(StateMergeError):
            merge_states(base, incoming)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "state.json"
            write_json_atomic(target, base)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), base)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
