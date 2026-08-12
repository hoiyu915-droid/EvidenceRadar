"""Structural tests for the GPT Work EvidenceRadar artifact contract."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.validate_gpt_work_artifacts import (
    load_json,
    main,
    validate_document,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SCHEMAS = ROOT / "schemas"


def _artifact(name: str) -> dict:
    return load_json(EXAMPLES / f"EvidenceRadar_{name}.json")


def _schema(name: str) -> dict:
    return load_json(SCHEMAS / f"evidence-radar-{name.lower()}.schema.json")


class GptWorkArtifactSchemaTests(unittest.TestCase):
    def test_loader_rejects_duplicate_names_and_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ambiguous.json"
            for payload in ('{"artifact_type":"first","artifact_type":"second"}', '{"value":NaN}'):
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(json.JSONDecodeError):
                        load_json(path)

    def test_in_memory_non_finite_number_is_not_a_json_schema_number(self) -> None:
        errors = validate_document(math.nan, {"type": "number"})
        self.assertTrue(any("expected type 'number'" in error for error in errors))

    def test_checked_in_minimal_examples_validate(self) -> None:
        for name in ("State", "Evidence", "Run"):
            with self.subTest(name=name):
                self.assertEqual(validate_document(_artifact(name), _schema(name)), [])

    def test_cli_validates_all_examples(self) -> None:
        self.assertEqual(main(["--root", str(ROOT)]), 0)

    def test_missing_state_history_is_explicitly_allowed(self) -> None:
        state = _artifact("State")
        state["history_status"] = "STATE_HISTORY_INCOMPLETE"
        state["history_note"] = "No prior state artifact was available at run start."
        self.assertEqual(validate_document(state, _schema("State")), [])

    def test_evidence_source_coverage_statuses_are_supported(self) -> None:
        for status in ("COMPLETE", "PARTIAL_SOURCE_COVERAGE", "SOURCE_ACCESS_GAP"):
            with self.subTest(status=status):
                evidence = _artifact("Evidence")
                evidence["coverage_status"] = status
                if status != "COMPLETE":
                    evidence["coverage"]["unavailable_sources"] = ["source-not-reachable"]
                    evidence["coverage"]["notes"] = ["Coverage gap recorded for this test fixture."]
                self.assertEqual(validate_document(evidence, _schema("Evidence")), [])

    def test_claim_statuses_are_supported(self) -> None:
        for status in ("SUPPORTED", "PARTIAL", "CONFLICT", "UNVERIFIED"):
            with self.subTest(status=status):
                evidence = _artifact("Evidence")
                evidence["claims"][0]["status"] = status
                self.assertEqual(validate_document(evidence, _schema("Evidence")), [])

    def test_numeric_claim_requires_value_symbol_unit_direction_and_comparison(self) -> None:
        evidence = _artifact("Evidence")
        del evidence["claims"][0]["measurement"]["unit"]
        errors = validate_document(evidence, _schema("Evidence"))
        self.assertTrue(any("measurement" in error and "unit" in error for error in errors))

    def test_non_numeric_claim_can_set_measurement_to_null(self) -> None:
        evidence = _artifact("Evidence")
        evidence["claims"][0]["measurement"] = None
        self.assertEqual(validate_document(evidence, _schema("Evidence")), [])

    def test_claim_requires_locator_and_source_url(self) -> None:
        evidence = _artifact("Evidence")
        del evidence["claims"][0]["locator"]
        del evidence["claims"][0]["source_url"]
        errors = validate_document(evidence, _schema("Evidence"))
        self.assertTrue(any("locator" in error for error in errors))
        self.assertTrue(any("source_url" in error for error in errors))

    def test_schema_rejects_unknown_top_level_fields(self) -> None:
        run = _artifact("Run")
        run["uses_server"] = True
        errors = validate_document(run, _schema("Run"))
        self.assertTrue(any("unexpected property 'uses_server'" in error for error in errors))

    def test_schema_rejects_invalid_source_url(self) -> None:
        evidence = _artifact("Evidence")
        evidence["claims"][0]["source_url"] = "not-a-url"
        errors = validate_document(evidence, _schema("Evidence"))
        self.assertTrue(any("source_url" in error and "pattern" in error for error in errors))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
