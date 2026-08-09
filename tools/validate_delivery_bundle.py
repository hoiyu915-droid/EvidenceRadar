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
from urllib.parse import parse_qsl, urlencode, urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_contract import (
    BUNDLE_FILENAMES,
    WORK_PRODUCER_PATHS,
    current_producer_errors,
)
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
    "identity_status",
    "oa_status",
    "oa_evidence",
    "access_status",
    "fulltext_kind",
    "download_urls",
    "fulltext_locations",
    "fulltext_access_status",
    "access_depth",
    "access_outcome",
    "topic_alignments",
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
        self.claim_ids: list[str] = []
        self.content_roles: list[str] = []
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
        claim_id = values.get("data-evidenceradar-claim-id")
        if claim_id:
            self.claim_ids.append(str(claim_id))
        content_role = values.get("data-content-role")
        if content_role:
            self.content_roles.append(str(content_role))
        if tag.casefold() == "html" and values.get("lang"):
            self.html_languages.append(str(values["lang"]))


def _schema_name(artifact_name: str) -> str:
    suffix = artifact_name.removeprefix("EvidenceRadar_").removesuffix(".json")
    return f"evidence-radar-{suffix.casefold()}.schema.json"


def _contract_marker(run: Mapping[str, Any], marker: str) -> bool:
    notes = run.get("notes")
    return isinstance(notes, list) and marker in notes


def _modern_contract(run: Mapping[str, Any]) -> bool:
    return _contract_marker(run, "SEMANTIC_CONTRACT_V2") or _contract_marker(
        run, "SEMANTIC_CONTRACT_V3"
    )


