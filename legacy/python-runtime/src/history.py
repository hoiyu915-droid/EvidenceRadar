"""Persistent cross-run literature registry and duplicate suppression."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import formal_taxonomy as taxonomy
from . import radar as core

SCHEMA_VERSION = "3.0"
DEFAULT_REGISTRY = Path("state/literature_registry.json")
LEGACY_EVENT_LEDGER = Path("state/readable_fulltext_event_ledger.json")
DEFAULT_RUN_HISTORY = Path("state/run_history.jsonl")


def _now() -> datetime:
    return datetime.now(ZoneInfo(core.TIMEZONE))


def _title_key(title: str) -> str:
    normalized = core.normalize_title(title)
    return "title:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def aliases_for(paper: core.Paper) -> list[str]:
    aliases: list[str] = []
    if paper.doi:
        aliases.append("doi:" + core.normalize_doi(paper.doi))
    if paper.pmid:
        aliases.append("pmid:" + paper.pmid.casefold())
    if paper.pmcid:
        aliases.append("pmcid:" + paper.pmcid.casefold())
    if paper.openalex_id:
        aliases.append("openalex:" + paper.openalex_id.rstrip("/").rsplit("/", 1)[-1].casefold())
    for attribute, prefix in (("arxiv_id", "arxiv"), ("anthology_id", "anthology")):
        value = str(getattr(paper, attribute, "") or "").strip()
        if value:
            aliases.append(f"{prefix}:{value.casefold()}")
    aliases.append(_title_key(paper.title))
    return list(dict.fromkeys(aliases))


def _serialized_events(paper: core.Paper) -> list[dict[str, Any]]:
    return [dict(event) for event in paper.events]


def _record_aliases(key: str, record: dict[str, Any]) -> list[str]:
    aliases = [key, *record.get("aliases", [])]
    identifiers = record.get("identifiers") or {}
    for field, prefix in (
        ("doi", "doi"), ("pmid", "pmid"), ("pmcid", "pmcid"),
        ("openalex_id", "openalex"), ("arxiv_id", "arxiv"),
        ("anthology_id", "anthology"),
    ):
        value = str(identifiers.get(field) or "").strip()
        if value:
            if field == "doi":
                value = core.normalize_doi(value)
            elif field == "openalex_id":
                value = value.rstrip("/").rsplit("/", 1)[-1]
            aliases.append(f"{prefix}:{value.casefold()}")
    if record.get("title"):
        aliases.append(_title_key(str(record["title"])))
    return list(dict.fromkeys(aliases))


class LiteratureRegistry:
    def __init__(
        self,
        path: Path = DEFAULT_REGISTRY,
        *,
        legacy_path: Path = LEGACY_EVENT_LEDGER,
        run_history_path: Path = DEFAULT_RUN_HISTORY,
        daily_dir: Path = Path("daily"),
    ) -> None:
        self.path = path
        self.legacy_path = legacy_path
        self.run_history_path = run_history_path
        self.daily_dir = daily_dir
        self.data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "timezone": core.TIMEZONE,
            "updated_at": None,
            "dedupe_priority": [
                "doi", "pmid", "pmcid", "arxiv_id", "anthology_id",
                "openalex_id", "normalized_title",
            ],
            "works": {},
        }
        self.alias_index: dict[str, str] = {}
        self.run_started_at = _now()
        self.run_stats: dict[str, int] = {
            "same_run_unique": 0,
            "new_works": 0,
            "new_events": 0,
            "history_duplicates": 0,
            "candidate_count": 0,
            "featured_count": 0,
        }
        self._load()
        self._migrate_legacy()
        self._bootstrap_daily()
        self._reindex()

    @property
    def works(self) -> dict[str, dict[str, Any]]:
        return self.data.setdefault("works", {})

    def _load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("works", {}), dict):
            raise core.RadarError(f"Invalid literature registry: {self.path}")
        self.data.update(loaded)
        self.data["schema_version"] = SCHEMA_VERSION

    def _migrate_legacy(self) -> None:
        if not self.legacy_path.exists():
            return
        legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        for key, record in (legacy.get("works") or {}).items():
            if key not in self.works:
                migrated = dict(record)
                migrated.setdefault("migration_source", str(self.legacy_path))
                migrated.setdefault("seen_count", 1)
                migrated.setdefault("outcome", "historical_fulltext_event")
                self.works[key] = migrated

    def _bootstrap_daily(self) -> None:
        if not self.daily_dir.exists():
            return
        for path in sorted(self.daily_dir.glob("*.md")):
            self._bootstrap_daily_file(path)

    def _bootstrap_daily_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        timestamp = self._timestamp_from_daily_name(path.name)
        current: dict[str, str] | None = None

        def flush() -> None:
            nonlocal current
            if not current or not current.get("title"):
                current = None
                return
            identifiers = {
                name: current[name]
                for name in ("doi", "pmid", "pmcid", "openalex_id")
                if current.get(name)
            }
            key = self._canonical_from_values(current["title"], identifiers)
            record = self.works.setdefault(
                key,
                {
                    "title": current["title"],
                    "first_seen_at": timestamp,
                    "radar_first_seen": timestamp,
                    "identifiers": identifiers,
                    "seen_count": 1,
                    "outcome": "historical_daily_output",
                    "migration_source": str(path),
                },
            )
            record.setdefault("aliases", _record_aliases(key, record))
            current = None

        title_patterns = (
            re.compile(r"^####\s+\d+\.\s+(?:\[(.+?)\]\([^)]*\)|(.+))$"),
            re.compile(r"^\d+\.\s+\*\*(?:\[(.+?)\]\([^)]*\)|(.+?))\*\*$"),
        )
        for line in text.splitlines():
            match = next((pattern.match(line) for pattern in title_patterns if pattern.match(line)), None)
            if match:
                flush()
                current = {"title": (match.group(1) or match.group(2) or "").strip()}
                continue
            if current is None:
                continue
            for label, field in (("DOI", "doi"), ("PMID", "pmid"), ("PMCID", "pmcid"), ("OpenAlex", "openalex_id")):
                identifier = re.search(rf"{label}\s+`([^`]+)`", line, flags=re.I)
                if identifier:
                    current[field] = identifier.group(1).strip()
        flush()

    @staticmethod
    def _timestamp_from_daily_name(name: str) -> str:
        match = re.match(r"(\d{8})\s+(\d{4})", name)
        if not match:
            return "historical"
        parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M").replace(tzinfo=ZoneInfo(core.TIMEZONE))
        return parsed.isoformat()

    @staticmethod
    def _canonical_from_values(title: str, identifiers: dict[str, str]) -> str:
        if identifiers.get("doi"):
            return "doi:" + core.normalize_doi(identifiers["doi"])
        for field in ("pmid", "pmcid", "arxiv_id", "anthology_id", "openalex_id"):
            if identifiers.get(field):
                value = str(identifiers[field]).rstrip("/").rsplit("/", 1)[-1].casefold()
                return f"{field.replace('_id', '')}:{value}"
        return _title_key(title)

    def _reindex(self) -> None:
        self.alias_index = {}
        for key, record in self.works.items():
            aliases = _record_aliases(key, record)
            record["aliases"] = aliases
            for alias in aliases:
                self.alias_index.setdefault(alias, key)

    def begin_run(self) -> None:
        self.run_started_at = _now()
        for key in self.run_stats:
            self.run_stats[key] = 0

    def filter_new(self, papers: Iterable[core.Paper]) -> list[core.Paper]:
        new_papers: list[core.Paper] = []
        now = self.run_started_at.isoformat()
        papers = list(papers)
        self.run_stats["same_run_unique"] = len(papers)
        for paper in papers:
            aliases = aliases_for(paper)
            existing_key = next((self.alias_index[alias] for alias in aliases if alias in self.alias_index), None)
            if existing_key:
                record = self.works[existing_key]
                was_preprint = bool(record.get("is_preprint"))
                was_oa = record.get("open_access") is True
                previous_versions = {
                    str(value).casefold() for value in record.get("repository_versions", [])
                }
                current_versions = {
                    str(value).casefold() for value in paper.repository_versions
                }
                version_upgrade = was_preprint and not paper.is_preprint
                oa_unlock = not was_oa and paper.open_access is True
                accepted_manuscript_unlock = (
                    "acceptedversion" in current_versions
                    and "acceptedversion" not in previous_versions
                )
                record["last_seen_at"] = now
                record["seen_count"] = int(record.get("seen_count", 1)) + 1
                record["sources"] = sorted(set(record.get("sources", [])) | {paper.source})
                record["streams"] = sorted(set(record.get("streams", [])) | set(paper.all_streams()))
                record["research_directions"] = sorted(
                    set(record.get("research_directions", [])) | set(taxonomy.directions_for(paper))
                )
                record["aliases"] = list(dict.fromkeys([*record.get("aliases", []), *aliases]))
                identifiers = record.setdefault("identifiers", {})
                for field in ("doi", "pmid", "pmcid", "openalex_id"):
                    value = getattr(paper, field)
                    if value:
                        identifiers[field] = value
                record["open_access"] = paper.open_access if paper.open_access is not None else record.get("open_access")
                record["is_preprint"] = paper.is_preprint
                observed = record.setdefault("observed_events", [])
                known_events = {
                    core.events.event_fingerprint(event) for event in observed
                }
                for event in _serialized_events(paper):
                    if core.events.event_fingerprint(event) not in known_events:
                        observed.append(event)
                record["fulltext_urls"] = list(
                    dict.fromkeys([*record.get("fulltext_urls", []), *paper.fulltext_urls])
                )
                record["repository_versions"] = sorted(previous_versions | current_versions)
                if version_upgrade or oa_unlock or accepted_manuscript_unlock:
                    if version_upgrade:
                        event = "preprint_to_peer_reviewed_upgrade"
                        event_at = paper.publication_date or now
                        precision = "date" if paper.publication_date else "timestamp"
                    elif accepted_manuscript_unlock:
                        event = "author_accepted_manuscript_first_available"
                        event_at = now
                        precision = "timestamp"
                    elif record.get("embargoed") is True:
                        event = "embargo_lifted"
                        event_at = now
                        precision = "timestamp"
                    else:
                        event = "oa_fulltext_first_available"
                        event_at = now
                        precision = "timestamp"
                    core.events.add_event(
                        paper, event, event_at,
                        source="EvidenceRadar registry",
                        source_field="cross_run_state_transition",
                        url=paper.fulltext_urls[0] if paper.fulltext_urls else "",
                        precision=precision,
                        confidence="registry_verified_transition",
                    )
                    record.setdefault("notified_events", []).append(
                        {"event": event, "date": paper.publication_date or None, "notified_at": now}
                    )
                    record["outcome"] = "new_version_event"
                    self.run_stats["new_events"] += 1
                    new_papers.append(paper)
                else:
                    self.run_stats["history_duplicates"] += 1
                continue

            key = aliases[0]
            record = {
                "title": paper.title,
                "authors": paper.authors,
                "journal_or_venue": paper.journal_or_venue,
                "publication_date": paper.publication_date,
                "first_seen_at": now,
                "last_seen_at": now,
                "radar_first_seen": now,
                "seen_count": 1,
                "sources": [paper.source],
                "streams": paper.all_streams(),
                "research_directions": taxonomy.directions_for(paper),
                "identifiers": {
                    field: getattr(paper, field)
                    for field in ("doi", "pmid", "pmcid", "openalex_id")
                    if getattr(paper, field)
                },
                "open_access": paper.open_access,
                "is_preprint": paper.is_preprint,
                "observed_events": _serialized_events(paper),
                "notified_events": [],
                "fulltext_urls": list(paper.fulltext_urls),
                "repository_versions": list(paper.repository_versions),
                "outcome": "retrieved",
                "aliases": aliases,
            }
            self.works[key] = record
            for alias in aliases:
                self.alias_index[alias] = key
            self.run_stats["new_works"] += 1
            new_papers.append(paper)
        return new_papers

    def mark_selection(self, featured: Any, candidate_pool: list[core.Paper]) -> None:
        candidate_keys = {alias for paper in candidate_pool for alias in aliases_for(paper)}
        featured_papers: list[core.Paper] = []
        if isinstance(featured, dict):
            for value in featured.values():
                if isinstance(value, dict):
                    for items in value.values():
                        featured_papers.extend(items)
                elif isinstance(value, list):
                    featured_papers.extend(value)
        featured_keys = {alias for paper in featured_papers for alias in aliases_for(paper)}
        self.run_stats["candidate_count"] = len(candidate_pool)
        self.run_stats["featured_count"] = len({paper.identity_key() for paper in featured_papers})
        for key, record in self.works.items():
            aliases = set(record.get("aliases", [])) | {key}
            if aliases & featured_keys:
                record["outcome"] = "featured"
            elif aliases & candidate_keys:
                record["outcome"] = "candidate"
            if aliases & candidate_keys:
                paper = next(
                    (
                        item for item in candidate_pool
                        if aliases & set(aliases_for(item))
                    ),
                    None,
                )
                if paper is not None:
                    notified = record.setdefault("notified_events", [])
                    fingerprints = {
                        "|".join(
                            str(item.get(field) or "").casefold()
                            for field in ("event", "occurred_at", "source", "source_field")
                        )
                        for item in notified
                    }
                    for event in paper.qualifying_events:
                        notification = {
                            "event": event.get("type"),
                            "occurred_at": event.get("occurred_at"),
                            "source": event.get("source"),
                            "source_field": event.get("source_field"),
                            "notified_at": self.run_started_at.isoformat(),
                        }
                        fingerprint = "|".join(
                            str(notification.get(field) or "").casefold()
                            for field in ("event", "occurred_at", "source", "source_field")
                        )
                        if fingerprint not in fingerprints:
                            notified.append(notification)
                            fingerprints.add(fingerprint)

    def save_success(self) -> None:
        now = _now().isoformat()
        self.data["updated_at"] = now
        self.data["schema_version"] = SCHEMA_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        self.run_history_path.parent.mkdir(parents=True, exist_ok=True)
        run_record = {
            "run_started_at": self.run_started_at.isoformat(),
            "committed_at": now,
            "status": "success",
            **self.run_stats,
            **core.RUN_CONTEXT,
            "registry_works": len(self.works),
        }
        with self.run_history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run_record, ensure_ascii=False) + "\n")


_REGISTRY: LiteratureRegistry | None = None
_BASE_DEDUPLICATE = core.deduplicate
_BASE_RENDER = core.render_markdown
_BASE_MAIN = core.main


def install() -> None:
    global _REGISTRY, _BASE_DEDUPLICATE, _BASE_RENDER, _BASE_MAIN
    _BASE_DEDUPLICATE = core.deduplicate
    _BASE_RENDER = core.render_markdown
    _BASE_MAIN = core.main
    _REGISTRY = LiteratureRegistry()

    def deduplicate_with_history(papers: Iterable[core.Paper]) -> list[core.Paper]:
        same_run_unique = _BASE_DEDUPLICATE(papers)
        assert _REGISTRY is not None
        return _REGISTRY.filter_new(same_run_unique)

    def render_with_history(
        generated_at: datetime,
        featured: Any,
        candidate_pool: list[core.Paper],
        retrieved_count: int,
        deduplicated_count: int,
        excluded_count: int,
        warnings: list[str],
    ) -> str:
        assert _REGISTRY is not None
        _REGISTRY.mark_selection(featured, candidate_pool)
        markdown = _BASE_RENDER(
            generated_at, featured, candidate_pool, retrieved_count,
            deduplicated_count, excluded_count, warnings,
        )
        stats = _REGISTRY.run_stats
        return markdown + "\n".join(
            [
                "## Persistent History",
                "",
                f"- Same-run unique: `{stats['same_run_unique']}`",
                f"- New works: `{stats['new_works']}`",
                f"- New version/full-text events: `{stats['new_events']}`",
                f"- Suppressed history duplicates: `{stats['history_duplicates']}`",
                "- Registry: `state/literature_registry.json`",
                "",
            ]
        )

    def main_with_history() -> int:
        assert _REGISTRY is not None
        _REGISTRY.begin_run()
        result = _BASE_MAIN()
        if result == 0:
            _REGISTRY.save_success()
        return result

    core.deduplicate = deduplicate_with_history
    core.render_markdown = render_with_history
    core.main = main_with_history
