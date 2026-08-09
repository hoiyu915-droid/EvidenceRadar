"""Canonical V3 Work renderer regression tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_delivery_bundle import ROOT, create_bundle
import tools.render_report_from_artifacts as renderer
from tools.render_report_from_artifacts import render_bundle
from tools.run_github_radar import RadarRuntimeError, render_report_from_documents
from tools.validate_delivery_bundle import validate_delivery_bundle


class CanonicalArtifactRendererTests(unittest.TestCase):
    def _load(self, bundle: Path, name: str) -> dict:
        return json.loads((bundle / name).read_text(encoding="utf-8"))

    def _save(self, bundle: Path, name: str, value: dict) -> None:
        (bundle / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _add_metadata_claim(self, bundle: Path, *, text: str) -> None:
        state = self._load(bundle, "EvidenceRadar_State.json")
        evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
        observations = {
            item["source_id"]: item
            for item in state["source_observations"]
            if item["access_outcome"] == "ACCESSIBLE"
            and item["access_depth"] in {"METADATA", "LANDING_PAGE", "ABSTRACT", "FULL_TEXT"}
        }
        source = next(
            item
            for item in state["source_registry"]
            if item["source_id"] in observations
            and item["source_role"]
            in {"FORMAL_PUBLICATION", "DISCOVERY_ONLY", "PRIMARY_RESEARCH", "SYSTEMATIC_SYNTHESIS"}
        )
        source_id = source["source_id"]
        claim_id = "claim-renderer-fixture"
        binding_id = "binding-renderer-fixture"
        evidence["claims"] = [
            {
                "claim_id": claim_id,
                "work_id": source["work_id"],
                "status": "UNVERIFIED",
                "claim_text": text,
                "measurement": None,
                "source_ids": [source_id],
                "source_url": source["canonical_url"],
                "locator": "bibliographic metadata",
                "claim_kind": "BIBLIOGRAPHIC_FACT",
                "claim_origin": "METADATA_REPORTED",
                "citation_binding_ids": [binding_id],
                "support_reason": "Metadata identifies the work; substantive findings remain unreviewed.",
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
                "locator": "bibliographic metadata",
                "support_scope": "CONTEXT_ONLY",
            }
        ]
        self._save(bundle, "EvidenceRadar_Evidence.json", evidence)

    def test_renderer_syncs_claim_registry_count_hash_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, _canonical = create_bundle(Path(directory))
            self._add_metadata_claim(bundle, text="This work has a registered bibliographic record.")

            digest = render_bundle(ROOT, bundle)
            state = self._load(bundle, "EvidenceRadar_State.json")
            run = self._load(bundle, "EvidenceRadar_Run.json")
            report = (bundle / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
            self.assertEqual(digest, run["report_sha256"])
            self.assertEqual(1, run["counts"]["claims"])
            self.assertEqual("claim-renderer-fixture", state["claim_registry"][0]["claim_id"])
            self.assertEqual(
                ["binding-renderer-fixture"],
                state["claim_registry"][0]["status_binding_ids"],
            )
            self.assertIn('data-evidenceradar-claim-id="claim-renderer-fixture"', report)
            errors, _run = validate_delivery_bundle(ROOT, bundle)
            self.assertEqual([], errors)

    def test_renderer_is_independent_of_identifier_object_order(self) -> None:
        """JSON key sorting cannot change the canonical HTML projection."""

        with tempfile.TemporaryDirectory() as directory:
            bundle, _canonical = create_bundle(Path(directory))
            run = self._load(bundle, "EvidenceRadar_Run.json")
            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            candidate = run["candidates"][0]
            candidate["identifiers"] = {
                "doi": "10.1000/delivery.fixture",
                "pmid": "12345678",
                "pmcid": "PMC1234567",
            }
            before_serialization = render_report_from_documents(run, evidence)

            candidate["identifiers"] = {
                "pmcid": "PMC1234567",
                "pmid": "12345678",
                "doi": "10.1000/delivery.fixture",
            }
            after_serialization = render_report_from_documents(run, evidence)

            self.assertEqual(before_serialization, after_serialization)
            self.assertIn(
                "DOI: 10.1000/delivery.fixture · PMCID: PMC1234567 · PMID: 12345678",
                after_serialization,
            )

    def test_renderer_rejects_reusing_claim_id_for_changed_text_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, _canonical = create_bundle(Path(directory))
            self._add_metadata_claim(bundle, text="Original stable claim text.")
            render_bundle(ROOT, bundle)
            state_before = (bundle / "EvidenceRadar_State.json").read_bytes()
            run_before = (bundle / "EvidenceRadar_Run.json").read_bytes()
            report_before = (bundle / "EvidenceRadar_Report.html").read_bytes()

            evidence = self._load(bundle, "EvidenceRadar_Evidence.json")
            evidence["claims"][0]["claim_text"] = "Different text under the same claim ID."
            self._save(bundle, "EvidenceRadar_Evidence.json", evidence)
            with self.assertRaisesRegex(RadarRuntimeError, "reused with different immutable claim_text_sha256"):
                render_bundle(ROOT, bundle)

            self.assertEqual(state_before, (bundle / "EvidenceRadar_State.json").read_bytes())
            self.assertEqual(run_before, (bundle / "EvidenceRadar_Run.json").read_bytes())
            self.assertEqual(report_before, (bundle / "EvidenceRadar_Report.html").read_bytes())

    def test_mid_commit_replace_failure_rolls_back_all_canonical_artifacts(self) -> None:
        """A failed multi-artifact rewrite leaves the last valid bundle intact."""

        artifact_names = (
            "EvidenceRadar_State.json",
            "EvidenceRadar_Evidence.json",
            "EvidenceRadar_Run.json",
            "EvidenceRadar_Report.html",
        )
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            before = {
                name: (bundle / name).read_bytes()
                for name in artifact_names
            }
            commit_calls: list[tuple[str, str]] = []
            real_commit = renderer._commit_staged

            def fail_once(staged: Path, target: Path) -> None:
                commit_calls.append((str(staged), str(target)))
                if len(commit_calls) == 2:
                    raise OSError("injected mid-commit replace failure")
                real_commit(staged, target)

            failure: Exception | None = None
            with patch.object(renderer, "_commit_staged", side_effect=fail_once):
                try:
                    render_bundle(ROOT, bundle)
                except (RadarRuntimeError, OSError) as exc:
                    failure = exc

            self.assertIsNotNone(failure)
            self.assertGreaterEqual(len(commit_calls), 2)
            for name, payload in before.items():
                self.assertEqual(payload, (bundle / name).read_bytes(), name)

            errors, _run = validate_delivery_bundle(
                ROOT, bundle, canonical_state=canonical
            )
            self.assertEqual([], errors)

    def test_pending_transaction_cannot_escape_bundle_via_staged_path(self) -> None:
        """Recovery rejects a journal that points staged output outside the bundle."""

        artifact_names = tuple(renderer._CANONICAL_WRITE_ORDER)
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            marker = Path(directory) / "outside-marker"
            marker_bytes = b"do-not-touch\n"
            marker.write_bytes(marker_bytes)
            before = {
                name: (bundle / name).read_bytes()
                for name in artifact_names
            }

            entries: list[dict[str, object]] = []
            for index, name in enumerate(artifact_names):
                backup = bundle / f".malicious-{index}.backup"
                backup.write_bytes(before[name])
                staged_name = (
                    "../outside-marker"
                    if index == 0
                    else f".malicious-{index}.staged"
                )
                entries.append(
                    {
                        "name": name,
                        "staged": staged_name,
                        "backup": backup.name,
                        "existed": True,
                    }
                )
            journal = {
                "version": 1,
                "token": "malicious-fixture",
                "entries": entries,
            }
            (bundle / renderer._TRANSACTION_JOURNAL).write_text(
                json.dumps(journal, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(RadarRuntimeError):
                render_bundle(ROOT, bundle)

            self.assertEqual(marker_bytes, marker.read_bytes())
            for name, payload in before.items():
                self.assertEqual(payload, (bundle / name).read_bytes(), name)
            errors, _run = validate_delivery_bundle(
                ROOT, bundle, canonical_state=canonical
            )
            self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