def _v3_contract(run: Mapping[str, Any]) -> bool:
    return _contract_marker(run, "SEMANTIC_CONTRACT_V3")


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

    modern = _modern_contract(run)
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
    modern = _modern_contract(run)
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
        if _v3_contract(run):
            navigation_count = parser.content_roles.count("navigation_summary")
            if navigation_count != len(displayed_ids):
                errors.append(
                    "V3 Report must mark exactly one navigation_summary per displayed candidate; "
                    f"observed {navigation_count} for {len(displayed_ids)} candidates"
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


def _source_can_support_claim_kind(
    source: Mapping[str, Any], claim_kind: str
) -> bool:
    """Apply the access depth appropriate to the kind of asserted fact.

    Bibliographic facts and attributed statements can be supported by an
    accessible metadata/abstract record.  Scientific findings, statistics,
    policy interpretations and other substantive claims retain the stricter
    direct-full-text requirement.  The V3 binding validator separately checks
    that the declared locator, source role and observed access depth agree.
    """

    if claim_kind in {"BIBLIOGRAPHIC_FACT", "ATTRIBUTION"}:
        return source.get("access_status") in {"METADATA", "ABSTRACT", "FULL_TEXT"}
    return _source_can_support_claim(source)


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
    modern = _modern_contract(run) or any(
        isinstance(item, Mapping) and _modern_access_field_present(item)
        for item in candidates
    )
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
                    if status == "SUCCESS" and (
                        not isinstance(http_status, int)
                        or isinstance(http_status, bool)
                        or not 200 <= http_status < 400
                    ):
                        errors.append(
                            f"Run.candidates[{index}] publisher SUCCESS requires HTTP 2xx/3xx, got {http_status!r}"
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
        if status == "SUCCESS" and (
            not isinstance(http_status, int)
            or isinstance(http_status, bool)
            or not 200 <= http_status < 400
        ):
            errors.append(
                f"Run.source_access[{source_id}] publisher SUCCESS requires HTTP 2xx/3xx, got {http_status!r}"
            )
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
        claim_kind = str(claim.get("claim_kind") or "OTHER")
        if status == "SUPPORTED" and not any(
            _source_can_support_claim_kind(source, claim_kind)
            for source in claim_sources
        ):
            if claim_kind in {"BIBLIOGRAPHIC_FACT", "ATTRIBUTION"}:
                errors.append(
                    f"Evidence.claims[{index}] SUPPORTED lacks accessible metadata for claim_kind={claim_kind}"
                )
            else:
                errors.append(
                    f"Evidence.claims[{index}] SUPPORTED has no substantive full-text source; discovery/abstract pages do not qualify"
                )
        claims_by_work.setdefault(work_id, []).append(claim)

    verified_work_ids: set[str] = set()
    for work_id, work_claims in claims_by_work.items():
        if work_claims and all(
            claim.get("status") == "SUPPORTED"
            and any(
                _source_can_support_claim_kind(
                    source, str(claim.get("claim_kind") or "OTHER")
                )
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

    modern = _modern_contract(run)
    v3 = _v3_contract(run)
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
        parity_fields = STATE_RUN_PARITY_FIELDS if v3 else tuple(
            field
            for field in STATE_RUN_PARITY_FIELDS
            if field not in {"identity_status", "access_depth", "access_outcome", "topic_alignments"}
        )
        for field in parity_fields:
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
            elif field == "identity_status":
                if current == "RESOLVED" and historical != "RESOLVED":
                    errors.append(
                        f"State.works[{work_id}].identity_status must preserve Run RESOLVED identity"
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
            elif field == "access_outcome":
                if not access_compatible(current, historical):
                    errors.append(
                        f"State.works[{work_id}].access_outcome is incompatible with current Run outcome"
                    )
            elif field == "access_depth":
                depth_rank = {
                    "NONE": 0,
                    "METADATA": 1,
                    "LANDING_PAGE": 2,
                    "ABSTRACT": 3,
                    "FULL_TEXT": 4,
                }
                if depth_rank.get(str(historical), -1) < depth_rank.get(str(current), -1):
                    errors.append(
                        f"State.works[{work_id}].access_depth cannot be shallower than current Run"
                    )
            elif field == "topic_alignments":
                current_entries = {canonical(item) for item in current} if isinstance(current, list) else set()
                historical_entries = {canonical(item) for item in historical} if isinstance(historical, list) else set()
                if not current_entries <= historical_entries:
                    errors.append(
                        f"State.works[{work_id}].topic_alignments must retain current Run alignments"
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


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _v3_stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        _canonical_value([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _v3_canonical_source_url(value: Any) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"fbclid", "gclid"}
    )
    normalized = parsed._replace(
        scheme=parsed.scheme.casefold(),
        netloc=parsed.netloc.casefold(),
        query=urlencode(query, doseq=True),
        fragment="",
    ).geturl()
    if parsed.path and parsed.path != "/":
        normalized = normalized.rstrip("/")
    return normalized


def _v3_contract_errors(
    *,
    root: Path,
    report_html: str,
    run: Mapping[str, Any],
    state: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[str]:
    """Validate the auditable retrieval/claim/canonical-output V3 contract."""

    if not _v3_contract(run):
        return []
    errors: list[str] = []
    run_id = str(run.get("run_id") or "")
    required_arrays = {
        "Run.retrieval_attempts": run.get("retrieval_attempts"),
        "Run.search_expansions": run.get("search_expansions"),
        "Run.followup_attempts": run.get("followup_attempts"),
        "State.source_registry": state.get("source_registry"),
        "State.source_observations": state.get("source_observations"),
        "State.gaps": state.get("gaps"),
        "State.work_relations": state.get("work_relations"),
        "State.claim_relations": state.get("claim_relations"),
        "State.claim_registry": state.get("claim_registry"),
        "Evidence.source_registry": evidence.get("source_registry"),
        "Evidence.source_observations": evidence.get("source_observations"),
        "Evidence.citation_bindings": evidence.get("citation_bindings"),
        "Evidence.effect_estimates": evidence.get("effect_estimates"),
        "Evidence.conflict_groups": evidence.get("conflict_groups"),
        "Evidence.inferences": evidence.get("inferences"),
    }
    for label, value in required_arrays.items():
        if not isinstance(value, list):
            errors.append(f"{label} is required by SEMANTIC_CONTRACT_V3")
    if errors:
        return errors

    try:
        import yaml

        streams_config = yaml.safe_load(
            (root / "config" / "streams.yml").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ImportError, AttributeError, TypeError) as exc:
        errors.append(f"cannot load configured V3 source registry: {exc}")
        return errors
    if not isinstance(streams_config, Mapping):
        return errors + ["config/streams.yml must contain a mapping"]
    configured_sources = {
        str(source_id)
        for stream in (streams_config.get("streams") or {}).values()
        if isinstance(stream, Mapping)
        for source_id in stream.get("sources", [])
        if str(source_id)
    }
    catalog_sources = {
        str(source_id)
        for source_id in (streams_config.get("source_catalog") or {})
    }
    if not configured_sources or configured_sources != catalog_sources:
        errors.append(
            "config/streams.yml source_catalog must exactly cover configured stream sources"
        )

    attempts = list(run.get("retrieval_attempts") or [])
    expansions = list(run.get("search_expansions") or [])
    followups = list(run.get("followup_attempts") or [])
    registry = list(state.get("source_registry") or [])
    observations = list(state.get("source_observations") or [])
    gaps = list(state.get("gaps") or [])
    claim_registry = list(state.get("claim_registry") or [])
    candidates = list(run.get("candidates") or [])
    evidence_works = list(evidence.get("works") or [])
    sources = list(evidence.get("sources") or [])
    claims = list(evidence.get("claims") or [])
    bindings = list(evidence.get("citation_bindings") or [])
    effects = list(evidence.get("effect_estimates") or [])
    conflict_groups = list(evidence.get("conflict_groups") or [])
    inferences = list(evidence.get("inferences") or [])

    def unique_index(values: list[Any], key: str, label: str) -> dict[str, Mapping[str, Any]]:
        index: dict[str, Mapping[str, Any]] = {}
        ordered: list[str] = []
        for position, value in enumerate(values):
            if not isinstance(value, Mapping):
                errors.append(f"{label}[{position}] must be an object")
                continue
            identifier = str(value.get(key) or "")
            if not identifier:
                errors.append(f"{label}[{position}].{key} must be non-empty")
                continue
            if identifier in index:
                errors.append(f"{label} contains duplicate {key}={identifier!r}")
            index[identifier] = value
            ordered.append(identifier)
        if ordered != sorted(ordered):
            errors.append(f"{label} must use deterministic {key} order")
        return index

    attempt_by_id = unique_index(attempts, "attempt_id", "Run.retrieval_attempts")
    expansion_by_id = unique_index(expansions, "expansion_id", "Run.search_expansions")
    unique_index(followups, "followup_id", "Run.followup_attempts")
    registry_by_id = unique_index(registry, "source_id", "State.source_registry")
    observation_by_id = unique_index(observations, "observation_id", "State.source_observations")
    gap_by_id = unique_index(gaps, "gap_id", "State.gaps")
    claim_registry_by_id = unique_index(
        claim_registry, "claim_id", "State.claim_registry"
    )
    claim_by_id = unique_index(claims, "claim_id", "Evidence.claims")
    binding_by_id = unique_index(bindings, "binding_id", "Evidence.citation_bindings")
    effect_by_id = unique_index(effects, "estimate_id", "Evidence.effect_estimates")
    conflict_by_id = unique_index(conflict_groups, "conflict_id", "Evidence.conflict_groups")
    unique_index(inferences, "inference_id", "Evidence.inferences")

    raw_queries = list(run.get("queries") or [])
    query_by_id = unique_index(raw_queries, "query_id", "Run.queries")
    queries = [item for item in raw_queries if isinstance(item, Mapping)]
    source_access = [item for item in run.get("source_access", []) if isinstance(item, Mapping)]
    source_access_by_id = {
        str(item.get("source_id") or ""): item
        for item in source_access
        if item.get("source_id")
    }
    source_access_ids = [
        str(item.get("source_id") or "") for item in source_access
    ]
    if len(source_access_ids) != len(set(source_access_ids)):
        errors.append("Run.source_access contains duplicate source_id values")
    coverage_checks = [
        item
        for item in (run.get("source_coverage") or {}).get("checks", [])
        if isinstance(item, Mapping)
    ]
    coverage_check_by_source = unique_index(
        coverage_checks, "source_id", "Run.source_coverage.checks"
    )
    requested_sources = {
        str(value)
        for value in (run.get("source_coverage") or {}).get("requested", [])
    }
    if requested_sources != configured_sources:
        errors.append(
            "Run.source_coverage.requested must exactly equal configured stream sources"
        )
    if set(coverage_check_by_source) != configured_sources:
        errors.append(
            "Run.source_coverage.checks must exactly cover configured stream sources"
        )
    for position, query in enumerate(queries):
        source_ids = [str(value) for value in query.get("source_ids", [])]
        if source_ids != sorted(set(source_ids)):
            errors.append(
                f"Run.queries[{position}].source_ids must be unique and sorted"
            )
        if len(source_ids) != 1:
            errors.append(
                f"Run.queries[{position}] must describe exactly one backend operation"
            )
        unknown = {
            str(value) for value in source_ids
        } - configured_sources
        if unknown:
            errors.append(
                f"Run.queries[{position}] references unconfigured sources: {sorted(unknown)}"
            )
    for position, access in enumerate(source_access):
        provider = str(access.get("provider") or "")
        if provider not in configured_sources:
            errors.append(
                f"Run.source_access[{position}] provider is not a configured source"
            )
    raw_candidate_count = (run.get("counts") or {}).get("raw_candidates")
    derived_raw_candidate_count = sum(
        int(query.get("result_count") or 0) for query in queries
    )
    if raw_candidate_count != derived_raw_candidate_count:
        errors.append(
            "Run.counts.raw_candidates must equal the sum of per-operation query results"
        )
    attempts_by_query_source: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    attempts_by_source: dict[str, list[Mapping[str, Any]]] = {}
    attempts_by_access_id: dict[str, list[Mapping[str, Any]]] = {}
    for position, attempt in enumerate(attempts):
        if not isinstance(attempt, Mapping):
            continue
        prefix = f"Run.retrieval_attempts[{position}]"
        status = str(attempt.get("status") or "")
        result_count = attempt.get("result_count")
        pagination = attempt.get("pagination")
        if attempt.get("receipt_origin") != "EXECUTOR":
            errors.append(f"{prefix}.receipt_origin must be EXECUTOR")
        if status == "NO_RESULTS" and result_count != 0:
            errors.append(f"{prefix} NO_RESULTS requires result_count=0")
        if status == "SUCCESS" and (not isinstance(result_count, int) or result_count < 1):
            errors.append(f"{prefix} SUCCESS requires result_count>=1")
        if status == "PARTIAL" and (
            not isinstance(result_count, int) or result_count < 1
        ):
            errors.append(f"{prefix} PARTIAL requires result_count>=1")
        if status == "NOT_ATTEMPTED":
            if result_count != 0:
                errors.append(f"{prefix} NOT_ATTEMPTED requires result_count=0")
            if not isinstance(pagination, Mapping) or pagination.get("pages_requested") != 0:
                errors.append(f"{prefix} NOT_ATTEMPTED cannot claim a requested page")
        if status == "FAILED" and result_count != 0:
            errors.append(f"{prefix} FAILED requires result_count=0; use PARTIAL when results were retained")
        if status in {"FAILED", "NOT_ATTEMPTED"} and not attempt.get("error_class"):
            errors.append(f"{prefix} {status} requires a bounded error_class")
        if isinstance(pagination, Mapping):
            requested_pages = pagination.get("pages_requested")
            received_pages = pagination.get("pages_received")
            if (
                isinstance(requested_pages, int)
                and isinstance(received_pages, int)
                and received_pages > requested_pages
            ):
                errors.append(f"{prefix} cannot receive more pages than requested")
            if status in {"SUCCESS", "NO_RESULTS", "PARTIAL"} and (
                not isinstance(requested_pages, int)
                or isinstance(requested_pages, bool)
                or requested_pages < 1
                or not isinstance(received_pages, int)
                or isinstance(received_pages, bool)
                or received_pages < 1
            ):
                errors.append(
                    f"{prefix} {status} requires at least one successfully received page"
                )
            if status in {"FAILED", "NOT_ATTEMPTED"} and received_pages != 0:
                errors.append(f"{prefix} {status} requires pages_received=0")
        query_id = str(attempt.get("query_id") or "")
        source_id = str(attempt.get("source_id") or "")
        access_id = str(attempt.get("source_access_id") or "")
        if source_id not in configured_sources:
            errors.append(f"{prefix}.source_id is not a configured source")
        linked_access = source_access_by_id.get(access_id) if access_id else None
        if access_id and linked_access is None:
            errors.append(
                f"{prefix} source_access_id references an unknown access record"
            )
        elif linked_access is not None and str(
            linked_access.get("provider") or ""
        ) != source_id:
            errors.append(
                f"{prefix}.source_id disagrees with its source_access provider"
            )
        if query_id:
            expected_attempt_id = _v3_stable_id(
                "attempt", run_id, attempt.get("stage"), query_id, source_id
            )
        elif access_id:
            expected_attempt_id = _v3_stable_id(
                "attempt", run_id, attempt.get("stage"), access_id
            )
        else:
            expected_attempt_id = _v3_stable_id(
                "attempt", run_id, "CHECK", source_id
            )
        if attempt.get("attempt_id") != expected_attempt_id:
            errors.append(f"{prefix}.attempt_id is not stable for this executor operation")
        if query_id:
            declared_query = query_by_id.get(query_id)
            if declared_query is None or source_id not in {
                str(value) for value in declared_query.get("source_ids", [])
            }:
                errors.append(
                    f"{prefix} query receipt is not declared by exactly one Run query"
                )
            attempts_by_query_source.setdefault((query_id, source_id), []).append(attempt)
            expected_work_ids = sorted(
                str(candidate.get("work_id"))
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and query_id in candidate.get("query_ids", [])
                and source_id in candidate.get("discovery_sources", [])
                and candidate.get("work_id")
            )
            expected_hash = hashlib.sha256(
                _canonical_value(expected_work_ids).encode("utf-8")
            ).hexdigest()
            if attempt.get("result_ids_sha256") != expected_hash:
                errors.append(f"{prefix}.result_ids_sha256 does not match the candidate ledger")
            request_limit = attempt.get("request_limit")
            window = run.get("window") if isinstance(run.get("window"), Mapping) else {}
            if not isinstance(request_limit, int) or isinstance(request_limit, bool):
                errors.append(f"{prefix}.request_limit is required for a query receipt")
            else:
                expected_fingerprint = hashlib.sha256(
                    _canonical_value(
                        {
                            "endpoint": str(attempt.get("endpoint") or ""),
                            "source_id": source_id,
                            "actual_query": str(attempt.get("actual_query") or ""),
                            "window": [window.get("start"), window.get("end")],
                            "limit": request_limit,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                if attempt.get("request_fingerprint") != expected_fingerprint:
                    errors.append(f"{prefix}.request_fingerprint does not match its query payload")
        elif access_id:
            access_record = linked_access
            if access_record is not None:
                expected_fingerprint = hashlib.sha256(
                    _canonical_value(
                        {
                            "url": str(access_record.get("url") or ""),
                            "provider": source_id,
                            "work_id": str(access_record.get("work_id") or ""),
                        }
                    ).encode("utf-8")
                ).hexdigest()
                if attempt.get("request_fingerprint") != expected_fingerprint:
                    errors.append(f"{prefix}.request_fingerprint does not match source_access")
        else:
            check = coverage_check_by_source.get(source_id)
            expected_check_stage = (
                "CONTENT_FETCH"
                if isinstance(check, Mapping)
                and check.get("stage") == "bounded_verification"
                else "DISCOVERY"
            )
            if check is None or attempt.get("stage") != expected_check_stage:
                errors.append(
                    f"{prefix} receipt is not declared by a query, source_access, or source CHECK"
                )
            else:
                expected_fingerprint = hashlib.sha256(
                    _canonical_value(
                        {
                            "source_id": source_id,
                            "status": str(check.get("status") or ""),
                            "stage": check.get("stage"),
                        }
                    ).encode("utf-8")
                ).hexdigest()
                if attempt.get("request_fingerprint") != expected_fingerprint:
                    errors.append(f"{prefix}.request_fingerprint does not match source CHECK")
        attempts_by_source.setdefault(source_id, []).append(attempt)
        if access_id:
            attempts_by_access_id.setdefault(access_id, []).append(attempt)

    for query in queries:
        query_id = str(query.get("query_id") or "")
        for source_id in query.get("source_ids", []):
            matches = attempts_by_query_source.get((query_id, str(source_id)), [])
            if len(matches) != 1:
                errors.append(
                    f"query {query_id!r}/{source_id!r} must have exactly one executor retrieval receipt"
                )
                continue
            attempt = matches[0]
            if attempt.get("status") != query.get("status") or attempt.get("result_count") != query.get("result_count"):
                errors.append(f"query {query_id!r} disagrees with its executor receipt")
            requested = str(attempt.get("requested_query") or "")
            actual = str(attempt.get("actual_query") or "")
            matching_expansions = [
                item
                for item in expansions
                if isinstance(item, Mapping)
                and item.get("query_id") == query_id
                and item.get("source_id") == source_id
            ]
            if actual != requested and len(matching_expansions) != 1:
                errors.append(f"query {query_id!r} transformed actual_query requires one expansion log")
            if actual == requested and matching_expansions:
                errors.append(f"query {query_id!r} has a spurious expansion log")
    for access in source_access:
        access_id = str(access.get("source_id") or "")
        matches = attempts_by_access_id.get(access_id, [])
        if len(matches) != 1:
            errors.append(f"source_access {access_id!r} must have exactly one executor receipt")
        else:
            endpoint = _v3_canonical_source_url(matches[0].get("endpoint"))
            access_url = _v3_canonical_source_url(access.get("url"))
            if endpoint != access_url:
                errors.append(
                    f"source_access {access_id!r} URL disagrees with its receipt endpoint"
                )
            if matches[0].get("status") != access.get("status"):
                errors.append(f"source_access {access_id!r} status disagrees with its receipt")
            if matches[0].get("result_count") != access.get("result_count"):
                errors.append(f"source_access {access_id!r} result_count disagrees with its receipt")
            expected_ids = [str(access.get("work_id"))] if access.get("work_id") else []
            expected_result_hash = hashlib.sha256(
                _canonical_value(expected_ids).encode("utf-8")
            ).hexdigest()
            if not matches[0].get("query_id") and matches[0].get("result_ids_sha256") != expected_result_hash:
                errors.append(f"source_access {access_id!r} result ID hash disagrees with its receipt")
    for check in coverage_checks:
        source_id = str(check.get("source_id") or "")
        matches = attempts_by_source.get(source_id, [])
        expected_stage = (
            "CONTENT_FETCH"
            if check.get("stage") == "bounded_verification"
            else "DISCOVERY"
        )
        matches = [item for item in matches if item.get("stage") == expected_stage]
        if not matches:
            errors.append(f"source CHECK {source_id!r} has no executor receipt")
            continue
        statuses = {str(item.get("status") or "NOT_ATTEMPTED") for item in matches}
        expected_count = sum(int(item.get("result_count") or 0) for item in matches)
        if "FAILED" in statuses or "PARTIAL" in statuses:
            expected_status = "FAILED"
        elif "SUCCESS" in statuses:
            expected_status = "SUCCESS"
        elif statuses and statuses <= {"NO_RESULTS"}:
            expected_status = "NO_RESULTS"
        else:
            expected_status = "NOT_ATTEMPTED"
        if check.get("status") != expected_status:
            errors.append(f"source CHECK {source_id!r} status disagrees with receipts")
        if check.get("result_count") != expected_count:
            errors.append(f"source CHECK {source_id!r} result_count disagrees with receipts")
        expected_checked_at = max(str(item.get("attempted_at") or "") for item in matches)
        if check.get("checked_at") != expected_checked_at:
            errors.append(f"source CHECK {source_id!r} checked_at disagrees with receipts")

    for expansion_id, expansion in expansion_by_id.items():
        query_id = str(expansion.get("query_id") or "")
        source_id = str(expansion.get("source_id") or "")
        attempt_matches = attempts_by_query_source.get((query_id, source_id), [])
        if len(attempt_matches) != 1:
            errors.append(f"search expansion {expansion_id!r} lacks one query receipt")
            continue
        attempt = attempt_matches[0]
        if expansion.get("original_query") != attempt.get("requested_query") or expansion.get("expanded_query") != attempt.get("actual_query"):
            errors.append(f"search expansion {expansion_id!r} disagrees with its query receipt")
        expected_expansion_id = _v3_stable_id(
            "expansion", query_id, source_id, expansion.get("expanded_query")
        )
        if expansion_id != expected_expansion_id:
            errors.append(f"search expansion {expansion_id!r} is not stable")

    if evidence.get("source_registry") != state.get("source_registry"):
        errors.append("Evidence.source_registry must be JSON-identical to State.source_registry")
    if evidence.get("source_observations") != state.get("source_observations"):
        errors.append("Evidence.source_observations must be JSON-identical to State.source_observations")
    state_work_ids = {
        str(item.get("work_id"))
        for item in state.get("works", [])
        if isinstance(item, Mapping) and item.get("work_id")
    }
    source_projection_by_id = unique_index(sources, "source_id", "Evidence.sources")
    observations_by_source: dict[str, list[Mapping[str, Any]]] = {}
    for observation in observations:
        if isinstance(observation, Mapping):
            observations_by_source.setdefault(
                str(observation.get("source_id") or ""), []
            ).append(observation)
    if set(source_projection_by_id) != set(registry_by_id):
        errors.append("Evidence.sources must project every stable source_registry ID exactly once")
    for source_id, entry in registry_by_id.items():
        canonical_url = _v3_canonical_source_url(entry.get("canonical_url"))
        if entry.get("canonical_url") != canonical_url:
            errors.append(f"State.source_registry source_id {source_id!r} URL is not canonical")
        expected_id = _v3_stable_id("src", canonical_url)
        if source_id != expected_id:
            errors.append(f"State.source_registry source_id {source_id!r} is not stable for its canonical URL")
        if str(entry.get("work_id") or "") not in state_work_ids:
            errors.append(f"source_registry {source_id!r} references unknown State work")
        projection = source_projection_by_id.get(source_id)
        if projection is not None and projection.get("url") != entry.get("canonical_url"):
            errors.append(f"Evidence.sources[{source_id}] URL differs from the stable registry")
        if projection is not None:
            projection_url = str(projection.get("url") or "")
            projection_locations = [
                item
                for item in projection.get("fulltext_locations", [])
                if isinstance(item, Mapping)
            ]
            for location in projection_locations:
                if _v3_canonical_source_url(location.get("url")) != projection_url:
                    errors.append(
                        f"Evidence.sources[{source_id}] fulltext location must have its own stable source entry"
                    )
            for download_url in projection.get("download_urls", []):
                if _v3_canonical_source_url(download_url) != projection_url:
                    errors.append(
                        f"Evidence.sources[{source_id}] download URL must have its own stable source entry"
                    )
            accessible_fulltext = [
                item
                for item in observations_by_source.get(source_id, [])
                if item.get("access_depth") == "FULL_TEXT"
                and item.get("access_outcome") == "ACCESSIBLE"
                and item.get("url") == projection_url
            ]
            claims_fulltext = (
                projection.get("access_status") == "FULL_TEXT"
                or projection.get("access_probe_status") == "ACCESSIBLE"
                or any(
                    item.get("access_status") == "ACCESSIBLE"
                    for item in projection_locations
                )
            )
            if claims_fulltext and not accessible_fulltext:
                errors.append(
                    f"Evidence.sources[{source_id}] full-text access lacks an accessible FULL_TEXT observation"
                )
            if claims_fulltext and _is_discovery_landing(
                projection_url, str(projection.get("source_type") or "")
            ):
                errors.append(
                    f"Evidence.sources[{source_id}] discovery landing cannot be full-text evidence"
                )
    for observation_id, observation in observation_by_id.items():
        source_id = str(observation.get("source_id") or "")
        entry = registry_by_id.get(source_id)
        if entry is None:
            errors.append(f"source observation {observation_id!r} references unknown source_id")
            continue
        if observation.get("url") != entry.get("canonical_url"):
            errors.append(f"source observation {observation_id!r} URL differs from registry")
        expected_id = _v3_stable_id(
            "obs", source_id, observation.get("run_id"), observation.get("attempt_id")
        )
        if observation_id != expected_id:
            errors.append(f"source observation {observation_id!r} is not a stable ID")
        if observation.get("run_id") == run_id:
            attempt = attempt_by_id.get(str(observation.get("attempt_id") or ""))
            if attempt is None:
                errors.append(
                    f"current source observation {observation_id!r} lacks its retrieval receipt"
                )
                continue
            if observation.get("observed_at") != attempt.get("attempted_at"):
                errors.append(
                    f"current source observation {observation_id!r} time differs from its receipt"
                )
            depth = str(observation.get("access_depth") or "NONE")
            outcome = str(observation.get("access_outcome") or "NOT_CHECKED")
            status = str(attempt.get("status") or "")
            stage = str(attempt.get("stage") or "")
            if outcome == "ACCESSIBLE" and status != "SUCCESS":
                errors.append(
                    f"current source observation {observation_id!r} ACCESSIBLE requires a SUCCESS receipt"
                )
            if outcome in {"BLOCKED", "PAYWALLED", "FAILED"} and status != "FAILED":
                errors.append(
                    f"current source observation {observation_id!r} blocked/failed outcome requires a FAILED receipt"
                )
            direct_stages = {"CONTENT_FETCH", "CLAIM_VERIFY", "FOLLOWUP"}
            if depth == "FULL_TEXT" and (
                outcome != "ACCESSIBLE"
                or status != "SUCCESS"
                or stage not in direct_stages
            ):
                errors.append(
                    f"current source observation {observation_id!r} FULL_TEXT ACCESSIBLE requires a successful direct-content receipt"
                )
            if depth == "FULL_TEXT" and _is_discovery_landing(
                observation.get("url"), str(entry.get("source_type") or "")
            ):
                errors.append(
                    f"current source observation {observation_id!r} discovery landing cannot be FULL_TEXT"
                )
            direct_observation = (
                depth == "FULL_TEXT"
                or stage in direct_stages
                and outcome in {"ACCESSIBLE", "BLOCKED", "PAYWALLED", "FAILED"}
            )
            if direct_observation:
                endpoint = _v3_canonical_source_url(attempt.get("endpoint"))
                if endpoint != observation.get("url"):
                    errors.append(
                        f"current source observation {observation_id!r} URL differs from its direct-content receipt endpoint"
                    )
    candidate_by_work = {
        str(item.get("work_id")): item
        for item in candidates
        if isinstance(item, Mapping) and item.get("work_id")
    }
    evidence_work_by_id = {
        str(item.get("work_id")): item
        for item in evidence_works
        if isinstance(item, Mapping) and item.get("work_id")
    }
    for work_id, candidate in candidate_by_work.items():
        for field in ("identity_status", "access_depth", "access_outcome", "topic_alignments"):
            if field not in candidate:
                errors.append(f"Run candidate {work_id!r} is missing V3 field {field!r}")
        alignments = candidate.get("topic_alignments")
        if not isinstance(alignments, list) or not alignments:
            errors.append(f"Run candidate {work_id!r} requires topic_alignments")
        else:
            criteria = {
                str(item.get("criterion_id"))
                for item in alignments
                if isinstance(item, Mapping)
            }
            expected = {f"stream:{stream}" for stream in candidate.get("streams", [])}
            if not expected <= criteria:
                errors.append(f"Run candidate {work_id!r} topic alignment omits a configured stream")
        unknown_query_ids = sorted(
            {
                str(value)
                for value in candidate.get("query_ids", [])
                if str(value) not in query_by_id
            }
        )
        if unknown_query_ids:
            errors.append(
                f"Run candidate {work_id!r} references unknown query IDs: {unknown_query_ids}"
            )
        evidence_work = evidence_work_by_id.get(work_id)
        if evidence_work is None:
            errors.append(f"Evidence.works is missing V3 Run candidate {work_id!r}")
        else:
            for field in ("identity_status", "access_depth", "access_outcome", "topic_alignments"):
                if evidence_work.get(field) != candidate.get(field):
                    errors.append(f"Evidence.works[{work_id}].{field} must equal Run candidate")

        registry_urls = {
            _v3_canonical_source_url(item.get("canonical_url"))
            for item in registry
            if isinstance(item, Mapping) and str(item.get("work_id") or "") == work_id
        }
        candidate_source_urls = {
            _v3_canonical_source_url(value)
            for value in candidate.get("source_urls", [])
            if _v3_canonical_source_url(value)
        }
        missing_registry_urls = sorted(candidate_source_urls - registry_urls)
        if missing_registry_urls:
            errors.append(
                f"Run candidate {work_id!r} source_urls are absent from the stable source registry"
            )

        location_urls = {
            _v3_canonical_source_url(item.get("url"))
            for item in candidate.get("fulltext_locations", [])
            if isinstance(item, Mapping) and _v3_canonical_source_url(item.get("url"))
        }
        download_urls = {
            _v3_canonical_source_url(value)
            for value in candidate.get("download_urls", [])
            if _v3_canonical_source_url(value)
        }
        if (location_urls | download_urls) - registry_urls:
            errors.append(
                f"Run candidate {work_id!r} full-text URLs are absent from the stable source registry"
            )
        current_accessible_fulltext_urls = {
            _v3_canonical_source_url(observation.get("url"))
            for observation in observations
            if isinstance(observation, Mapping)
            and observation.get("run_id") == run_id
            and observation.get("access_depth") == "FULL_TEXT"
            and observation.get("access_outcome") == "ACCESSIBLE"
            and str(
                registry_by_id.get(str(observation.get("source_id") or ""), {}).get(
                    "work_id"
                )
                or ""
            )
            == work_id
        }
        current_direct_outcomes: dict[str, set[str]] = {}
        for observation in observations:
            if not isinstance(observation, Mapping) or observation.get("run_id") != run_id:
                continue
            observation_source = registry_by_id.get(
                str(observation.get("source_id") or ""), {}
            )
            if str(observation_source.get("work_id") or "") != work_id:
                continue
            attempt = attempt_by_id.get(str(observation.get("attempt_id") or ""))
            if not isinstance(attempt, Mapping) or attempt.get("stage") not in {
                "CONTENT_FETCH",
                "CLAIM_VERIFY",
                "FOLLOWUP",
            }:
                continue
            observation_url = _v3_canonical_source_url(observation.get("url"))
            if observation_url:
                current_direct_outcomes.setdefault(observation_url, set()).add(
                    str(observation.get("access_outcome") or "")
                )
        accessible_location_urls = {
            _v3_canonical_source_url(item.get("url"))
            for item in candidate.get("fulltext_locations", [])
            if isinstance(item, Mapping)
            and item.get("access_status") == "ACCESSIBLE"
            and _v3_canonical_source_url(item.get("url"))
        }
        if not accessible_location_urls <= current_accessible_fulltext_urls:
            errors.append(
                f"Run candidate {work_id!r} ACCESSIBLE full-text locations lack current direct receipts"
            )
        for location in candidate.get("fulltext_locations", []):
            if not isinstance(location, Mapping):
                continue
            location_status = str(location.get("access_status") or "")
            if location_status not in {"BLOCKED", "PAYWALLED", "FAILED"}:
                continue
            location_url = _v3_canonical_source_url(location.get("url"))
            if location_status not in current_direct_outcomes.get(location_url, set()):
                errors.append(
                    f"Run candidate {work_id!r} {location_status} full-text location lacks a current direct receipt"
                )
        if (
            candidate.get("access_status") == "ACCESSIBLE"
            or candidate.get("access_depth") == "FULL_TEXT"
        ) and not (
            current_accessible_fulltext_urls
            & (accessible_location_urls | location_urls | download_urls)
        ):
            errors.append(
                f"Run candidate {work_id!r} FULL_TEXT/ACCESSIBLE status lacks a current direct receipt"
            )
        negative_status = str(candidate.get("access_status") or "")
        if negative_status in {"BLOCKED", "PAYWALLED", "FAILED"} and not any(
            negative_status in outcomes for outcomes in current_direct_outcomes.values()
        ):
            errors.append(
                f"Run candidate {work_id!r} {negative_status} status lacks a current direct receipt"
            )

    current_checks = {
        str(item.get("source_id")): str(item.get("status") or "")
        for item in (run.get("source_coverage") or {}).get("checks", [])
        if isinstance(item, Mapping)
    }
    for gap_id, gap in gap_by_id.items():
        expected_gap_id = _v3_stable_id(
            "gap", gap.get("gap_type"), gap.get("scope_id")
        )
        if gap_id != expected_gap_id:
            errors.append(f"State.gaps[{gap_id}] gap_id is not stable")
        expected_scope_type = {
            "CONTENT_INACCESSIBLE": "WORK",
            "IDENTITY_UNRESOLVED": "WORK",
            "CLAIM_UNVERIFIED": "CLAIM",
            "NUMERIC_CONFLICT": "CLAIM",
        }.get(str(gap.get("gap_type") or ""))
        if gap.get("gap_type") == "SOURCE_UNAVAILABLE" and gap.get(
            "scope_type"
        ) not in {"SOURCE_SYSTEM", "SOURCE"}:
            errors.append(
                f"State.gaps[{gap_id}] scope_type is incompatible with gap_type"
            )
        if expected_scope_type and gap.get("scope_type") != expected_scope_type:
            errors.append(
                f"State.gaps[{gap_id}] scope_type is incompatible with gap_type"
            )
        known_scope_ids = {
            "SOURCE_SYSTEM": set(
                str(value)
                for value in (run.get("source_coverage") or {}).get("requested", [])
            ),
            "SOURCE": set(registry_by_id),
            "WORK": state_work_ids,
            "CLAIM": set(claim_registry_by_id),
        }.get(str(gap.get("scope_type") or ""), set())
        historical_deferred_system = (
            gap.get("scope_type") == "SOURCE_SYSTEM"
            and gap.get("status") in {"DEFERRED", "RESOLVED", "UNRESOLVABLE"}
        )
        if (
            str(gap.get("scope_id") or "") not in known_scope_ids
            and not historical_deferred_system
        ):
            errors.append(f"State.gaps[{gap_id}] references unknown scope")
        receipt_ids = gap.get("receipt_ids")
        if not isinstance(receipt_ids, list) or len(receipt_ids) != len(set(receipt_ids)):
            errors.append(f"State.gaps[{gap_id}].receipt_ids must be unique")
        if gap.get("status") == "RESOLVED":
            resolution = str(gap.get("resolution_receipt_id") or "")
            if not resolution or resolution not in set(receipt_ids or []):
                errors.append(f"State.gaps[{gap_id}] RESOLVED requires a resolution receipt")
            if gap.get("last_attempt_run") == run_id:
                attempt = attempt_by_id.get(resolution)
                if attempt is None or attempt.get("status") not in {"SUCCESS", "NO_RESULTS"}:
                    errors.append(f"State.gaps[{gap_id}] current resolution receipt is not successful")
        attempt_count = gap.get("attempt_count")
        max_attempts = gap.get("max_attempts")
        if (
            isinstance(attempt_count, int)
            and not isinstance(attempt_count, bool)
            and isinstance(max_attempts, int)
            and not isinstance(max_attempts, bool)
        ):
            if attempt_count >= max_attempts and gap.get("status") not in {
                "RESOLVED",
                "UNRESOLVABLE",
            }:
                errors.append(
                    f"State.gaps[{gap_id}] reaching max_attempts requires UNRESOLVABLE or RESOLVED"
                )
            if gap.get("status") == "UNRESOLVABLE" and attempt_count < max_attempts:
                errors.append(
                    f"State.gaps[{gap_id}] UNRESOLVABLE requires attempt_count >= max_attempts"
                )
    for source_id, status in current_checks.items():
        if status not in {"FAILED", "NOT_ATTEMPTED"}:
            continue
        gap_id = _v3_stable_id("gap", "SOURCE_UNAVAILABLE", source_id)
        gap = gap_by_id.get(gap_id)
        if gap is None or gap.get("status") not in {"OPEN", "UNRESOLVABLE", "DEFERRED"}:
            errors.append(f"failed source {source_id!r} must remain in the gap backlog")
    for access in source_access:
        if access.get("status") != "FAILED" or not access.get("work_id"):
            continue
        if str(access.get("provider") or "") not in {"publisher", "formal", "formal_proceedings_or_publisher"}:
            continue
        gap_id = _v3_stable_id("gap", "CONTENT_INACCESSIBLE", access.get("work_id"))
        if gap_id not in gap_by_id:
            errors.append(f"failed content access for {access.get('work_id')!r} lacks a backlog gap")
    for position, followup in enumerate(followups):
        if not isinstance(followup, Mapping):
            continue
        gap = gap_by_id.get(str(followup.get("gap_id") or ""))
        attempt = attempt_by_id.get(str(followup.get("attempt_id") or ""))
        if gap is None or gap.get("first_seen_run") == run_id:
            errors.append(f"Run.followup_attempts[{position}] must reference a pre-existing gap")
        if attempt is None or attempt.get("status") == "NOT_ATTEMPTED":
            errors.append(f"Run.followup_attempts[{position}] must reference a real current receipt")
        elif attempt.get("stage") != "FOLLOWUP":
            errors.append(
                f"Run.followup_attempts[{position}] must reference a FOLLOWUP receipt"
            )
        if gap is not None and followup.get("attempt_id") not in gap.get("receipt_ids", []):
            errors.append(f"Run.followup_attempts[{position}] receipt is absent from the gap ledger")
        if gap is not None:
            if followup.get("scope_type") != gap.get("scope_type") or followup.get("scope_id") != gap.get("scope_id"):
                errors.append(f"Run.followup_attempts[{position}] scope must equal its gap scope")
            compatible_triggers = {
                "SOURCE_UNAVAILABLE": {"PRIMARY_SOURCE_MISSING"},
                "CONTENT_INACCESSIBLE": {"FULLTEXT_MISSING", "FORMAL_VERSION_UNRESOLVED"},
                "IDENTITY_UNRESOLVED": {"IDENTIFIER_CONFLICT", "FORMAL_VERSION_UNRESOLVED", "PREPRINT_TO_VOR_CHECK"},
                "CLAIM_UNVERIFIED": {"POPULATION_GAP", "CLAIM_CONTRADICTION", "PRIMARY_SOURCE_MISSING"},
                "NUMERIC_CONFLICT": {"NUMERIC_CONFLICT", "POPULATION_GAP"},
            }
            if followup.get("trigger") not in compatible_triggers.get(str(gap.get("gap_type") or ""), set()):
                errors.append(f"Run.followup_attempts[{position}] trigger is incompatible with its gap")
            if gap.get("scope_type") == "WORK" and followup.get("parent_candidate_id") != gap.get("scope_id"):
                errors.append(f"Run.followup_attempts[{position}] must bind its parent candidate")
        if attempt is not None:
            expected_query = str(
                attempt.get("actual_query")
                or attempt.get("requested_query")
                or attempt.get("endpoint")
                or ""
            )
            for field, expected in (
                ("source_backend", str(attempt.get("source_id") or "")),
                ("attempted_at", str(attempt.get("attempted_at") or "")),
                ("result", str(attempt.get("status") or "")),
                ("query", expected_query),
            ):
                if followup.get(field) != expected:
                    errors.append(
                        f"Run.followup_attempts[{position}].{field} must equal its executor receipt"
                    )
            expected_outcome = (
                "RESOLVED"
                if attempt.get("status") in {"SUCCESS", "NO_RESULTS"}
                else "STILL_OPEN"
            )
            if followup.get("outcome") != expected_outcome:
                errors.append(f"Run.followup_attempts[{position}] outcome disagrees with its receipt")
            expected_resolved = [str(followup.get("gap_id"))] if expected_outcome == "RESOLVED" else []
            if followup.get("resolved_gap_ids") != expected_resolved:
                errors.append(f"Run.followup_attempts[{position}] resolved_gap_ids disagree with outcome")
        expected_followup_id = _v3_stable_id(
            "followup", followup.get("gap_id"), followup.get("attempt_id")
        )
        if followup.get("followup_id") != expected_followup_id:
            errors.append(f"Run.followup_attempts[{position}].followup_id is not stable")

    source_observations_by_id = observations_by_source
    binding_ids_by_claim: dict[str, list[str]] = {}
    for binding_id, binding in binding_by_id.items():
        claim_id = str(binding.get("claim_id") or "")
        source_id = str(binding.get("source_id") or "")
        binding_ids_by_claim.setdefault(claim_id, []).append(binding_id)
        claim = claim_by_id.get(claim_id)
        registry_entry = registry_by_id.get(source_id)
        if claim is None:
            errors.append(f"citation binding {binding_id!r} references unknown claim")
        if registry_entry is None:
            errors.append(f"citation binding {binding_id!r} references unknown stable source")
            continue
        if binding.get("source_url") != registry_entry.get("canonical_url"):
            errors.append(f"citation binding {binding_id!r} URL differs from source_registry")
        origin = str(binding.get("extraction_origin") or "")
        depth = str(binding.get("access_depth") or "")
        if origin == "MODEL_INFERENCE":
            errors.append(f"citation binding {binding_id!r} cannot use MODEL_INFERENCE")
        if origin == "FULLTEXT_EXTRACTED" and depth != "FULL_TEXT":
            errors.append(f"citation binding {binding_id!r} full-text origin requires FULL_TEXT depth")
        if origin == "ABSTRACT_EXTRACTED" and depth not in {"ABSTRACT", "FULL_TEXT"}:
            errors.append(f"citation binding {binding_id!r} abstract origin exceeds access depth")
        accessible_depths = {
            str(item.get("access_depth"))
            for item in source_observations_by_id.get(source_id, [])
            if item.get("access_outcome") == "ACCESSIBLE"
        }
        depth_rank = {"NONE": 0, "METADATA": 1, "LANDING_PAGE": 2, "ABSTRACT": 3, "FULL_TEXT": 4}
        if not any(depth_rank.get(value, -1) >= depth_rank.get(depth, 99) for value in accessible_depths):
            errors.append(f"citation binding {binding_id!r} exceeds observed accessible depth")

    role_compatibility = {
        "SCIENTIFIC_FINDING": {"PRIMARY_RESEARCH", "SYSTEMATIC_SYNTHESIS", "FORMAL_PUBLICATION"},
        "OFFICIAL_STATISTIC": {"OFFICIAL_AUTHORITY", "SYSTEMATIC_SYNTHESIS"},
        "ATTRIBUTION": {"SELF_STATEMENT", "SECONDARY_REPORT", "OFFICIAL_AUTHORITY", "FORMAL_PUBLICATION", "OTHER"},
        "POLICY_STATEMENT": {"OFFICIAL_AUTHORITY", "SELF_STATEMENT"},
        "BIBLIOGRAPHIC_FACT": {"FORMAL_PUBLICATION", "DISCOVERY_ONLY", "PRIMARY_RESEARCH", "SYSTEMATIC_SYNTHESIS"},
        "OTHER": {"PRIMARY_RESEARCH", "SYSTEMATIC_SYNTHESIS", "OFFICIAL_AUTHORITY", "FORMAL_PUBLICATION", "SECONDARY_REPORT", "SELF_STATEMENT", "OTHER"},
    }
    conflict_claim_ids = {
        str(claim_id)
        for group in conflict_groups
        if isinstance(group, Mapping)
        for claim_id in group.get("claim_ids", [])
    }
    for claim_id, claim in claim_by_id.items():
        for field in ("claim_kind", "claim_origin", "citation_binding_ids", "support_reason"):
            if field not in claim:
                errors.append(f"Evidence.claims[{claim_id}] is missing V3 field {field!r}")
        declared_bindings = claim.get("citation_binding_ids")
        actual_bindings = sorted(binding_ids_by_claim.get(claim_id, []))
        if not isinstance(declared_bindings, list) or sorted(declared_bindings) != actual_bindings:
            errors.append(f"Evidence.claims[{claim_id}] citation_binding_ids do not match bindings")
            continue
        claim_bindings = [binding_by_id[value] for value in actual_bindings]
        binding_source_ids = sorted({str(item.get("source_id") or "") for item in claim_bindings})
        if sorted(str(value) for value in claim.get("source_ids", [])) != binding_source_ids:
            errors.append(f"Evidence.claims[{claim_id}] source_ids do not match citation bindings")
        if claim.get("source_url") not in {item.get("source_url") for item in claim_bindings}:
            errors.append(f"Evidence.claims[{claim_id}] source_url is not bound")
        if claim.get("locator") not in {item.get("locator") for item in claim_bindings}:
            errors.append(f"Evidence.claims[{claim_id}] locator is not bound")
        kind = str(claim.get("claim_kind") or "OTHER")
        claim_origin = str(claim.get("claim_origin") or "")
        registry_claim = claim_registry_by_id.get(claim_id)
        expected_claim_hash = hashlib.sha256(
            str(claim.get("claim_text") or "").encode("utf-8")
        ).hexdigest()
        if registry_claim is None:
            errors.append(f"State.claim_registry is missing current claim {claim_id!r}")
        else:
            expected_registry_projection = {
                "work_id": str(claim.get("work_id") or ""),
                "claim_kind": kind,
                "claim_origin": claim_origin,
                "claim_text_sha256": expected_claim_hash,
                "status": str(claim.get("status") or ""),
                "source_ids": sorted(str(value) for value in claim.get("source_ids", [])),
                "status_binding_ids": sorted(
                    str(value) for value in claim.get("citation_binding_ids", [])
                ),
                "last_seen_run": run_id,
            }
            for field, expected in expected_registry_projection.items():
                observed = registry_claim.get(field)
                if field == "source_ids" and isinstance(observed, list):
                    observed = sorted(str(value) for value in observed)
                if observed != expected:
                    errors.append(
                        f"State.claim_registry[{claim_id}].{field} must project the current Evidence claim"
                    )
        binding_origins = {
            str(item.get("extraction_origin") or "") for item in claim_bindings
        }
        if claim_origin == "MODEL_INFERENCE":
            errors.append(
                f"Evidence.claims[{claim_id}] MODEL_INFERENCE belongs in Evidence.inferences"
            )
        elif claim_origin and claim_origin not in binding_origins:
            errors.append(
                f"Evidence.claims[{claim_id}] claim_origin is not represented by a citation binding"
            )
        if claim_origin == "METADATA_REPORTED" and kind not in {"BIBLIOGRAPHIC_FACT", "ATTRIBUTION"}:
            errors.append(
                f"Evidence.claims[{claim_id}] metadata origin cannot support this claim_kind"
            )
        compatible = [
            item
            for item in claim_bindings
            if registry_by_id.get(str(item.get("source_id") or ""), {}).get("source_role")
            in role_compatibility.get(kind, set())
        ]
        if claim.get("status") == "SUPPORTED":
            if not compatible:
                errors.append(f"Evidence.claims[{claim_id}] source role is incompatible with claim_kind")
            if kind not in {"BIBLIOGRAPHIC_FACT", "ATTRIBUTION"} and not any(
                item.get("extraction_origin") == "FULLTEXT_EXTRACTED"
                and item.get("access_depth") == "FULL_TEXT"
                and item.get("support_scope") in {"EXACT", "QUALIFIED"}
                for item in compatible
            ):
                errors.append(f"Evidence.claims[{claim_id}] SUPPORTED requires compatible full-text extraction")
            if kind not in {"BIBLIOGRAPHIC_FACT", "ATTRIBUTION"} and claim_origin != "FULLTEXT_EXTRACTED":
                errors.append(f"Evidence.claims[{claim_id}] SUPPORTED claim_origin must be FULLTEXT_EXTRACTED")
            if registry_claim is not None and registry_claim.get("last_status_change_run") == run_id:
                depth_rank = {"NONE": 0, "METADATA": 1, "LANDING_PAGE": 2, "ABSTRACT": 3, "FULL_TEXT": 4}
                has_current_support_event = any(
                    observation.get("run_id") == run_id
                    and observation.get("access_outcome") == "ACCESSIBLE"
                    and depth_rank.get(str(observation.get("access_depth") or "NONE"), -1)
                    >= depth_rank.get(str(binding.get("access_depth") or "NONE"), 99)
                    for binding in compatible
                    for observation in source_observations_by_id.get(
                        str(binding.get("source_id") or ""), []
                    )
                )
                if not has_current_support_event:
                    errors.append(
                        f"Evidence.claims[{claim_id}] status promotion requires a current accessible source observation"
                    )
        measurement = claim.get("measurement")
        numeric_text = re.search(
            r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)(?:%|‰)?",
            str(claim.get("claim_text") or ""),
        )
        if numeric_text and not isinstance(measurement, Mapping):
            errors.append(
                f"Evidence.claims[{claim_id}] visible numeric content requires structured measurement"
            )
        if isinstance(measurement, Mapping):
            for field in ("population", "denominator", "timeframe", "estimator", "method", "uncertainty"):
                if not isinstance(measurement.get(field), str) or not str(measurement[field]).strip():
                    errors.append(f"Evidence.claims[{claim_id}] numeric measurement lacks {field}")
            estimate_ids = claim.get("effect_estimate_ids")
            if not isinstance(estimate_ids, list) or not estimate_ids:
                errors.append(f"Evidence.claims[{claim_id}] numeric measurement requires effect_estimate_ids")
            else:
                for estimate_id in estimate_ids:
                    estimate = effect_by_id.get(str(estimate_id))
                    if estimate is None or estimate.get("claim_id") != claim_id:
                        errors.append(f"Evidence.claims[{claim_id}] references an invalid effect estimate")
        if claim.get("status") == "CONFLICT" and claim_id not in conflict_claim_ids:
            errors.append(f"Evidence.claims[{claim_id}] CONFLICT requires a conflict_group")
    effect_ids_by_claim: dict[str, list[str]] = {}
    for estimate_id, estimate in effect_by_id.items():
        estimate_claim_id = str(estimate.get("claim_id") or "")
        effect_ids_by_claim.setdefault(estimate_claim_id, []).append(estimate_id)
        estimate_claim = claim_by_id.get(estimate_claim_id)
        if estimate_claim is None:
            errors.append(f"effect estimate {estimate_id!r} references unknown claim")
        elif estimate_id not in (estimate_claim.get("effect_estimate_ids") or []):
            errors.append(
                f"effect estimate {estimate_id!r} is not declared by its claim"
            )
    for claim_id, claim in claim_by_id.items():
        declared_effects = sorted(
            str(value) for value in claim.get("effect_estimate_ids", [])
        )
        observed_effects = sorted(effect_ids_by_claim.get(claim_id, []))
        if declared_effects != observed_effects:
            errors.append(
                f"Evidence.claims[{claim_id}] effect_estimate_ids do not match the estimate ledger"
            )

    for conflict_id, group in conflict_by_id.items():
        claim_ids = [str(value) for value in group.get("claim_ids", [])]
        dimensions = [str(value) for value in group.get("dimensions", [])]
        if claim_ids != sorted(claim_ids):
            errors.append(f"conflict group {conflict_id!r} claim_ids must be sorted")
        if dimensions != sorted(dimensions):
            errors.append(f"conflict group {conflict_id!r} dimensions must be sorted")
        for claim_id in group.get("claim_ids", []):
            if str(claim_id) not in claim_by_id:
                errors.append(f"conflict group {conflict_id!r} references unknown claim")
        if group.get("status") == "RESOLVED" and not str(
            group.get("resolution") or ""
        ).strip():
            errors.append(
                f"conflict group {conflict_id!r} RESOLVED requires a resolution"
            )
    for position, inference in enumerate(inferences):
        if not isinstance(inference, Mapping):
            continue
        if inference.get("origin") != "MODEL_INFERENCE":
            errors.append(f"Evidence.inferences[{position}].origin must be MODEL_INFERENCE")
        if str(inference.get("claim_id") or "") not in claim_by_id:
            errors.append(f"Evidence.inferences[{position}] references unknown claim")
        for binding_id in inference.get("basis_binding_ids", []):
            if str(binding_id) not in binding_by_id:
                errors.append(f"Evidence.inferences[{position}] references unknown binding")

    for claim_id, registry_claim in claim_registry_by_id.items():
        if str(registry_claim.get("work_id") or "") not in state_work_ids:
            errors.append(f"State.claim_registry[{claim_id}] references unknown work")
        source_ids = registry_claim.get("source_ids")
        if not isinstance(source_ids, list) or source_ids != sorted(set(source_ids)):
            errors.append(f"State.claim_registry[{claim_id}].source_ids must be unique and sorted")
        for source_id in source_ids or []:
            if str(source_id) not in registry_by_id:
                errors.append(f"State.claim_registry[{claim_id}] references unknown source")
        binding_ids = registry_claim.get("status_binding_ids")
        if not isinstance(binding_ids, list) or binding_ids != sorted(set(binding_ids)):
            errors.append(f"State.claim_registry[{claim_id}].status_binding_ids must be unique and sorted")
        if registry_claim.get("last_seen_run") == run_id and claim_id not in claim_by_id:
            errors.append(
                f"State.claim_registry[{claim_id}] last_seen_run is current but Evidence.claims omits it"
            )

    relation_sets = (
        (state.get("work_relations", []), "relation_id", "from_work_id", "to_work_id", state_work_ids, "State.work_relations", "workrel"),
        (state.get("claim_relations", []), "relation_id", "from_claim_id", "to_claim_id", set(claim_registry_by_id), "State.claim_relations", "claimrel"),
    )
    for values, key, left, right, known_ids, label, prefix in relation_sets:
        relation_index = unique_index(list(values), key, label)
        for relation_id, relation in relation_index.items():
            if relation.get(left) == relation.get(right):
                errors.append(f"{label}[{relation_id}] cannot be self-referential")
            if (
                str(relation.get(left) or "") not in known_ids
                or str(relation.get(right) or "") not in known_ids
            ):
                errors.append(f"{label}[{relation_id}] references unknown endpoints")
            expected_relation_id = _v3_stable_id(
                prefix,
                relation.get(left),
                relation.get(right),
                relation.get("relation_type"),
            )
            if relation_id != expected_relation_id:
                errors.append(f"{label}[{relation_id}] relation_id is not stable")

    parser = DeliveryHtmlParser()
    try:
        parser.feed(report_html)
        parser.close()
    except Exception as exc:
        errors.append(f"V3 canonical Report HTML cannot be parsed: {exc}")
    claim_ids = sorted(claim_by_id)
    if sorted(parser.claim_ids) != claim_ids or len(parser.claim_ids) != len(set(parser.claim_ids)):
        errors.append("V3 Report claim markers must match Evidence.claims exactly")
    if parser.content_roles.count("substantive_claim") != len(claim_ids):
        errors.append("V3 Report must mark every substantive claim and no extras")
    claim_meta = parser.meta.get("evidenceradar-claim-count", [])
    if claim_meta != [str(len(claim_ids))]:
        errors.append("V3 Report claim-count meta must equal Evidence.claims")
    observed_hash = hashlib.sha256(report_html.encode("utf-8")).hexdigest()
    if run.get("report_sha256") != observed_hash:
        errors.append("Run.report_sha256 must bind the exact Report HTML bytes")
    rendering = run.get("rendering")
    if not isinstance(rendering, Mapping) or rendering.get("renderer_id") != "evidenceradar-html-v3":
        errors.append("Run.rendering must select evidenceradar-html-v3")
    else:
        try:
            from tools.run_github_radar import render_report_from_documents

            expected_html = render_report_from_documents(dict(run), dict(evidence))
        except Exception as exc:
            errors.append(f"V3 canonical renderer failed: {exc}")
        else:
            if expected_html != report_html:
                errors.append(
                    "V3 Report HTML is not the canonical byte-identical projection of Run + Evidence"
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
    require_semantic_contract_v3: bool = False,
) -> list[str]:
    """Validate decoded delivery values without touching the filesystem."""

    root = Path(root).resolve()
    errors: list[str] = []
    documents = {
        "EvidenceRadar_State.json": state,
        "EvidenceRadar_Evidence.json": evidence,
        "EvidenceRadar_Run.json": run,
    }
    if require_semantic_contract_v3 and not _v3_contract(run):
        errors.append("SEMANTIC_CONTRACT_V3 is required for this delivery")
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
    errors.extend(
        _v3_contract_errors(
            root=root,
            report_html=report_html,
            run=run,
            state=state,
            evidence=evidence,
        )
    )
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
    if manifest.get("format") != "evidenceradar-work-pack":
        errors.append("Work Pack manifest format must be evidenceradar-work-pack")
    if manifest.get("source_commit") != protocol_commit:
        errors.append("Run.protocol_commit must equal manifest.json source_commit")
    if reject_dirty and manifest.get("git_dirty") is not False:
        errors.append("public delivery rejects a dirty Work Pack manifest")
    records = manifest.get("files")
    if not isinstance(records, list):
        return errors + ["Work Pack manifest.files must be an array"]
    if not records:
        errors.append("Work Pack manifest.files must not be empty")
    if manifest.get("file_count") != len(records):
        errors.append("Work Pack manifest.file_count must equal len(files)")
    declared_paths = [
        str(item.get("path") or "")
        for item in records
        if isinstance(item, Mapping)
    ]
    if declared_paths != sorted(declared_paths):
        errors.append("Work Pack manifest files must use deterministic path order")
    if len(declared_paths) != len(set(declared_paths)):
        errors.append("Work Pack manifest contains duplicate file paths")
    missing_producer_paths = sorted(set(WORK_PRODUCER_PATHS) - set(declared_paths))
    if missing_producer_paths:
        errors.append(
            "Work Pack manifest omits required producer paths: "
            + ", ".join(missing_producer_paths)
        )
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
    require_semantic_contract_v3: bool = False,
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
        # Path.read_text uses universal-newline translation, which would make
        # CRLF bytes appear identical to the canonical LF renderer.  Decode
        # the raw bytes directly so report_sha256 and byte parity bind the
        # artifact that will actually be published.
        report_html = (bundle / "EvidenceRadar_Report.html").read_bytes().decode(
            "utf-8"
        )
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
            require_semantic_contract_v3=require_semantic_contract_v3,
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
    parser.add_argument("--require-semantic-contract-v3", action="store_true")
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
        require_semantic_contract_v3=args.require_semantic_contract_v3,
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
