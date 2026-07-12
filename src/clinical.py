"""Clinical Medicine stream support for EvidenceRadar."""

from __future__ import annotations

from typing import Iterable

from . import quality
from . import radar as core

_BASE_IS_STREAM_RELEVANT = quality.is_stream_relevant
_BASE_SCORE_RELEVANCE = quality.score_relevance

CLINICAL_JOURNAL_SIGNALS = (
    "jama network open",
    "eclinicalmedicine",
    "bmc medicine",
    "bmj open",
    "communications medicine",
    "plos medicine",
    "bmj medicine",
    "lancet regional health",
    "new england journal of medicine",
    "the lancet",
    "jama",
    "the bmj",
    "annals of internal medicine",
    "nature medicine",
)

CLINICAL_TITLE_SIGNALS = (
    "randomized controlled trial",
    "randomised controlled trial",
    "clinical trial",
    "systematic review",
    "meta-analysis",
    "meta analysis",
    "clinical guideline",
    "practice guideline",
    "consensus statement",
    "diagnosis",
    "screening",
    "treatment",
    "therapy",
    "prevention",
    "prognosis",
    "mortality",
    "morbidity",
    "adverse event",
    "disease risk",
    "public health",
)

STRONG_CLINICAL_TYPES = {
    "randomized controlled trial",
    "controlled clinical trial",
    "clinical trial",
    "meta-analysis",
    "systematic review",
    "practice guideline",
    "guideline",
}


def is_stream_relevant(paper: core.Paper) -> bool:
    if paper.stream != "clinical_medicine":
        return _BASE_IS_STREAM_RELEVANT(paper)

    title = paper.title.casefold()
    journal = paper.journal_or_venue.casefold()
    publication_types = {value.casefold() for value in paper.publication_types if value}
    return (
        quality.has_any(journal, CLINICAL_JOURNAL_SIGNALS)
        or quality.has_any(title, CLINICAL_TITLE_SIGNALS)
        or bool(publication_types & STRONG_CLINICAL_TYPES)
    )


def score_relevance(paper: core.Paper, relevance_terms: Iterable[str]) -> int:
    if paper.stream != "clinical_medicine":
        return _BASE_SCORE_RELEVANCE(paper, relevance_terms)
    if not is_stream_relevant(paper):
        return 0

    text = quality.combined_text(paper)
    title = paper.title.casefold()
    journal = paper.journal_or_venue.casefold()
    matched = {term.casefold() for term in relevance_terms if term.casefold() in text}
    title_matches = sum(1 for term in matched if term in title)
    journal_bonus = 8 if quality.has_any(journal, CLINICAL_JOURNAL_SIGNALS) else 0
    return min(100, 68 + journal_bonus + len(matched) * 4 + title_matches * 4)


def install() -> None:
    quality.is_stream_relevant = is_stream_relevant
    quality.score_relevance = score_relevance
    core.score_relevance = score_relevance
