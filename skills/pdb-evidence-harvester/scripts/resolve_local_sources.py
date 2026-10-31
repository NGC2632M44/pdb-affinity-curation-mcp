#!/usr/bin/env python3
"""Find local primary papers/supplements by DOI, PDB code and title tokens."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


SUPPORTED = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".csv",
    ".tsv",
    ".xml",
    ".html",
    ".htm",
    ".tif",
    ".tiff",
    ".png",
    ".jpg",
    ".jpeg",
    ".zip",
}


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")[:180] or "file"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def index_sources(roots: list[Path]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        iterator = root.rglob("*") if root.is_dir() else [root]
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            output.append(
                {
                    "path": resolved,
                    "filename_norm": normalized(path.name),
                    "path_norm": normalized(str(path)),
                }
            )
    return output


def query_tokens(row: dict[str, str]) -> list[tuple[str, str]]:
    tokens = []
    doi = clean(row.get("Resolved_DOI") or row.get("DOI") or row.get("doi"))
    if doi:
        tokens.append(("doi", normalized(doi)))
    title = clean(row.get("title") or row.get("citation_title"))
    words = [
        word
        for word in re.findall(r"[A-Za-z]{5,}", title)
        if word.lower()
        not in {
            "influenza",
            "structural",
            "structure",
            "development",
            "inhibitors",
            "inhibitor",
            "analysis",
            "characterization",
            "virus",
        }
    ]
    for size in (5, 4, 3):
        if len(words) >= size:
            tokens.append(("title", normalized(" ".join(words[:size]))))
            break
    pdb_id = clean(row.get("PDB_ID") or row.get("PDB ID")).upper()
    is_reference = bool(clean(row.get("citedOrder") or row.get("authorString")))
    if not is_reference and re.fullmatch(r"[0-9A-Z]{4}", pdb_id):
        tokens.append(("pdb", pdb_id.lower()))
    return tokens


def resolve_pmid_doi(source: str, record_id: str, timeout: int) -> str:
    if not source or not record_id:
        return ""
    query = f"EXT_ID:{record_id}"
    if source.upper() == "MED":
        query += " AND SRC:MED"
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + urllib.parse.urlencode(
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": 1,
            }
        )
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "pdb-local-source-resolver/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result = ((payload.get("resultList") or {}).get("result") or [])
        return clean(result[0].get("doi")) if result else ""
    except Exception:
        return ""


def best_match(tokens: list[tuple[str, str]], index: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = []
    for token_type, token in tokens:
        if len(token) < 4:
            continue
        for item in index:
            if token in item["filename_norm"] or token in item["path_norm"]:
                score = 100 if token_type == "doi" else 70 if token_type == "pdb" else 50
                filename = str(item["path"]).lower()
                if any(
                    token in filename
                    for token in ("_main", "-main", "_article", "-article", "fulltext")
                ):
                    score += 15
                if any(
                    token in filename
                    for token in ("_si_", "_si.", "-si-", "-si.", "supplement")
                ):
                    score -= 20
                matches.append((score, item))
    if not matches:
        return None
    matches.sort(key=lambda pair: (-pair[0], len(str(pair[1]["path"])), str(pair[1]["path"])))
    return matches[0][1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", action="append", required=True, type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--pdb-citations", type=Path)
    parser.add_argument("--resolve-pmids", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    index = index_sources(args.source_dir)
    queries = []
    queries.extend(read_csv(args.references))
    queries.extend(read_csv(args.pdb_citations))
    pmid_doi: dict[tuple[str, str], str] = {}
    if args.resolve_pmids:
        identifiers = sorted(
            {
                (clean(query.get("source")), clean(query.get("id")))
                for query in queries
                if clean(query.get("source")) and clean(query.get("id"))
                and not clean(query.get("DOI") or query.get("doi"))
            }
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(
                lambda pair: (
                    pair,
                    resolve_pmid_doi(pair[0], pair[1], args.timeout),
                ),
                identifiers,
            )
            pmid_doi = {pair: doi for pair, doi in results}
    rows = []
    for query in queries:
        identifier = (clean(query.get("source")), clean(query.get("id")))
        if args.resolve_pmids and pmid_doi.get(identifier):
            query["Resolved_DOI"] = pmid_doi[identifier]
        tokens = query_tokens(query)
        match = best_match(tokens, index)
        pdb_id = clean(query.get("PDB_ID")).upper() or "REFERENCES"
        if not match:
            rows.append(
                {
                    "PDB_ID": pdb_id,
                    "DOI": clean(query.get("DOI") or query.get("doi")),
                    "Resolved_DOI": clean(query.get("Resolved_DOI")),
                    "Title": clean(query.get("title") or query.get("citation_title")),
                    "Query_Tokens": "; ".join(f"{kind}:{value}" for kind, value in tokens),
                    "Match_Status": "not_found_locally",
                }
            )
            continue
        source = Path(match["path"])
        destination = (
            args.output_dir
            / "local_sources"
            / pdb_id
            / safe_name(source.name)
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        rows.append(
            {
                "PDB_ID": pdb_id,
                "DOI": clean(query.get("DOI") or query.get("doi")),
                "Resolved_DOI": clean(query.get("Resolved_DOI")),
                "Title": clean(query.get("title") or query.get("citation_title")),
                "Query_Tokens": "; ".join(f"{kind}:{value}" for kind, value in tokens),
                "Match_Status": "matched",
                "Source_Path": str(destination),
                "Original_Path": str(source),
                "SHA256": sha256(destination),
            }
        )
    write_csv(args.output_dir / "local_source_matches.csv", rows)
    print(
        f"{sum(row['Match_Status'] == 'matched' for row in rows)}/{len(rows)} "
        f"queries matched locally; output={args.output_dir}"
    )


if __name__ == "__main__":
    main()
