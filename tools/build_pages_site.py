#!/usr/bin/env python3
"""Build a validated static GitHub Pages site for the latest radar report."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delivery_contract import BUNDLE_FILENAMES
from tools.validate_delivery_bundle import validate_delivery_bundle


class PagesBuildError(RuntimeError):
    pass


def github_pages_base_url(repository: str) -> str:
    parts = repository.strip().split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise PagesBuildError("repository must use owner/name form")
    owner, name = parts
    if name.casefold() == f"{owner}.github.io".casefold():
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{name}"


def _url(base: str, relative: str = "") -> str:
    base = base.rstrip("/")
    return f"{base}/{relative.lstrip('/')}" if relative else f"{base}/"


def build_pages_site(
    *,
    root: Path,
    bundle: Path,
    output_dir: Path,
    repository: str,
    base_url: str | None = None,
    canonical_state: Path | None = None,
    require_current_producer: bool = True,
) -> dict[str, Any]:
    root = Path(root).resolve()
    bundle = Path(bundle).resolve()
    output_dir = Path(output_dir).resolve()
    errors, run = validate_delivery_bundle(
        root,
        bundle,
        canonical_state=canonical_state,
        require_current_producer=require_current_producer,
        require_semantic_contract_v3=True,
        reject_dirty=True,
    )
    if errors:
        raise PagesBuildError("delivery bundle is not publishable:\n" + "\n".join(errors))
    if run is None:
        raise PagesBuildError("validated delivery is missing Run metadata")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PagesBuildError(f"Pages output directory must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    base_url = str(base_url or github_pages_base_url(repository)).rstrip("/")
    if not base_url.startswith("https://"):
        raise PagesBuildError("Pages base URL must use HTTPS")
    run_id = str(run["run_id"])
    safe_run_id = re.sub(r"[^A-Za-z0-9._+-]", "-", run_id).strip(".-")
    if not safe_run_id:
        raise PagesBuildError("run_id does not contain a safe path component")
    encoded_run_id = quote(safe_run_id, safe="-._~+")
    latest_dir = output_dir / "latest"
    immutable_dir = output_dir / "runs" / safe_run_id
    latest_dir.mkdir(parents=True)
    immutable_dir.mkdir(parents=True)

    for name in BUNDLE_FILENAMES:
        source = bundle / name
        shutil.copyfile(source, latest_dir / name)
        shutil.copyfile(source, immutable_dir / name)
    shutil.copyfile(bundle / "EvidenceRadar_Report.html", output_dir / "index.html")
    shutil.copyfile(bundle / "EvidenceRadar_Report.html", latest_dir / "index.html")
    shutil.copyfile(bundle / "EvidenceRadar_Report.html", immutable_dir / "index.html")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    links = {
        "schema_version": "1.0",
        "artifact_type": "EvidenceRadar_Public_Links",
        "repository": repository,
        "run_id": run_id,
        "execution_lane": run.get("execution_lane"),
        "protocol_commit": run.get("protocol_commit"),
        "report_url": _url(base_url),
        "links_json_url": _url(base_url, "links.json"),
        "latest": {
            "report_html": _url(base_url, "latest/EvidenceRadar_Report.html"),
            "state_json": _url(base_url, "latest/EvidenceRadar_State.json"),
            "evidence_json": _url(base_url, "latest/EvidenceRadar_Evidence.json"),
            "run_json": _url(base_url, "latest/EvidenceRadar_Run.json"),
        },
        "immutable_run": {
            "report_html": _url(base_url, f"runs/{encoded_run_id}/"),
            "state_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_State.json"),
            "evidence_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_Evidence.json"),
            "run_json": _url(base_url, f"runs/{encoded_run_id}/EvidenceRadar_Run.json"),
        },
    }
    payload = json.dumps(links, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (output_dir / "links.json").write_text(payload, encoding="utf-8")
    (latest_dir / "links.json").write_text(payload, encoding="utf-8")
    print(json.dumps(links, ensure_ascii=False, sort_keys=True))
    return links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path, default=Path("artifacts/current"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True, help="GitHub owner/name")
    parser.add_argument("--base-url", help="configured Pages base URL, including a custom domain")
    parser.add_argument("--canonical-state", type=Path)
    parser.add_argument("--skip-current-producer-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        build_pages_site(
            root=args.root,
            bundle=args.bundle,
            output_dir=args.output_dir,
            repository=args.repository,
            base_url=args.base_url,
            canonical_state=args.canonical_state,
            require_current_producer=not args.skip_current_producer_check,
        )
    except PagesBuildError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
