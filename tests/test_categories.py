from datetime import datetime
from zoneinfo import ZoneInfo

from src import categories, clinical, formal_taxonomy, quality
from src.radar import Paper

quality.install()
clinical.install()
categories.install()
formal_taxonomy.install()


def make_paper(stream: str, index: int, total_score: float = 90.0, strong: bool = False) -> Paper:
    titles = {
        "clinical_medicine": f"Randomized controlled trial of treatment in patients {index}",
        "sport_science": f"Resistance training and athletic performance {index}",
        "sport_nutrition": f"Protein nutrition in athletes and exercise performance {index}",
        "fitness_health": f"Physical activity and mortality in adults {index}",
        "llm_social": f"AI companion attachment and human relationship study {index}",
        "llm_l3_retrieval_grounding": f"Large language model retrieval grounding and reranking benchmark {index}",
        "human_h1_llm_relationship": f"Large language model companion attachment and human relationship study {index}",
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
        one_line_reason="Randomized controlled trial；證據設計優先；與臨床醫學核心證據線高度相關",
    )


def test_category_assignment_uses_five_top_level_pools():
    assert categories.category_for(make_paper("clinical_medicine", 1)) == "clinical_medicine"
    assert categories.category_for(make_paper("sport_science", 1)) == "sport_science"
    assert categories.category_for(make_paper("sport_nutrition", 1)) == "sport_nutrition_fitness"
    assert categories.category_for(make_paper("fitness_health", 1)) == "sport_nutrition_fitness"
    assert categories.category_for(make_paper("llm_l3_retrieval_grounding", 1)) == "llm_research"
    assert categories.category_for(make_paper("human_h1_llm_relationship", 1)) == "human_ai"


def test_sport_cannot_consume_clinical_quota():
    papers = []
    papers.extend(make_paper("sport_science", i, 100 - i / 100) for i in range(40))
    papers.extend(make_paper("clinical_medicine", i, 70 - i / 100) for i in range(40))
    papers.extend(make_paper("sport_nutrition", i, 80 - i / 100) for i in range(40))
    papers.extend(make_paper("llm_l3_retrieval_grounding", i, 75 - i / 100) for i in range(40))
    papers.extend(make_paper("human_h1_llm_relationship", i, 74 - i / 100) for i in range(40))

    scoring = {
        "selection": {"candidate_min_score": 45, "candidate_hard_max": 30},
        "category_selection": {
            "candidate_hard_max": 30,
            "direction_caps": {"candidate_max_per_direction": 30},
        },
    }
    selected = categories.select_candidate_pool(papers, scoring)
    counts = {category: 0 for category in categories.CATEGORY_ORDER}
    for paper in selected:
        counts[categories.category_for(paper)] += 1

    assert len(selected) == 150
    assert counts == {
        "clinical_medicine": 30,
        "sport_science": 30,
        "sport_nutrition_fitness": 30,
        "llm_research": 30,
        "human_ai": 30,
    }


def test_featured_is_capped_independently_per_category():
    papers = []
    for stream in (
        "clinical_medicine", "sport_science", "sport_nutrition",
        "llm_l3_retrieval_grounding", "human_h1_llm_relationship",
    ):
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
            "direction_caps": {"featured_max_per_direction": 8},
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


def test_cross_clinical_sport_reason_uses_final_category():
    paper = make_paper("clinical_medicine", 1)
    paper.secondary_streams = ["sport_science"]
    scoring = {
        "selection": {"candidate_min_score": 45, "candidate_hard_max": 30},
        "category_selection": {"candidate_hard_max": 30},
    }
    selected = categories.select_candidate_pool([paper], scoring)
    assert len(selected) == 1
    assert selected[0].one_line_reason.endswith("跨臨床與運動科學研究線")
    assert "與臨床醫學核心證據線高度相關" not in selected[0].one_line_reason


def test_render_rewrites_reason_after_core_rebuild():
    paper = make_paper("clinical_medicine", 1)
    paper.secondary_streams = ["sport_science"]
    paper.one_line_reason = "Randomized controlled trial；證據設計優先；與臨床醫學核心證據線高度相關"
    featured = {
        category: {"anchor": [], "strong_watch": [], "weird_but_important": []}
        for category in categories.CATEGORY_ORDER
    }
    featured["sport_science"]["anchor"] = [paper]
    markdown = categories.render_markdown(
        datetime(2026, 7, 12, 22, 45, tzinfo=ZoneInfo("Asia/Tokyo")),
        featured,
        [paper],
        retrieved_count=1,
        deduplicated_count=1,
        excluded_count=0,
        warnings=[],
    )
    assert "跨臨床與運動科學研究線" in markdown
    sport_section = markdown.split("## Sport Science", 1)[1]
    assert "與臨床醫學核心證據線高度相關" not in sport_section
