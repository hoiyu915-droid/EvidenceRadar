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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from urllib.parse import urlparse


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


# These values intentionally describe *access observations*, not OA truth.
# In particular, OA=YES with a blocked 403/429 probe is valid and expected.
PUBLISHER_ACCESS_STATUSES = {"SUCCESS", "FAILED", "NOT_ATTEMPTED"}
FULLTEXT_ACCESS_STATUSES = {
    "ACCESSIBLE",
    "BLOCKED",
    "PAYWALLED",
    "FAILED",
    "NOT_CHECKED",
}
FULLTEXT_KINDS = {"PDF", "HTML", "REPOSITORY", "ABSTRACT_ONLY", "UNKNOWN"}
OA_STATUSES = {"YES", "NO", "UNKNOWN"}
STATE_RUN_PARITY_FIELDS = (
    "oa_status",
    "oa_evidence",
    "access_status",
    "fulltext_kind",
    "download_urls",
    "fulltext_locations",
    "fulltext_access_status",
    "event_class",
)
FORMAL_SOURCE_TYPES = {
    "publisher",
    "journal",
    "conference_proceedings",
    "formal_proceedings_or_publisher",
}
DISCOVERY_HOSTS = {
    "pubmed.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "arxiv.org",
}


class DeliveryHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.work_ids: list[str] = []
        self.featured_ids: list[str] = []
        self.featured_flags: dict[str, str] = {}
        self.html_languages: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).casefold(): value for key, value in attrs}
        if tag.casefold() == "meta":
            name = str(values.get("name") or "").casefold()
            if name.startswith("evidenceradar-"):
                self.meta.setdefault(name, []).append(str(values.get("content") or ""))
        work_id = values.get("data-evidenceradar-work-id")
        if work_id:
            work_id_text = str(work_id)
            self.work_ids.append(work_id_text)
            featured = values.get("data-featured")
            if featured is not None:
                featured_text = str(featured).casefold()
                self.featured_flags[work_id_text] = featured_text
                if featured_text == "true":
                    self.featured_ids.append(work_id_text)
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

    notes = run.get("notes")
    modern = isinstance(notes, list) and "SEMANTIC_CONTRACT_V2" in notes
    queries = run.get("queries")
    if isinstance(queries, list):
        query_count = counts.get("queries")
        if isinstance(query_count, int) and not isinstance(query_count, bool) and query_count != len(queries):
            errors.append(f"Run.counts.queries={query_count} but Run.queries contains {len(queries)} items")
    derived_counts = {
        "priority_candidates": sum(
            1
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("triage_status") in {"PRIORITY", "REVIEW_REQUIRED"}
        ),
        "lower_priority_candidates": sum(
            1
            for item in candidates
            if isinstance(item, Mapping) and item.get("triage_status") == "LOWER_PRIORITY"
        ),
        "qualifying_event_candidates": sum(
            1
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("event_status") in {"QUALIFYING", "ALREADY_NOTIFIED"}
        ),
        "summaries_translated_zh_tw": sum(
            1
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("summary_basis") == "TRANSLATED_ABSTRACT_EXCERPT_ZH_TW"
        ),
        "summaries_fallback_zh_tw": sum(
            1
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("summary_basis") in {"ZH_TW_METADATA_TEMPLATE", "TITLE_ONLY_ZH_TW"}
        ),
        # The automated runner emits a notification only for a newly
        # qualifying event whose bounded publisher probe succeeded.  Already
        # notified events remain visible but do not increment this run count.
        "notified_events": sum(
            1
            for item in candidates
            if isinstance(item, Mapping)
            and item.get("event_status") == "QUALIFYING"
            and item.get("publisher_access_status") == "SUCCESS"
        ),
    }
    for field, derived in derived_counts.items():
        if modern and field not in counts:
            errors.append(f"Run.counts is missing semantic contract field {field!r}")
        observed = counts.get(field)
        if isinstance(observed, int) and not isinstance(observed, bool) and observed != derived:
            errors.append(f"Run.counts.{field}={observed} but candidate ledger derives {derived}")

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
    notes = run.get("notes")
    modern = isinstance(notes, list) and "SEMANTIC_CONTRACT_V2" in notes
    if modern:
        featured_count = _require_integer(counts, "featured_candidates", errors)
        featured_meta = str(featured_count if featured_count is not None else "")
        values = parser.meta.get("evidenceradar-featured-candidates", [])
        if values != [featured_meta]:
            errors.append(
                "Report meta 'evidenceradar-featured-candidates' must occur once with "
                f"value {featured_meta!r}; observed {values!r}"
            )
        if featured_count is not None and featured_count != len(parser.featured_ids):
            errors.append(
                f"Run.counts.featured_candidates={featured_count} but HTML marks "
                f"{len(parser.featured_ids)} featured items"
            )
        missing_flags = sorted(set(parser.work_ids) - set(parser.featured_flags))
        if missing_flags:
            errors.append(
                "Report HTML modern candidate markers must include data-featured for every item; "
                f"missing={missing_flags[:5]!r}"
            )
        extra_featured = sorted(set(parser.featured_ids) - set(displayed_ids))
        if extra_featured:
            errors.append(
                "Report HTML featured markers must be displayed Run candidates; "
                f"extra={extra_featured[:5]!r}"
            )
    return errors


