"""Reproducibility and safety tests for the public ChatGPT Work Pack."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from unittest import mock
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from tools.build_work_pack import build_work_pack, collect_source_files, load_pack_spec
from tools.delivery_contract import WORK_PRODUCER_PATHS
from tools.run_github_radar import Candidate, event_record
from tools.verify_work_pack import (
    WorkPackVerificationError,
    verify_archive,
    verify_extracted_root,
)

ROOT = Path(__file__).resolve().parents[1]
CLEAN_GIT_STATE = {
    "git_commit": "c" * 40,
    "source_commit": "c" * 40,
    "git_dirty": False,
    "git_state": "clean",
}


def build_clean_work_pack(*args, **kwargs):
    with mock.patch("tools.build_work_pack._git_state", return_value=CLEAN_GIT_STATE):
        return build_work_pack(*args, **kwargs)


class WorkPackTests(unittest.TestCase):
    def test_allowlist_is_terminal_work_surface_not_github_control_plane(self) -> None:
        spec = load_pack_spec(ROOT / "release" / "work-pack-manifest.json")
        paths = {relative for relative, _source in collect_source_files(ROOT, spec)}
        for required in (
            ".agents/skills/evidence-radar/SKILL.md",
            "WORK_ENTRY.md",
            "EVIDENCE_RADAR_PROTOCOL.md",
            "requirements.txt",
            "requirements.lock",
            "config/deployment.yml",
            "config/radar_master.json",
            "docs/WORK_SETUP.md",
            "docs/SEMANTIC_CONTRACT_V3.md",
            "schemas/evidence-radar-state.schema.json",
            "state/current/EvidenceRadar_State.json",
            "templates/gpt-work-instructions.md",
            "tools/delivery_contract.py",
            "tools/featured_selection.py",
            "tools/materialize_delivery_aliases.py",
            "tools/merge_radar_state.py",
            "tools/network_safety.py",
            "tools/package_work_delivery.py",
            "tools/publisher_feed.py",
            "tools/radar_control.py",
            "tools/render_report_from_artifacts.py",
            "tools/run_github_radar.py",
            "tools/run_work_radar.py",
            "tools/strict_json.py",
            "tools/validate_delivery_bundle.py",
            "tools/validate_gpt_work_artifacts.py",
            "tools/verify_work_pack.py",
        ):
            self.assertIn(required, paths)
        for forbidden in (
            ".github/workflows/daily-radar.yml",
            "templates/work-stage-b-automation.md",
            "tools/build_work_pack.py",
            "tools/run_local_runtime.py",
            "tools/translation_handoff.py",
            "legacy/python-runtime/src/run.py",
            "state/literature_registry.json",
        ):
            self.assertNotIn(forbidden, paths)
        for producer_path in WORK_PRODUCER_PATHS:
            self.assertIn(producer_path, paths)

    def test_user_entry_is_one_download_then_terminal_four_file_delivery(self) -> None:
        entry = (ROOT / "WORK_ENTRY.md").read_text(encoding="utf-8")
        setup = (ROOT / "docs" / "WORK_SETUP.md").read_text(encoding="utf-8")
        instructions = (ROOT / "templates" / "gpt-work-instructions.md").read_text(
            encoding="utf-8"
        )
        protocol = (ROOT / "EVIDENCE_RADAR_PROTOCOL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for document in (entry, setup, instructions, protocol):
            self.assertIn("end-to-end", document)
            self.assertIn("EvidenceRadar_Report.html", document)
            self.assertIn("EvidenceRadar_State.json", document)
            self.assertIn("EvidenceRadar_Evidence.json", document)
            self.assertIn("EvidenceRadar_Run.json", document)
        for document in (setup, instructions):
            self.assertIn("EvidenceRadar-WorkPack-current.zip", document)
            self.assertIn("tools/verify_work_pack.py", document)
            self.assertNotIn("Repository-first", document)
        self.assertIn("Never return `TRANSLATION_REQUIRED`", instructions)
        self.assertIn("Traditional Chinese translation itself", " ".join(entry.split()))
        self.assertIn("do not invoke GitHub Actions", entry)
        self.assertIn("## 唯一用家執行路徑", readme)
        self.assertIn("執行 Radar 不需要 GitHub workflow、issue、PR 或 Stage B", readme)
        self.assertFalse((ROOT / ".github/workflows/daily-radar.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/translation-stage-b.yml").exists())
        self.assertTrue((ROOT / "legacy/github-actions/daily-radar.yml").exists())
        self.assertTrue((ROOT / "legacy/github-actions/translation-stage-b.yml").exists())
        self.assertIn("post_download_github_access: false", (ROOT / "config" / "deployment.yml").read_text(encoding="utf-8"))

    def test_work_instructions_bind_rss_inventory_to_verified_master_config(self) -> None:
        documents = (
            (ROOT / "WORK_ENTRY.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "WORK_SETUP.md").read_text(encoding="utf-8"),
            (ROOT / "templates" / "gpt-work-instructions.md").read_text(
                encoding="utf-8"
            ),
        )
        for document in documents:
            with self.subTest(document=document[:32]):
                self.assertIn("config/radar_master.json", document)
                self.assertIn("rss_atom", document)
                self.assertIn("feeds", document)
                self.assertIn("remembered", document)
                self.assertIn("crossref_issn", document)
                self.assertIn(
                    "retrieval_backend: rss_atom+crossref_journal_window", document
                )
                self.assertIn("result_count", document)
                self.assertIn("window_record_count", document)
                self.assertIn("fail closed", document)

    def test_build_is_byte_reproducible_and_manifest_verifies_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_clean_work_pack(ROOT, root / "first", source_date_epoch=0)
            second = build_clean_work_pack(ROOT, root / "second", source_date_epoch=0)
            first_bytes = first.archive_path.read_bytes()
            second_bytes = second.archive_path.read_bytes()
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first.archive_sha256, hashlib.sha256(first_bytes).hexdigest())
            self.assertEqual(first.archive_sha256, second.archive_sha256)
            self.assertEqual(
                f"{first.archive_sha256}  {first.archive_path.name}\n",
                first.checksum_path.read_text(encoding="utf-8"),
            )

            with ZipFile(first.archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(sorted(names[:-1]), names[:-1])
                self.assertEqual("manifest.json", names[-1])
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual("evidenceradar-work-pack", manifest["format"])
                self.assertEqual("1.6.1", manifest["pack_version"])
                self.assertEqual("chatgpt_work", manifest["execution_lane"])
                self.assertEqual("WORK_ENTRY.md", manifest["entrypoint"])
                self.assertEqual("tools/run_work_radar.py", manifest["executor"])
                self.assertTrue(manifest["terminal_delivery"])
                self.assertFalse(manifest["post_download_github_access"])
                self.assertEqual(
                    ["tools/run_github_radar.py"], manifest["disabled_entrypoints"]
                )
                self.assertEqual("source_and_package_storage_only", manifest["github_role"])
                self.assertEqual(
                    "state/current/EvidenceRadar_State.json",
                    manifest["seed_state"]["path"],
                )
                self.assertEqual(len(manifest["files"]), manifest["file_count"])
                self.assertTrue(manifest["reproducible"])
                for item in manifest["files"]:
                    self.assertEqual(
                        item["sha256"], hashlib.sha256(archive.read(item["path"])).hexdigest()
                    )
                    self.assertEqual(item["size"], len(archive.read(item["path"])))
                for info in archive.infolist():
                    path = PurePosixPath(info.filename)
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertEqual((1980, 1, 1, 0, 0, 0), info.date_time)

    def test_archive_and_fresh_extraction_verify_without_github_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_clean_work_pack(ROOT, temporary / "dist", source_date_epoch=0)
            verified = verify_archive(result.archive_path, result.checksum_path)
            self.assertEqual(result.archive_sha256, verified["archive_sha256"])
            extracted = temporary / "extracted"
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
            root_result = verify_extracted_root(extracted)
            self.assertEqual("chatgpt_work", root_result["execution_lane"])
            for forbidden in (
                "tools/run_local_runtime.py",
                "tools/translation_handoff.py",
                "templates/work-stage-b-automation.md",
            ):
                self.assertFalse((extracted / forbidden).exists())

            disabled_runner = subprocess.run(
                [sys.executable, "tools/run_github_radar.py", "--help"],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(2, disabled_runner.returncode, disabled_runner.stdout)
            self.assertIn(
                "GitHub runner CLI is disabled in the ChatGPT Work Pack",
                disabled_runner.stdout,
            )

            verifier = subprocess.run(
                [sys.executable, "tools/verify_work_pack.py", "--root", "."],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, verifier.returncode, verifier.stdout)
            self.assertIn('"status": "PASS"', verifier.stdout)
            state_validation = subprocess.run(
                [
                    sys.executable,
                    "tools/validate_gpt_work_artifacts.py",
                    "--root",
                    ".",
                    "state/current/EvidenceRadar_State.json",
                ],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, state_validation.returncode, state_validation.stdout)
            package_help = subprocess.run(
                [sys.executable, "tools/package_work_delivery.py", "--help"],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, package_help.returncode, package_help.stdout)
            self.assertIn("--input-manifest", package_help.stdout)
            executor_help = subprocess.run(
                [sys.executable, "tools/run_work_radar.py", "--help"],
                cwd=extracted,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(0, executor_help.returncode, executor_help.stdout)
            self.assertIn("--delivery-dir", executor_help.stdout)

    def test_extracted_verifier_rejects_tampering_and_control_plane_injection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_clean_work_pack(ROOT, temporary / "dist", source_date_epoch=0)
            extracted = temporary / "extracted"
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
            state = extracted / "state" / "current" / "EvidenceRadar_State.json"
            original = state.read_bytes()
            state.write_bytes(original + b"\n")
            with self.assertRaisesRegex(WorkPackVerificationError, "does not match manifest"):
                verify_extracted_root(extracted)
            state.write_bytes(original)
            injected = extracted / "tools" / "run_local_runtime.py"
            injected.write_text("raise SystemExit('wrong lane')\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkPackVerificationError, "payload set mismatch"):
                verify_extracted_root(extracted)

    def test_fresh_extraction_executes_from_work_observations_to_four_files(self) -> None:
        """Prove a clean pack starts before artifacts and reaches terminal delivery."""

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = build_clean_work_pack(ROOT, temporary / "dist", source_date_epoch=0)
            extracted = temporary / "extracted"
            with ZipFile(result.archive_path) as archive:
                archive.extractall(extracted)
            verify_extracted_root(extracted)

            run_id = "chatgpt-work-terminal-fixture"
            end_at = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
            fixture_url = "https://example.test/work-executor"
            candidate = Candidate(
                title="Work executor controlled study fixture",
                stream="clinical_medicine",
                category="clinical_medicine",
                source="pubmed",
                publication_date="2026-08-08",
                authors=["A. Example"],
                venue="Fixture Journal",
                abstract="A controlled fixture summary with 12 participants.",
                doi="10.1000/work.executor",
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
                query_ids=["work-query-pubmed"],
                observed_streams=["clinical_medicine"],
                observed_sources=["pubmed"],
                triage_status="PRIORITY",
                triage_reasons=["fixture"],
            )
            discovery_sources = (
                "acl_anthology",
                "arxiv",
                "europe_pmc",
                "jama_network_open",
                "nature_communications",
                "openalex",
                "openreview",
                "pmlr",
                "pubmed",
            )
            inventory_urls = {
                "jama_network_open": (
                    "https://api.crossref.org/journals/2574-3805/works?"
                    "filter=from-online-pub-date%3A2026-08-06%2Cuntil-online-pub-date%3A2026-08-09"
                    "&rows=1000&cursor=%2A"
                ),
                "nature_communications": (
                    "https://api.crossref.org/journals/2041-1723/works?"
                    "filter=from-online-pub-date%3A2026-08-06%2Cuntil-online-pub-date%3A2026-08-09"
                    "&rows=1000&cursor=%2A"
                ),
            }
            queries = [
                {
                    "query_id": f"work-query-{source}",
                    "category": "clinical_medicine",
                    "query": "controlled study fixture",
                    "searched_at": end_at.isoformat(),
                    "source_ids": [source],
                    "status": "SUCCESS" if source == "pubmed" else "NO_RESULTS",
                    "result_count": 1 if source == "pubmed" else 0,
                }
                for source in discovery_sources
            ]
            source_access = [
                {
                    "source_id": f"work-{source}",
                    "provider": source,
                    "url": (
                        inventory_urls[source]
                        if source in {"jama_network_open", "nature_communications"}
                        else f"https://example.test/{source}"
                    ),
                    "accessed_at": end_at.isoformat(),
                    "status": "SUCCESS" if source == "pubmed" else "NO_RESULTS",
                    "result_count": 1 if source == "pubmed" else 0,
                    "http_requests_attempted": (
                        2
                        if source in {"jama_network_open", "nature_communications"}
                        else 1
                    ),
                    "http_responses_received": (
                        2
                        if source in {"jama_network_open", "nature_communications"}
                        else 1
                    ),
                    "cache_reused": False,
                    **(
                        {
                            "retrieval_complete": True,
                            "retrieval_backend": "rss_atom+crossref_journal_window",
                            "feed_entry_count": 0,
                            "registry_record_count": 0,
                            "unusable_record_count": 0,
                            "window_record_count": 0,
                            "inventory_url": inventory_urls[source],
                            "inventory_pages_requested": 2,
                            "inventory_pages_received": 2,
                        }
                        if source in {"jama_network_open", "nature_communications"}
                        else {}
                    ),
                }
                for source in discovery_sources
            ]
            publisher_access = [
                {
                    "source_id": "work-publisher-001",
                    "provider": "publisher",
                    "work_id": candidate.work_id,
                    "candidate_title": candidate.title,
                    "category": candidate.category,
                    "url": "https://doi.org/10.1000/work.executor",
                    "accessed_at": end_at.isoformat(),
                    "status": "SUCCESS",
                    "result_count": 1,
                    "http_status": 200,
                    "http_requests_attempted": 1,
                    "http_responses_received": 1,
                    "cache_reused": False,
                }
            ]
            work_input = {
                "artifact_type": "EvidenceRadar_WorkInput",
                "schema_version": "1.0",
                "run_id": run_id,
                "end_at": end_at.isoformat(),
                "profile_id": "owner_daily",
                "raw_candidate_count": 1,
                "queries": queries,
                "source_access": source_access,
                "checked_sources": list(discovery_sources),
                "searched_sources": list(discovery_sources),
                "unavailable_sources": [],
                "priority_candidate_ids": [candidate.work_id],
                "publisher_access": publisher_access,
                "publisher_warnings": [],
                "candidates": [
                    {
                        "work_id": candidate.work_id,
                        "candidate": asdict(candidate),
                        "translation": {
                            "title_zh_tw": "Work 執行器對照研究測試",
                            "summary_zh_tw": (
                                "這項對照研究測試納入十二名參與者，用於驗證繁體中文摘要、"
                                "來源收據、四檔一致性與終端交付。"
                            ),
                        },
                    }
                ],
            }
            input_path = temporary / "EvidenceRadar_WorkInput.json"
            input_path.write_text(
                json.dumps(work_input, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"

            def run_packaged(*arguments: str) -> subprocess.CompletedProcess[str]:
                completed = subprocess.run(
                    [sys.executable, *arguments],
                    cwd=extracted,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout)
                return completed

            bundle = temporary / "work-run"
            delivery = temporary / "delivery"
            execution = run_packaged(
                "tools/run_work_radar.py",
                "--root",
                str(extracted),
                "--input",
                str(input_path),
                "--run-dir",
                str(bundle),
                "--delivery-dir",
                str(delivery),
            )
            summary = json.loads(execution.stdout.splitlines()[-1])
            self.assertEqual("COMPLETE", summary["status"])
            self.assertEqual(4, len(summary["delivery_aliases"]))

            direct_files = sorted(delivery.glob("*__EvidenceRadar_*"))
            self.assertEqual(4, len(direct_files))
            canonical_by_suffix = {
                path.name.split("__", 1)[1]: path for path in direct_files
            }
            for name in (
                "EvidenceRadar_Report.html",
                "EvidenceRadar_State.json",
                "EvidenceRadar_Evidence.json",
                "EvidenceRadar_Run.json",
            ):
                self.assertEqual(
                    (bundle / name).read_bytes(), canonical_by_suffix[name].read_bytes()
                )
            packaged = delivery / f"EvidenceRadar-WorkRun-{run_id}"
            self.assertTrue(packaged.is_dir())
            self.assertTrue((delivery / f"EvidenceRadar-WorkRun-{run_id}.zip").is_file())
            self.assertTrue(
                (delivery / f"EvidenceRadar-WorkRun-{run_id}.zip.sha256").is_file()
            )

            # The whole terminal path must leave the verified package immutable.
            self.assertEqual([], list(extracted.rglob("*.pyc")))
            self.assertEqual([], list(extracted.rglob("__pycache__")))
            verify_extracted_root(extracted)

    def test_work_pack_release_publishes_stable_latest_assets_from_main(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "work-pack-release.yml").read_text(
            encoding="utf-8"
        )
        for marker in (
            "Require exact clean main checkout",
            '"state/current/EvidenceRadar_State.json"',
            "python tools/build_work_pack.py",
            "python tools/verify_work_pack.py",
            "EvidenceRadar-WorkPack-current.zip",
            "work-pack-${GITHUB_SHA}",
            "evidenceradar-work-pack-release",
            "A newer main commit exists",
            "--cleanup-tag",
            "'.immutable'",
            "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            "EvidenceRadar-WorkPack-current.sigstore.json",
            "gh release create",
            "--latest",
        ):
            self.assertIn(marker, workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn('- "tools/run_github_radar.py"', workflow)
        self.assertNotIn("schedule:", workflow)
        runtime_workflow = (ROOT / ".github" / "workflows" / "runtime-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--latest=false", runtime_workflow)
        self.assertIn("evidenceradar-runtime-release", runtime_workflow)
        self.assertIn("--cleanup-tag", runtime_workflow)
        self.assertIn("'.immutable'", runtime_workflow)
        self.assertIn(
            "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            runtime_workflow,
        )


if __name__ == "__main__":
    unittest.main()
