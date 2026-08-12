from src import quality
from src.radar import Paper


def paper(title: str, abstract: str = "", stream: str = "sport_science", types=None) -> Paper:
    return Paper(
        title=title,
        abstract=abstract,
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-07-12",
        stream=stream,
        source="Test",
        publication_types=types or [],
    )


def test_protocol_cannot_impersonate_rct():
    item = paper(
        "Resistance training: protocol for a pilot randomized controlled trial",
        "Participants will be randomized.",
        types=["Randomized Controlled Trial"],
    )
    assert quality.classify_study(item)[:2] == ("Protocol", "Protocol/U")
    assert quality.hard_exclusion_reason(item) == "protocol/design paper"


def test_letter_cannot_impersonate_rct():
    item = paper(
        "Re: Resistance training in asthma: a randomized trial",
        "This letter discusses a trial.",
        types=["Letter"],
    )
    assert quality.classify_study(item)[:2] == ("Correspondence", "Letter/U")


def test_scoping_review_is_not_systematic_review_anchor():
    item = paper("Injury risk in youth sport: a scoping review", types=["Review"])
    assert quality.classify_study(item)[:2] == ("Scoping review", "Scope/B")


def test_observational_title_overrides_bad_rct_metadata():
    item = paper(
        "A retrospective cohort comparison of rehabilitation strategies",
        "Previous randomized trials reported mixed results.",
        types=["Randomized Controlled Trial"],
    )
    assert quality.classify_study(item)[:2] == ("Cohort study", "Cohort/B+")


def test_animal_longitudinal_study_is_preclinical():
    item = paper(
        "Longitudinal muscle morphology after immobilization in rats",
        "Female rats were followed during recovery.",
    )
    assert quality.classify_study(item)[:2] == ("Preclinical study", "Preclinical/U")


def test_animal_review_is_preclinical_synthesis():
    item = paper(
        "Exercise-mediated cardiac protection: evidence from animal models",
        "A narrative review of rodent studies.",
        stream="fitness_health",
        types=["Review"],
    )
    assert quality.classify_study(item)[:2] == ("Preclinical synthesis", "Preclinical/U")


def test_incidental_exercise_in_abstract_does_not_pass_sport_gate():
    item = paper(
        "Surgery versus endobronchial valves for lung bullae",
        "Exercise capacity was a secondary outcome.",
    )
    assert not quality.is_stream_relevant(item)


def test_generic_biomechanical_plaque_review_does_not_pass_sport_gate():
    item = paper("Biomechanical determinants of coronary plaque erosion")
    assert not quality.is_stream_relevant(item)


def test_c_reactive_protein_recovery_is_not_sport_nutrition():
    item = paper(
        "Longitudinal C-reactive protein during postoperative recovery",
        stream="sport_nutrition",
    )
    assert not quality.is_stream_relevant(item)


def test_true_sport_nutrition_title_passes():
    item = paper(
        "Rehydration failure after rapid weight regain in elite judo athletes",
        stream="sport_nutrition",
    )
    assert quality.is_stream_relevant(item)


def test_llm_social_requires_technology_and_relational_signal():
    false_item = paper(
        "The platform economy and commercialization of sport participation",
        stream="llm_social",
    )
    true_item = paper(
        "Attachment to AI companions after long-term chatbot use",
        "A study of loneliness and human-AI relationships.",
        stream="llm_social",
    )
    assert not quality.is_stream_relevant(false_item)
    assert quality.is_stream_relevant(true_item)


def test_real_rct_remains_rct():
    item = paper(
        "Creatine supplementation and sprint performance: a randomized controlled trial",
        "Athletes were randomly assigned to creatine or placebo.",
        stream="sport_nutrition",
        types=["Randomized Controlled Trial"],
    )
    assert quality.classify_study(item) == ("Randomized controlled trial", "RCT/A", 88)
