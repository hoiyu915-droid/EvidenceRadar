"""Formal LLM and human-AI research taxonomy for EvidenceRadar.

Venues are publication identity only.  Classification is based on the
research problem a work studies, and a work may carry multiple directions.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from . import quality
from . import radar as core

LLM_DIRECTIONS = (
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
    "L7",
    "L8",
    "L9",
)
HUMAN_DIRECTIONS = ("H1", "H2")
DIRECTION_ORDER = (*LLM_DIRECTIONS, *HUMAN_DIRECTIONS)

DIRECTION_TITLES = {
    "L1": "Model Behavior & Alignment",
    "L2": "Context & Inference-Time Computation",
    "L3": "Retrieval & Grounding",
    "L4": "Memory & Personalization",
    "L5": "Agents & Decision Systems",
    "L6": "Multi-Agent Systems",
    "L7": "Systems, Runtime & Interfaces",
    "L8": "Training, Adaptation & Model Architecture",
    "L9": "Evaluation, Benchmarks & Measurement",
    "H1": "Human–LLM Interaction / Relationship",
    "H2": "Human–AI Interaction / HCI",
}

STREAM_TO_DIRECTION = {
    "llm_l1_model_behavior": "L1",
    "llm_l2_context_inference": "L2",
    "llm_l3_retrieval_grounding": "L3",
    "llm_l4_memory_personalization": "L4",
    "llm_l5_agents_decision": "L5",
    "llm_l6_multi_agent": "L6",
    "llm_l7_systems_runtime": "L7",
    "llm_l8_training_architecture": "L8",
    "llm_l9_evaluation_measurement": "L9",
    "human_h1_llm_relationship": "H1",
    "human_h2_ai_hci": "H2",
    # Backward-compatible interpretation of the former mixed stream.
    "llm_social": "H1",
}

LLM_SIGNALS = (
    "large language model",
    "language model",
    "llm",
    "foundation model",
    "generative ai",
    "chatbot",
    "vision-language model",
)
AI_SIGNALS = (*LLM_SIGNALS, "artificial intelligence", "human-ai", "human–ai", "ai system")

DIRECTION_TERMS = {
    "L1": (
        "instruction following", "sycophancy", "hallucination", "uncertainty",
        "calibration", "robustness", "jailbreak", "alignment", "model behavior",
        "model behaviour", "safety",
    ),
    "L2": (
        "long context", "long-context", "context selection", "context compression",
        "test-time compute", "inference-time compute", "prompting", "in-context learning",
        "reasoning scaffold", "self-reflection", "self reflection",
        "reasoning verification", "answer verification", "self-verification",
    ),
    "L3": (
        "retrieval", "reranking", "re-ranking", "query rewriting", "grounding",
        "citation verification", "evidence selection", "source authority", "rag",
        "retrieval-augmented", "retrieval augmented",
    ),
    "L4": (
        "episodic memory", "semantic memory", "working memory", "continual memory",
        "memory consolidation", "forgetting", "temporal decay", "memory routing",
        "personalization", "personalisation", "user model", "longitudinal adaptation",
    ),
    "L5": (
        "planning", "tool use", "tool-use", "action selection", "agent loop",
        "computer-use", "computer use", "browser agent", "autonomous execution",
        "long-horizon", "long horizon", "credit assignment", "agent safety",
    ),
    "L6": (
        "multi-agent", "multi agent", "multiagent", "agent coordination",
        "role assignment", "task decomposition", "debate", "cooperation",
        "competition", "collective failure", "emergent behavior", "emergent behaviour",
    ),
    "L7": (
        "serving", "inference system", "runtime", "orchestration", "state machine",
        "directed acyclic graph", "model context protocol", "mcp", "protocol",
        "sandbox", "observability", "latency", "reliability",
    ),
    "L8": (
        "pretraining", "pre-training", "post-training", "post training", "sft",
        "reinforcement learning", "preference optimization", "preference optimisation",
        "distillation", "continual learning", "peft", "mixture of experts", "moe",
        "data curriculum", "synthetic data", "model architecture",
    ),
    "L9": (
        "benchmark", "evaluation", "measurement", "contamination", "judge reliability",
        "llm-as-a-judge", "llm as a judge", "evaluation validity", "ecological validity",
        "metric", "test set",
    ),
    "H1": (
        "attachment", "companionship", "companion", "anthropomorphism", "trust",
        "emotional dependence", "emotional dependency", "social connection",
        "well-being", "wellbeing", "persona", "relationship", "relational",
        "intimacy", "loneliness", "self-disclosure", "parasocial",
    ),
    "H2": (
        "interaction design", "decision support", "human-ai interaction",
        "human–ai interaction", "human ai interaction", "agency", "collaboration",
        "appropriation", "trust", "user study", "human-computer interaction", "hci",
        "computer-supported cooperative work", "cscw",
    ),
}

NAVIGATION_ALIASES = {
    "graph engineering": "workflow/state-machine orchestration",
    "loop engineering": "iterative agent control / planning",
    "harness ai": "runtime/orchestration/tool execution",
    "rag 2.0": "retrieval planning / reranking / grounding / evidence selection",
    "memory layers": "episodic/semantic/working memory architecture",
}


def _text(paper: core.Paper) -> str:
    return f"{paper.title} {paper.abstract}".casefold()


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term.casefold() in text for term in terms)


def is_formal_ai_stream(stream: str) -> bool:
    return stream in STREAM_TO_DIRECTION


def directions_for(paper: core.Paper) -> list[str]:
    """Return stable multi-label directions, ordered L1–L9 then H1–H2."""
    found = {
        STREAM_TO_DIRECTION[stream]
        for stream in paper.all_streams()
        if stream in STREAM_TO_DIRECTION
    }
    text = _text(paper)
    llm_context = _has_any(text, LLM_SIGNALS)
    ai_context = _has_any(text, AI_SIGNALS)
    for direction in LLM_DIRECTIONS:
        if llm_context and _has_any(text, DIRECTION_TERMS[direction]):
            found.add(direction)
    if llm_context and _has_any(text, DIRECTION_TERMS["H1"]):
        found.add("H1")
    if ai_context and _has_any(text, DIRECTION_TERMS["H2"]):
        found.add("H2")
    return [direction for direction in DIRECTION_ORDER if direction in found]


def primary_direction(paper: core.Paper) -> str | None:
    for stream in paper.all_streams():
        if stream in STREAM_TO_DIRECTION:
            return STREAM_TO_DIRECTION[stream]
    directions = directions_for(paper)
    return directions[0] if directions else None


def category_for(paper: core.Paper) -> str | None:
    directions = directions_for(paper)
    if any(direction in HUMAN_DIRECTIONS for direction in directions):
        # Explicit human research streams own the paper when deduplication also
        # found it through a technical query.
        if any(STREAM_TO_DIRECTION.get(stream) in HUMAN_DIRECTIONS for stream in paper.all_streams()):
            return "human_ai"
    if any(direction in LLM_DIRECTIONS for direction in directions):
        return "llm_research"
    if any(direction in HUMAN_DIRECTIONS for direction in directions):
        return "human_ai"
    return None


def matches_stream(paper: core.Paper) -> bool:
    direction = STREAM_TO_DIRECTION.get(paper.stream)
    if direction is None:
        return False
    title = paper.title.casefold()
    text = _text(paper)
    if direction == "H1":
        return _has_any(text, LLM_SIGNALS) and _has_any(text, DIRECTION_TERMS["H1"])
    if direction == "H2":
        return _has_any(text, AI_SIGNALS) and _has_any(text, DIRECTION_TERMS["H2"])
    return (
        _has_any(text, LLM_SIGNALS)
        and _has_any(text, DIRECTION_TERMS[direction])
        and (_has_any(title, LLM_SIGNALS) or _has_any(title, DIRECTION_TERMS[direction]))
    )


def canonical_problem(term: str) -> str:
    return NAVIGATION_ALIASES.get(term.casefold().strip(), term.strip())


def direction_label(direction: str) -> str:
    return f"{direction}｜{DIRECTION_TITLES[direction]}"


def diverse_order(
    papers: list[core.Paper],
    hard_max: int,
    *,
    max_per_direction: int,
) -> list[core.Paper]:
    """Reserve one slot per active direction, then fill globally by value."""
    ranked = sorted(papers, key=lambda item: (item.total_score, item.publication_date), reverse=True)
    groups: dict[str, list[core.Paper]] = defaultdict(list)
    unclassified: list[core.Paper] = []
    for paper in ranked:
        direction = primary_direction(paper)
        if direction:
            groups[direction].append(paper)
        else:
            unclassified.append(paper)

    selected: list[core.Paper] = []
    counts: dict[str, int] = defaultdict(int)
    active = sorted(
        groups,
        key=lambda direction: groups[direction][0].total_score,
        reverse=True,
    )
    for direction in active:
        if len(selected) >= hard_max:
            break
        selected.append(groups[direction][0])
        counts[direction] += 1

    selected_ids = {id(paper) for paper in selected}
    for paper in [*ranked, *unclassified]:
        if len(selected) >= hard_max:
            break
        if id(paper) in selected_ids:
            continue
        direction = primary_direction(paper)
        if direction and counts[direction] >= max_per_direction:
            continue
        selected.append(paper)
        selected_ids.add(id(paper))
        if direction:
            counts[direction] += 1
    return selected


_BASE_RELEVANCE = quality.score_relevance
_BASE_IS_STREAM_RELEVANT = quality.is_stream_relevant
_BASE_REASON = core.build_reason
_BASE_INTEREST = core.score_interest
_BASE_FEATURED_RENDER = core.render_featured_item
_BASE_CANDIDATE_RENDER = core.render_candidate_item


def score_relevance(paper: core.Paper, relevance_terms: Iterable[str]) -> int:
    if not is_formal_ai_stream(paper.stream):
        return _BASE_RELEVANCE(paper, relevance_terms)
    if not matches_stream(paper):
        return 0
    text = _text(paper)
    title = paper.title.casefold()
    matched = {term.casefold() for term in relevance_terms if term.casefold() in text}
    title_matches = sum(1 for term in matched if term in title)
    return min(100, 66 + len(matched) * 4 + title_matches * 5)


def score_interest(paper: core.Paper) -> int:
    score = _BASE_INTEREST(paper)
    if is_formal_ai_stream(paper.stream):
        score += min(12, max(0, len(directions_for(paper)) - 1) * 3)
    return min(100, score)


def build_reason(paper: core.Paper) -> str:
    reason = _BASE_REASON(paper)
    directions = directions_for(paper)
    if directions:
        parts = [part for part in reason.split("；") if part]
        parts.append("研究問題：" + "、".join(directions))
        return "；".join(dict.fromkeys(parts))
    return reason


def _direction_line(paper: core.Paper) -> str:
    directions = directions_for(paper)
    return "、".join(direction_label(direction) for direction in directions)


def render_featured_item(paper: core.Paper, rank: int) -> str:
    rendered = _BASE_FEATURED_RENDER(paper, rank)
    label = _direction_line(paper)
    if not label:
        return rendered
    lines = rendered.splitlines()
    insert_at = 3 if len(lines) >= 3 else len(lines)
    lines.insert(insert_at, f"- **Research directions:** {label}")
    return "\n".join(lines)


def render_candidate_item(paper: core.Paper, rank: int) -> str:
    rendered = _BASE_CANDIDATE_RENDER(paper, rank)
    label = _direction_line(paper)
    if not label:
        return rendered
    lines = rendered.splitlines()
    lines.insert(2, f"   - Research directions: {label}")
    return "\n".join(lines)


def install() -> None:
    global _BASE_RELEVANCE, _BASE_IS_STREAM_RELEVANT, _BASE_REASON, _BASE_INTEREST
    global _BASE_FEATURED_RENDER, _BASE_CANDIDATE_RENDER
    _BASE_RELEVANCE = core.score_relevance
    _BASE_IS_STREAM_RELEVANT = quality.is_stream_relevant
    _BASE_REASON = core.build_reason
    _BASE_INTEREST = core.score_interest
    _BASE_FEATURED_RENDER = core.render_featured_item
    _BASE_CANDIDATE_RENDER = core.render_candidate_item
    quality.is_stream_relevant = lambda paper: (
        matches_stream(paper) if is_formal_ai_stream(paper.stream) else _BASE_IS_STREAM_RELEVANT(paper)
    )
    quality.score_relevance = score_relevance
    core.score_relevance = score_relevance
    core.score_interest = score_interest
    core.build_reason = build_reason
    core.render_featured_item = render_featured_item
    core.render_candidate_item = render_candidate_item
