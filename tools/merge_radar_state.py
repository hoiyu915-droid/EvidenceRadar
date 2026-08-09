#!/usr/bin/env python3
"""Merge EvidenceRadar State artifacts from independent execution lanes.

The active radar can be run from more than one lane.  A lane therefore writes
an observed state against a possibly stale base instead of assuming that its
read and write happen under a repository lock.  This module provides the
small, deterministic merge primitive used by both lanes.  It intentionally
uses only the Python standard library so that it is also useful in a fresh
ChatGPT Work project.

The merge is a union, never a replacement:

* works are matched by the configured identifier priority and then by a
  normalized title when that fallback is unambiguous;
* event identifiers are an idempotency key;
* observation bounds use the earliest ``first_seen_at`` and latest
  ``last_seen_at`` while counts use the greatest observed count; and
* list-valued provenance is sorted and de-duplicated before serialization.

``merge_states`` returns a new object and never mutates either input.  The CLI
uses an atomic replace for its output file, which keeps a reader from seeing a
partially-written JSON document during concurrent lane runs.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _datetime
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


JsonObject = dict[str, Any]

_IDENTIFIER_FIELDS = (
    "doi",
    "pmid",
    "pmcid",
    "arxiv_id",
    "anthology_id",
    "openalex_id",
)
_DEFAULT_DEDUPE_PRIORITY = _IDENTIFIER_FIELDS + ("normalized_title",)
_ALLOWED_LANES = {"github_actions", "chatgpt_work"}
_STATE_REQUIRED = {
    "schema_version",
    "artifact_type",
    "generated_at",
    "timezone",
    "history_status",
    "dedupe_priority",
    "works",
    "notified_events",
}


class StateMergeError(ValueError):
    """Raised when two state documents cannot be merged safely."""


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used for hashes and ties."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_sha256(state: Mapping[str, Any]) -> str:
    """Return a SHA-256 digest of a state document in canonical JSON form."""

    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def _parse_datetime(value: Any, *, field: str) -> _datetime.datetime:
    if not isinstance(value, str) or not value.strip():
        raise StateMergeError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = _datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateMergeError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise StateMergeError(f"{field} must include a timezone: {value!r}")
    return parsed


def _canonical_text(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _canonical_identifier(field: str, value: Any) -> str:
    """Normalize identifiers for comparison without changing stored values."""

    text = _canonical_text(value)
    if field == "doi":
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
    return text.rstrip(".;,")


def _dedupe_priority(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> list[str]:
    """Combine priority declarations while retaining their first-seen order."""

    result: list[str] = []
    for state in (base, incoming):
        priority = state.get("dedupe_priority", _DEFAULT_DEDUPE_PRIORITY)
        if not isinstance(priority, list) or not priority:
            raise StateMergeError("dedupe_priority must be a non-empty list")
        for field in priority:
            if field not in _DEFAULT_DEDUPE_PRIORITY:
                raise StateMergeError(f"unsupported dedupe priority field: {field!r}")
            if field not in result:
                result.append(field)
    return result or list(_DEFAULT_DEDUPE_PRIORITY)


def _require_state(state: Mapping[str, Any], label: str) -> None:
    if not isinstance(state, Mapping):
        raise StateMergeError(f"{label} must be a JSON object")
    missing = sorted(_STATE_REQUIRED.difference(state))
    if missing:
        raise StateMergeError(f"{label} is missing required fields: {', '.join(missing)}")
    if state.get("artifact_type") != "EvidenceRadar_State":
        raise StateMergeError(f"{label}.artifact_type must be EvidenceRadar_State")
    if state.get("schema_version") != "1.0":
        raise StateMergeError(f"{label}.schema_version must be 1.0")
    if not isinstance(state.get("works"), list):
        raise StateMergeError(f"{label}.works must be a list")
    if not isinstance(state.get("notified_events"), list):
        raise StateMergeError(f"{label}.notified_events must be a list")
    _parse_datetime(state.get("generated_at"), field=f"{label}.generated_at")
    timezone = state.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        raise StateMergeError(f"{label}.timezone must be a non-empty string")
    for index, work in enumerate(state["works"]):
        if not isinstance(work, Mapping):
            raise StateMergeError(f"{label}.works[{index}] must be an object")
        for field in ("work_id", "title", "normalized_title", "first_seen_at", "last_seen_at"):
            if not isinstance(work.get(field), str) or not work[field].strip():
                raise StateMergeError(f"{label}.works[{index}].{field} must be non-empty")
        _parse_datetime(work["first_seen_at"], field=f"{label}.works[{index}].first_seen_at")
        _parse_datetime(work["last_seen_at"], field=f"{label}.works[{index}].last_seen_at")
        if not isinstance(work.get("identifiers"), Mapping):
            raise StateMergeError(f"{label}.works[{index}].identifiers must be an object")
    for index, event in enumerate(state["notified_events"]):
        if not isinstance(event, Mapping):
            raise StateMergeError(f"{label}.notified_events[{index}] must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise StateMergeError(f"{label}.notified_events[{index}].event_id must be non-empty")


def _work_tokens(work: Mapping[str, Any], priority: Sequence[str]) -> dict[str, str]:
    identifiers = work.get("identifiers") or {}
    tokens: dict[str, str] = {}
    for field in priority:
        value = work.get("normalized_title") if field == "normalized_title" else identifiers.get(field)
        if value is None or not str(value).strip():
            continue
        token = _canonical_identifier(field, value)
        if token:
            tokens[field] = token
    return tokens


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        # Keeping the lower root makes the result independent of input order
        # after components have been sorted for serialization.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root


def _identities_match_on_title(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    """Return whether title fallback is safe for a pair of records."""

    for field in _IDENTIFIER_FIELDS:
        left_value, right_value = left.get(field), right.get(field)
        if left_value and right_value and left_value != right_value:
            return False
    return True


def _group_work_indexes(
    works: Sequence[Mapping[str, Any]], priority: Sequence[str]
) -> tuple[_UnionFind, list[dict[str, str]]]:
    tokens = [_work_tokens(work, priority) for work in works]
    union = _UnionFind(len(works))
    component_values: list[dict[str, set[str]]] = [
        {
            field: {value}
            for field, value in item.items()
            if field in _IDENTIFIER_FIELDS
        }
        for item in tokens
    ]

    def union_checked(left: int, right: int, *, matched_on: str) -> None:
        left_root, right_root = union.find(left), union.find(right)
        if left_root == right_root:
            return
        for field in _IDENTIFIER_FIELDS:
            left_values = component_values[left_root].get(field, set())
            right_values = component_values[right_root].get(field, set())
            if left_values and right_values and left_values != right_values:
                raise StateMergeError(
                    "conflicting strong identifiers would be transitively merged "
                    f"on {matched_on}: {field}={sorted(left_values)!r} vs "
                    f"{sorted(right_values)!r}"
                )
        merged_values: dict[str, set[str]] = {}
        for field in _IDENTIFIER_FIELDS:
            values = component_values[left_root].get(field, set()) | component_values[right_root].get(field, set())
            if values:
                merged_values[field] = values
        union.union(left_root, right_root)
        merged_root = union.find(left_root)
        discarded_root = right_root if merged_root == left_root else left_root
        component_values[merged_root] = merged_values
        component_values[discarded_root] = {}

    # Strong identifiers are safe to union directly.  The priority controls
    # lookup order and tie-breaking, while every declared identifier remains a
    # usable alias for an observed work.
    by_identifier: dict[tuple[str, str], int] = {}
    for index, item in enumerate(tokens):
        for field in priority:
            if field == "normalized_title":
                continue
            token = item.get(field)
            if token is None:
                continue
            key = (field, token)
            previous = by_identifier.get(key)
            if previous is None:
                by_identifier[key] = index
            else:
                union_checked(previous, index, matched_on=field)

    # Title fallback is only used when it cannot bridge two independently
    # identified works.  This avoids merging two different DOI records merely
    # because a short title happens to be identical, while still allowing a
    # title-only stale record to join the one identified component it matches.
    by_title: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(tokens):
        title = item.get("normalized_title")
        if title:
            by_title[title].append(index)
    for indexes in by_title.values():
        components = {union.find(index) for index in indexes if any(tokens[index].get(f) for f in _IDENTIFIER_FIELDS)}
        if len(components) > 1:
            # Conflicting identifiers under one title are retained separately.
            continue
        if len(components) == 1:
            anchor = next(iter(components))
            for index in indexes:
                if _identities_match_on_title(tokens[anchor], tokens[index]):
                    union_checked(anchor, index, matched_on="normalized_title")
        else:
            anchor = indexes[0]
            for index in indexes[1:]:
                union_checked(anchor, index, matched_on="normalized_title")
    return union, tokens


def _stable_unique(values: Iterable[Any], *, text: bool = False) -> list[Any]:
    seen: dict[str, Any] = {}
    for value in values:
        if value is None:
            continue
        key = _canonical_text(value) if text and isinstance(value, str) else canonical_json(value)
        previous = seen.get(key)
        if previous is None or canonical_json(value) < canonical_json(previous):
            seen[key] = value
    return [seen[key] for key in sorted(seen)]


def _time_key(value: Any, fallback: str = "") -> tuple[float, str]:
    try:
        return (_parse_datetime(value, field="timestamp").timestamp(), str(value))
    except StateMergeError:
        return (float("-inf"), fallback)


def _latest_value(records: Sequence[Mapping[str, Any]], field: str) -> Any:
    candidates = [record for record in records if record.get(field) not in (None, "")]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda record: (
            _time_key(record.get("last_seen_at")),
            _canonical_text(record.get(field)),
            canonical_json(record.get(field)),
        ),
    ).get(field)


def _merge_work(records: Sequence[Mapping[str, Any]], tokens: Sequence[Mapping[str, str]], priority: Sequence[str]) -> JsonObject:
    first = min(records, key=lambda record: (_time_key(record.get("first_seen_at")), str(record.get("first_seen_at"))))
    last = max(records, key=lambda record: (_time_key(record.get("last_seen_at")), str(record.get("last_seen_at"))))

    identifier_values: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        identifiers = record.get("identifiers") or {}
        for field, value in identifiers.items():
            if value not in (None, ""):
                identifier_values[str(field)].append(value)
    identifiers: dict[str, Any] = {}
    for field in sorted(identifier_values):
        values = identifier_values[field]
        canonical_values = sorted(
            values,
            key=lambda value: (_canonical_identifier(field, value), str(value)),
        )
        identifiers[field] = canonical_values[0]

    title = _latest_value(records, "title") or str(first.get("title"))
    normalized_title = _latest_value(records, "normalized_title") or _canonical_text(title)
    work_ids = sorted({str(record.get("work_id")) for record in records if record.get("work_id")})
    # Prefer the strongest canonical identity when it is already represented
    # as a work_id; otherwise choose the lexical id so reversed inputs produce
    # byte-identical output.
    identity_candidates: list[str] = []
    for field in priority:
        value = next((tokens[index].get(field) for index, record in enumerate(records) if tokens[index].get(field)), None)
        if value:
            identity_candidates.append(f"{field}:{value}")
    represented_identities = [candidate for candidate in identity_candidates if candidate in work_ids]
    if represented_identities:
        work_id = represented_identities[0]
    elif work_ids:
        work_id = min(work_ids)
    elif identity_candidates:
        work_id = identity_candidates[0]
    else:
        work_id = f"title:{_canonical_text(normalized_title)}"

    merged: JsonObject = {
        "work_id": work_id,
        "title": title,
        "normalized_title": normalized_title,
        "identifiers": identifiers,
        "first_seen_at": first["first_seen_at"],
        "last_seen_at": last["last_seen_at"],
        "seen_count": max(int(record.get("seen_count", 1) or 1) for record in records),
        "notified_event_ids": _stable_unique(
            (event_id for record in records for event_id in (record.get("notified_event_ids") or [])),
            text=True,
        ),
    }

    list_fields = (
        "streams",
        "source_urls",
        "repository_versions",
        "notes",
        "download_urls",
        "oa_evidence",
    )
    for field in list_fields:
        values = [value for record in records for value in (record.get(field) or [])]
        if values:
            merged[field] = _stable_unique(values, text=True)
    category = _latest_value(records, "category")
    if category is not None:
        merged["category"] = category
    for field in ("open_access", "is_preprint"):
        values = [record[field] for record in records if isinstance(record.get(field), bool)]
        if values:
            merged[field] = any(values)

    # OA is a publication/repository property and must never be inferred from
    # whether this particular run could open the full text.  Preserve a
    # positive observation across stale branches while retaining every
    # evidence record.  The legacy boolean is accepted as an upgrade input.
    oa_values = [
        str(record.get("oa_status"))
        for record in records
        if record.get("oa_status") in {"YES", "NO", "UNKNOWN"}
    ]
    legacy_oa = [
        record["open_access"]
        for record in records
        if isinstance(record.get("open_access"), bool)
    ]
    if "YES" in oa_values or any(legacy_oa):
        merged["oa_status"] = "YES"
    elif "NO" in oa_values or any(value is False for value in legacy_oa):
        merged["oa_status"] = "NO"
    else:
        merged["oa_status"] = "UNKNOWN"

    # Access/full-text fields describe observations, not a permanent property.
    # Merge locations by URL and let an actual probe outrank NOT_CHECKED even
    # when a stale branch was generated later.  This prevents an unprobed run
    # from erasing a prior ACCESSIBLE/BLOCKED observation.
    location_candidates: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for record in records:
        locations = record.get("fulltext_locations")
        if not isinstance(locations, list):
            continue
        for location in locations:
            if not isinstance(location, Mapping):
                continue
            url = location.get("url")
            if isinstance(url, str) and url.strip():
                location_candidates[url.strip()].append((record, location))
    merged_locations: list[JsonObject] = []
    for url in sorted(location_candidates):
        candidates = location_candidates[url]
        probed = [
            item
            for item in candidates
            if item[1].get("access_status") not in (None, "", "NOT_CHECKED", "UNKNOWN")
        ]
        pool = probed or candidates
        _record, location = max(
            pool,
            key=lambda item: (
                _time_key(item[0].get("last_seen_at")),
                canonical_json(item[1]),
            ),
        )
        merged_locations.append(copy.deepcopy(dict(location)))
    merged["fulltext_locations"] = merged_locations

    actual_access_records = [
        record
        for record in records
        if record.get("access_status") not in (None, "", "NOT_CHECKED", "UNKNOWN")
    ]
    access_status = _latest_value(actual_access_records or records, "access_status")
    merged["access_status"] = access_status or "NOT_CHECKED"

    location_statuses = {
        str(location.get("access_status"))
        for location in merged_locations
        if location.get("access_status") not in (None, "", "NOT_CHECKED", "UNKNOWN")
    }
    if merged["access_status"] not in {"NOT_CHECKED", "UNKNOWN"}:
        location_statuses.add(str(merged["access_status"]))
    if len(location_statuses) > 1:
        merged["fulltext_access_status"] = "MIXED"
    elif location_statuses:
        merged["fulltext_access_status"] = next(iter(location_statuses))
    elif merged_locations:
        merged["fulltext_access_status"] = "NOT_CHECKED"
    else:
        merged["fulltext_access_status"] = "UNKNOWN"

    location_kinds = {str(location.get("kind")) for location in merged_locations}
    if "PDF" in location_kinds:
        merged["fulltext_kind"] = "PDF"
    elif "REPOSITORY" in location_kinds:
        merged["fulltext_kind"] = "REPOSITORY"
    elif "HTML" in location_kinds:
        merged["fulltext_kind"] = "HTML"
    else:
        fulltext_kind = _latest_value(records, "fulltext_kind")
        merged["fulltext_kind"] = fulltext_kind or "UNKNOWN"
    merged.setdefault("download_urls", [])
    merged.setdefault("oa_evidence", [])
    return merged


def _merge_event_candidates(candidates: Sequence[Mapping[str, Any]]) -> JsonObject:
    """Choose one deterministic event payload for an idempotent event_id."""

    immutable_fields = (
        "work_id",
        "event_type",
        "occurred_at",
        "source",
        "source_url",
        "source_field",
        "precision",
        "confidence",
    )
    for field in immutable_fields:
        values = {
            canonical_json(candidate.get(field))
            for candidate in candidates
        }
        if len(values) > 1:
            event_id = candidates[0].get("event_id", "<unknown>")
            raise StateMergeError(
                f"event_id collision for {event_id!r}: immutable field {field!r} conflicts"
            )

    return copy.deepcopy(
        max(
            candidates,
            key=lambda event: (
                _time_key(event.get("notified_at")),
                canonical_json(event),
            ),
        )
    )


def _merge_provenance(
    base: Mapping[str, Any],
    incoming: Mapping[str, Any],
    *,
    provenance: Mapping[str, Any] | None,
    execution_lane: str | None,
    protocol_commit: str | None,
    parent_run_ids: Iterable[str],
) -> JsonObject:
    # The public artifact contract keeps provenance fields at the top level so
    # a Work author can inspect them without dereferencing a second object.
    # Reading an older nested object here is harmless and lets a stale branch
    # participate in a merge while it is being upgraded.
    base_provenance = base.get("provenance") if isinstance(base.get("provenance"), Mapping) else base
    incoming_provenance = incoming.get("provenance") if isinstance(incoming.get("provenance"), Mapping) else incoming
    supplied = provenance if isinstance(provenance, Mapping) else {}

    lane = execution_lane or supplied.get("execution_lane") or incoming_provenance.get("execution_lane") or base_provenance.get("execution_lane") or "chatgpt_work"
    if lane not in _ALLOWED_LANES:
        raise StateMergeError(f"execution_lane must be one of {sorted(_ALLOWED_LANES)!r}")
    commit = protocol_commit or supplied.get("protocol_commit") or incoming_provenance.get("protocol_commit") or base_provenance.get("protocol_commit") or "unknown"
    if not isinstance(commit, str) or not commit.strip():
        raise StateMergeError("protocol_commit must be a non-empty string")

    parents: list[str] = []
    for source in (base_provenance, incoming_provenance, supplied):
        value = source.get("parent_run_ids")
        if isinstance(value, list):
            parents.extend(str(item) for item in value if str(item).strip())
    for state in (base, incoming):
        run_id = state.get("last_run_id")
        if isinstance(run_id, str) and run_id.strip():
            parents.append(run_id)
    parents.extend(str(item) for item in parent_run_ids if str(item).strip())
    return {
        "execution_lane": lane,
        "protocol_commit": commit,
        "base_state_sha256": state_sha256(base),
        "parent_run_ids": _stable_unique(parents, text=True),
    }


def merge_states(
    base_state: Mapping[str, Any],
    incoming_state: Mapping[str, Any],
    *,
    provenance: Mapping[str, Any] | None = None,
    execution_lane: str | None = None,
    protocol_commit: str | None = None,
    parent_run_ids: Iterable[str] = (),
) -> JsonObject:
    """Return the deterministic union of two EvidenceRadar State artifacts.

    ``base_state`` may be stale.  It is deliberately not treated as a version
    check: every work and event from both inputs participates in the union.
    """

    _require_state(base_state, "base_state")
    _require_state(incoming_state, "incoming_state")
    if base_state.get("timezone") != incoming_state.get("timezone"):
        raise StateMergeError("cannot merge state artifacts with different timezones")

    base = copy.deepcopy(dict(base_state))
    incoming = copy.deepcopy(dict(incoming_state))
    priority = _dedupe_priority(base, incoming)
    works = list(base.get("works", [])) + list(incoming.get("works", []))
    union, tokens = _group_work_indexes(works, priority)
    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(works)):
        components[union.find(index)].append(index)

    merged_works: list[JsonObject] = []
    work_id_map: dict[str, str] = {}
    component_sort: list[tuple[str, int, JsonObject]] = []
    for indexes in components.values():
        records = [works[index] for index in indexes]
        merged = _merge_work(records, [tokens[index] for index in indexes], priority)
        for record in records:
            old_id = record.get("work_id")
            if isinstance(old_id, str) and old_id:
                work_id_map[old_id] = merged["work_id"]
        identity = min(
            (tokens[index].get(field, "") for index in indexes for field in priority if tokens[index].get(field)),
            default=_canonical_text(merged["work_id"]),
        )
        component_sort.append((identity, min(indexes), merged))
    for _, _, merged in sorted(component_sort, key=lambda item: (item[0], item[2]["work_id"])):
        merged_works.append(merged)

    event_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in list(base.get("notified_events", [])) + list(incoming.get("notified_events", [])):
        item = copy.deepcopy(dict(event))
        old_work_id = item.get("work_id")
        if isinstance(old_work_id, str) and old_work_id in work_id_map:
            item["work_id"] = work_id_map[old_work_id]
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id:
            event_by_id[event_id].append(item)
    merged_events = [_merge_event_candidates(event_by_id[event_id]) for event_id in sorted(event_by_id)]

    # Event references are part of the state index as well.  Adding them here
    # keeps a stale branch from deleting a notification id that another lane
    # already observed.
    events_by_work: dict[str, list[str]] = defaultdict(list)
    for event in merged_events:
        event_work_id = event.get("work_id")
        event_id = event.get("event_id")
        if isinstance(event_work_id, str) and isinstance(event_id, str):
            events_by_work[event_work_id].append(event_id)
    for work in merged_works:
        work["notified_event_ids"] = _stable_unique(
            list(work.get("notified_event_ids", [])) + events_by_work.get(work["work_id"], []),
            text=True,
        )

    generated_candidates = [base, incoming]
    generated = max(
        generated_candidates,
        key=lambda state: (
            _time_key(state.get("generated_at")),
            str(state.get("last_run_id") or ""),
            canonical_json(state),
        ),
    )
    history_status = (
        "STATE_HISTORY_INCOMPLETE"
        if base.get("history_status") == "STATE_HISTORY_INCOMPLETE"
        or incoming.get("history_status") == "STATE_HISTORY_INCOMPLETE"
        else generated.get("history_status", "COMPLETE")
    )
    history_notes = _stable_unique(
        [state.get("history_note") for state in (base, incoming) if state.get("history_note")],
        text=True,
    )
    last_run_id = generated.get("last_run_id")
    if not isinstance(last_run_id, str) or not last_run_id.strip():
        run_ids = [state.get("last_run_id") for state in (base, incoming) if state.get("last_run_id")]
        last_run_id = sorted(str(run_id) for run_id in run_ids)[-1] if run_ids else None

    result: JsonObject = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_State",
        "generated_at": generated["generated_at"],
        "timezone": base["timezone"],
        "history_status": history_status,
        "dedupe_priority": priority,
        "works": merged_works,
        "notified_events": merged_events,
    }
    result.update(_merge_provenance(
            base,
            incoming,
            provenance=provenance,
            execution_lane=execution_lane,
            protocol_commit=protocol_commit,
            parent_run_ids=parent_run_ids,
        ))
    if history_notes:
        result["history_note"] = " | ".join(str(note) for note in history_notes)
    if isinstance(last_run_id, str) and last_run_id:
        result["last_run_id"] = last_run_id
    notes = _stable_unique(
        [note for state in (base, incoming) for note in (state.get("notes") or [])],
        text=True,
    )
    if notes:
        result["notes"] = notes
    return result


# Friendly aliases for callers that describe the operation as a document
# merge rather than a state merge.
merge_state = merge_states
merge_state_documents = merge_states


def write_json_atomic(path: Path, value: Any) -> None:
    """Write canonical pretty JSON and atomically replace *path*."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _load_json(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateMergeError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateMergeError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path, help="stale/base EvidenceRadar_State.json")
    parser.add_argument("incoming", type=Path, help="new state from another execution lane")
    parser.add_argument("-o", "--output", type=Path, help="atomically write merged JSON to this path")
    parser.add_argument("--execution-lane", choices=sorted(_ALLOWED_LANES))
    parser.add_argument("--protocol-commit")
    parser.add_argument("--parent-run-id", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        merged = merge_states(
            _load_json(args.base),
            _load_json(args.incoming),
            execution_lane=args.execution_lane,
            protocol_commit=args.protocol_commit,
            parent_run_ids=args.parent_run_id,
        )
        if args.output:
            write_json_atomic(args.output, merged)
        else:
            print(json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=2))
    except StateMergeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
