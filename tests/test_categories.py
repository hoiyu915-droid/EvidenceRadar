from src import categories, clinical, quality
from src.radar import Paper

quality.install()
clinical.install()


def make_paper(stream: str, index: int, total_score: float = 90.0, strong: bool = False) -> Paper:
    titles = {
        "clinical_medicine": f"Randomized controlled trial of treatment in patients {index}",
        "sport_science": f"Resistance training and athletic performance {index}",
        "sport_nutrition": f"Protein nutrition in athletes and exercise performance {index}",
        "fitness_health": f"Physical activity and mortality in adults {index}",
        "llm_social": f"AI companion attachment and human relationship study {index}",
    }
    design = "Qualitative study" if strong else "Randomized controlled trial"
    tier = "Qual/A" if strong else "RCT/A"
    evidence = 65 if strong else 88
    return Paper(
        title=titles[stream],
        abstract="Participants were studied with a valid design.",
        authors=[],
        journal_or_venue="JAMA Network Open" if stream == "clinical_medicine" else "Test Journal",
        publication_date="2026-07-12",
        stream=stream,
        source="Test",
        study_design=design,
        evidence_tier=tier,
        evidence_score=evidence,
        relevance_score=90,
        interest_score=75,
        practical_score=70,
        total_score=total_score,
    )


def test_category_assignment_uses_four_top_level_pools():
    assert categories.category_for(make_paper("clinical_medicine", 1)) == "clinical_medicine"
    assert categories.category_for(make_paper("sport_science", 1)) == "sport_science"
    assert categories.category_for(make_paper("sport_nutrition", 1)) == "sport_nutrition_fitness"
    assert categories.category_for(make_paper("fitness_health", 1)) == "sport_nutrition_fitness"
    assert categories.category_for(make_paper("llm_social", 1)) == "llm_social"


def test_sport_cannot_consume_clinical_quota():
    papers = []
    papers.extend(make_paper("sport_science", i, 100 - i / 100) for i in range(40))
    papers.extend(make_paper("clinical_medicine", i, 70 - i / 100) for i in range(40))
    papers.extend(make_paper("sport_nutrition", i, 80 - i / 100) for i in range(40))
    papers.extend(make_paper("llm_social", i, 75 - i / 100) for i in range(40))

    scoring = {
        "selection": {"candidate_min_score": 45, "candidate_hard_max": 30},
        "category_selection": {"candidate_hard_max": 30},
    }
    selected = categories.select_candidate_pool(papers, scoring)
    counts = {category: 0 for category in categories.CATEGORY_ORDER}
    for paper in selected:
        counts[categories.category_for(paper)] += 1

    assert len(selected) == 120
    assert counts == {
        "clinical_medicine": 30,
        "sport_science": 30,
        "sport_nutrition_fitness": 30,
        "llm_social": 30,
    }


def test_featured_is_capped_independently_per_category():
    papers = []
    for stream in ("clinical_medicine", "sport_science", "sport_nutrition", "llm_social"):
        papers.extend(make_paper(stream, i) for i in range(4))
        papers.extend(make_paper(stream, i + 10, strong=True) for i in range(4))

    scoring = {
        "selection": {
            "featured_hard_max": 8,
            "section_caps": {"anchor": 4, "strong_watch": 4, "weird_but_important": 2},
        },
        "category_selection": {
            "featured_hard_max": 8,
            "section_caps": {"anchor": 4, "strong_watch": 4, "weird_but_important": 2},
        },
        "rules": {
            "anchor_min_evidence": 75,
            "strong_min_evidence": 55,
            "strong_min_relevance": 60,
            "weird_min_interest": 85,
        },
    }
    featured = categories.select_featured(papers, scoring)
    for category in categories.CATEGORY_ORDER:
        assert sum(len(items) for items in featured[category].values()) == 8
