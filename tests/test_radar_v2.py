from src.radar import Paper
from src.radar_v2 import (
    hard_exclusion_reason,
    is_stream_relevant,
    strict_classify_study,
    strict_score_relevance,
)


def paper(title: str, abstract: str = "", stream: str = "sport_science", types=None) -> Paper:
    return Paper(
        title=title,
        abstract=abstract,
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-07-12",
        stream=stream,
        source="PubMed",
        publication_types=types or [],
    )


def test_protocol_cannot_impersonate_rct():
    item = paper(
        "Effects of velocity-based resistance training: protocol for a pilot randomized controlled trial",
        "Participants will be randomized in a future trial.",
        types=["Randomized Controlled Trial"],
    )
    assert strict_classify_study(item)[:2] == ("Protocol", "Protocol/U")
    assert hard_exclusion_reason(item) == "protocol/design paper"


def test_letter_cannot_impersonate_rct():
    item = paper(
        "Re: Effects of resistance training in asthma: a randomized trial",
        "This letter discusses a triple-blind randomized trial.",
        types=["Letter"],
    )
    assert strict_classify_study(item)[:2] == ("Correspondence", "Letter/U")
    assert hard_exclusion_reason(item) == "correspondence/editorial"


def test_scoping_review_is_not_systematic_review_anchor():
    item = paper(
        "Risk factors for injury in youth sport: a methodological scoping review",
        "We conducted a systematic search.",
        types=["Review"],
    )
    assert strict_classify_study(item)[:2] == ("Scoping review", "Scope/B")


def test_observational_title_overrides_stray_randomized_language():
    item = paper(
        "A retrospective cohort comparison of rehabilitation strategies",
        "Previous randomized trials reported mixed findings.",
        types=["Randomized Controlled Trial"],
    )
    assert strict_classify_study(item)[:2] == ("Cohort study", "Cohort/B+")


def test_sport_nutrition_requires_sport_and_nutrition_context():
    false_positive = paper(
        "Nursing leadership and digital transformation",
        "Staff training included nutrition education.",
        stream="sport_nutrition",
    )
    true_positive = paper(
        "Protein timing after resistance exercise in athletes",
        "Sports nutrition intervention after exercise training.",
        stream="sport_nutrition",
    )
    assert not is_stream_relevant(false_positive)
    assert strict_score_relevance(false_positive, ["protein", "nutrition"]) == 0
    assert is_stream_relevant(true_positive)


def test_llm_social_requires_technology_and_relational_signal():
    false_positive = paper(
        "The platform economy and commercialization of sport participation",
        "A systematic review of social participation.",
        stream="llm_social",
    )
    true_positive = paper(
        "Attachment to AI companions after long-term chatbot use",
        "A longitudinal study of loneliness and human-AI relationships.",
        stream="llm_social",
    )
    assert not is_stream_relevant(false_positive)
    assert is_stream_relevant(true_positive)


def test_real_randomized_trial_remains_rct():
    item = paper(
        "Creatine supplementation and sprint performance: a randomized controlled trial",
        "Athletes were randomly assigned to creatine or placebo.",
        stream="sport_nutrition",
        types=["Randomized Controlled Trial"],
    )
    assert strict_classify_study(item) == ("Randomized controlled trial", "RCT/A", 88)
