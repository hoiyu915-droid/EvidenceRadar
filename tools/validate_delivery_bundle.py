#!/usr/bin/env python3
"""Validate a complete EvidenceRadar delivery bundle fail closed.

Schema validation alone cannot prove that the HTML renders the Run ledger or
that a current bundle came from the declared producer.  This validator checks
the four artifacts together, including provenance, source coverage, candidate
counts, HTML item markers, canonical State parity and producer-version drift.
It uses only the Python standard library and is included in the Work Pack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_contract import BUNDLE_FILENAMES, current_producer_errors
from tools.validate_gpt_work_artifacts import load_json, validate_document


PROVENANCE_FIELDS = (
    "execution_lane",
    "protocol_commit",
    "base_state_sha256",
    "parent_run_ids",
)
SOURCE_COVERAGE_FIELDS = (
    "requested",
    "checked",
    "searched",
    "unavailable",
    "all_configured_sources_checked",
    "checks",
)
EXPECTED_ARTIFACT_MAP = {
    "report_html": "EvidenceRadar_Report.html",
    "state_json": "EvidenceRadar_State.json",
    "evidence_json": "EvidenceRadar_Evidence.json",
    "run_json": "EvidenceRadar_Run.json",
}


class DeliveryHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.work_ids: list[str] = []
        self.html_languages: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).casefold(): value for key, value in attrs}
        if tag.casefold() == "meta":
            name = str(values.get("name") or "").casefold()
            if name.startswith("evidenceradar-"):
                self.meta.setdefault(name, []).append(str(values.get("content") or ""))
        work_id = values.get("data-evidenceradar-work-id")
        if work_id:
            self.work_ids.append(str(work_id))
        if tag.casefold() == "html" and values.get("lang"):
            self.html_languages.append(str(values["lang"]))


def _schema_name(artifact_name: str) -> str:
    suffix = artifact_name.removeprefix("EvidenceRadar_").removesuffix(".json")
    return f"evidence-radar-{suffix.casefold()}.schema.json"


def _load_object(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: cannot load JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.name}: document must be a JSON object")
        return None
    return value


def _require_integer(
    counts: Mapping[str, Any],
    key: str,
    errors: list[str],
) -> int | None:
    value = counts.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"Run.counts.{key} must be a non-negative integer")
        return None
    return value


def _candidate_errors(run: Mapping[str, Any], report_html: str) -> list[str]:
    errors: list[str] = []
    candidates = run.get("candidates")
    counts = run.get("counts")
    if not isinstance(candidates, list):
        return ["Run.candidates must contain the complete candidate ledger"]
    if not isinstance(counts, Mapping):
        return ["Run.counts must be an object"]

    deduplicated = _require_integer(counts, "deduplicated_candidates", errors)
    displayed_count = _require_integer(counts, "displayed_candidates", errors)
    if deduplicated is not None and deduplicated != len(candidates):
        errors.append(
            f"Run.counts.deduplicated_candidates={deduplicated} but ledger has {len(candidates)} items"
        )

    work_ids: list[str] = []
    displayed_ids: list[str] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            errors.append(f"Run.candidates[{index}] must be an object")
            continue
        work_id = item.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            errors.append(f"Run.candidates[{index}].work_id must be non-empty")
            continue
        work_ids.append(work_id)
        if item.get("displayed_in_report") is True:
            displayed_ids.append(work_id)
            if item.get("summary_language") != "zh-TW":
                errors.append(f"displayed candidate {work_id} must use summary_language zh-TW")
            if not isinstance(item.get("content_summary"), str) or not str(item["content_summary"]).strip():
                errors.append(f"displayed candidate {work_id} is missing content_summary")
    if len(work_ids) != len(set(work_ids)):
        errors.append("Run.candidates contains duplicate work_id values")
    if displayed_count is not None and displayed_count != len(displayed_ids):
        errors.append(
            f"Run.counts.displayed_candidates={displayed_count} but ledger marks {len(displayed_ids)} displayed"
        )

    parser = DeliveryHtmlParser()
    try:
        parser.feed(report_html)
        parser.close()
    except Exception as exc:  # HTMLParser errors are unusual but must fail closed.
        errors.append(f"Report HTML cannot be parsed: {exc}")
        return errors
    required_meta = {
        "evidenceradar-run-id": str(run.get("run_id") or ""),
        "evidenceradar-execution-lane": str(run.get("execution_lane") or ""),
        "evidenceradar-protocol-commit": str(run.get("protocol_commit") or ""),
        "evidenceradar-displayed-candidates": str(len(displayed_ids)),
    }
    for name, expected in required_meta.items():
        values = parser.meta.get(name, [])
        if values != [expected]:
            errors.append(
                f"Report meta {name!r} must occur once with value {expected!r}; observed {values!r}"
            )
    if not parser.html_languages or not any(
        language.casefold() in {"zh-hant", "zh-tw"} for language in parser.html_languages
    ):
        errors.append("Report HTML must declare lang=zh-Hant or zh-TW")
    if len(parser.work_ids) != len(set(parser.work_ids)):
        errors.append("Report HTML contains duplicate data-evidenceradar-work-id values")
    if set(parser.work_ids) != set(displayed_ids):
        missing = sorted(set(displayed_ids) - set(parser.work_ids))
        extra = sorted(set(parser.work_ids) - set(displayed_ids))
        errors.append(
            "Report candidate markers do not match the displayed Run ledger"
            + (f"; missing={missing[:5]!r}" if missing else "")
            + (f"; extra={extra[:5]!r}" if extra else "")
        )
    return errors


def validate_delivery_payload(
    root: Path,
    *,
    report_html: str,
    state: Mapping[str, Any],
    evidence: Mapping[str, Any],
    run: Mapping[str, Any],
    expected_lane: str | None = None,
    expected_protocol_commit: str | None = None,
) -> list[str]:
    """Validate decoded delivery values without touching the filesystem."""

    root = Path(root).resolve()
    errors: list[str] = []
    documents = {
        "EvidenceRadar_State.json": state,
        "EvidenceRadar_Evidence.json": evidence,
        "EvidenceRadar_Run.json": run,
    }
    for artifact_name, document in documents.items():
        schema_path = root / "schemas" / _schema_name(artifact_name)
        try:
            schema = load_json(schema_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"cannot load {schema_path}: {exc}")
            continue
        for message in validate_document(document, schema):
            errors.append(f"{artifact_name}: {message}")

    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        errors.append("Run.run_id must be non-empty")
    if evidence.get("run_id") != run_id:
        errors.append("Evidence.run_id must equal Run.run_id")
    if state.get("last_run_id") != run_id:
        errors.append("State.last_run_id must equal Run.run_id")

    for field in PROVENANCE_FIELDS:
        if field not in run:
            errors.append(f"Run is missing required delivery provenance field {field!r}")
        if field not in state:
            errors.append(f"State is missing required delivery provenance field {field!r}")
        if field in run and field in state and run.get(field) != state.get(field):
            errors.append(f"State.{field} must equal Run.{field}")
    lane = run.get("execution_lane")
    protocol_commit = run.get("protocol_commit")
    if expected_lane is not None and lane != expected_lane:
        errors.append(f"execution_lane must be {expected_lane!r}; observed {lane!r}")
    if expected_protocol_commit is not None and protocol_commit != expected_protocol_commit:
        errors.append(
            "protocol_commit must equal the checked-out producer commit "
            f"{expected_protocol_commit!r}; observed {protocol_commit!r}"
        )
    base_hash = run.get("base_state_sha256")
    if not isinstance(base_hash, str) or re.fullmatch(r"[0-9a-fA-F]{64}", base_hash) is None:
        errors.append("Run.base_state_sha256 must be a 64-character SHA-256 digest")
    parents = run.get("parent_run_ids")
    if not isinstance(parents, list) or any(not isinstance(item, str) or not item for item in parents):
        errors.append("Run.parent_run_ids must be an array of non-empty strings")

    if run.get("artifacts") != EXPECTED_ARTIFACT_MAP:
        errors.append("Run.artifacts must name the canonical four-file bundle")

    run_coverage = run.get("source_coverage")
    evidence_coverage = evidence.get("coverage")
    if not isinstance(run_coverage, Mapping):
        errors.append("Run.source_coverage must be an object")
    elif not isinstance(evidence_coverage, Mapping):
        errors.append("Evidence.coverage must be an object")
    else:
        for field in SOURCE_COVERAGE_FIELDS:
            if run_coverage.get(field) != evidence_coverage.get(field):
                errors.append(f"Evidence.coverage.{field} must equal Run.source_coverage.{field}")
        requested = run_coverage.get("requested")
        checked = run_coverage.get("checked")
        checks = run_coverage.get("checks")
        if isinstance(requested, list) and isinstance(checked, list):
            if set(requested) != set(checked):
                errors.append("every requested source must have a CHECK record")
        if isinstance(requested, list) and isinstance(checks, list):
            check_ids = [item.get("source_id") for item in checks if isinstance(item, Mapping)]
            if len(check_ids) != len(set(check_ids)) or set(check_ids) != set(requested):
                errors.append("source_coverage.checks must contain exactly one record per requested source")
        counts = run.get("counts")
        if isinstance(counts, Mapping):
            for count_key, coverage_key in (
                ("sources_requested", "requested"),
                ("sources_checked", "checked"),
                ("sources_searched", "searched"),
                ("sources_unavailable", "unavailable"),
            ):
                value = counts.get(count_key)
                coverage_value = run_coverage.get(coverage_key)
                if not isinstance(coverage_value, list) or value != len(coverage_value):
                    errors.append(
                        f"Run.counts.{count_key} must equal len(source_coverage.{coverage_key})"
                    )

    errors.extend(_candidate_errors(run, report_html))
    return errors


def _manifest_errors(
    root: Path,
    manifest_path: Path,
    *,
    protocol_commit: str,
    reject_dirty: bool,
) -> list[str]:
    errors: list[str] = []
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot load Work Pack manifest {manifest_path}: {exc}"]
    if manifest.get("source_commit") != protocol_commit:
        errors.append("Run.protocol_commit must equal manifest.json source_commit")
    if reject_dirty and manifest.get("git_dirty") is not False:
        errors.append("public delivery rejects a dirty Work Pack manifest")
    records = manifest.get("files")
    if not isinstance(records, list):
        return errors + ["Work Pack manifest.files must be an array"]
    for item in records:
        if not isinstance(item, Mapping):
            errors.append("Work Pack manifest file record must be an object")
            continue
        relative = item.get("path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(relative, str) or not relative or ".." in Path(relative).parts:
            errors.append(f"unsafe Work Pack manifest path: {relative!r}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"Work Pack file is missing: {relative}")
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_hash or len(payload) != expected_size:
            errors.append(f"Work Pack file does not match manifest: {relative}")
    return errors


def validate_delivery_bundle(
    root: Path,
    bundle: Path,
    *,
    canonical_state: Path | None = None,
    expected_lane: str | None = None,
    expected_protocol_commit: str | None = None,
    manifest: Path | None = None,
    require_current_producer: bool = False,
    reject_dirty: bool = False,
) -> tuple[list[str], dict[str, Any] | None]:
    root = Path(root).resolve()
    bundle = Path(bundle).resolve()
    errors: list[str] = []
    for name in BUNDLE_FILENAMES:
        path = bundle / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty delivery artifact: {path}")
    if errors:
        return errors, None

    state = _load_object(bundle / "EvidenceRadar_State.json", errors)
    evidence = _load_object(bundle / "EvidenceRadar_Evidence.json", errors)
    run = _load_object(bundle / "EvidenceRadar_Run.json", errors)
    try:
        report_html = (bundle / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"EvidenceRadar_Report.html: cannot read UTF-8 HTML: {exc}")
        report_html = ""
    if state is None or evidence is None or run is None:
        return errors, run
    errors.extend(
        validate_delivery_payload(
            root,
            report_html=report_html,
            state=state,
            evidence=evidence,
            run=run,
            expected_lane=expected_lane,
            expected_protocol_commit=expected_protocol_commit,
        )
    )

    if canonical_state is not None:
        canonical = _load_object(Path(canonical_state).resolve(), errors)
        if canonical is not None and canonical != state:
            errors.append("canonical State must be JSON-identical to bundle EvidenceRadar_State.json")

    lane = str(run.get("execution_lane") or "")
    protocol_commit = str(run.get("protocol_commit") or "")
    if manifest is not None:
        errors.extend(
            _manifest_errors(
                root,
                Path(manifest).resolve(),
                protocol_commit=protocol_commit,
                reject_dirty=reject_dirty,
            )
        )
    if require_current_producer:
        errors.extend(
            current_producer_errors(
                root,
                execution_lane=lane,
                protocol_commit=protocol_commit,
            )
        )
    if reject_dirty and protocol_commit.endswith("-dirty"):
        errors.append("public delivery rejects a dirty protocol_commit")
    return errors, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--canonical-state", type=Path)
    parser.add_argument("--expected-lane", choices=("github_actions", "chatgpt_work"))
    parser.add_argument("--expected-protocol-commit")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--require-current-producer", action="store_true")
    parser.add_argument("--reject-dirty", action="store_true")
    args = parser.parse_args(argv)
    errors, run = validate_delivery_bundle(
        args.root,
        args.bundle,
        canonical_state=args.canonical_state,
        expected_lane=args.expected_lane,
        expected_protocol_commit=args.expected_protocol_commit,
        manifest=args.manifest,
        require_current_producer=args.require_current_producer,
        reject_dirty=args.reject_dirty,
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    counts = run.get("counts", {}) if isinstance(run, Mapping) else {}
    print(
        "PASS: EvidenceRadar delivery bundle "
        f"run_id={run.get('run_id')} lane={run.get('execution_lane')} "
        f"candidates={counts.get('deduplicated_candidates')} "
        f"displayed={counts.get('displayed_candidates')}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