def _url_host_path(url: Any) -> tuple[str, str]:
    if not isinstance(url, str):
        return "", ""
    parsed = urlparse(url)
    return parsed.netloc.casefold().removeprefix("www."), parsed.path.casefold()


def _is_discovery_landing(url: Any, source_type: str = "") -> bool:
    """Return whether *url* is an index/abstract landing page.

    A HTTP 200 on a DOI, PubMed, or arXiv ``/abs`` page is still discovery
    access.  It must not be promoted to publisher/formal full-text evidence.
    arXiv ``/pdf`` links and explicit PDF suffixes are full-text locations.
    """

    host, path = _url_host_path(url)
    source_type = source_type.casefold()
    if host == "doi.org" or host.endswith(".doi.org"):
        return True
    if host == "pubmed.ncbi.nlm.nih.gov" or host == "ncbi.nlm.nih.gov":
        return True
    if host == "openalex.org" or host.endswith(".openalex.org") or source_type == "openalex":
        return True
    if host in {"europepmc.org", "ebi.ac.uk"} or source_type == "europe_pmc":
        # Europe PMC/PMC article pages backed by a PMCID are repository full
        # text; MED/index/API/search pages remain discovery-only.  The PMC
        # host is included because adapters may emit its direct PDF/HTML URL
        # while retaining ``source_type=europe_pmc``.
        if (
            host in {"europepmc.org", "pmc.ncbi.nlm.nih.gov"}
            and (path.startswith("/articles/pmc") or path.startswith("/article/pmc"))
        ):
            return False
        return True
    if host == "arxiv.org" or source_type == "arxiv":
        if path.startswith("/pdf/") or path.startswith("/html/") or path.endswith((".pdf", ".html")):
            return False
        return True
    if host == "openreview.net" or source_type == "openreview":
        if path.startswith("/pdf") or path.endswith(".pdf"):
            return False
        return True
    return False


