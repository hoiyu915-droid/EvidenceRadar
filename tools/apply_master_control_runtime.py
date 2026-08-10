#!/usr/bin/env python3
"""Materialize the master-control integration into the legacy runner, fail closed.

This is a migration bridge: the authoritative source/taxonomy/query/profile data
lives in config/radar_master.json, while the existing runner remains byte-stable
in the repository.  The formal daily workflow applies this deterministic,
versioned transformation before executing the runner.  Every replacement is
counted; upstream drift aborts rather than silently falling back to legacy
hard-coded source routing.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


class RuntimePatchError(RuntimeError):
    pass


DISCOVERY_KEYS_BLOCK = '''DISCOVERY_ADAPTER_KEYS = {
    "pubmed",
    "europe_pmc",
    "openalex",
    "arxiv",
    "openreview",
    "acl_anthology",
    "pmlr",
    "rss_atom",
}
'''

FETCH_RSS_ATOM = r'''
def fetch_rss_atom(
    session: requests.Session,
    query: str,
    stream: str,
    category: str,
    start_date: date,
    end_date: date,
    max_results: int,
    *,
    source_id: str,
    source_config: dict[str, Any],
    cache: dict[str, Any],
) -> list[Candidate]:
    try:
        records = fetch_feed_records(
            session,
            source_id=source_id,
            source_config=source_config,
            query=query,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            cache=cache,
            user_agent=USER_AGENT,
        )
    except PublisherFeedError as exc:
        raise RadarRuntimeError(str(exc)) from exc
    candidates: list[Candidate] = []
    for record in records:
        publication_date = str(record.get("publication_date") or "")
        landing_url = str(record.get("landing_url") or record.get("feed_url") or "")
        events = []
        if publication_date:
            events.append(
                event_record(
                    "version_of_record_first_online",
                    publication_date,
                    source_id,
                    str(record.get("source_field") or "publisher_feed"),
                    landing_url,
                    "date",
                    "publisher_supplied_feed",
                )
            )
        candidates.append(
            Candidate(
                title=str(record.get("title") or ""),
                stream=stream,
                category=category,
                source=source_id,
                publication_date=publication_date,
                authors=[str(value) for value in record.get("authors", []) if str(value)],
                venue=str(record.get("venue") or ""),
                abstract=str(record.get("summary") or ""),
                doi=normalize_doi(str(record.get("doi") or "")),
                landing_url=landing_url,
                open_access=None,
                provider_publication_types=["journal article"],
                events=events,
            )
        )
    return candidates
'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimePatchError(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


def patch_runner_source(source: str) -> str:
    source = replace_once(
        source,
        '''from tools.translation_handoff import (\n    TranslationHandoffError,\n    load_and_validate_translation_response,\n    load_translation_request,\n    request_sha256,\n    write_translation_request,\n)\n''',
        '''from tools.translation_handoff import (\n    TranslationHandoffError,\n    load_and_validate_translation_response,\n    load_translation_request,\n    request_sha256,\n    write_translation_request,\n)\nfrom tools.publisher_feed import PublisherFeedError, fetch_feed_records\nfrom tools.radar_control import RadarControlError, load_master_runtime\nfrom tools.featured_selection import (\n    featured_policy_from_output,\n    featured_policy_note,\n    parse_featured_policy_note,\n    select_featured_work_ids_v2,\n)\n''',
        "master-control imports",
    )
    source = replace_once(
        source,
        'VERIFICATION_SOURCES = {"publisher", "formal_proceedings_or_publisher"}\n',
        'VERIFICATION_SOURCES = {"publisher", "formal_proceedings_or_publisher"}\n' + DISCOVERY_KEYS_BLOCK,
        "discovery adapter key registry",
    )
    source = replace_once(
        source,
        '\ndef score_candidate(candidate: Candidate, relevance_terms: Iterable[str]) -> int:\n',
        '\n' + FETCH_RSS_ATOM + '\n\ndef score_candidate(candidate: Candidate, relevance_terms: Iterable[str]) -> int:\n',
        "generic RSS/Atom adapter insertion",
    )
    source = replace_once(
        source,
        '''    adapters: dict[str, Callable[..., list[Candidate]]] = {\n        "pubmed": fetch_pubmed,\n        "europe_pmc": fetch_europe_pmc,\n        "openalex": fetch_openalex,\n        "arxiv": fetch_arxiv,\n        "openreview": fetch_openreview,\n        "acl_anthology": fetch_acl_anthology,\n        "pmlr": fetch_pmlr,\n    }\n''',
        '''    adapters: dict[str, Callable[..., list[Candidate]]] = {\n        "pubmed": fetch_pubmed,\n        "europe_pmc": fetch_europe_pmc,\n        "openalex": fetch_openalex,\n        "arxiv": fetch_arxiv,\n        "openreview": fetch_openreview,\n        "acl_anthology": fetch_acl_anthology,\n        "pmlr": fetch_pmlr,\n        "rss_atom": fetch_rss_atom,\n    }\n    source_catalog = {\n        str(source_id): dict(config)\n        for source_id, config in streams.get("source_catalog", {}).items()\n        if isinstance(config, dict)\n    }\n    verification_sources = set(\n        str(value)\n        for value in streams.get("source_check_contract", {}).get(\n            "bounded_verification_sources", []\n        )\n    ) | VERIFICATION_SOURCES\n''',
        "dynamic discovery adapter table",
    )
    source = replace_once(
        source,
        '                if discovery_source in VERIFICATION_SOURCES:\n                    continue\n',
        '                if discovery_source in verification_sources:\n                    continue\n',
        "verification-source skip",
    )
    source = replace_once(
        source,
        '''                fetcher = adapters.get(discovery_source)\n                if fetcher is None:\n                    error = f"No automated discovery adapter for configured source: {discovery_source}."\n''',
        '''                source_config = source_catalog.get(discovery_source, {})\n                adapter_key = str(source_config.get("adapter") or discovery_source)\n                fetcher = adapters.get(adapter_key)\n                if fetcher is None:\n                    error = (\n                        "No automated discovery adapter for configured source: "\n                        f"{discovery_source} (adapter={adapter_key})."\n                    )\n''',
        "source-id to adapter routing",
    )
    source = replace_once(
        source,
        '''                        if discovery_source == "arxiv":\n                            found = fetcher(*fetch_args, sleep=time.sleep)\n                        elif discovery_source in {"acl_anthology", "pmlr"}:\n                            found = fetcher(*fetch_args, cache=source_cache)\n                        else:\n                            found = fetcher(*fetch_args)\n''',
        '''                        if adapter_key == "arxiv":\n                            found = fetcher(*fetch_args, sleep=time.sleep)\n                        elif adapter_key in {"acl_anthology", "pmlr"}:\n                            found = fetcher(*fetch_args, cache=source_cache)\n                        elif adapter_key == "rss_atom":\n                            found = fetcher(\n                                *fetch_args,\n                                source_id=discovery_source,\n                                source_config=source_config,\n                                cache=source_cache,\n                            )\n                        else:\n                            found = fetcher(*fetch_args)\n''',
        "adapter-specific invocation",
    )
    source = replace_once(
        source,
        '''    publisher_hard_max: int | None = None,\n    protocol_commit: str | None = None,\n    translation_request_path: Path | None = None,\n''',
        '''    publisher_hard_max: int | None = None,\n    protocol_commit: str | None = None,\n    profile_id: str | None = None,\n    translation_request_path: Path | None = None,\n''',
        "execute profile argument",
    )
    source = replace_once(
        source,
        '''    streams = load_yaml(root / "config" / "streams.yml")\n    scoring = load_yaml(root / "config" / "scoring.yml")\n    output = load_yaml(root / "config" / "output.yml")\n    deployment = load_yaml(root / "config" / "deployment.yml")\n''',
        '''    output = load_yaml(root / "config" / "output.yml")\n    deployment = load_yaml(root / "config" / "deployment.yml")\n    master_path = root / "config" / "radar_master.json"\n    if master_path.exists():\n        try:\n            master_runtime = load_master_runtime(master_path, profile_id=profile_id)\n        except RadarControlError as exc:\n            raise RadarRuntimeError(f"invalid radar master control: {exc}") from exc\n        streams = master_runtime.streams\n        scoring = master_runtime.scoring\n        SOURCE_ENDPOINTS.update(master_runtime.source_endpoints)\n        output.setdefault("selection", {})["categories"] = master_runtime.category_order\n    else:\n        streams = load_yaml(root / "config" / "streams.yml")\n        scoring = load_yaml(root / "config" / "scoring.yml")\n''',
        "master runtime configuration loading",
    )
    source = replace_once(
        source,
        '    verification_requested = requested_sources & VERIFICATION_SOURCES\n',
        '''    configured_verification_sources = set(\n        str(value)\n        for value in streams.get("source_check_contract", {}).get(\n            "bounded_verification_sources", []\n        )\n    ) | VERIFICATION_SOURCES\n    verification_requested = requested_sources & configured_verification_sources\n''',
        "dynamic verification source set",
    )
    source = replace_once(
        source,
        '''    adapter_sources = {\n        "pubmed",\n        "europe_pmc",\n        "openalex",\n        "arxiv",\n        "openreview",\n        "acl_anthology",\n        "pmlr",\n    }\n    unsupported = requested_sources - adapter_sources - VERIFICATION_SOURCES\n''',
        '''    adapter_sources = {\n        str(source_id)\n        for source_id, source_config in streams.get("source_catalog", {}).items()\n        if isinstance(source_config, dict)\n        and str(source_config.get("adapter") or source_id) in DISCOVERY_ADAPTER_KEYS\n    }\n    unsupported = requested_sources - adapter_sources - configured_verification_sources\n''',
        "dynamic adapter coverage set",
    )
    source = replace_once(
        source,
        '    parser.add_argument("--protocol-commit")\n',
        '''    parser.add_argument("--protocol-commit")\n    parser.add_argument(\n        "--profile",\n        help="Profile id from config/radar_master.json (defaults to control_plane.default_profile)",\n    )\n''',
        "profile CLI",
    )
    source = replace_once(
        source,
        '            protocol_commit=args.protocol_commit,\n',
        '            protocol_commit=args.protocol_commit,\n            profile_id=args.profile,\n',
        "profile execute forwarding",
    )
    source = replace_once(
        source,
        'def select_featured_work_ids(\n    candidate_records: list[dict[str, Any]],\n    *,\n    target_per_category: int = 5,\n    hard_max_per_category: int = 8,\n    excluded_event_classes: set[str] | None = None,\n) -> set[str]:\n    """Select a readable daily digest without dropping the full ledger."""\n\n    target = max(1, int(target_per_category))\n    hard_max = max(target, int(hard_max_per_category))\n    excluded_event_classes = excluded_event_classes or {\n        "BACKFILL_INDEXING",\n        "CORRECTION_NOTICE",\n    }\n    by_category: dict[str, list[dict[str, Any]]] = {}\n    for item in candidate_records:\n        by_category.setdefault(str(item.get("category") or ""), []).append(item)\n    triage_rank = {"PRIORITY": 0, "REVIEW_REQUIRED": 1, "LOWER_PRIORITY": 2}\n    selected: set[str] = set()\n    for items in by_category.values():\n        eligible = [\n            item\n            for item in items\n            if str(item.get("event_status") or "") != "NO_QUALIFYING_EVENT"\n            and str(item.get("event_class") or "OTHER") not in excluded_event_classes\n        ]\n        eligible.sort(\n            key=lambda item: (\n                triage_rank.get(str(item.get("triage_status") or ""), 3),\n                -int(item.get("routing_score") or 0),\n                str(item.get("work_id") or ""),\n            )\n        )\n        preferred = [\n            item\n            for item in eligible\n            if str(item.get("triage_status") or "") in {"PRIORITY", "REVIEW_REQUIRED"}\n        ]\n        chosen = preferred[:hard_max]\n        if len(chosen) < target:\n            chosen.extend(eligible[len(chosen) : target])\n        selected.update(str(item.get("work_id")) for item in chosen if item.get("work_id"))\n    return selected\n',
        'def select_featured_work_ids(\n    candidate_records: list[dict[str, Any]],\n    *,\n    target_per_category: int = 5,\n    hard_max_per_category: int = 8,\n    excluded_event_classes: set[str] | None = None,\n    featured_policy: dict[str, Any] | None = None,\n) -> set[str]:\n    """Select a readable digest without truncating the complete ledger."""\n\n    return select_featured_work_ids_v2(\n        candidate_records,\n        target_per_category=target_per_category,\n        hard_max_per_category=hard_max_per_category,\n        excluded_event_classes=excluded_event_classes,\n        policy=featured_policy,\n    )\n',
        'featured selection policy v2 wrapper',
    )
    source = replace_once(
        source,
        '    featured_hard_max_per_category: int = 8,\n    featured_excluded_event_classes: set[str] | None = None,\n) -> str:\n',
        '    featured_hard_max_per_category: int = 8,\n    featured_excluded_event_classes: set[str] | None = None,\n    featured_policy: dict[str, Any] | None = None,\n) -> str:\n',
        'render featured policy argument',
    )
    source = replace_once(
        source,
        '        hard_max_per_category=featured_hard_max_per_category,\n        excluded_event_classes=featured_excluded_event_classes,\n    )\n',
        '        hard_max_per_category=featured_hard_max_per_category,\n        excluded_event_classes=featured_excluded_event_classes,\n        featured_policy=featured_policy,\n    )\n',
        'render featured policy selection',
    )
    source = replace_once(
        source,
        '<p class="panel-intro">今日精選只接受本輪 {window_hours} 小時內有合格事件的候選；每類再依閱讀層級與分數顯示約 {featured_target_per_category}–{featured_hard_max_per_category} 項。完整去重候選仍保留在收合的完整池，可搜尋、展開與依事件分流；無合格事件者不會被標成今日優先。分數只協助閱讀順序，不代表研究價值。</p>\n',
        '<p class="panel-intro">今日精選只接受本輪 {window_hours} 小時內有合格事件的候選；閱讀量由 profile 的每類 target/hard、ranking pool 與 final digest budget 控制。完整去重候選仍保留在收合的完整池，可搜尋、展開與依事件分流；無合格事件者不會被標成今日優先。分數只協助閱讀順序，不代表研究價值。</p>\n',
        'report digest policy prose',
    )
    source = replace_once(
        source,
        '        featured_excluded_event_classes=set(\n            str(value) for value in rendering.get("featured_excluded_event_classes", [])\n        ),\n    )\n',
        '        featured_excluded_event_classes=set(\n            str(value) for value in rendering.get("featured_excluded_event_classes", [])\n        ),\n        featured_policy=parse_featured_policy_note(run.get("notes", [])),\n    )\n',
        'canonical renderer policy replay',
    )
    source = replace_once(
        source,
        '    featured_target = int(featured_config.get("target_min", 5))\n    featured_hard_max = int(featured_config.get("hard_max", 8))\n    candidate_pool_config = selection_config.get("candidate_pool", {})\n',
        '    featured_target = int(featured_config.get("target_min", 5))\n    featured_hard_max = int(featured_config.get("hard_max", 8))\n    featured_policy = featured_policy_from_output(\n        selection_config,\n        default_target_per_category=featured_target,\n        default_hard_max_per_category=featured_hard_max,\n    )\n    candidate_pool_config = selection_config.get("candidate_pool", {})\n',
        'resolve featured selection policy',
    )
    source = replace_once(
        source,
        '        hard_max_per_category=featured_hard_max,\n        excluded_event_classes=excluded_featured_classes,\n    )\n',
        '        hard_max_per_category=featured_hard_max,\n        excluded_event_classes=excluded_featured_classes,\n        featured_policy=featured_policy,\n    )\n',
        'execute featured selection policy',
    )
    source = replace_once(
        source,
        '            "STUDY_CLASSIFICATION_V1",\n            *( ["CHATBOT_TRANSLATION_HANDOFF_V1"] if handoff_request is not None else [] ),\n',
        '            "STUDY_CLASSIFICATION_V1",\n            featured_policy_note(featured_policy),\n            f"RADAR_PROFILE:{streams.get(\'control_plane\', {}).get(\'profile_id\', \'legacy\')}",\n            *( ["CHATBOT_TRANSLATION_HANDOFF_V1"] if handoff_request is not None else [] ),\n',
        'persist featured policy and profile notes',
    )
    return source


def patch_runner(path: Path, *, check_only: bool = False) -> bool:
    original = path.read_text(encoding="utf-8")
    patched = patch_runner_source(original)
    if patched == original:
        raise RuntimePatchError("runner patch produced no change")
    if not check_only:
        path.write_text(patched, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        patch_runner(args.runner, check_only=args.check)
    except (OSError, RuntimePatchError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
