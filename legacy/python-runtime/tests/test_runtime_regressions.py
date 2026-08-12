from src import quality
from src import run as _runtime_guards  # noqa: F401 - installs classification guards
from src.radar import Paper


def make_paper(title: str, abstract: str = "", types=None) -> Paper:
    return Paper(
        title=title,
        abstract=abstract,
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-07-12",
        stream="fitness_health",
        source="Test",
        publication_types=types or [],
    )


def test_adult_rat_longitudinal_study_is_preclinical():
    item = make_paper(
        "Longitudinal muscle morphology after immobilization in adult rats",
        "Adult female rats were followed during recovery.",
    )
    assert quality.classify_study(item)[:2] == ("Preclinical study", "Preclinical/U")


def test_explicit_animal_model_review_is_preclinical_even_with_human_translation():
    item = make_paper(
        "Exercise-mediated cardiac protection: Evidence from Animal Models",
        "This review discusses possible translation to human clinical practice.",
        types=["Review"],
    )
    assert quality.classify_study(item)[:2] == ("Preclinical synthesis", "Preclinical/U")