def _source_can_support_claim(source: Mapping[str, Any]) -> bool:
    """Whether an Evidence source is substantive full-text evidence.

    ``FULL_TEXT`` is necessary but not sufficient.  Discovery-only PubMed and
    arXiv landing pages are explicitly rejected, as are DOI landing links.
    """

    if source.get("access_status") != "FULL_TEXT":
        return False
    source_type = str(source.get("source_type") or "").casefold()
    url = source.get("url")
    # A source may retain a DOI/index URL for provenance while carrying a
    # separately probed direct repository location.  That explicit location
    # is the only way a landing URL can support a claim.
    locations = source.get("fulltext_locations")
    if isinstance(locations, list):
        if any(
            isinstance(item, Mapping)
            and item.get("access_status") == "ACCESSIBLE"
            and str(item.get("kind") or "") in {"PDF", "HTML", "REPOSITORY"}
            and isinstance(item.get("url"), str)
            and not _is_discovery_landing(item.get("url"), source_type)
            for item in locations
        ):
            return True
    if source_type in {"pubmed", "openalex"}:
        return False
    direct_urls = [url]
    download_urls = source.get("download_urls")
    if isinstance(download_urls, list):
        direct_urls.extend(download_urls)
    direct_url_available = any(
        isinstance(candidate_url, str)
        and candidate_url.startswith(("http://", "https://"))
        and not _is_discovery_landing(candidate_url, source_type)
        for candidate_url in direct_urls
    )
    return (
        source.get("access_probe_status") == "ACCESSIBLE"
        and source.get("fulltext_kind") in {"PDF", "HTML", "REPOSITORY"}
        and direct_url_available
    )


def _modern_access_field_present(item: Mapping[str, Any]) -> bool:
    return any(
        key in item
        for key in (
            "oa_status",
            "oa_evidence",
            "access_status",
            "fulltext_kind",
            "download_urls",
            "fulltext_locations",
            "fulltext_access_status",
        )
    )


