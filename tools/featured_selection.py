from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


FEATURED_POLICY_NOTE_PREFIX = "FEATURED_SELECTION_POLICY_V2:"


class FeaturedSelectionError(ValueError):
    pass


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FeaturedSelectionError(f"{label} must be a positive integer")
    return value


def normalize_featured_policy(
    policy: Mapping[str, Any] | None,
    *,
    default_target_per_category: int,
    default_hard_max_per_category: int,
) -> dict[str, Any]:
    target_default = _positive_int(default_target_per_category, "default_target_per_category")
    hard_default = _positive_int(default_hard_max_per_category, "default_hard_max_per_category")
    if target_default > hard_default:
        raise FeaturedSelectionError("default target cannot exceed default hard max")
    raw = dict(policy or {})
    ranking = raw.get("ranking_pool_max_per_category")
    if ranking is not None:
        ranking = _positive_int(ranking, "ranking_pool_max_per_category")
    per_category_raw = raw.get("per_category", {})
    if not isinstance(per_category_raw, Mapping):
        raise FeaturedSelectionError("per_category must be an object")
    per_category: dict[str, dict[str, int]] = {}
    for category, values in per_category_raw.items():
        if not isinstance(category, str) or not category:
            raise FeaturedSelectionError("per_category keys must be non-empty strings")
        if not isinstance(values, Mapping):
            raise FeaturedSelectionError(f"per_category.{category} must be an object")
        target = _positive_int(values.get("target", target_default), f"per_category.{category}.target")
        hard = _positive_int(values.get("hard_max", hard_default), f"per_category.{category}.hard_max")
        if target > hard:
            raise FeaturedSelectionError(f"per_category.{category} target cannot exceed hard max")
        per_category[category] = {"target": target, "hard_max": hard}
    target_total = raw.get("target_total")
    hard_total = raw.get("hard_max_total")
    if target_total is not None:
        target_total = _positive_int(target_total, "target_total")
    if hard_total is not None:
        hard_total = _positive_int(hard_total, "hard_max_total")
    if target_total is not None and hard_total is not None and target_total > hard_total:
        raise FeaturedSelectionError("target_total cannot exceed hard_max_total")
    return {
        "ranking_pool_max_per_category": ranking,
        "per_category": per_category,
        "target_total": target_total,
        "hard_max_total": hard_total,
    }


def featured_policy_from_output(
    selection: Mapping[str, Any] | None,
    *,
    default_target_per_category: int,
    default_hard_max_per_category: int,
) -> dict[str, Any]:
    selection = dict(selection or {})
    ranking = selection.get("ranking_pool", {})
    featured = selection.get("featured", {})
    if not isinstance(ranking, Mapping):
        raise FeaturedSelectionError("selection.ranking_pool must be an object")
    if not isinstance(featured, Mapping):
        raise FeaturedSelectionError("selection.featured must be an object")
    final_digest = featured.get("final_digest", {})
    if final_digest is None:
        final_digest = {}
    if not isinstance(final_digest, Mapping):
        raise FeaturedSelectionError("selection.featured.final_digest must be an object")
    return normalize_featured_policy(
        {
            "ranking_pool_max_per_category": ranking.get("max_per_category"),
            "per_category": featured.get("per_category", {}),
            "target_total": final_digest.get("target"),
            "hard_max_total": final_digest.get("hard_max"),
        },
        default_target_per_category=default_target_per_category,
        default_hard_max_per_category=default_hard_max_per_category,
    )


def featured_policy_note(policy: Mapping[str, Any]) -> str:
    normalized = {
        "ranking_pool_max_per_category": policy.get("ranking_pool_max_per_category"),
        "per_category": policy.get("per_category", {}),
        "target_total": policy.get("target_total"),
        "hard_max_total": policy.get("hard_max_total"),
    }
    return FEATURED_POLICY_NOTE_PREFIX + json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def parse_featured_policy_note(notes: Iterable[Any]) -> dict[str, Any] | None:
    matches = [str(note) for note in notes if str(note).startswith(FEATURED_POLICY_NOTE_PREFIX)]
    if not matches:
        return None
    if len(matches) != 1:
        raise FeaturedSelectionError("Run notes must contain at most one featured-selection policy")
    try:
        decoded = json.loads(matches[0][len(FEATURED_POLICY_NOTE_PREFIX):])
    except json.JSONDecodeError as exc:
        raise FeaturedSelectionError(f"invalid featured-selection policy note: {exc}") from exc
    if not isinstance(decoded, dict):
        raise FeaturedSelectionError("featured-selection policy note must decode to an object")
    return decoded


