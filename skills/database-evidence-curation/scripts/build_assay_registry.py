#!/usr/bin/env python3
"""Build an assay-level registry with field coverage and explicit gaps."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clipped(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def first_nonempty(rows: list[dict[str, str]], field: str) -> str:
    for row in rows:
        value = row.get(field)
        if nonempty(value):
            return str(value).strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assay-column", default="assay_id")
    parser.add_argument("--compound-column", default="canonical_smiles")
    parser.add_argument("--source-column", default="source")
    parser.add_argument("--required-fields", default="")
    parser.add_argument("--max-value-chars", type=int, default=500)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if args.assay_column not in fields:
        raise SystemExit(f"missing assay column: {args.assay_column}")
    if not rows:
        raise SystemExit("input CSV has no data rows")

    required = [item.strip() for item in args.required_fields.split(",") if item.strip()]
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        assay_id = str(row.get(args.assay_column, "")).strip()
        if assay_id:
            grouped.setdefault(assay_id, []).append(row)
    if not grouped:
        raise SystemExit("no non-empty assay identifiers")

    assays: list[dict[str, Any]] = []
    for assay_id, assay_rows in sorted(grouped.items()):
        present = {
            field
            for field in fields
            if any(nonempty(row.get(field)) for row in assay_rows)
        }
        missing = [field for field in fields if field not in present]
        missing_required = [field for field in required if field not in present]
        compounds = {
            str(row.get(args.compound_column, "")).strip()
            for row in assay_rows
            if nonempty(row.get(args.compound_column))
        }
        sources = sorted(
            {
                str(row.get(args.source_column, "")).strip()
                for row in assay_rows
                if nonempty(row.get(args.source_column))
            }
        )
        if not sources and "family" in fields:
            sources = sorted(
                {
                    str(row.get("family", "")).strip()
                    for row in assay_rows
                    if nonempty(row.get("family"))
                }
            )
        representative = {
            field: clipped(first_nonempty(assay_rows, field), args.max_value_chars)
            for field in fields
        }
        context_flags = {
            "has_sequence": "sequence" in present,
            "has_endpoint": "endpoint" in present,
            "has_target": any(field in present for field in ("target_chembl_id", "uniprot_id", "target")),
            "has_readout": "readout_class" in present,
            "has_substrate": "substrate_text" in present,
            "has_assay_type": "assay_type" in present,
        }
        assays.append(
            {
                "assay_id": assay_id,
                "row_count": len(assay_rows),
                "unique_compounds": len(compounds),
                "sources": sources,
                "present_fields": sorted(present),
                "missing_fields": missing,
                "missing_required_fields": missing_required,
                "metadata_status": "complete_required" if not missing_required else "partial_required",
                "context_flags": context_flags,
                "values": representative,
            }
        )

    field_coverage = {}
    for field in fields:
        present_groups = sum(field in assay["present_fields"] for assay in assays)
        row_present = sum(nonempty(row.get(field)) for row in rows)
        field_coverage[field] = {
            "present_groups": present_groups,
            "total_groups": len(assays),
            "group_fraction": present_groups / len(assays),
            "present_rows": row_present,
            "total_rows": len(rows),
            "row_fraction": row_present / len(rows),
        }

    payload = {
        "ok": True,
        "schema_version": "assay-registry-v1",
        "input": {
            "path": str(args.input.resolve()),
            "sha256": sha256_file(args.input),
        },
        "assay_column": args.assay_column,
        "compound_column": args.compound_column,
        "row_count": len(rows),
        "assay_count": len(assays),
        "required_fields": required,
        "field_coverage": field_coverage,
        "assays": assays,
        "rule": "An assay registry summarizes observed fields. Missing fields remain missing; they are never imputed or filled from another assay.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "assays": len(assays),
                "rows": len(rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
