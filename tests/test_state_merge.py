"""Tests for deterministic, concurrent-safe EvidenceRadar state merging."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.merge_radar_state import (
    StateMergeError,
    canonical_json,
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
        self.assertEqual(merged["parent_run_ids"], ["run-base", "run-incoming"])

        # Applying the same stale branch again is a no-op, including byte
        # ordering and provenance, which is the key concurrency guarantee.
        repeated = merge_states(merged, incoming)
        # The audit hash is intentionally directional: it identifies the
        # caller's base snapshot.  The merged state itself stays identical.
        self.assertEqual(repeated["works"], merged["works"])
        self.assertEqual(repeated["notified_events"], merged["notified_events"])
        self.assertEqual(repeated["parent_run_ids"], merged["parent_run_ids"])

    def test_title_fallback_is_unambiguous_but_conflicting_identifiers_stay_separate(self) -> None:
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
        self.assertEqual(len(merged["works"]), 1)

        conflicting = _state(
            generated="2026-08-01T00:00:00+00:00",
            run_id="run-c",
            works=[_work("doi-other", identifiers={"doi": "10.1/b"})],
            events=[],
        )
        self.assertEqual(len(merge_states(base, conflicting)["works"]), 2)

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
