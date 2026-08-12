#!/usr/bin/env python3
"""Fail closed on public-release licensing and repository hygiene."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "LICENSE": "Apache License",
    "LICENSE-CONTENT.md": "CC BY 4.0",
    "NOTICE.md": "Research-source material",
    "CONTRIBUTING.md": "Contributing to EvidenceRadar",
    "SECURITY.md": "Security Policy",
    "GOVERNANCE.md": "is the primary maintainer",
    "CITATION.cff": "cff-version: 1.2.0",
    "ROADMAP.md": "Roadmap",
    "PUBLIC_RELEASE_AUDIT.md": "Public release audit",
    "requirements.lock": "--hash=sha256:",
    "requirements-dev.lock": "--hash=sha256:",
    "legacy/python-runtime/requirements.lock": "--hash=sha256:",
    "ruff.toml": "select =",
}

BANNED_PATHS = (
    re.compile(r"(^|/)\.env(?:\.|$)", re.I),
    re.compile(r"(^|/)(?:credentials?|secrets?)(?:\.|/|$)", re.I),
    re.compile(r"(^|/)data/raw(?:/|$)", re.I),
    re.compile(r"(^|/)(?:raw[-_]?fulltext|full[-_]?text[-_]?cache)(?:/|$)", re.I),
    re.compile(r"\.(?:pdf|epub|p12|pfx|pem|key|jks|keystore)$", re.I),
)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)

TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".jsonl", ".yml", ".yaml", ".html", ".cff"}
ACTION_USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^#\s]+)", re.MULTILINE)
FULL_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def repository_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def action_pin_errors(paths: list[Path]) -> list[str]:
    """Require immutable commit pins for every remotely sourced Action."""

    errors: list[str] = []
    for path in paths:
        content = path.read_text(encoding="utf-8")
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for target in ACTION_USES_RE.findall(content):
            if target.startswith("./"):
                continue
            _separator, _at, ref = target.rpartition("@")
            if FULL_COMMIT_RE.fullmatch(ref) is None:
                errors.append(
                    f"GitHub Action is not pinned to a full commit SHA: "
                    f"{display_path} -> {target}"
                )
    return errors


def main() -> int:
    errors: list[str] = []

    for relative, marker in REQUIRED.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing required file: {relative}")
            continue
        if marker not in path.read_text(encoding="utf-8"):
            errors.append(f"missing required marker in {relative}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for marker in (
        "## 唯一用家執行路徑",
        "GitHub 在這條路徑只負責原始碼與版本化 ZIP 儲存",
        "Codex is **not** part of the radar runtime",
        "## License",
    ):
        if marker not in readme:
            errors.append(f"README boundary missing: {marker}")

    for relative in repository_files():
        if any(pattern.search(relative) for pattern in BANNED_PATHS):
            errors.append(f"public-release forbidden path: {relative}")
            continue
        path = ROOT / relative
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            errors.append(f"possible credential pattern in: {relative}")

    workflow_paths = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    workflow_paths.extend(sorted((ROOT / "legacy" / "github-actions").glob("*.y*ml")))
    errors.extend(action_pin_errors(workflow_paths))

    if errors:
        for error in sorted(set(errors)):
            print(f"FAIL: {error}")
        return 1

    print("PASS: public-release licensing and repository hygiene")
    return 0


if __name__ == "__main__":
    sys.exit(main())
