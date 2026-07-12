"""Strict classification, domain relevance, and fallback discovery for EvidenceRadar."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any, Iterable

from . import radar as core

CROSSREF_WORKS = "https://api.crossref.org/works"
_ORIGINAL_FETCH_OPENALEX = core.fetch_openalex

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
    "animal model",
    "animal models",
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

SPORT_TITLE = (
    "exercise",
    "athlete",
    "athletic",
    "sport",
    "soccer",
    "football",
    "basketball",
    "hockey",
    "resistance training",
    "strength training",
    "endurance training",
    "physical training",
    "training load",
    "strength and conditioning",
    "muscle",
    "hypertrophy",
    "plyometric",
    "neuromuscular",
    "physical performance",
    "aerobic conditioning",
    "blood flow restriction",
    "acl",
    "tendon",
    "gait",
    "kinematic",
    "musculoskeletal",
    "cartilage",
)

FITNESS_TITLE = (
    "physical activity",
    "physical fitness",
    "cardiorespiratory fitness",
    "cardiopulmonary exercise",
    "cpet",
    "exercise",
    "sedentary",
    "step count",
    "steps per day",
    "walking",
    "muscular strength",
    "grip strength",
    "handgrip strength",
    "sarcopenia",
    "vo2max",
    "vo2 max",
    "aerobic capacity",
)

NUTRITION_ACTIVITY_TITLE = (
    "athlete",
    "athletic",
    "sport",
    "exercise",
    "training",
    "sprint",
    "endurance",
    "strength",
    "muscle",
    "performance",
    "rehydration",
)

NUTRITION_TITLE = (
    "nutrition",
    "nutrient",
    "diet",
    "protein",
    "amino acid",
    "carbohydrate",
    "glycogen",
    "creatine",
    "hydration",
    "rehydration",
    "fluid",
    "supplement",
    "energy availability",
    "relative energy deficiency",
    "red-s",
    "caffeine",
    "electrolyte",
)

LLM_TECH = (
    "large language model",
    "llm",
    "chatbot",
    "conversational agent",
    "generative ai",
    "ai companion",
    "artificial intelligence companion",
    "machine companion",
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


def compact(value: str | None) -> str:
    return core.compact_whitespace(value).casefold()


def has_any(text: str, terms: Iterable[str]) -> bool:
    text = text.casefold()
    return any(term.casefold() in text for term in terms)


def matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) is not None for pattern in patterns)


def type_set(paper: core.Paper) -> set[str]:
    return {compact(value) for value in paper.publication_types if value}


def combined_text(paper: core.Paper) -> str:
    return f"{paper.title} {paper.abstract}".casefold()


def is_correspondence(paper: core.Paper) -> bool:
    return bool(type_set(paper) & CORRESPONDENCE_TYPES) or matches_any(
        paper.title, CORRESPONDENCE_TITLE_PATTERNS
    )


def is_protocol(paper: core.Paper) -> bool:
    return "clinical trial protocol" in type_set(paper) or matches_any(paper.title, PROTOCOL_PATTERNS)


def is_nonhuman_only(paper: core.Paper) -> bool:
    text = combined_text(paper)
    return has_any(text, ANIMAL_SIGNALS) and not has_any(text, HUMAN_SIGNALS)


def is_stream_relevant(paper: core.Paper) -> bool:
    title = paper.title.casefold()
    abstract = paper.abstract.casefold()

    if paper.stream == "sport_science":
        return has_any(title, SPORT_TITLE)

    if paper.stream == "fitness_health":
        return has_any(title, FITNESS_TITLE)

    if paper.stream == "sport_nutrition":
        return has_any(title, NUTRITION_ACTIVITY_TITLE) and has_any(title, NUTRITION_TITLE)

    if paper.stream == "llm_social":
        tech_title = has_any(title, LLM_TECH)
        relation_title = has_any(title, LLM_RELATIONAL)
        tech_anywhere = tech_title or has_any(abstract, LLM_TECH)
        relation_anywhere = relation_title or has_any(abstract, LLM_RELATIONAL)
        return tech_anywhere and relation_anywhere and (tech_title or relation_title)

    return False


def classify_study(paper: core.Paper) -> tuple[str, str, int]:
    types = type_set(paper)
    title = paper.title.casefold()
    text = combined_text(paper)

    if is_correspondence(paper):
        return "Correspondence", "Letter/U", 12
    if is_protocol(paper):
        return "Protocol", "Protocol/U", 25
    if "retracted publication" in types or "expression of concern" in types:
        return "Retracted/flagged", "Flagged/U", 0
    if "scoping review" in title or "mapping review" in title:
        return "Scoping review", "Scope/B", 55

    # Non-human evidence must never inherit human RCT/longitudinal tiers.
    if is_nonhuman_only(paper):
        if "meta-analysis" in title or "systematic review" in title or "review" in types:
            return "Preclinical synthesis", "Preclinical/U", 45
        return "Preclinical study", "Preclinical/U", 40

    # Explicit title declarations override stray abstract language and miscoded metadata.
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
    if "review" in types or paper.publication_types == ["review"]:
        return "Narrative review", "Rev/B", 58
    if paper.is_preprint:
        return "Preprint", "Preprint/U", 35
    return "Other", "Other/U", 38


def score_relevance(paper: core.Paper, relevance_terms: Iterable[str]) -> int:
    if not is_stream_relevant(paper):
        return 0
    text = combined_text(paper)
    title = paper.title.casefold()
    matched = {term.casefold() for term in relevance_terms if term.casefold() in text}
    title_matches = sum(1 for term in matched if term in title)
    return min(100, 62 + len(matched) * 6 + title_matches * 5)


def hard_exclusion_reason(paper: core.Paper) -> str | None:
    if is_correspondence(paper):
        return "correspondence/editorial"
    if is_protocol(paper):
        return "protocol/design paper"
    if paper.study_design == "Retracted/flagged":
        return "retracted or editorially flagged"
    if not is_stream_relevant(paper):
        return "title-primary stream relevance gate failed"
    return None


def select_candidate_pool(papers: list[core.Paper], scoring: dict[str, Any]) -> list[core.Paper]:
    selection = scoring["selection"]
    min_score = float(selection.get("candidate_min_score", 45))
    hard_max = int(selection.get("candidate_hard_max", 30))
    stream_limits = {key: int(value) for key, value in scoring.get("stream_limits", {}).items()}
    counts: dict[str, int] = {}
    selected: list[core.Paper] = []

    for paper in sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True):
        if hard_exclusion_reason(paper) is not None:
            continue
        min_relevance = 65 if paper.stream == "llm_social" else 62
        if paper.relevance_score < min_relevance or paper.total_score < min_score:
            continue
        if paper.evidence_tier in {"Other/U", "Preclinical/U"} and paper.interest_score < 70:
            continue
        stream_limit = stream_limits.get(paper.stream, hard_max)
        if counts.get(paper.stream, 0) >= stream_limit:
            continue
        selected.append(paper)
        counts[paper.stream] = counts.get(paper.stream, 0) + 1
        if len(selected) >= hard_max:
            break
    return selected


def feature_section(paper: core.Paper, scoring: dict[str, Any]) -> str | None:
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
        and paper.relevance_score >= 65
    ):
        return "anchor"
    if (
        paper.evidence_score >= int(rules.get("strong_min_evidence", 55))
        and paper.relevance_score >= int(rules.get("strong_min_relevance", 60))
        and paper.study_design not in {"Scoping review", "Narrative review", "Preclinical synthesis"}
    ):
        return "strong_watch"
    if paper.interest_score >= int(rules.get("weird_min_interest", 85)) and paper.relevance_score >= 65:
        return "weird_but_important"
    return None


def crossref_date(item: dict[str, Any]) -> str:
    for key in ("published-online", "published-print", "published", "created", "issued"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if not parts:
            continue
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return f"{year:04d}-01-01"
    return ""


def fetch_crossref(
    query: str,
    stream: str,
    start_date: date,
    end_date: date,
    max_results: int,
) -> list[core.Paper]:
    params: dict[str, Any] = {
        "query.bibliographic": query,
        "filter": f"from-pub-date:{start_date.isoformat()},until-pub-date:{end_date.isoformat()}",
        "sort": "published",
        "order": "desc",
        "rows": min(max_results, 50),
    }
    email = os.getenv("NCBI_EMAIL", "").strip()
    if email:
        params["mailto"] = email
    payload = core.request(CROSSREF_WORKS, params=params, attempts=2).json()
    papers: list[core.Paper] = []
    for item in (payload.get("message") or {}).get("items", []):
        titles = item.get("title") or []
        title = core.compact_whitespace(titles[0] if titles else "")
        if not title:
            continue
        authors = []
        for author in item.get("author") or []:
            name = core.compact_whitespace(f"{author.get('family', '')} {author.get('given', '')}")
            if name:
                authors.append(name)
        containers = item.get("container-title") or []
        work_type = core.compact_whitespace(item.get("type"))
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
        papers.append(
            core.Paper(
                title=title,
                abstract=core.compact_whitespace(abstract),
                authors=authors,
                journal_or_venue=core.compact_whitespace(containers[0] if containers else work_type),
                publication_date=crossref_date(item),
                stream=stream,
                source="Crossref fallback",
                doi=core.normalize_doi(item.get("DOI")),
                publication_types=[work_type] if work_type else [],
                is_preprint=work_type in {"posted-content", "preprint"},
            )
        )
    return papers


def fetch_openalex_with_fallback(
    query: str,
    stream: str,
    start_date: date,
    end_date: date,
    max_results: int,
) -> list[core.Paper]:
    try:
        return _ORIGINAL_FETCH_OPENALEX(query, stream, start_date, end_date, max_results)
    except (core.RadarError, ValueError):
        return fetch_crossref(query, stream, start_date, end_date, max_results)


def install() -> None:
    core.classify_study = classify_study
    core.score_relevance = score_relevance
    core.fetch_openalex = fetch_openalex_with_fallback
    core.select_candidate_pool = select_candidate_pool
    core.feature_section = feature_section
