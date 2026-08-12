from datetime import datetime
from zoneinfo import ZoneInfo

from src import events
from src.radar import Paper

JST = ZoneInfo("Asia/Tokyo")


def paper() -> Paper:
    return Paper(
        title="A formal research work",
        abstract="",
        authors=[],
        journal_or_venue="Test",
        publication_date="2026-08-08",
        stream="llm_l9_evaluation_measurement",
        source="Test source",
    )


def test_precise_timestamp_is_filtered_by_true_72_hour_window():
    item = paper()
    events.add_event(
        item,
        "first_formal_indexing",
        "2026-08-05T12:00:01+09:00",
        source="Registry",
        source_field="first-index-date",
        precision="timestamp",
    )
    end_at = datetime(2026, 8, 8, 12, 0, tzinfo=JST)
    cutoff = datetime(2026, 8, 5, 12, 0, tzinfo=JST)
    assert events.filter_window([item], cutoff, end_at) == [item]


def test_date_only_cutoff_day_is_rejected_as_boundary_ambiguous():
    item = paper()
    events.add_event(
        item,
        "formal_version_verified",
        "2026-08-05",
        source="Provider",
        source_field="publication_date",
    )
    end_at = datetime(2026, 8, 8, 12, 0, tzinfo=JST)
    cutoff = datetime(2026, 8, 5, 12, 0, tzinfo=JST)
    assert events.filter_window([item], cutoff, end_at) == []


def test_provider_date_is_formal_evidence_not_an_updated_event():
    item = paper()
    events.ensure_provider_event(item)
    assert [event["type"] for event in item.events] == ["formal_version_verified"]
    assert item.events[0]["source_field"] == "publication_date"