def _rank(item: Mapping[str, Any]) -> tuple[int, int, str]:
    triage_rank = {"PRIORITY": 0, "REVIEW_REQUIRED": 1, "LOWER_PRIORITY": 2}
    return (
        triage_rank.get(str(item.get("triage_status") or ""), 3),
        -int(item.get("routing_score") or 0),
        str(item.get("work_id") or ""),
    )


def select_featured_work_ids_v2(
    candidate_records: list[dict[str, Any]],
    *,
    target_per_category: int = 5,
    hard_max_per_category: int = 8,
    excluded_event_classes: set[str] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> set[str]:
    """Select a bounded digest while retaining the complete candidate ledger.

    The ranking-pool cap only limits which items may compete for *featured*
    slots. It never deletes candidates or changes displayed/full-ledger status.
    """
    normalized = normalize_featured_policy(
        policy,
        default_target_per_category=target_per_category,
        default_hard_max_per_category=hard_max_per_category,
    )
    excluded = excluded_event_classes or {"BACKFILL_INDEXING", "CORRECTION_NOTICE"}
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in candidate_records:
        by_category.setdefault(str(item.get("category") or ""), []).append(item)

    chosen_by_category: dict[str, list[dict[str, Any]]] = {}
    eligible_by_category: dict[str, list[dict[str, Any]]] = {}
    anchors: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    for category, items in by_category.items():
        eligible = [
            item for item in items
            if str(item.get("event_status") or "") != "NO_QUALIFYING_EVENT"
            and str(item.get("event_class") or "OTHER") not in excluded
            and item.get("work_id")
        ]
        eligible.sort(key=_rank)
        ranking_cap = normalized["ranking_pool_max_per_category"]
        if ranking_cap is not None:
            eligible = eligible[:ranking_cap]
        eligible_by_category[category] = eligible
        category_limits = normalized["per_category"].get(category, {})
        target = int(category_limits.get("target", target_per_category))
        hard = int(category_limits.get("hard_max", hard_max_per_category))
        preferred = [
            item for item in eligible
            if str(item.get("triage_status") or "") in {"PRIORITY", "REVIEW_REQUIRED"}
        ]
        chosen = preferred[:hard]
        chosen_ids = {str(item["work_id"]) for item in chosen}
        if len(chosen) < target:
            for item in eligible:
                if str(item["work_id"]) in chosen_ids:
                    continue
                chosen.append(item)
                chosen_ids.add(str(item["work_id"]))
                if len(chosen) >= target:
                    break
        chosen = chosen[:hard]
        chosen_by_category[category] = chosen
        anchors.extend(chosen[: min(target, len(chosen))])
        extras.extend(chosen[min(target, len(chosen)):])

    selected: dict[str, dict[str, Any]] = {
        str(item["work_id"]): item for item in [*anchors, *extras]
    }

    target_total = normalized["target_total"]
    if target_total is not None and len(selected) < target_total:
        fill: list[dict[str, Any]] = []
        for category, eligible in eligible_by_category.items():
            category_limits = normalized["per_category"].get(category, {})
            hard = int(category_limits.get("hard_max", hard_max_per_category))
            current = chosen_by_category.get(category, [])
            remaining = max(0, hard - len(current))
            if remaining:
                fill.extend(
                    item for item in eligible
                    if str(item["work_id"]) not in selected
                )
        fill.sort(key=_rank)
        for item in fill:
            if len(selected) >= target_total:
                break
            category = str(item.get("category") or "")
            category_limits = normalized["per_category"].get(category, {})
            hard = int(category_limits.get("hard_max", hard_max_per_category))
            current_count = sum(1 for existing in selected.values() if str(existing.get("category") or "") == category)
            if current_count < hard:
                selected[str(item["work_id"])] = item

    hard_total = normalized["hard_max_total"]
    if hard_total is not None and len(selected) > hard_total:
        anchor_by_id = {str(item["work_id"]): item for item in anchors}
        keep: dict[str, dict[str, Any]] = {}
        for item in sorted(anchor_by_id.values(), key=_rank):
            if len(keep) >= hard_total:
                break
            keep[str(item["work_id"])] = item
        if len(keep) < hard_total:
            remaining = [item for work_id, item in selected.items() if work_id not in keep]
            remaining.sort(key=_rank)
            for item in remaining:
                if len(keep) >= hard_total:
                    break
                keep[str(item["work_id"])] = item
        selected = keep
    return set(selected)
