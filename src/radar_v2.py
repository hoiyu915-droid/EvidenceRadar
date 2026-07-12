#!/usr/bin/env python3
"""EvidenceRadar v0.2 strict triage layer.

This module wraps the v0.1 fetch/render pipeline and replaces the permissive
classification and selection functions. The goal is high recall without letting
protocols, correspondence, miscoded observational studies, animal-only work, or
cross-domain OpenAlex noise impersonate high-level evidence.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from . import radar as core
except ImportError:  # Executed as: python src/radar_v2.py
    import radar as core  # type: ignore


CORRESPONDENCE_TYPES = {
    "letter",
    "comment",
    "editorial",
    "news",
    "published erratum",
    "correction",
    "expression of concern",
    "retracted publication",
}

CORRESPONDENCE_TITLE_PATTERNS = (
    r"^\s*re\s*:",
    r"^\s*reply\b",
    r"^\s*response to\b",
    r"^\s*comment on\b",
    r"^\s*letter to the editor\b",
    r"^\s*author response\b",
)

PROTOCOL_PATTERNS = (
    r"\bstudy protocol\b",
    r"\bprotocol for\b",
    r"\btrial protocol\b",
    r"\bprotocol of\b",
    r"\brationale and design\b",
    r"\bdesign and rationale\b",
    r"\bmethods and design\b",
    r"\bpilot protocol\b",
)

OBSERVATIONAL_TITLE_PATTERNS = (
    r"\bretrospective\b",
    r"\bprospective cohort\b",
    r"\bcohort study\b",
    r"\bcross[- ]sectional\b",
    r"\bcase[- ]control\b",
    r"\bquasi[- ]experimental\b",
    r"\bnon[- ]randomi[sz]ed\b",
)

HUMAN_SIGNALS = (
    "participant",
    "patient",
    "adult",
    "adolescent",
    "child",
    "athlete",
    "people",
    "human",
    "men",
    "women",
)

ANIMAL_SIGNALS = (
    "mice",
    "mouse",
    "rats",
    "rat model",
    "murine",
    "rodent",
    "zebrafish",
    "drosophila",
    "dairy cow",
    "cattle",
    "bovine",
    "porcine",
    "swine",
)

STREAM_ANCHORS: dict[str, tuple[str, ...]] = {
    "sport_science": (
        "exercise",
        "athlete",
        "athletic",
        "sport",
        "resistance training",
        "strength training",
        "endurance training",
        "physical training",
        "training load",
        "muscle",
        "hypertrophy",
        "biomechanic",
        "plyometric",
        "neuromuscular",
        "physical performance",
        "exercise capacity",
        "rehabilitation",
    ),
    "fitness_health": (
        "physical activity",
        "physical fitness",
        "cardiorespiratory fitness",
        "exercise",
        "sedentary",
        "step count",
        "steps per day",
        "walking",
        "muscular strength",
        "sarcopenia",
        "vo2max",
        "vo2 max",
        "exercise capacity",
    ),
}

SPORT_NUTRITION_ACTIVITY = (
    "athlete",
    "athletic",
    "sport",
    "exercise",
    "physical activity",
    "resistance training",
    "endurance training",
    "training load",
    "exercise performance",
)

SPORT_NUTRITION_DIET = (
    "sports nutrition",
    "nutrition",
    "diet",
    "protein",
    "amino acid",
    "carbohydrate",
    "glycogen",
    "creatine",
    "hydration",
    "fluid intake",
    "supplement",
    "energy availability",
    "relative energy deficiency",
    "red-s",
    "caffeine",
    "electrolyte",
)

LLM_TECH = (
    "large language model",
    " llm",
    "llm-",
    "chatbot",
    "conversational agent",
    "generative ai",
    "ai companion",
    "artificial intelligence companion",
    "machine companion",
    "social robot",
    "replika",
    "character.ai",
    "kindroid",
    "human-ai",
    "human–ai",
)

LLM_RELATIONAL = (
    "companion",
    "companionship",
    "relationship",
    "relational",
    "attachment",
    "intimacy",
    "loneliness",
    "emotional support",
    "social support",
    "self-disclosure",
    "self disclosure",
    "parasocial",
    "anthropomorph",
    "dependency",
    "friendship",
    "social substitution",
    "well-being",
    "wellbeing",
    "mental health",
)


def combined_text(paper: core.Paper) -> str:
    return f"{paper.title} {paper.abstract}".casefold()


def type_set(paper: core.Paper) -> set[str]:
    return {value.casefold().strip() for value in paper.publication_types if value}


def has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term.casefold() in text for term in terms)


def matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) is not None for pattern in patterns)


def is_correspondence(paper: core.Paper) -> bool:
    title = paper.title.casefold()
    return bool(type_set(paper) & CORRESPONDENCE_TYPES) or matches_any(title, CORRESPONDENCE_TITLE_PATTERNS)


def is_protocol(paper: core.Paper) -> bool:
    title = paper.title.casefold()
    types = type_set(paper)
    return "clinical trial protocol" in types or matches_any(title, PROTOCOL_PATTERNS)


def is_nonhuman_only(paper: core.Paper) -> bool:
    text = combined_text(paper)
    return has_any(text, ANIMAL_SIGNALS) and not has_any(text, HUMAN_SIGNALS)


def is_stream_relevant(paper: core.Paper) -> bool:
    text = combined_text(paper)
    if paper.stream == "sport_nutrition":
        return has_any(text, SPORT_NUTRITION_ACTIVITY) and has_any(text, SPORT_NUTRITION_DIET)
    if paper.stream == "llm_social":
        return has_any(text, LLM_TECH) and has_any(text, LLM_RELATIONAL)
    anchors = STREAM_ANCHORS.get(paper.stream, ())
    return has_any(text, anchors)


def strict_classify_study(paper: core.Paper) -> tuple[str, str, int]:
    types = type_set(paper)
    title = paper.title.casefold()
    text = combined_text(paper)

    if is_correspondence(paper):
        return "Correspondence", "Letter/U", 12
    if is_protocol(paper):
        return "Protocol", "Protocol/U", 25
    if "retracted publication" in types or "expression of concern" in types:
        return "Retracted/flagged", "Flagged/U", 0

    # Title-level design declarations override stray abstract phrases and bad metadata.
    if "scoping review" in title or "mapping review" in title:
        return "Scoping review", "Scope/B", 55
    if re.search(r"\bquasi[- ]experimental\b|\bnon[- ]randomi[sz]ed\b", title):
        return "Quasi-experimental study", "Quasi/B+", 64
    if re.search(r"\bretrospective\b|\bcohort study\b|\bcase[- ]control\b", title):
        return "Cohort study", "Cohort/B+", 70
    if re.search(r"\bcross[- ]sectional\b", title):
        return "Cross-sectional study", "Cross/B", 50

    if "meta-analysis" in types or "meta-analysis" in title or "meta analysis" in title:
        return "Meta-analysis", "Meta/A", 95
    if "systematic review" in types or "systematic review" in title:
        return "Systematic review", "SR/A", 90
    if "practice guideline" in types or "guideline" in types or "clinical guideline" in title:
        return "Guideline", "Guideline/A", 92
    if "consensus development conference" in types or "consensus statement" in title:
        return "Consensus statement", "Stmt/B+", 78

    rct_type = bool(types & {"randomized controlled trial", "controlled clinical trial"})
    rct_text = re.search(
        r"\brandomi[sz]ed (?:controlled |clinical |crossover |cross-over )?trial\b|"
        r"\bparticipants (?:were )?randomly assigned\b|\brandom allocation\b",
        text,
    ) is not None
    if rct_type or rct_text:
        return "Randomized controlled trial", "RCT/A", 88

    if "field experiment" in text or "field study" in title:
        return "Field experiment", "Field/A", 82
    if "longitudinal" in title or "prospective study" in types:
        return "Longitudinal study", "Long/A", 78
    if "cohort" in title or "cohort" in types:
        return "Cohort study", "Cohort/B+", 72
    if "crossover" in title or "cross-over" in title:
        return "Crossover study", "Mechanistic/B+", 70
    if "qualitative" in title or "thematic analysis" in text or "interview study" in text:
        return "Qualitative study", "Qual/A", 65
    if "cross-sectional" in title or "cross sectional" in title:
        return "Cross-sectional study", "Cross/B", 52
    if "survey" in title or "questionnaire" in title:
        return "Survey", "Survey/B", 48
    if is_nonhuman_only(paper):
        return "Preclinical study", "Preclinical/U", 42
    if "review" in types or paper.publication_types == ["review"]:
        return "Narrative review", "Rev/B", 58
    if paper.is_preprint:
        return "Preprint", "Preprint/U", 35
    return "Other", "Other/U", 38


def strict_score_relevance(paper: core.Paper, relevance_terms: Iterable[str]) -> int:
    if not is_stream_relevant(paper):
        return 0
    text = combined_text(paper)
    title = paper.title.casefold()
    matched = {term.casefold() for term in relevance_terms if term.casefold() in text}
    title_matches = sum(1 for term in matched if term in title)
    return min(100, 55 + len(matched) * 6 + title_matches * 4)


def hard_exclusion_reason(paper: core.Paper) -> str | None:
    if is_correspondence(paper):
        return "correspondence/editorial"
    if is_protocol(paper):
        return "protocol/design paper"
    if paper.study_design == "Retracted/flagged":
        return "retracted or editorially flagged"
    if not is_stream_relevant(paper):
        return "stream relevance gate failed"
    return None


def strict_select_candidate_pool(papers: list[core.Paper], scoring: dict[str, Any]) -> list[core.Paper]:
    selection = scoring["selection"]
    min_score = float(selection.get("candidate_min_score", 45))
    hard_max = int(selection.get("candidate_hard_max", 30))
    stream_limits = {key: int(value) for key, value in scoring.get("stream_limits", {}).items()}
    counts: dict[str, int] = {}
    selected: list[core.Paper] = []

    ranked = sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True)
    for paper in ranked:
        if hard_exclusion_reason(paper) is not None:
            continue
        min_relevance = 60 if paper.stream == "llm_social" else 55
        if paper.relevance_score < min_relevance or paper.total_score < min_score:
            continue
        stream_limit = stream_limits.get(paper.stream, hard_max)
        if counts.get(paper.stream, 0) >= stream_limit:
            continue
        selected.append(paper)
        counts[paper.stream] = counts.get(paper.stream, 0) + 1
        if len(selected) >= hard_max:
            break
    return selected


def strict_feature_section(paper: core.Paper, scoring: dict[str, Any]) -> str | None:
    if hard_exclusion_reason(paper) is not None:
        return None

    rules = scoring.get("rules", {})
    anchor_designs = {
        "Meta-analysis",
        "Systematic review",
        "Guideline",
        "Randomized controlled trial",
        "Field experiment",
        "Longitudinal study",
    }
    if (
        paper.study_design in anchor_designs
        and paper.evidence_score >= int(rules.get("anchor_min_evidence", 75))
        and paper.relevance_score >= 60
    ):
        return "anchor"
    if (
        paper.evidence_score >= int(rules.get("strong_min_evidence", 55))
        and paper.relevance_score >= int(rules.get("strong_min_relevance", 60))
        and paper.study_design not in {"Scoping review", "Narrative review"}
    ):
        return "strong_watch"
    if paper.interest_score >= int(rules.get("weird_min_interest", 85)) and paper.relevance_score >= 65:
        return "weird_but_important"
    return None


def install_strict_layer() -> None:
    core.classify_study = strict_classify_study
    core.score_relevance = strict_score_relevance
    core.select_candidate_pool = strict_select_candidate_pool
    core.feature_section = strict_feature_section


def main() -> int:
    install_strict_layer()
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
