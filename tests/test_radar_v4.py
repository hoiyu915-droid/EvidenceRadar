from src.radar import Paper
from src import radar_v3 as precision
from src import radar_v4 as runtime


def paper(title: str, abstract: str = "", stream: str = "sport_science") -> Paper:
    return Paper(
        title=title,
        abstract=abstract,
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-07-12",
        stream=stream,
        source="Test",
    )


def test_clinical_abstract_incidental_exercise_is_not_sport_science():
    item = paper(
        "Retrospective comparison of surgery and endobronchial valves for lung bullae",
        "Exercise capacity was one of several secondary outcomes.",
        "sport_science",
    )
    assert not precision.title_primary_relevance(item)


def test_infant_mortality_paper_is_not_fitness_health():
    item = paper(
        "Transfusion volume and mortality in extremely preterm infants",
        "Physical activity was not measured.",
        "fitness_health",
    )
    assert not precision.title_primary_relevance(item)


def test_general_nutrition_rct_is_not_sport_nutrition():
    item = paper(
        "Probiotics for enhanced tissue carotenoid status: a randomized trial",
        "Physical activity was recorded as a covariate.",
        "sport_nutrition",
    )
    assert not precision.title_primary_relevance(item)


def test_true_sport_nutrition_title_passes():
    item = paper(
        "Rehydration failure after rapid weight regain in elite judo athletes",
        "Repeated-measures study.",
        "sport_nutrition",
    )
    assert precision.title_primary_relevance(item)


def test_llm_relational_title_passes():
    item = paper(
        "Attachment to AI companions after long-term chatbot use",
        "A study of loneliness and human-AI relationships.",
        "llm_social",
    )
    assert precision.title_primary_relevance(item)


def test_runtime_install_does_not_recurse():
    runtime.install()
    item = paper("Resistance exercise and muscle strength in older adults")
    assert runtime.hard_exclusion_v4(item) is None
