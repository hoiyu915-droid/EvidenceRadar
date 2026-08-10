from src.radar import Paper, classify_study, deduplicate, normalize_doi, normalize_title


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC.1 ") == "10.1000/abc.1"


def test_normalize_title():
    assert normalize_title("AI-Companion: A Study") == "ai companion a study"


def test_classify_randomized_trial():
    paper = Paper(
        title="A randomized trial of creatine",
        abstract="Participants were randomized to intervention or placebo.",
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-01-01",
        stream="sport_nutrition",
        source="PubMed",
    )
    design, tier, score = classify_study(paper)
    assert design == "Randomized controlled trial"
    assert tier == "RCT/A"
    assert score == 88


def test_deduplicate_merges_streams():
    first = Paper(
        title="Exercise and Protein",
        abstract="",
        authors=[],
        journal_or_venue="A",
        publication_date="2026-01-01",
        stream="sport_science",
        source="PubMed",
        doi="10.1/test",
        total_score=60,
    )
    second = Paper(
        title="Exercise and Protein",
        abstract="",
        authors=[],
        journal_or_venue="A",
        publication_date="2026-01-01",
        stream="sport_nutrition",
        source="OpenAlex",
        doi="https://doi.org/10.1/TEST",
        total_score=70,
    )
    result = deduplicate([first, second])
    assert len(result) == 1
    assert set(result[0].all_streams()) == {"sport_science", "sport_nutrition"}
