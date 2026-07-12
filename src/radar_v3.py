#!/usr/bin/env python3
"""EvidenceRadar v0.3 precision layer.

Adds title-primary domain gates and a Crossref fallback for the LLM/social
stream when OpenAlex is rate-limited or unavailable.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any, Iterable

try:
    from . import radar as core
    from . import radar_v2 as strict
except ImportError:  # Executed as: python src/radar_v3.py
    import radar as core  # type: ignore
    import radar_v2 as strict  # type: ignore


CROSSREF_WORKS = "https://api.crossref.org/works"
_ORIGINAL_FETCH_OPENALEX = core.fetch_openalex

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
    "biomechanic",
    "plyometric",
    "neuromuscular",
    "physical performance",
    "aerobic conditioning",
    "blood flow restriction",
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
    "recovery",
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

LLM_TECH_TITLE = (
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

LLM_RELATION_TITLE = (
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
    "parasocial",
    "anthropomorph",
    "dependency",
    "friendship",
    "social substitution",
    "well-being",
    "wellbeing",
    "mental health",
)


def has_any(text: str, terms: Iterable[str]) -> bool:
    text = text.casefold()
    return any(term.casefold() in text for term in terms)


def title_primary_relevance(paper: core.Paper) -> bool:
    title = paper.title.casefold()
    abstract = paper.abstract.casefold()

    if paper.stream == "sport_science":
        return has_any(title, SPORT_TITLE)

    if paper.stream == "fitness_health":
        return has_any(title, FITNESS_TITLE)

    if paper.stream == "sport_nutrition":
        activity_in_title = has_any(title, NUTRITION_ACTIVITY_TITLE)
        nutrition_in_title = has_any(title, NUTRITION_TITLE)
        # Requiring both concepts in the title kills generic clinical nutrition,
        # nursing-training, materials-sensing, and incidental covariate matches.
        return activity_in_title and nutrition_in_title

    if paper.stream == "llm_social":
        tech_title = has_any(title, LLM_TECH_TITLE)
        relation_title = has_any(title, LLM_RELATION_TITLE)
        tech_anywhere = tech_title or has_any(abstract, LLM_TECH_TITLE)
        relation_anywhere = relation_title or has_any(abstract, LLM_RELATION_TITLE)
        return tech_anywhere and relation_anywhere and (tech_title or relation_title)

    return False


def score_relevance_v3(paper: core.Paper, relevance_terms: Iterable[str]) -> int:
    if not title_primary_relevance(paper):
        return 0
    text = f"{paper.title} {paper.abstract}".casefold()
    title = paper.title.casefold()
    matched = {term.casefold() for term in relevance_terms if term.casefold() in text}
    title_matches = sum(1 for term in matched if term in title)
    return min(100, 62 + len(matched) * 6 + title_matches * 5)


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


def clean_crossref_abstract(value: str | None) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return core.compact_whitespace(value)


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
        papers.append(
            core.Paper(
                title=title,
                abstract=clean_crossref_abstract(item.get("abstract")),
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


def hard_exclusion_v3(paper: core.Paper) -> str | None:
    base = strict.hard_exclusion_reason(paper)
    if base and base != "stream relevance gate failed":
        return base
    if not title_primary_relevance(paper):
        return "title-primary stream relevance gate failed"
    return None


def candidate_pool_v3(papers: list[core.Paper], scoring: dict[str, Any]) -> list[core.Paper]:
    selection = scoring["selection"]
    min_score = float(selection.get("candidate_min_score", 45))
    hard_max = int(selection.get("candidate_hard_max", 30))
    stream_limits = {key: int(value) for key, value in scoring.get("stream_limits", {}).items()}
    counts: dict[str, int] = {}
    selected: list[core.Paper] = []

    for paper in sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True):
        if hard_exclusion_v3(paper) is not None:
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


def feature_section_v3(paper: core.Paper, scoring: dict[str, Any]) -> str | None:
    if hard_exclusion_v3(paper) is not None:
        return None
    return strict.strict_feature_section(paper, scoring)


def install_precision_layer() -> None:
    strict.is_stream_relevant = title_primary_relevance
    strict.strict_score_relevance = score_relevance_v3
    strict.hard_exclusion_reason = hard_exclusion_v3

    core.classify_study = strict.strict_classify_study
    core.score_relevance = score_relevance_v3
    core.fetch_openalex = fetch_openalex_with_fallback
    core.select_candidate_pool = candidate_pool_v3
    core.feature_section = feature_section_v3


def main() -> int:
    install_precision_layer()
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
