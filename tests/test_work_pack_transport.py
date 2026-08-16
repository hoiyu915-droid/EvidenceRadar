from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from tools.build_work_pack_transport import (
    CANONICAL_ARCHIVE,
    CANONICAL_CHECKSUM,
    CANONICAL_PROVENANCE,
    TRANSPORT_MANIFEST,
    WorkPackTransportError,
    build_transport,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "a" * 40
VERSION = "1.6.1+aaaaaaaaaaaa"


class WorkPackTransportTests(unittest.TestCase):
    def _inputs(
        self, root: Path, *, source_commit: str = SOURCE_COMMIT
    ) -> tuple[Path, Path, Path]:
        archive = root / CANONICAL_ARCHIVE
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as value:
            value.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "evidenceradar-work-pack",
                        "source_commit": source_commit,
                        "git_commit": source_commit,
                        "pack_version": VERSION,
                    },
                    sort_keys=True,
                ),
            )
            value.writestr("WORK_ENTRY.md", "fixture\n")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum = root / CANONICAL_CHECKSUM
        checksum.write_text(f"{digest}  {CANONICAL_ARCHIVE}\n", encoding="utf-8")
        provenance = root / CANONICAL_PROVENANCE
        provenance.write_text('{"fixture": true}\n', encoding="utf-8")
        return archive, checksum, provenance

    def test_build_stages_one_canonical_work_pack_and_transport_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, checksum, provenance = self._inputs(root)
            output = root / "transport"
            manifest = build_transport(
                archive,
                checksum,
                provenance,
                source_commit=SOURCE_COMMIT,
                version=VERSION,
                output_dir=output,
            )
            self.assertEqual(
                {
                    CANONICAL_ARCHIVE,
                    CANONICAL_CHECKSUM,
                    CANONICAL_PROVENANCE,
                    TRANSPORT_MANIFEST,
                },
                {path.name for path in output.iterdir()},
            )
            self.assertEqual("byte_transport_only", manifest["transport_role"])
            self.assertEqual(CANONICAL_ARCHIVE, manifest["canonical_member"])
            self.assertEqual(SOURCE_COMMIT, manifest["source_commit"])
            self.assertEqual(3, len(manifest["members"]))
            self.assertNotIn(
                "EvidenceRadar-WorkPack-v",
                " ".join(path.name for path in output.iterdir()),
            )

    def test_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, checksum, provenance = self._inputs(root)
            checksum.write_text(
                f"{'0' * 64}  {CANONICAL_ARCHIVE}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(WorkPackTransportError, "checksum mismatch"):
                build_transport(
                    archive,
                    checksum,
                    provenance,
                    source_commit=SOURCE_COMMIT,
                    version=VERSION,
                    output_dir=root / "transport",
                )

    def test_rejects_inner_source_commit_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, checksum, provenance = self._inputs(
                root, source_commit="b" * 40
            )
            with self.assertRaisesRegex(WorkPackTransportError, "source_commit"):
                build_transport(
                    archive,
                    checksum,
                    provenance,
                    source_commit=SOURCE_COMMIT,
                    version=VERSION,
                    output_dir=root / "transport",
                )

    def test_release_workflow_uploads_only_staged_transport_directory(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "work-pack-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("python tools/build_work_pack_transport.py", workflow)
        block = workflow.split("- name: Upload ChatGPT transport artifact", 1)[1].split(
            "- name: Publish Work Pack as latest GitHub Release", 1
        )[0]
        self.assertIn("dist/work-pack-transport/", block)
        self.assertNotIn("steps.build.outputs.archive", block)
        self.assertNotIn("steps.build.outputs.checksum", block)

    def test_consumer_contract_documents_outer_zip_fallback(self) -> None:
        documents = {
            "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
            "WORK_ENTRY.md": (ROOT / "WORK_ENTRY.md").read_text(encoding="utf-8"),
            "docs/WORK_SETUP.md": (ROOT / "docs" / "WORK_SETUP.md").read_text(
                encoding="utf-8"
            ),
            ".agents/skills/evidence-radar/SKILL.md": (
                ROOT / ".agents" / "skills" / "evidence-radar" / "SKILL.md"
            ).read_text(encoding="utf-8"),
        }
        for path, document in documents.items():
            with self.subTest(path=path):
                self.assertIn("TRANSPORT_MANIFEST.json", document)
                self.assertIn("workflow artifact", document.lower())
        self.assertIn(
            "download_workflow_artifact",
            documents[".agents/skills/evidence-radar/SKILL.md"],
        )


if __name__ == "__main__":
    unittest.main()
