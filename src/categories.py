"""Four independent EvidenceRadar category pools and Markdown renderer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import quality
from . import radar as core

CATEGORY_ORDER = (
    "clinical_medicine",
    "sport_science",
    "sport_nutrition_fitness",
    "llm_social",
)

CATEGORY_TITLES = {
    "clinical_medicine": "Clinical Medicine",
    "sport_science": "Sport Science",
    "sport_nutrition_fitness": "Sport Nutrition & Fitness",
    "llm_social": "LLM & Social Impact",
}


def category_for(paper: core.Paper) -> str | None:
    """Assign one primary category without duplicating a paper across pools."""
    streams = set(paper.all_streams())
    if "llm_social" in streams:
        return "llm_social"
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
    """Rewrite relevance wording after the final deduplicated category is known."""
    stale_phrases = {
        "與核心運動／體適能研究線高度相關",
        "與臨床醫學核心證據線高度相關",
        "與核心運動科學研究線高度相關",
        "與核心運動營養／體適能研究線高度相關",
        "跨臨床與運動科學研究線",
        "跨臨床與運動營養／體適能研究線",
    }
    parts = [part for part in paper.one_line_reason.split("；") if part and part not in stale_phrases]
    streams = set(paper.all_streams())

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


def select_candidate_pool(papers: list[core.Paper], scoring: dict[str, Any]) -> list[core.Paper]:
    """Select up to N papers independently for each top-level category."""
    legacy = scoring.get("selection", {})
    config = _category_config(scoring)
    hard_max = int(config.get("candidate_hard_max", legacy.get("candidate_hard_max", 30)))
    min_score = float(config.get("candidate_min_score", legacy.get("candidate_min_score", 45)))
    relevance_minima = {
        "clinical_medicine": 58,
        "sport_science": 62,
        "sport_nutrition_fitness": 62,
        "llm_social": 65,
        **{key: int(value) for key, value in config.get("min_relevance", {}).items()},
    }
    buckets: dict[str, list[core.Paper]] = {category: [] for category in CATEGORY_ORDER}

    for paper in sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True):
        if quality.hard_exclusion_reason(paper) is not None:
            continue
        category = category_for(paper)
        if category is None or len(buckets[category]) >= hard_max:
            continue
        if paper.relevance_score < relevance_minima[category] or paper.total_score < min_score:
            continue
        if paper.evidence_tier in {"Other/U", "Preclinical/U"} and paper.interest_score < 70:
            continue
        paper.one_line_reason = category_reason(paper, category)
        buckets[category].append(paper)

    return [paper for category in CATEGORY_ORDER for paper in buckets[category]]


def select_featured(
    candidate_pool: list[core.Paper], scoring: dict[str, Any]
) -> dict[str, dict[str, list[core.Paper]]]:
    """Select Featured independently inside every category."""
    legacy = scoring.get("selection", {})
    config = _category_config(scoring)
    hard_max = int(config.get("featured_hard_max", legacy.get("featured_hard_max", 8)))
    caps = config.get("section_caps", legacy.get("section_caps", {}))
    result = {
        category: {"anchor": [], "strong_watch": [], "weird_but_important": []}
        for category in CATEGORY_ORDER
    }

    totals = {category: 0 for category in CATEGORY_ORDER}
    for paper in candidate_pool:
        category = category_for(paper)
        if category is None or totals[category] >= hard_max:
            continue
        section = quality.feature_section(paper, scoring)
        if section is None:
            continue
        if len(result[category][section]) >= int(caps.get(section, hard_max)):
            continue
        result[category][section].append(paper)
        totals[category] += 1
    return result


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
            # core.main rebuilds reasons after Europe PMC enrichment; final category
            # wording therefore belongs here, immediately before Markdown rendering.
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
        f"- Featured: `{total_featured}` across four independent categories",
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
        category_featured_ids = {
            paper.identity_key()
            for section in featured[category].values()
            for paper in section
        }
        remaining = [
            paper for paper in buckets[category] if paper.identity_key() not in category_featured_ids
        ]
        featured_count = len(category_featured_ids)
        lines.extend(
            [
                f"## {CATEGORY_TITLES[category]}",
                "",
                f"- Featured: `{featured_count}`",
                f"- Candidate Pool: `{len(buckets[category])}`（含 Featured；其餘 `{len(remaining)}` 篇）",
                "",
                "> 每類獨立排序與截斷；其他類別不得吃掉本類配額。",
                "",
            ]
        )

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
            f"- Deduplicated: `{deduplicated_count}`",
            f"- Excluded before category pools: `{excluded_count}`",
            f"- Warnings: {'；'.join(warnings) if warnings else 'None'}",
            "",
            "## Interpretation Guardrail",
            "",
            "> 本檔是發現與分流層，不是最終證據審核。正式引用前仍須完成 DOI/PMID 存在性、全文、校正／撤稿、方法與斷言核對。",
            "",
        ]
    )
    return "\n".join(lines)


def install() -> None:
    core.select_candidate_pool = select_candidate_pool
    core.select_featured = select_featured
    core.render_markdown = render_markdown
