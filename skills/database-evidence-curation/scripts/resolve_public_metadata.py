#!/usr/bin/env python3
"""Resolve DOI, PMID or PMCID identifiers through public metadata APIs."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "database-evidence-curation/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(str(last_error))


def resolve(identifier: str, kind: str, timeout: int) -> dict[str, Any]:
    value = identifier.strip()
    if not value:
        raise ValueError("empty identifier")
    if kind == "auto":
        if value.upper().startswith("PMC") or value.isdigit():
            kind = "europepmc"
        else:
            kind = "doi"
    if kind == "doi":
        if value.startswith("10.7270/"):
            url = f"https://api.datacite.org/dois/{urllib.parse.quote(value, safe='')}"
            data = get_json(url, timeout)["data"]["attributes"]
            return {
                "identifier": value,
                "kind": "doi",
                "source_api": "DataCite",
                "title": (data.get("titles") or [{}])[0].get("title"),
                "year": data.get("publicationYear"),
                "publisher": data.get("publisher"),
                "resource_type": (data.get("types") or {}).get("resourceTypeGeneral"),
                "url": data.get("url"),
                "provenance_url": url,
            }
        url = f"https://api.crossref.org/works/{urllib.parse.quote(value, safe='')}"
        data = get_json(url, timeout)["message"]
        issued = data.get("issued") or data.get("published-online") or data.get("published-print") or {}
        parts = issued.get("date-parts") or [[None]]
        return {
            "identifier": value,
            "kind": "doi",
            "source_api": "Crossref",
            "title": " ".join(data.get("title") or [""]),
            "year": parts[0][0] if parts and parts[0] else None,
            "publisher": data.get("publisher"),
            "resource_type": data.get("type"),
            "url": data.get("URL"),
            "provenance_url": url,
        }
    query = f'PMCID:"{value}"' if value.upper().startswith("PMC") else f'EXT_ID:"{value}"'
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode({"query": query, "format": "json", "pageSize": 5})
    payload = get_json(url, timeout)
    hits = (payload.get("resultList") or {}).get("result") or []
    return {
        "identifier": value,
        "kind": "europepmc",
        "source_api": "Europe PMC",
        "hit_count": int(payload.get("hitCount", 0)),
        "records": [
            {
                "title": hit.get("title"),
                "doi": hit.get("doi"),
                "pmid": hit.get("pmid"),
                "pmcid": hit.get("pmcid"),
                "is_open_access": hit.get("isOpenAccess"),
                "in_epmc": hit.get("inEPMC"),
                "year": hit.get("pubYear"),
            }
            for hit in hits
        ],
        "provenance_url": url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--id-column", default="doi")
    parser.add_argument("--identifiers", default="")
    parser.add_argument("--kind", choices=["auto", "doi", "europepmc"], default="auto")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    identifiers = [item.strip() for item in args.identifiers.split(",") if item.strip()]
    if args.input:
        with args.input.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if args.id_column not in (reader.fieldnames or []):
                raise SystemExit(f"missing identifier column: {args.id_column}")
            identifiers.extend(str(row.get(args.id_column, "")).strip() for row in reader)
    identifiers = sorted({item for item in identifiers if item})
    if not identifiers:
        raise SystemExit("no identifiers supplied")
    results = []
    for identifier in identifiers:
        try:
            results.append(resolve(identifier, args.kind, args.timeout))
        except Exception as error:
            results.append({"identifier": identifier, "kind": args.kind, "status": "error", "error": str(error)})
    payload = {
        "ok": all(item.get("status") != "error" for item in results),
        "identifier_count": len(results),
        "results": results,
        "rule": "Bibliographic metadata does not establish assay facts; keep it separate from primary evidence.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "identifier_count": len(results), "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
