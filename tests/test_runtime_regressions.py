from src import run  # applies runtime human-signal override
from src.radar import Paper
from src import quality


def test_adult_rat_longitudinal_study_is_preclinical():
    item = Paper(
        title="Longitudinal muscle morphology after immobilization in adult rats",
        abstract="Adult female rats were followed during recovery.",
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-07-12",
        stream="sport_science",
        source="Test",
    )
    assert quality.classify_study(item)[:2] == ("Preclinical study", "Preclinical/U")
