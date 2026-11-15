#!/usr/bin/env python3
"""Turn assay-level metadata gaps into a prioritized, traceable source queue."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def queue_id(assay_id: str, missing: list[str]) -> str:
    digest = hashlib.sha256((assay_id + "|" + "|".join(missing)).encode("utf-8")).hexdigest()
    return "AQ-" + digest[:12].upper()


def source_for(missing: list[str], values: dict[str, Any]) -> str:
    if any(field in missing for field in ("substrate_text", "readout_class", "description", "assay_type")):
        if nonempty(values.get("doi")):
            return "primary_paper_or_supplement"
        return "primary_paper_or_supplement"
    if "sequence" in missing or "construct" in missing:
        return "uniprot_or_primary_paper"
    if any(field in missing for field in ("target_chembl_id", "target", "organism")):
        return "chembl_or_primary_paper"
    if any(field in missing for field in ("endpoint", "unit", "relation")):
        return "primary_assay_record"
    return "primary_paper_or_supplement"


def source_hint(values: dict[str, Any]) -> str:
    for field in ("doi", "document_chembl_id", "assay_id", "target_chembl_id"):
        value = values.get(field)
        if nonempty(value):
            return f"{field}={str(value).strip()}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--priority-fields",
        default="substrate_text,readout_class,expression_system,tag_class,sequence,endpoint,target_chembl_id,assay_type,description",
    )
    parser.add_argument("--include-ready", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    assays = payload.get("assays") or payload.get("groups") or []
    priority = [item.strip() for item in args.priority_fields.split(",") if item.strip()]
    rows: list[dict[str, Any]] = []
    for assay in assays:
        key = assay.get("key") if isinstance(assay.get("key"), dict) else {}
        assay_id = str(assay.get("assay_id") or key.get("assay_id") or "").strip()
        if not assay_id:
            continue
        present = set(assay.get("present_fields") or assay.get("fields_present") or [])
        missing = [field for field in priority if field not in present]
        if not missing and not args.include_ready:
            continue
        values = assay.get("values") or {}
        row_count = int(assay.get("row_count") or 0)
        unique_compounds = int(assay.get("unique_compounds") or 0)
        score = (len(missing) * 20) + (row_count * 0.1) + (unique_compounds * 0.2)
        rows.append(
            {
                "queue_id": queue_id(assay_id, missing),
                "assay_id": assay_id,
                "family": ",".join(assay.get("sources") or []),
                "unique_compounds": unique_compounds,
                "rows": row_count,
                "missing_fields": ";".join(missing),
                "priority_score": round(score, 2),
                "next_source": source_for(missing, values),
                "source_hint": source_hint(values),
                "status": "ready" if not missing else "queued",
                "reason": "metadata_context_missing" if missing else "complete_priority_context",
            }
        )
    rows.sort(key=lambda item: (-float(item["priority_score"]), item["assay_id"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "queue_id",
        "assay_id",
        "family",
        "unique_compounds",
        "rows",
        "missing_fields",
        "priority_score",
        "next_source",
        "source_hint",
        "status",
        "reason",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "ok": True,
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "assays_total": len(assays),
        "queue_items": len(rows),
        "ready_items": sum(item["status"] == "ready" for item in rows),
        "missing_field_counts": {
            field: sum(field in str(item["missing_fields"]).split(";") for item in rows)
            for field in priority
        },
        "next_source_counts": {
            source: sum(item["next_source"] == source for item in rows)
            for source in sorted({str(item["next_source"]) for item in rows})
        },
        "rule": "A queue item is a collection task, not evidence. It must be resolved by an exact primary or structured source and recorded with a locator or blocker.",
    }
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "queue_items": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
