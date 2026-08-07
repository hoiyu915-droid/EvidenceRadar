import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_runtime_writes_output_registry_and_run_history(tmp_path):
    (tmp_path / "config").mkdir()
    streams = {
        "lookback_days": 3,
        "per_query": 5,
        "streams": {
            "llm_l3_retrieval_grounding": {
                "sources": ["openalex"],
                "relevance_terms": ["retrieval", "grounding", "reranking"],
                "queries": ["test query"],
            }
        },
    }
    scoring = {
        "selection": {"candidate_min_score": 0, "candidate_hard_max": 30, "featured_hard_max": 8},
        "category_selection": {
            "candidate_min_score": 0,
            "candidate_hard_max": 30,
            "featured_hard_max": 8,
            "min_relevance": {"llm_research": 0},
            "section_caps": {"anchor": 8, "strong_watch": 8, "weird_but_important": 2},
            "direction_caps": {
                "candidate_max_per_direction": {"llm_research": 6},
                "featured_max_per_direction": {"llm_research": 2},
            },
        },
        "weights": {
            "evidence_quality": 0.4,
            "personal_relevance": 0.3,
            "novelty_interest": 0.2,
            "practical_impact": 0.1,
        },
        "rules": {"strong_min_evidence": 0, "strong_min_relevance": 0},
    }
    (tmp_path / "config" / "streams.yml").write_text(yaml.safe_dump(streams), encoding="utf-8")
    (tmp_path / "config" / "scoring.yml").write_text(yaml.safe_dump(scoring), encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import sys
from src import categories, clinical, formal_taxonomy, history, quality, radar

quality.install()
clinical.install()
categories.install()
formal_taxonomy.install()
history.install()

def fake_openalex(query, stream, start_date, end_date, max_results):
    return [radar.Paper(
        title="Retrieval grounding and reranking for large language models",
        abstract="A benchmark of evidence selection and citation verification.",
        authors=["Example A"],
        journal_or_venue="ACL Findings",
        publication_date="2026-08-08",
        stream=stream,
        source="Test",
        doi="10.1000/runtime-smoke",
        open_access=True,
        publication_types=["review"],
    )]

radar.fetch_openalex = fake_openalex
radar.enrich_europe_pmc = lambda paper: None
sys.argv = [
    "radar", "--streams", "config/streams.yml", "--scoring", "config/scoring.yml",
    "--end-at", "2026-08-08T12:00:00+09:00",
]
raise SystemExit(radar.main())
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    outputs = list((tmp_path / "daily").glob("*.md"))
    assert len(outputs) == 1
    assert "L3｜Retrieval & Grounding" in outputs[0].read_text(encoding="utf-8")
    assert "Formal version verified" in outputs[0].read_text(encoding="utf-8")
    html_outputs = list((tmp_path / "daily").glob("*.html"))
    assert len(html_outputs) == 1
    html_report = html_outputs[0].read_text(encoding="utf-8")
    assert "<!doctype html>" in html_report
    assert "Retrieval grounding and reranking" in html_report
    assert "<script" not in html_report
    registry = json.loads((tmp_path / "state" / "literature_registry.json").read_text())
    assert "doi:10.1000/runtime-smoke" in registry["works"]
    run = json.loads((tmp_path / "state" / "run_history.jsonl").read_text().splitlines()[-1])
    assert run["new_works"] == 1
    assert run["event_qualified_count"] == 1