def _access_contract_errors(run: Mapping[str, Any]) -> list[str]:
    """Check the independent OA and full-text access contract on candidates."""

    errors: list[str] = []
    candidates = run.get("candidates")
    if not isinstance(candidates, list):
        return errors
    notes = run.get("notes")
    modern = (
        isinstance(notes, list) and "SEMANTIC_CONTRACT_V2" in notes
    ) or any(isinstance(item, Mapping) and _modern_access_field_present(item) for item in candidates)
    event_class_seen = any(
        isinstance(item, Mapping) and "event_class" in item for item in candidates
    )
    oa_counts = {status: 0 for status in sorted(OA_STATUSES)}
    fulltext_counts = {status: 0 for status in sorted(FULLTEXT_ACCESS_STATUSES)}
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            continue
        prefix = f"Run.candidates[{index}]"
        if modern:
            for field in ("oa_status", "oa_evidence", "access_status", "fulltext_kind", "download_urls"):
                if field not in item:
                    errors.append(f"{prefix} is missing semantic contract field {field!r}")
        if event_class_seen and "event_class" not in item:
            errors.append(f"{prefix} is missing semantic contract field 'event_class'")

        oa_status = item.get("oa_status")
        if oa_status is not None and oa_status not in OA_STATUSES:
            errors.append(f"{prefix}.oa_status must be YES, NO, or UNKNOWN")
        if oa_status in oa_counts:
            oa_counts[oa_status] += 1
        evidence = item.get("oa_evidence")
        if oa_status == "YES" and (not isinstance(evidence, list) or not evidence):
            errors.append(f"{prefix}.oa_status=YES requires non-empty oa_evidence")
        if isinstance(evidence, list):
            evidence_keys = [
                json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for entry in evidence
                if isinstance(entry, Mapping)
            ]
            if len(evidence_keys) != len(set(evidence_keys)):
                errors.append(f"{prefix}.oa_evidence must contain unique observations")
            if evidence_keys != sorted(evidence_keys):
                errors.append(f"{prefix}.oa_evidence must use deterministic order")
            for evidence_index, entry in enumerate(evidence):
                if not isinstance(entry, Mapping):
                    errors.append(f"{prefix}.oa_evidence[{evidence_index}] must be an object")
                    continue
                for field in ("source", "evidence_type", "value"):
                    if not isinstance(entry.get(field), str) or not entry[field].strip():
                        errors.append(f"{prefix}.oa_evidence[{evidence_index}].{field} must be non-empty")
        if item.get("open_access") is True and oa_status == "NO":
            errors.append(f"{prefix} contradicts legacy open_access=true with oa_status=NO")

        access_status = item.get("access_status")
        if access_status is not None and access_status not in FULLTEXT_ACCESS_STATUSES:
            errors.append(f"{prefix}.access_status is not a valid full-text access status")
        if access_status in fulltext_counts:
            fulltext_counts[access_status] += 1
        aggregate = item.get("fulltext_access_status")
        if aggregate is not None and aggregate not in FULLTEXT_ACCESS_STATUSES | {"MIXED", "UNKNOWN"}:
            errors.append(f"{prefix}.fulltext_access_status is not a valid aggregate status")
        fulltext_kind = item.get("fulltext_kind")
        if fulltext_kind is not None and fulltext_kind not in FULLTEXT_KINDS:
            errors.append(f"{prefix}.fulltext_kind is not a valid full-text kind")
        download_urls = item.get("download_urls")
        if download_urls is not None:
            if not isinstance(download_urls, list):
                errors.append(f"{prefix}.download_urls must be an array")
            else:
                for url_index, url in enumerate(download_urls):
                    if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
                        errors.append(f"{prefix}.download_urls[{url_index}] must be an HTTP(S) URL")
        if access_status == "ACCESSIBLE":
            if fulltext_kind in {"ABSTRACT_ONLY", "UNKNOWN"}:
                errors.append(f"{prefix} marks ABSTRACT_ONLY/UNKNOWN as ACCESSIBLE full text")
            if isinstance(download_urls, list) and not download_urls:
                errors.append(f"{prefix}.access_status=ACCESSIBLE requires a download URL")
        if fulltext_kind == "ABSTRACT_ONLY" and access_status == "ACCESSIBLE":
            errors.append(f"{prefix}.fulltext_kind=ABSTRACT_ONLY cannot be ACCESSIBLE")
    counts = run.get("counts")
    if isinstance(counts, Mapping):
        count_fields = {
            "YES": "oa_yes",
            "NO": "oa_no",
            "UNKNOWN": "oa_unknown",
        }
        access_count_fields = {
            "ACCESSIBLE": "fulltext_accessible",
            "BLOCKED": "fulltext_blocked",
            "PAYWALLED": "fulltext_paywalled",
            "FAILED": "fulltext_failed",
            "NOT_CHECKED": "fulltext_not_checked",
        }
        for status, field in count_fields.items():
            if modern and field not in counts:
                errors.append(f"Run.counts is missing semantic contract field {field!r}")
            observed = counts.get(field)
            if isinstance(observed, int) and not isinstance(observed, bool) and observed != oa_counts[status]:
                errors.append(f"Run.counts.{field}={observed} but candidate ledger derives {oa_counts[status]}")
        for status, field in access_count_fields.items():
            if modern and field not in counts:
                errors.append(f"Run.counts is missing semantic contract field {field!r}")
            observed = counts.get(field)
            if isinstance(observed, int) and not isinstance(observed, bool) and observed != fulltext_counts[status]:
                errors.append(
                    f"Run.counts.{field}={observed} but candidate ledger derives {fulltext_counts[status]}"
                )
    return errors


