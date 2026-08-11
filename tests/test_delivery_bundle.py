"""Cross-file delivery and GitHub Pages regression tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools import build_pages_site as pages_builder
from tools.build_pages_site import PagesBuildError, build_pages_site, github_pages_base_url
from tools.package_work_delivery import package_work_delivery
from tools.run_github_radar import Candidate, DiscoveryResult, event_record, execute
from tools.validate_delivery_bundle import validate_delivery_bundle


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Tokyo")
FIXTURE_PROTOCOL_COMMIT = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=ROOT,
    check=True,
    text=True,
    stdout=subprocess.PIPE,
).stdout.strip()


def create_bundle(
    directory: Path,
    *,
    protocol_commit: str = FIXTURE_PROTOCOL_COMMIT,
    run_id: str = "github-actions-delivery-fixture",
    doi: str = "10.1000/delivery.fixture",
    execution_lane: str = "github_actions",
) -> tuple[Path, Path]:
    end_at = datetime(2026, 8, 9, 12, 0, tzinfo=TZ)
    fixture_url = f"https://example.test/{doi.rsplit('/', 1)[-1]}"
    item = Candidate(
        title="Delivery contract fixture",
        stream="clinical_medicine",
        category="clinical_medicine",
        source="pubmed",
        publication_date="2026-08-08",
        venue="Fixture Journal",
        doi=doi,
        abstract="A controlled fixture summary with 12 participants.",
        landing_url=fixture_url,
        events=[
            event_record(
                "version_of_record_first_online",
                "2026-08-08",
                "pubmed",
                "ArticleDate",
                fixture_url,
                "date",
                "provider_metadata",
            )
        ],
        score=90,
        triage_status="PRIORITY",
        triage_reasons=["fixture"],
        observed_streams=["clinical_medicine"],
        observed_sources=["pubmed"],
    )
    discovery_sources = {
        "pubmed",
        "europe_pmc",
        "openalex",
        "arxiv",
        "openreview",
        "acl_anthology",
        "pmlr",
    }

    def discoverer(*_args: object, **_kwargs: object) -> DiscoveryResult:
        source_access = [
            {
                "source_id": f"{source}-fixture",
                "provider": source,
                "url": f"https://example.test/{source}",
                "accessed_at": end_at.isoformat(),
                "status": "SUCCESS" if source == "pubmed" else "NO_RESULTS",
                "result_count": 1 if source == "pubmed" else 0,
                "http_requests_attempted": 1,
                "http_responses_received": 1,
                "cache_reused": False,
            }
            for source in sorted(discovery_sources)
        ]
        return DiscoveryResult(
            all_candidates=[item],
            priority_candidates=[item],
            raw_candidate_count=1,
            queries=[
                {
                    "query_id": "fixture-query",
                    "category": "clinical_medicine",
                    "query": "delivery fixture",
                    "searched_at": end_at.isoformat(),
                    "source_ids": ["pubmed"],
                    "status": "SUCCESS",
                    "result_count": 1,
                }
            ],
            source_access=source_access,
            checked_sources=set(discovery_sources),
            searched_sources=set(discovery_sources),
            unavailable_sources=set(),
        )

    def publisher_probe(*_args: object, **_kwargs: object):
        access = {
            "source_id": "publisher-fixture",
            "provider": "publisher",
            "url": f"https://doi.org/{doi}",
            "accessed_at": end_at.isoformat(),
            "status": "SUCCESS",
            "result_count": 1,
            "work_id": item.work_id,
            "candidate_title": item.title,
            "category": item.category,
            "http_status": 200,
            "http_requests_attempted": 1,
            "http_responses_received": 1,
            "cache_reused": False,
        }
        return [(item, access)], [access], []

    bundle = directory / "bundle"
    canonical_state = directory / "state" / "EvidenceRadar_State.json"
    execute(
        root=ROOT,
        output_dir=bundle,
        state_path=canonical_state,
        end_at=end_at,
        run_id=run_id,
        execution_lane=execution_lane,
        protocol_commit=protocol_commit,
        discoverer=discoverer,
        publisher_probe=publisher_probe,
    )
    return bundle, canonical_state


def create_pages_history_manifest(path: Path, bundles: list[Path]) -> Path:
    entries = []
    for bundle in bundles:
        run = json.loads(
            (bundle / "EvidenceRadar_Run.json").read_text(encoding="utf-8")
        )
        run_id = run["run_id"]
        destination = path.parent / run_id
        shutil.copytree(bundle, destination)
        records = []
        for name in pages_builder.BUNDLE_FILENAMES:
            payload = (destination / name).read_bytes()
            records.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            )
        entries.append(
            {
                "directory": run_id,
                "files": records,
                "protocol_commit": run["protocol_commit"],
                "run_id": run_id,
            }
        )
    manifest = {
        "format": pages_builder.PAGES_HISTORY_FORMAT,
        "manifest_version": "1",
        "runs": sorted(entries, key=lambda item: item["run_id"]),
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def recorded_validator_stub(**kwargs: object) -> pages_builder.ValidatedRunBundle:
    return pages_builder.ValidatedRunBundle(
        run_id=str(kwargs["run_id"]),
        protocol_commit=str(kwargs["protocol_commit"]),
        payloads={
            name: bytes(payload)
            for name, payload in dict(kwargs["payloads"]).items()
        },
        source=str(kwargs["source"]),
    )


class DeliveryBundleTests(unittest.TestCase):
    def test_valid_bundle_has_exact_html_and_run_candidate_parity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            errors, run = validate_delivery_bundle(
                ROOT,
                bundle,
                canonical_state=canonical,
                expected_lane="github_actions",
                expected_protocol_commit=FIXTURE_PROTOCOL_COMMIT,
            )
            self.assertEqual([], errors)
            self.assertEqual(1, run["counts"]["deduplicated_candidates"])
            report = (bundle / "EvidenceRadar_Report.html").read_text(encoding="utf-8")
            self.assertEqual(1, report.count("data-evidenceradar-work-id="))

    def test_html_candidate_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            report_path = bundle / "EvidenceRadar_Report.html"
            report = report_path.read_text(encoding="utf-8")
            report_path.write_text(
                report.replace("data-evidenceradar-work-id=", "data-omitted-work-id=", 1),
                encoding="utf-8",
            )
            errors, _run = validate_delivery_bundle(ROOT, bundle, canonical_state=canonical)
            self.assertTrue(any("candidate markers" in error for error in errors), errors)

    def test_schema_valid_files_cannot_hide_cross_file_provenance_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle, canonical = create_bundle(Path(directory))
            state_path = bundle / "EvidenceRadar_State.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["execution_lane"] = "chatgpt_work"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            errors, _run = validate_delivery_bundle(ROOT, bundle, canonical_state=canonical)
            self.assertTrue(any("State.execution_lane" in error for error in errors), errors)

    def test_pages_site_exposes_stable_and_immutable_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            bundle, canonical = create_bundle(temporary)
            output = temporary / "site"
            links = build_pages_site(
                root=ROOT,
                bundle=bundle,
                output_dir=output,
                repository="example-owner/EvidenceRadar",
                canonical_state=canonical,
                require_current_producer=False,
            )
            self.assertEqual(
                "https://example-owner.github.io/EvidenceRadar/",
                links["report_url"],
            )
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "links.json").is_file())
            self.assertTrue(
                (output / "runs" / "github-actions-delivery-fixture" / "index.html").is_file()
            )
            self.assertEqual(
                links,
                json.loads((output / "links.json").read_text(encoding="utf-8")),
            )

    def test_pages_site_rebuilds_complete_approved_history_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            historical, _ = create_bundle(
                temporary / "historical",
                run_id="github-actions-history-fixture",
                doi="10.1000/history.fixture",
            )
            history_root = temporary / "approved-history"
            history_root.mkdir()
            manifest = create_pages_history_manifest(
                history_root / "pages-history.json",
                [current, historical],
            )
            unlisted = history_root / "unlisted-overlay"
            unlisted.mkdir()
            (unlisted / "index.html.gz").write_bytes(b"not trusted")
            output = temporary / "site"
            with mock.patch.object(
                pages_builder,
                "_validate_with_recorded_producer",
                side_effect=recorded_validator_stub,
            ):
                links = build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=output,
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )

            for run_id in (
                "github-actions-delivery-fixture",
                "github-actions-history-fixture",
            ):
                archived = output / "runs" / run_id
                self.assertTrue((archived / "index.html").is_file())
                for name in pages_builder.BUNDLE_FILENAMES:
                    self.assertTrue((archived / name).is_file())
            self.assertFalse((output / "runs" / "unlisted-overlay").exists())
            inventory = json.loads(
                (output / "runs" / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, inventory["run_count"])
            self.assertEqual(2, links["immutable_archive"]["run_count"])
            self.assertEqual(
                "https://example-owner.github.io/EvidenceRadar/runs/index.json",
                links["immutable_archive"]["index_json"],
            )

    def test_pages_archive_rebuild_defers_only_recorded_renderer_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            run = json.loads(
                (current / "EvidenceRadar_Run.json").read_text(encoding="utf-8")
            )
            history_root = temporary / "approved-history"
            history_root.mkdir()
            manifest = create_pages_history_manifest(
                history_root / "pages-history.json",
                [current],
            )
            recorded_validator = mock.Mock(side_effect=recorded_validator_stub)
            with (
                mock.patch.object(
                    pages_builder,
                    "validate_delivery_bundle",
                    return_value=(
                        [pages_builder.HISTORICAL_RENDER_DRIFT_ERROR],
                        run,
                    ),
                ),
                mock.patch.object(
                    pages_builder,
                    "_validate_with_recorded_producer",
                    recorded_validator,
                ),
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "archive-rebuild-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )
            recorded_validator.assert_called_once()

            with (
                mock.patch.object(
                    pages_builder,
                    "validate_delivery_bundle",
                    return_value=(
                        [pages_builder.HISTORICAL_RENDER_DRIFT_ERROR],
                        run,
                    ),
                ),
                self.assertRaisesRegex(PagesBuildError, "approved history manifest"),
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "unbound-rebuild-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                )

            recorded_validator.reset_mock()
            with (
                mock.patch.object(
                    pages_builder,
                    "validate_delivery_bundle",
                    return_value=(["unexpected semantic drift"], run),
                ),
                mock.patch.object(
                    pages_builder,
                    "_validate_with_recorded_producer",
                    recorded_validator,
                ),
                self.assertRaisesRegex(PagesBuildError, "unexpected semantic drift"),
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "invalid-rebuild-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )
            recorded_validator.assert_not_called()

    def test_pages_history_hash_drift_fails_without_writing_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            history_root = temporary / "approved-history"
            history_root.mkdir()
            manifest = create_pages_history_manifest(
                history_root / "pages-history.json",
                [current],
            )
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["runs"][0]["files"][0]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(value), encoding="utf-8")
            output = temporary / "site"
            with self.assertRaisesRegex(PagesBuildError, "SHA-256 mismatch"):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=output,
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )
            self.assertFalse(output.exists())

            archived_state = (
                history_root
                / "github-actions-delivery-fixture"
                / "EvidenceRadar_State.json"
            )
            state = json.loads(archived_state.read_text(encoding="utf-8"))
            state["execution_lane"] = "chatgpt_work"
            archived_state.write_text(json.dumps(state), encoding="utf-8")
            value = json.loads(manifest.read_text(encoding="utf-8"))
            state_record = next(
                item
                for item in value["runs"][0]["files"]
                if item["path"] == "EvidenceRadar_State.json"
            )
            state_payload = archived_state.read_bytes()
            state_record["sha256"] = hashlib.sha256(state_payload).hexdigest()
            state_record["size"] = len(state_payload)
            report_record = value["runs"][0]["files"][0]
            report_payload = (
                history_root
                / "github-actions-delivery-fixture"
                / "EvidenceRadar_Report.html"
            ).read_bytes()
            report_record["sha256"] = hashlib.sha256(report_payload).hexdigest()
            manifest.write_text(json.dumps(value), encoding="utf-8")
            producer_validator = mock.Mock(side_effect=recorded_validator_stub)
            with (
                mock.patch.object(
                    pages_builder,
                    "_validate_with_recorded_producer",
                    producer_validator,
                ),
                self.assertRaisesRegex(PagesBuildError, "current delivery contract"),
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "semantic-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )
            producer_validator.assert_not_called()

    def test_pages_history_rejects_run_id_collision_and_symlink(self) -> None:
        prior_entry = {"run_id": "immutable-run", "sha256": "a" * 64}
        pages_builder._validate_append_only_history(
            {"runs": [prior_entry]},
            {"runs": [prior_entry, {"run_id": "new-run"}]},
        )
        with self.assertRaisesRegex(PagesBuildError, "append-only"):
            pages_builder._validate_append_only_history(
                {"runs": [prior_entry]},
                {"runs": []},
            )
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            first, _ = create_bundle(
                temporary / "first",
                run_id="Case-Run",
                doi="10.1000/case.first",
            )
            second, _ = create_bundle(
                temporary / "second",
                run_id="case-run",
                doi="10.1000/case.second",
            )
            history_root = temporary / "approved-history"
            history_root.mkdir()
            manifest = create_pages_history_manifest(
                history_root / "pages-history.json",
                [current, first, second],
            )
            with self.assertRaisesRegex(PagesBuildError, "duplicate/colliding run_id"):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "collision-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )

            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["runs"] = [
                item
                for item in value["runs"]
                if item["run_id"] == "github-actions-delivery-fixture"
            ]
            manifest.write_text(json.dumps(value), encoding="utf-8")
            archived_current = history_root / "github-actions-delivery-fixture"
            shutil.rmtree(archived_current)
            archived_current.symlink_to(current, target_is_directory=True)
            with self.assertRaisesRegex(PagesBuildError, "regular directory"):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "symlink-site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )

    def test_pages_history_uses_explicit_multi_commit_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "history-repository"
            repository.mkdir()

            def git(*args: str) -> str:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return completed.stdout.strip()

            def history(run_ids: list[str]) -> dict[str, object]:
                return {
                    "format": pages_builder.PAGES_HISTORY_FORMAT,
                    "manifest_version": "1",
                    "runs": [
                        {"run_id": run_id, "baseline_fixture": run_id}
                        for run_id in sorted(run_ids)
                    ],
                }

            git("init", "--initial-branch=main")
            git("config", "user.email", "pages-test@example.test")
            git("config", "user.name", "Pages Test")
            manifest = repository / "runs" / "pages-history.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(history(["immutable-a", "immutable-b"])),
                encoding="utf-8",
            )
            git("add", "runs/pages-history.json")
            git("commit", "-m", "baseline inventory")
            deployed_baseline = git("rev-parse", "HEAD")

            manifest.write_text(
                json.dumps(history(["immutable-b"])),
                encoding="utf-8",
            )
            git("commit", "-am", "shrink inventory in earlier commit")
            immediate_parent = git("rev-parse", "HEAD")

            manifest.write_text(
                json.dumps(history(["immutable-b", "new-run"])),
                encoding="utf-8",
            )
            git("commit", "-am", "add a replacement entry")

            current_value = json.loads(manifest.read_text(encoding="utf-8"))
            previous_parent_value = pages_builder._previous_history_manifest(
                repository,
                manifest,
                immediate_parent,
            )
            pages_builder._validate_append_only_history(
                previous_parent_value,
                current_value,
            )
            with self.assertRaisesRegex(
                PagesBuildError,
                "prior entry was removed or changed: immutable-a",
            ):
                pages_builder._manifest_history_candidates(
                    repository,
                    manifest,
                    baseline_commit=deployed_baseline,
                )
            with self.assertRaisesRegex(
                PagesBuildError,
                "prior entry was removed or changed: immutable-a",
            ):
                pages_builder._manifest_history_candidates(
                    repository,
                    manifest,
                    baseline_commit=immediate_parent,
                )

    def test_pages_history_current_run_bytes_cannot_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            conflicting, _ = create_bundle(
                temporary / "conflicting",
                run_id="github-actions-delivery-fixture",
                doi="10.1000/conflicting.fixture",
            )
            history_root = temporary / "approved-history"
            history_root.mkdir()
            manifest = create_pages_history_manifest(
                history_root / "pages-history.json",
                [conflicting],
            )
            with (
                mock.patch.object(
                    pages_builder,
                    "_validate_with_recorded_producer",
                    side_effect=recorded_validator_stub,
                ),
                self.assertRaisesRegex(PagesBuildError, "different validated bytes"),
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=temporary / "site",
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )

    def test_pages_history_accepts_verified_workrun_and_rejects_zip_bomb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            current, canonical = create_bundle(temporary / "current")
            history_root = temporary / "approved-history"
            packaged = package_work_delivery(
                current,
                history_root,
                source_date_epoch=0,
                validation_root=ROOT,
                expected_lane="github_actions",
            )
            run = json.loads(
                (current / "EvidenceRadar_Run.json").read_text(encoding="utf-8")
            )
            records = []
            for name in pages_builder.BUNDLE_FILENAMES:
                payload = (current / name).read_bytes()
                records.append(
                    {
                        "path": name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size": len(payload),
                    }
                )
            manifest = history_root / "pages-history.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format": pages_builder.PAGES_HISTORY_FORMAT,
                        "manifest_version": "1",
                        "runs": [
                            {
                                "archive": packaged.archive_path.name,
                                "archive_sha256": packaged.archive_sha256,
                                "files": records,
                                "protocol_commit": run["protocol_commit"],
                                "run_id": run["run_id"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = temporary / "site"
            with mock.patch.object(
                pages_builder,
                "_validate_with_recorded_producer",
                side_effect=recorded_validator_stub,
            ):
                build_pages_site(
                    root=ROOT,
                    bundle=current,
                    output_dir=output,
                    repository="example-owner/EvidenceRadar",
                    canonical_state=canonical,
                    require_current_producer=False,
                    history_manifests=[manifest],
                )
            self.assertTrue(
                (output / "runs" / run["run_id"] / "EvidenceRadar_Run.json").is_file()
            )

            bomb = temporary / "bomb.zip"
            with zipfile.ZipFile(
                bomb,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr("bomb", b"0" * (1024 * 1024))
            with self.assertRaisesRegex(PagesBuildError, "compression ratio"):
                pages_builder._preflight_history_zip(bomb)

    def test_user_site_repository_uses_origin_without_project_suffix(self) -> None:
        self.assertEqual(
            "https://example-owner.github.io",
            github_pages_base_url("example-owner/example-owner.github.io"),
        )


if __name__ == "__main__":
    unittest.main()
