import json
from pathlib import Path

from src.history import LiteratureRegistry
from src.radar import Paper


def paper(title: str, doi: str = "", stream: str = "sport_science") -> Paper:
    return Paper(
        title=title,
        abstract="",
        authors=["Example A"],
        journal_or_venue="Test",
        publication_date="2026-08-08",
        stream=stream,
        source="Test",
        doi=doi,
    )


def registry(tmp_path: Path) -> LiteratureRegistry:
    return LiteratureRegistry(
        tmp_path / "literature_registry.json",
        legacy_path=tmp_path / "missing-legacy.json",
        run_history_path=tmp_path / "run_history.jsonl",
        daily_dir=tmp_path / "daily",
    )


def test_cross_run_doi_duplicate_is_suppressed(tmp_path):
    first = registry(tmp_path)
    first.begin_run()
    assert len(first.filter_new([paper("First title", "10.1000/example")])) == 1
    first.save_success()

    second = registry(tmp_path)
    second.begin_run()
    assert second.filter_new([paper("Publisher-corrected title", "https://doi.org/10.1000/EXAMPLE")]) == []
    assert second.run_stats["history_duplicates"] == 1


def test_normalized_title_is_fallback_identity(tmp_path):
    state = registry(tmp_path)
    state.begin_run()
    assert len(state.filter_new([paper("Memory, Retrieval & Grounding")])) == 1
    assert state.filter_new([paper("Memory Retrieval Grounding")]) == []


def test_success_writes_registry_and_run_ledger(tmp_path):
    state = registry(tmp_path)
    state.begin_run()
    state.filter_new([paper("Persistent record", "10.1000/persist")])
    state.save_success()
    payload = json.loads((tmp_path / "literature_registry.json").read_text())
    assert "doi:10.1000/persist" in payload["works"]
    run = json.loads((tmp_path / "run_history.jsonl").read_text().splitlines()[-1])
    assert run["status"] == "success"
    assert run["new_works"] == 1


def test_preprint_upgrade_is_a_new_event_not_a_duplicate(tmp_path):
    state = registry(tmp_path)
    state.begin_run()
    preprint = paper("A language model benchmark", "10.1000/upgrade")
    preprint.is_preprint = True
    assert len(state.filter_new([preprint])) == 1
    state.save_success()

    upgraded = registry(tmp_path)
    upgraded.begin_run()
    formal = paper("A language model benchmark", "10.1000/upgrade")
    assert len(upgraded.filter_new([formal])) == 1
    assert upgraded.run_stats["new_events"] == 1
    assert upgraded.run_stats["history_duplicates"] == 0


def test_new_accepted_manuscript_location_is_a_timestamped_event(tmp_path):
    state = registry(tmp_path)
    state.begin_run()
    original = paper("Repository transition", "10.1000/aam")
    original.open_access = False
    assert len(state.filter_new([original])) == 1
    state.save_success()

    upgraded = registry(tmp_path)
    upgraded.begin_run()
    accepted = paper("Repository transition", "10.1000/aam")
    accepted.open_access = True
    accepted.repository_versions = ["acceptedVersion"]
    accepted.fulltext_urls = ["https://repository.example/aam.pdf"]
    assert len(upgraded.filter_new([accepted])) == 1
    assert any(
        event["type"] == "author_accepted_manuscript_first_available"
        and event["precision"] == "timestamp"
        for event in accepted.events
    )


def test_legacy_fulltext_ledger_is_migrated_into_dedupe_index(tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "works": {
                    "doi:10.1000/legacy": {
                        "title": "Legacy full-text work",
                        "identifiers": {"doi": "10.1000/legacy"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = LiteratureRegistry(
        tmp_path / "registry.json",
        legacy_path=legacy,
        run_history_path=tmp_path / "runs.jsonl",
        daily_dir=tmp_path / "daily",
    )
    state.begin_run()
    assert state.filter_new([paper("Legacy full-text work", "10.1000/legacy")]) == []


def test_historical_daily_output_is_bootstrapped(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "20260801 0617.Rader.md").write_text(
        "#### 1. [Historical radar work](https://doi.org/10.1000/daily)\n\n"
        "- **IDs:** DOI `10.1000/daily`\n",
        encoding="utf-8",
    )
    state = LiteratureRegistry(
        tmp_path / "registry.json",
        legacy_path=tmp_path / "missing.json",
        run_history_path=tmp_path / "runs.jsonl",
        daily_dir=daily,
    )
    state.begin_run()
    assert state.filter_new([paper("Historical radar work", "10.1000/daily")]) == []
