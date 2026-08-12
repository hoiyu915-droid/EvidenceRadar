from src import categories
from src import formal_taxonomy as taxonomy
from src.radar import Paper


def paper(title: str, stream: str, abstract: str = "") -> Paper:
    return Paper(
        title=title,
        abstract=abstract,
        authors=[],
        journal_or_venue="ACL Findings",
        publication_date="2026-08-08",
        stream=stream,
        source="Test",
        relevance_score=90,
        interest_score=80,
        evidence_score=70,
        total_score=80,
    )


def test_venue_is_not_taxonomy():
    item = paper(
        "A study unrelated to language models",
        "sport_science",
    )
    assert taxonomy.directions_for(item) == []
    assert categories.category_for(item) == "sport_science"


def test_retrieval_memory_agent_paper_is_multilabel():
    item = paper(
        "Retrieval-grounded memory for large language model agents",
        "llm_l3_retrieval_grounding",
        "We study reranking, episodic memory, planning, tool use, and citation verification.",
    )
    assert taxonomy.directions_for(item) == ["L3", "L4", "L5"]
    assert categories.category_for(item) == "llm_research"


def test_human_relationship_stream_stays_independent():
    item = paper(
        "Attachment to large language model companions",
        "human_h1_llm_relationship",
        "A longitudinal study of companionship, trust, and loneliness.",
    )
    assert "H1" in taxonomy.directions_for(item)
    assert categories.category_for(item) == "human_ai"


def test_navigation_terms_are_not_direction_names():
    assert taxonomy.canonical_problem("RAG 2.0") == (
        "retrieval planning / reranking / grounding / evidence selection"
    )
    assert "RAG 2.0" not in taxonomy.DIRECTION_TITLES.values()


def test_diverse_order_reserves_active_directions():
    papers = []
    for index in range(8):
        item = paper(
            f"Large language model retrieval grounding benchmark {index}",
            "llm_l3_retrieval_grounding",
        )
        item.total_score = 100 - index
        papers.append(item)
    memory = paper(
        "Large language model episodic memory and personalization",
        "llm_l4_memory_personalization",
    )
    memory.total_score = 60
    agent = paper(
        "Large language model agent planning and tool use",
        "llm_l5_agents_decision",
    )
    agent.total_score = 59
    ordered = taxonomy.diverse_order(papers + [memory, agent], 5, max_per_direction=3)
    assert {taxonomy.primary_direction(item) for item in ordered} == {"L3", "L4", "L5"}
    assert sum(taxonomy.primary_direction(item) == "L3" for item in ordered) == 3
