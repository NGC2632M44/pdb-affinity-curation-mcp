#!/usr/bin/env python3
"""Fetch ChEMBL assay, document and target metadata with an on-disk cache."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


BASE = "https://www.ebi.ac.uk/chembl/api/data"


def fetch_json(path: str, cache_dir: Path, timeout: int) -> dict[str, Any]:
    cache_file = cache_dir / path.replace("/", "__")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    url = f"{BASE}/{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "database-evidence-curation/1.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload
        except Exception as error:  # network failures are recorded by caller
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def compact_assay(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "assay_chembl_id": payload.get("assay_chembl_id"),
        "description": payload.get("description"),
        "assay_type": payload.get("assay_type"),
        "assay_type_description": payload.get("assay_type_description"),
        "assay_test_type": payload.get("assay_test_type"),
        "assay_organism": payload.get("assay_organism"),
        "assay_tissue": payload.get("assay_tissue"),
        "assay_cell_type": payload.get("assay_cell_type"),
        "cell_chembl_id": payload.get("cell_chembl_id"),
        "assay_subcellular_fraction": payload.get("assay_subcellular_fraction"),
        "target_chembl_id": payload.get("target_chembl_id"),
        "document_chembl_id": payload.get("document_chembl_id"),
        "confidence_score": payload.get("confidence_score"),
        "relationship_type": payload.get("relationship_type"),
        "bao_label": payload.get("bao_label"),
        "variant_sequence": payload.get("variant_sequence"),
        "assay_parameters": payload.get("assay_parameters") or [],
    }


def fetch_one(assay_id: str, cache_dir: Path, timeout: int, include_relations: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"assay_chembl_id": assay_id, "fetch_status": "error"}
    try:
        assay = fetch_json(f"assay/{assay_id}.json", cache_dir, timeout)
        result.update(compact_assay(assay))
        result["fetch_status"] = "ok"
        if include_relations and assay.get("document_chembl_id"):
            document_id = assay["document_chembl_id"]
            try:
                document = fetch_json(f"document/{document_id}.json", cache_dir, timeout)
                result["document_doi"] = document.get("doi")
                result["document_title"] = document.get("title")
                result["document_year"] = document.get("year")
                result["document_journal"] = document.get("journal")
            except Exception as error:
                result["document_fetch_error"] = str(error)
        if include_relations and assay.get("target_chembl_id"):
            target_id = assay["target_chembl_id"]
            try:
                target = fetch_json(f"target/{target_id}.json", cache_dir, timeout)
                result["target_pref_name"] = target.get("pref_name")
                result["target_type"] = target.get("target_type")
                result["target_organism"] = target.get("organism")
                result["target_components"] = target.get("target_components") or []
            except Exception as error:
                result["target_fetch_error"] = str(error)
    except Exception as error:
        result["fetch_error"] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--assay-column", default="assay_id")
    parser.add_argument("--assay-ids", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("chembl_cache"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--include-document-target", action="store_true")
    args = parser.parse_args()
    ids = [item.strip() for item in args.assay_ids.split(",") if item.strip()]
    if args.input:
        with args.input.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if args.assay_column not in (reader.fieldnames or []):
                raise SystemExit(f"missing assay column: {args.assay_column}")
            ids.extend(str(row.get(args.assay_column, "")).strip() for row in reader)
    ids = sorted({item for item in ids if item})
    if not ids:
        raise SystemExit("no assay IDs supplied")
    records = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(fetch_one, assay_id, args.cache_dir, args.timeout, args.include_document_target) for assay_id in ids]
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda item: item.get("assay_chembl_id", ""))
    payload = {
        "ok": all(record.get("fetch_status") == "ok" for record in records),
        "source": BASE,
        "assay_count": len(records),
        "fetch_ok": sum(record.get("fetch_status") == "ok" for record in records),
        "include_document_target": args.include_document_target,
        "records": records,
        "rule": "Metadata is a lead. Exact assay/construct/endpoint acceptance still requires context matching.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("ok", "assay_count", "fetch_ok", "output") if key in payload}, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
