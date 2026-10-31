#!/usr/bin/env python3
"""Turn raw blockers into an auditable resolution ledger.

The ledger distinguishes a resolved blocker from a genuinely inaccessible
licensed source. It never treats an API absence as a successful search.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {clean(key): clean(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def index_by_pdb(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        pdb_id = clean(row.get("PDB_ID")).upper()
        if pdb_id:
            output.setdefault(pdb_id, []).append(row)
    return output


def resolve_row(
    row: dict[str, str],
    *,
    identity: dict[str, str] | None,
    downloads: list[dict[str, str]],
    sources: list[dict[str, str]],
    database_ledger: list[dict[str, str]],
    browser_manifest: list[dict[str, Any]],
    patent_candidates: list[dict[str, str]],
) -> dict[str, str]:
    pdb_id = clean(row.get("PDB_ID")).upper()
    blocker = clean(row.get("blocker") or row.get("Blocker_Type")).lower()
    detail = clean(row.get("detail") or row.get("Detail"))
    route = ""
    status = "open"
    evidence = ""
    required = "yes"
    action = ""
    if blocker in {"no_primary_citation_identifier", "no_primary_citation"}:
        if identity and clean(identity.get("Resolution")) in {
            "direct_citation_and_code",
            "resolved_component_peer",
        }:
            route = "RCSB exact-component peer identity"
            status = "resolved_with_context"
            evidence = clean(identity.get("Evidence_Summary"))
            required = "yes"
        else:
            action = "Resolve DOI/PMID from Crossref, Europe PMC and exact-component peers."
    elif blocker in {
        "europepmc_no_match",
        "subscription_or_unresolved_fulltext",
        "no_unblocked_fulltext_found",
        "main_text_missing",
    }:
        usable_downloads = [
            item
            for item in downloads
            if clean(item.get("status")) in {"downloaded", "downloaded_pdf", "hashed"}
            and clean(item.get("path"))
        ]
        usable_sources = [
            item
            for item in sources
            if clean(item.get("status")).lower()
            in {"available", "hashed", "downloaded", "manual_access"}
            and clean(item.get("path") or item.get("Evidence_Path"))
        ]
        candidate = (usable_sources or usable_downloads)
        if candidate:
            item = candidate[0]
            path = Path(clean(item.get("path") or item.get("Evidence_Path")))
            route = "local or OA fulltext"
            status = "resolved"
            evidence = str(path)
            required = "yes"
        elif browser_manifest:
            successful = [
                item
                for item in browser_manifest
                if clean(item.get("status")).startswith("downloaded_")
                and item.get("files")
            ]
            if successful:
                route = "authorized browser"
                status = "resolved"
                evidence = successful[0]["files"][0]
            else:
                route = "authorized browser"
                status = "requires_authorized_session"
                evidence = clean(browser_manifest[0].get("status"))
                action = "Supply an authorized browser profile/storage state or a lawful user export."
        else:
            action = "Run browser_fetch.mjs with an authorized profile, then retry OA/landing-page routes."
    elif blocker in {"supplement_not_downloaded", "supplement_archive_not_downloaded"}:
        usable = [
            item
            for item in downloads + sources
            if clean(item.get("status")) in {"downloaded", "hashed", "available"}
            and clean(item.get("path") or item.get("Evidence_Path"))
        ]
        if usable:
            route = "downloaded supplement"
            status = "resolved"
            evidence = clean(usable[0].get("path") or usable[0].get("Evidence_Path"))
        else:
            action = "Fetch the supplement landing-page link or authorized publisher file."
    elif blocker in {"patent_search_failed", "patent_search_503"}:
        route = "patent search"
        if patent_candidates:
            status = "resolved_candidate_only"
            evidence = clean(patent_candidates[0].get("publication_number"))
            action = "Open the patent example/table before using it as evidence."
        else:
            status = "evidence_exhausted_no_hit"
            evidence = detail
            required = "no"
            action = "Retry with an authorized patent database or PatMap/Reaxys/SciFinder export."
    elif "reaxys" in blocker or "scifinder" in blocker:
        route = "licensed database"
        if database_ledger:
            status = "resolved"
            evidence = clean(database_ledger[0].get("Export_Path") or database_ledger[0].get("Query"))
        else:
            status = "requires_authorized_session"
            action = "Use an authorized Reaxys/SciFinder session and record query, date and export hash."
    else:
        action = "Classify the blocker and add a route-specific receipt."
    return {
        "PDB_ID": pdb_id,
        "Blocker_Type": blocker,
        "Original_Detail": detail,
        "Resolution_Status": status,
        "Resolution_Route": route,
        "Resolution_Evidence": evidence,
        "Evidence_SHA256": sha256(Path(evidence)) if evidence and Path(evidence).exists() else "",
        "Required_For_Acceptance": required,
        "Remaining_Action": action,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-pack", required=True, type=Path)
    parser.add_argument("--identity-resolution", type=Path)
    parser.add_argument("--source-register", type=Path)
    parser.add_argument("--database-ledger", type=Path)
    parser.add_argument("--browser-manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    blockers = read_csv(args.evidence_pack / "blockers.csv")
    downloads = read_csv(args.evidence_pack / "downloads.csv")
    sources = read_csv(args.source_register)
    database_ledger = read_csv(args.database_ledger)
    patent_candidates = read_csv(args.evidence_pack / "patent_candidates.csv")
    identity_rows = read_csv(args.identity_resolution)
    identity = {clean(item.get("PDB_ID")).upper(): item for item in identity_rows}
    browser_manifest = []
    if args.browser_manifest and args.browser_manifest.exists():
        browser_manifest = json.loads(args.browser_manifest.read_text(encoding="utf-8"))
    source_by_pdb = index_by_pdb(sources)
    downloads_by_pdb = index_by_pdb(downloads)
    ledger_by_pdb = index_by_pdb(database_ledger)
    output = []
    for row in blockers:
        pdb_id = clean(row.get("PDB_ID")).upper()
        output.append(
            resolve_row(
                row,
                identity=identity.get(pdb_id),
                downloads=downloads_by_pdb.get(pdb_id, []),
                sources=source_by_pdb.get(pdb_id, []),
                database_ledger=ledger_by_pdb.get(pdb_id, []),
                browser_manifest=[
                    item
                    for item in browser_manifest
                    if not pdb_id or pdb_id.lower() in clean(item.get("url")).lower()
                ],
                patent_candidates=patent_candidates,
            )
        )
    write_csv(args.output, output)
    print(
        json.dumps(
            {
                "blockers": len(output),
                "resolved": sum(
                    item["Resolution_Status"].startswith("resolved") for item in output
                ),
                "open": sum(
                    item["Resolution_Status"] in {"open", "requires_authorized_session"}
                    and item["Required_For_Acceptance"] == "yes"
                    for item in output
                ),
                "output": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