def _publisher_and_review_errors(
    run: Mapping[str, Any], evidence: Mapping[str, Any]
) -> list[str]:
    """Re-derive access and review counts from the ledgers.

    Counts in Run are presentation fields.  This function treats candidates,
    source_access and Evidence claims as the source of truth and fails closed
    on semantic drift that JSON Schema cannot detect.
    """

    errors: list[str] = []
    candidates = run.get("candidates")
    counts = run.get("counts")
    source_access = run.get("source_access")
    claims = evidence.get("claims")
    sources = evidence.get("sources")
    if not isinstance(candidates, list) or not isinstance(counts, Mapping):
        return errors
    if not isinstance(source_access, list):
        source_access = []
    if not isinstance(claims, list):
        claims = []
    if not isinstance(sources, list):
        sources = []

    source_by_id: dict[str, Mapping[str, Any]] = {
        str(item.get("source_id")): item
        for item in sources
        if isinstance(item, Mapping) and item.get("source_id")
    }
    publisher_records: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(source_access):
        if not isinstance(record, Mapping):
            continue
        provider = str(record.get("provider") or "").casefold()
        url = record.get("url")
        if provider in {"publisher", "formal_proceedings_or_publisher", "formal"}:
            status = str(record.get("status") or "")
            if status not in PUBLISHER_ACCESS_STATUSES:
                errors.append(
                    f"Run.source_access[{index}] publisher status must be "
                    "SUCCESS, FAILED, or NOT_ATTEMPTED"
                )
            source_id = str(record.get("source_id") or "")
            if source_id:
                publisher_records[source_id] = record
            host, _path = _url_host_path(url)
            # DOI resolution is a valid bounded publisher probe target but is
            # not substantive full text; all other discovery/index landing
            # pages must not be recorded as formal verification success.
            discovery_landing = _is_discovery_landing(url, provider)
            doi_landing = host == "doi.org" or host.endswith(".doi.org")
            if discovery_landing and not doi_landing:
                errors.append(
                    f"Run.source_access[{index}] discovery landing cannot be publisher/formal verification"
                )

    derived = {"SUCCESS": 0, "FAILED": 0, "NOT_ATTEMPTED": 0}
    candidate_work_ids: set[str] = set()
    review_status_by_work: dict[str, str] = {}
    referenced_publisher_ids: set[str] = set()
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            continue
        work_id = str(item.get("work_id") or "")
        if not work_id:
            continue
        candidate_work_ids.add(work_id)
        status = str(item.get("publisher_access_status") or "")
        review_status_by_work[work_id] = str(item.get("review_status") or "")
        if status not in PUBLISHER_ACCESS_STATUSES:
            errors.append(f"Run.candidates[{index}].publisher_access_status is invalid")
            continue
        derived[status] += 1
        access_id = item.get("publisher_access_id")
        if status in {"SUCCESS", "FAILED"}:
            if not isinstance(access_id, str) or not access_id:
                errors.append(f"Run.candidates[{index}] attempted publisher access lacks publisher_access_id")
            else:
                referenced_publisher_ids.add(access_id)
                record = publisher_records.get(access_id)
                if record is None:
                    errors.append(
                        f"Run.candidates[{index}] publisher_access_id={access_id!r} has no publisher source_access record"
                    )
                elif str(record.get("status") or "") != status:
                    errors.append(
                        f"Run.candidates[{index}] publisher status disagrees with source_access[{access_id}]"
                    )
                else:
                    http_status = record.get("http_status")
                    candidate_http_status = item.get("publisher_http_status")
                    if (
                        isinstance(http_status, int)
                        and isinstance(candidate_http_status, int)
                        and http_status != candidate_http_status
                    ):
                        errors.append(
                            f"Run.candidates[{index}] publisher_http_status disagrees with source_access[{access_id}]"
                        )
                    if status == "SUCCESS" and http_status in {401, 403, 429}:
                        errors.append(
                            f"Run.candidates[{index}] publisher SUCCESS cannot carry HTTP {http_status}"
                        )
                    result_count = record.get("result_count")
                    if status == "SUCCESS" and isinstance(result_count, int) and result_count < 1:
                        errors.append(
                            f"Run.source_access[{access_id}] SUCCESS requires result_count >= 1"
                        )
                    if status == "FAILED" and isinstance(result_count, int) and result_count != 0:
                        errors.append(
                            f"Run.source_access[{access_id}] FAILED requires result_count=0"
                        )
        elif access_id is not None:
            errors.append(f"Run.candidates[{index}] NOT_ATTEMPTED cannot carry publisher_access_id")

    for source_id, record in publisher_records.items():
        status = str(record.get("status") or "")
        if status not in {"SUCCESS", "FAILED"}:
            continue
        http_status = record.get("http_status")
        result_count = record.get("result_count")
        if status == "SUCCESS" and http_status in {401, 403, 429}:
            errors.append(f"Run.source_access[{source_id}] publisher SUCCESS cannot carry HTTP {http_status}")
        if status == "SUCCESS" and isinstance(result_count, int) and result_count < 1:
            errors.append(f"Run.source_access[{source_id}] SUCCESS requires result_count >= 1")
        if status == "FAILED" and isinstance(result_count, int) and result_count != 0:
            errors.append(f"Run.source_access[{source_id}] FAILED requires result_count=0")
        work_id = str(record.get("work_id") or "")
        if not work_id:
            errors.append(f"Run.source_access[{source_id}] attempted publisher record lacks work_id")
        elif work_id not in candidate_work_ids:
            errors.append(f"Run.source_access[{source_id}] work_id is not present in Run.candidates")
        if source_id not in referenced_publisher_ids:
            errors.append(f"Run.source_access[{source_id}] attempted record is not linked by publisher_access_id")

    for key, count_key in (
        ("SUCCESS", "publisher_accessible"),
        ("FAILED", "publisher_failed"),
        ("NOT_ATTEMPTED", "publisher_not_attempted"),
    ):
        observed = counts.get(count_key)
        if isinstance(observed, int) and not isinstance(observed, bool) and observed != derived[key]:
            errors.append(
                f"Run.counts.{count_key}={observed} but candidate ledger derives {derived[key]}"
            )
    attempted = counts.get("publisher_attempted")
    if isinstance(attempted, int) and not isinstance(attempted, bool) and attempted != derived["SUCCESS"] + derived["FAILED"]:
        errors.append(
            f"Run.counts.publisher_attempted={attempted} but candidate ledger derives {derived['SUCCESS'] + derived['FAILED']}"
        )

    # A source claim is eligible for verification only when every claim has a
    # substantive source.  PARTIAL/CONFLICT/UNVERIFIED always disqualify the
    # work, even if another claim for the same work is SUPPORTED.
    claims_by_work: dict[str, list[Mapping[str, Any]]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            continue
        work_id = str(claim.get("work_id") or "")
        if work_id not in candidate_work_ids:
            errors.append(f"Evidence.claims[{index}].work_id is not present in Run.candidates")
        status = str(claim.get("status") or "")
        source_ids = claim.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            continue
        claim_sources = []
        for source_id in source_ids:
            source = source_by_id.get(str(source_id))
            if source is None:
                errors.append(f"Evidence.claims[{index}] references unknown source_id {source_id!r}")
            else:
                claim_sources.append(source)
        if status == "SUPPORTED" and not any(_source_can_support_claim(source) for source in claim_sources):
            errors.append(
                f"Evidence.claims[{index}] SUPPORTED has no substantive full-text source; discovery/abstract pages do not qualify"
            )
        claims_by_work.setdefault(work_id, []).append(claim)

    verified_work_ids: set[str] = set()
    for work_id, work_claims in claims_by_work.items():
        if work_claims and all(
            claim.get("status") == "SUPPORTED"
            and any(
                _source_can_support_claim(source)
                for source_id in claim.get("source_ids", [])
                if (source := source_by_id.get(str(source_id))) is not None
            )
            for claim in work_claims
        ):
            verified_work_ids.add(work_id)

    verified_count = counts.get("verified_works")
    if isinstance(verified_count, int) and not isinstance(verified_count, bool) and verified_count != len(verified_work_ids):
        errors.append(
            f"Run.counts.verified_works={verified_count} but Evidence claims derive {len(verified_work_ids)}"
        )
    claim_count = counts.get("claims")
    if isinstance(claim_count, int) and not isinstance(claim_count, bool) and claim_count != len(claims):
        errors.append(f"Run.counts.claims={claim_count} but Evidence contains {len(claims)} claims")
    pending = counts.get("review_pending")
    derived_pending = len(candidate_work_ids - verified_work_ids)
    if isinstance(pending, int) and not isinstance(pending, bool) and pending != derived_pending:
        errors.append(
            f"Run.counts.review_pending={pending} but candidate/Evidence ledgers derive {derived_pending}"
        )
    for work_id, status in review_status_by_work.items():
        if status == "VERIFIED" and work_id not in verified_work_ids:
            errors.append(
                f"Run.candidates[{work_id}].review_status=VERIFIED without all-SUPPORTED substantive Evidence"
            )
        if work_id in verified_work_ids and status != "VERIFIED":
            errors.append(
                f"Run candidate {work_id} has verified Evidence but review_status={status!r}"
            )
    return errors


def _state_run_parity_errors(
    run: Mapping[str, Any], state: Mapping[str, Any]
) -> list[str]:
    """Require modern Run candidate semantics to be mirrored in State."""

    notes = run.get("notes")
    modern = isinstance(notes, list) and "SEMANTIC_CONTRACT_V2" in notes
    candidates = run.get("candidates")
    works = state.get("works")
    if not modern or not isinstance(candidates, list) or not isinstance(works, list):
        return []
    errors: list[str] = []
    work_by_id = {
        str(item.get("work_id")): item
        for item in works
        if isinstance(item, Mapping) and item.get("work_id")
    }

    def canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def entry_keys(values: Any, key: str) -> set[str]:
        if not isinstance(values, list):
            return set()
        return {
            str(item.get(key))
            for item in values
            if isinstance(item, Mapping) and item.get(key)
        }

    def access_compatible(current: Any, historical: Any) -> bool:
        if isinstance(current, (dict, list)) or isinstance(historical, (dict, list)):
            return False
        if current in {"NOT_CHECKED", "UNKNOWN", None}:
            return True
        if historical in {current, "MIXED"}:
            return True
        return historical in {"NOT_CHECKED", "UNKNOWN", None} and current in {
            "NOT_CHECKED",
            "UNKNOWN",
        }

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        work_id = str(candidate.get("work_id") or "")
        state_work = work_by_id.get(work_id)
        if state_work is None:
            errors.append(f"State.works is missing modern Run candidate work_id={work_id!r}")
            continue
        for field in STATE_RUN_PARITY_FIELDS:
            if field not in candidate:
                errors.append(f"Run.candidates[{index}] is missing State parity field {field!r}")
                continue
            if field not in state_work:
                errors.append(f"State.works[{work_id}] is missing Run parity field {field!r}")
                continue
            current = candidate.get(field)
            historical = state_work.get(field)
            if field == "oa_status":
                # State is monotonic: a current UNKNOWN/NO observation must
                # not erase affirmative historical OA evidence.
                if current == "YES" and historical != "YES":
                    errors.append(
                        f"State.works[{work_id}].oa_status must preserve Run YES evidence"
                    )
            elif field == "oa_evidence":
                current_entries = {canonical(item) for item in current} if isinstance(current, list) else set()
                historical_entries = {canonical(item) for item in historical} if isinstance(historical, list) else set()
                if not current_entries <= historical_entries:
                    errors.append(
                        f"State.works[{work_id}].oa_evidence must contain all current Run evidence"
                    )
            elif field == "download_urls":
                current_urls = set(current) if isinstance(current, list) else set()
                historical_urls = set(historical) if isinstance(historical, list) else set()
                if not current_urls <= historical_urls:
                    errors.append(
                        f"State.works[{work_id}].download_urls must contain all current Run URLs"
                    )
            elif field == "fulltext_locations":
                current_urls = entry_keys(current, "url")
                historical_urls = entry_keys(historical, "url")
                if not current_urls <= historical_urls:
                    errors.append(
                        f"State.works[{work_id}].fulltext_locations must retain current Run URLs"
                    )
                historical_by_url = {
                    str(item.get("url")): item
                    for item in historical
                    if isinstance(item, Mapping) and item.get("url")
                } if isinstance(historical, list) else {}
                for location in current if isinstance(current, list) else []:
                    if not isinstance(location, Mapping):
                        continue
                    historical_location = historical_by_url.get(str(location.get("url")))
                    if historical_location is None:
                        continue
                    if not access_compatible(
                        location.get("access_status"), historical_location.get("access_status")
                    ):
                        errors.append(
                            f"State.works[{work_id}].fulltext_locations access status is incompatible with current Run"
                        )
            elif field in {"access_status", "fulltext_access_status"}:
                if not access_compatible(current, historical):
                    errors.append(
                        f"State.works[{work_id}].{field} is incompatible with current Run status"
                    )
            elif field == "fulltext_kind":
                if current in {"ABSTRACT_ONLY", "UNKNOWN", None}:
                    continue
                if historical in {current, "MIXED"}:
                    continue
                historical_urls = entry_keys(state_work.get("fulltext_locations"), "url")
                current_urls = set(candidate.get("download_urls", []))
                if not current_urls <= historical_urls:
                    errors.append(
                        f"State.works[{work_id}].fulltext_kind is incompatible with current Run"
                    )
            elif field == "event_class" and canonical(current) != canonical(historical):
                errors.append(
                    f"State.works[{work_id}].event_class must equal Run.candidates[{index}].event_class"
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
    errors.extend(_access_contract_errors(run))
    errors.extend(_publisher_and_review_errors(run, evidence))
    errors.extend(_state_run_parity_errors(run, state))
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
        if not isinstance(relative, str) or not relative:
            errors.append(f"unsafe Work Pack manifest path: {relative!r}")
            continue
        relative_path = Path(relative)
        posix_path = PurePosixPath(relative)
        windows_path = PureWindowsPath(relative)
        # Manifest paths are archive-relative POSIX names.  Reject absolute,
        # dot-component and drive/UNC forms before joining with the root.  A
        # path that resolves outside root is rejected even if it contains no
        # literal ``..`` (for example, via a symlink).
        if (
            relative_path.is_absolute()
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or not relative_path.parts
            or any(part in {".", "..", ""} for part in relative_path.parts)
        ):
            errors.append(f"unsafe Work Pack manifest path: {relative!r}")
            continue
        path = root / relative_path
        resolved_root = root.resolve()
        resolved_path = path.resolve(strict=False)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError:
            errors.append(f"unsafe Work Pack manifest path escapes root: {relative!r}")
            continue
        # Do not follow a symlink anywhere in the manifest path, even when it
        # happens to resolve underneath the root.
        cursor = resolved_root
        symlink_found = False
        for part in relative_path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                symlink_found = True
                break
        if symlink_found:
            errors.append(f"unsafe Work Pack manifest path uses symlink: {relative!r}")
            continue
        if not path.is_file():
            errors.append(f"Work Pack file is missing: {relative}")
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_hash or len(payload) != expected_size:
            errors.append(f"Work Pack file does not match manifest: {relative}")
    return errors


def _canonical_artifact_path_errors(bundle: Path, name: str) -> list[str]:
    """Reject symlinked or escaping canonical bundle artifacts."""

    errors: list[str] = []
    relative_path = Path(name)
    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (
        relative_path.is_absolute()
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or not relative_path.parts
        or any(part in {".", "..", ""} for part in relative_path.parts)
    ):
        return [f"unsafe canonical artifact path: {name!r}"]
    root = bundle.resolve()
    path = bundle / name
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        errors.append(f"canonical artifact escapes bundle root: {name}")
        return errors
    cursor = root
    for part in Path(name).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            errors.append(f"canonical artifact must not be a symlink: {name}")
            break
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
        errors.extend(_canonical_artifact_path_errors(bundle, name))
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
