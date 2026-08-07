"""Independent EvidenceRadar pools with formal AI direction balancing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import formal_taxonomy as taxonomy
from . import quality
from . import radar as core

CATEGORY_ORDER = (
    "clinical_medicine",
    "sport_science",
    "sport_nutrition_fitness",
    "llm_research",
    "human_ai",
)

CATEGORY_TITLES = {
    "clinical_medicine": "Clinical Medicine",
    "sport_science": "Sport Science",
    "sport_nutrition_fitness": "Sport Nutrition & Fitness",
    "llm_research": "LLM Research",
    "human_ai": "Human–AI Research",
}


def category_for(paper: core.Paper) -> str | None:
    """Assign one primary category; venues never participate in this decision."""
    formal = taxonomy.category_for(paper)
    if formal:
        return formal
    streams = set(paper.all_streams())
    if "sport_nutrition" in streams:
        return "sport_nutrition_fitness"
    if "sport_science" in streams:
        return "sport_science"
    if "fitness_health" in streams:
        return "sport_nutrition_fitness"
    if "clinical_medicine" in streams:
        return "clinical_medicine"
    return None


def category_reason(paper: core.Paper, category: str) -> str:
    stale_phrases = {
        "與核心運動／體適能研究線高度相關",
        "與臨床醫學核心證據線高度相關",
        "與核心運動科學研究線高度相關",
        "與核心運動營養／體適能研究線高度相關",
        "跨臨床與運動科學研究線",
        "跨臨床與運動營養／體適能研究線",
        "命中 LLM 伴侶或社會影響主題",
    }
    parts = [part for part in paper.one_line_reason.split("；") if part and part not in stale_phrases]
    streams = set(paper.all_streams())
    directions = taxonomy.directions_for(paper)

    if directions:
        parts.append("研究問題：" + "、".join(directions))
    if paper.relevance_score >= 75:
        if category == "clinical_medicine":
            parts.append("與臨床醫學核心證據線高度相關")
        elif category == "sport_science":
            parts.append(
                "跨臨床與運動科學研究線"
                if "clinical_medicine" in streams
                else "與核心運動科學研究線高度相關"
            )
        elif category == "sport_nutrition_fitness":
            parts.append(
                "跨臨床與運動營養／體適能研究線"
                if "clinical_medicine" in streams
                else "與核心運動營養／體適能研究線高度相關"
            )
    return "；".join(dict.fromkeys(parts))


def _category_config(scoring: dict[str, Any]) -> dict[str, Any]:
    return scoring.get("category_selection", {})


def _eligible_by_category(
    papers: list[core.Paper], scoring: dict[str, Any]
) -> dict[str, list[core.Paper]]:
    legacy = scoring.get("selection", {})
    config = _category_config(scoring)
    min_score = float(config.get("candidate_min_score", legacy.get("candidate_min_score", 45)))
    relevance_minima = {
        "clinical_medicine": 58,
        "sport_science": 62,
        "sport_nutrition_fitness": 62,
        "llm_research": 65,
        "human_ai": 65,
        **{key: int(value) for key, value in config.get("min_relevance", {}).items()},
    }
    buckets: dict[str, list[core.Paper]] = {category: [] for category in CATEGORY_ORDER}
    for paper in sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True):
        if quality.hard_exclusion_reason(paper) is not None:
            continue
        category = category_for(paper)
        if category is None:
            continue
        if paper.relevance_score < relevance_minima[category] or paper.total_score < min_score:
            continue
        if paper.evidence_tier in {"Other/U", "Preclinical/U"} and paper.interest_score < 70:
            continue
        paper.one_line_reason = category_reason(paper, category)
        buckets[category].append(paper)
    return buckets


def select_candidate_pool(papers: list[core.Paper], scoring: dict[str, Any]) -> list[core.Paper]:
    """Keep independent category quotas and reserve active AI directions."""
    legacy = scoring.get("selection", {})
    config = _category_config(scoring)
    hard_max = int(config.get("candidate_hard_max", legacy.get("candidate_hard_max", 30)))
    direction_caps = config.get("direction_caps", {})
    cap_config = direction_caps.get("candidate_max_per_direction", 6)
    buckets = _eligible_by_category(papers, scoring)
    selected: list[core.Paper] = []
    for category in CATEGORY_ORDER:
        items = buckets[category]
        if category in {"llm_research", "human_ai"}:
            per_direction = (
                int(cap_config.get(category, hard_max))
                if isinstance(cap_config, dict)
                else int(cap_config)
            )
            items = taxonomy.diverse_order(
                items,
                hard_max,
                max_per_direction=per_direction,
            )
        else:
            items = items[:hard_max]
        selected.extend(items)
    return selected


def select_featured(
    candidate_pool: list[core.Paper], scoring: dict[str, Any]
) -> dict[str, dict[str, list[core.Paper]]]:
    """Select Featured per category; technical directions receive a first pass."""
    legacy = scoring.get("selection", {})
    config = _category_config(scoring)
    hard_max = int(config.get("featured_hard_max", legacy.get("featured_hard_max", 8)))
    caps = config.get("section_caps", legacy.get("section_caps", {}))
    direction_caps = config.get("direction_caps", {})
    cap_config = direction_caps.get("featured_max_per_direction", 2)
    result = {
        category: {"anchor": [], "strong_watch": [], "weird_but_important": []}
        for category in CATEGORY_ORDER
    }

    buckets = {category: [] for category in CATEGORY_ORDER}
    for paper in candidate_pool:
        category = category_for(paper)
        section = quality.feature_section(paper, scoring)
        if category and section:
            buckets[category].append(paper)

    for category in CATEGORY_ORDER:
        ordered = buckets[category]
        if category in {"llm_research", "human_ai"}:
            max_per_direction = (
                int(cap_config.get(category, hard_max))
                if isinstance(cap_config, dict)
                else int(cap_config)
            )
            ordered = taxonomy.diverse_order(
                ordered,
                hard_max,
                max_per_direction=max_per_direction,
            )
        total = 0
        for paper in ordered:
            if total >= hard_max:
                break
            section = quality.feature_section(paper, scoring)
            if section is None or len(result[category][section]) >= int(caps.get(section, hard_max)):
                continue
            result[category][section].append(paper)
            total += 1
    return result


def _coverage_line(papers: list[core.Paper]) -> str:
    directions = {
        direction
        for paper in papers
        for direction in taxonomy.directions_for(paper)
    }
    ordered = [direction for direction in taxonomy.DIRECTION_ORDER if direction in directions]
    return "、".join(ordered) if ordered else "None"


def render_markdown(
    generated_at: datetime,
    featured: dict[str, dict[str, list[core.Paper]]],
    candidate_pool: list[core.Paper],
    retrieved_count: int,
    deduplicated_count: int,
    excluded_count: int,
    warnings: list[str],
) -> str:
    buckets = {category: [] for category in CATEGORY_ORDER}
    for paper in candidate_pool:
        category = category_for(paper)
        if category:
            paper.one_line_reason = category_reason(paper, category)
            buckets[category].append(paper)

    total_featured = sum(
        len(items)
        for category in CATEGORY_ORDER
        for items in featured[category].values()
    )
    lines = [
        f"# Evidence Radar — {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Generated: `{generated_at.isoformat()}`",
        f"- Timezone: `{core.TIMEZONE}`",
        f"- Featured: `{total_featured}` across five independent categories",
        f"- Candidate Pool: `{len(candidate_pool)}` total; maximum `30` per category",
        "- Status: `AUTO-TRIAGE` — 尚未完成全文與引用核實",
        "",
    ]

    section_titles = {
        "anchor": "### Anchor Evidence",
        "strong_watch": "### Strong Watch",
        "weird_but_important": "### Weird but Important",
    }
    for category in CATEGORY_ORDER:
        category_featured = [paper for section in featured[category].values() for paper in section]
        category_featured_ids = {paper.identity_key() for paper in category_featured}
        remaining = [paper for paper in buckets[category] if paper.identity_key() not in category_featured_ids]
        lines.extend(
            [
                f"## {CATEGORY_TITLES[category]}",
                "",
                f"- Featured: `{len(category_featured_ids)}`",
                f"- Candidate Pool: `{len(buckets[category])}`（含 Featured；其餘 `{len(remaining)}` 篇）",
            ]
        )
        if category in {"llm_research", "human_ai"}:
            lines.extend(
                [
                    f"- Candidate direction coverage: `{_coverage_line(buckets[category])}`",
                    f"- Featured direction coverage: `{_coverage_line(category_featured)}`",
                ]
            )
        lines.extend(["", "> 每類獨立排序；AI 類先保留跨方向召回，再按價值補位。", ""])

        rank = 1
        for section in ("anchor", "strong_watch", "weird_but_important"):
            lines.extend([section_titles[section], ""])
            papers = featured[category][section]
            if not papers:
                lines.extend(["_本次沒有符合門檻的研究。_", ""])
                continue
            for paper in papers:
                lines.extend([core.render_featured_item(paper, rank), ""])
                rank += 1

        lines.extend(["### Candidate Pool", ""])
        if remaining:
            for index, paper in enumerate(remaining, 1):
                lines.extend([core.render_candidate_item(paper, index), ""])
        else:
            lines.extend(["_沒有額外候選。_", ""])

    lines.extend(
        [
            "## Run Notes",
            "",
            f"- Retrieved: `{retrieved_count}`",
            f"- New after same-run and cross-run deduplication: `{deduplicated_count}`",
            f"- Event-qualified inside rolling window: `{core.RUN_CONTEXT.get('event_qualified_count', deduplicated_count)}`",
            f"- Excluded before category pools: `{excluded_count}`",
            f"- Warnings: {'；'.join(warnings) if warnings else 'None'}",
            "",
            "## Interpretation Guardrail",
            "",
            "> 本檔是發現與分流層，不是最終證據審核。Venue 只記錄 publication identity，不作研究分類；正式引用前仍須完成全文、版本事件、校正／撤稿、方法與斷言核對。",
            "",
        ]
    )
    return "\n".join(lines)


def install() -> None:
    core.select_candidate_pool = select_candidate_pool
    core.select_featured = select_featured
    core.render_markdown = render_markdown
