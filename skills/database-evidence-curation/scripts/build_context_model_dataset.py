#!/usr/bin/env python3
"""Export measurement rows for context-aware modeling without imputing gaps."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assay-column", default="assay_id")
    parser.add_argument(
        "--model-fields",
        default="canonical_smiles,p_activity,sequence,assay_type,readout_class,expression_system,tag_class,substrate_text,endpoint,target_chembl_id",
    )
    args = parser.parse_args()
    model_fields = [item.strip() for item in args.model_fields.split(",") if item.strip()]
    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if args.assay_column not in fields:
        raise SystemExit(f"missing assay column: {args.assay_column}")
    if not rows:
        raise SystemExit("input CSV has no data rows")

    output_fields = fields + ["assay_group", "missing_model_fields", "model_eligible", "imputation"]
    eligible_rows = 0
    missing_counts = {field: 0 for field in model_fields}
    assay_ids = set()
    for row in rows:
        assay_id = str(row.get(args.assay_column, "")).strip()
        if assay_id:
            assay_ids.add(assay_id)
        missing = [field for field in model_fields if not nonempty(row.get(field))]
        for field in missing:
            missing_counts[field] += 1
        row["assay_group"] = assay_id
        row["missing_model_fields"] = ";".join(missing)
        row["model_eligible"] = "1" if not missing else "0"
        row["imputation"] = "none"
        if not missing:
            eligible_rows += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "ok": True,
        "schema_version": "context-model-dataset-v1",
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "row_count": len(rows),
        "assay_count": len(assay_ids),
        "model_fields": model_fields,
        "model_eligible_rows": eligible_rows,
        "model_ineligible_rows": len(rows) - eligible_rows,
        "model_eligible_fraction": eligible_rows / len(rows),
        "missing_counts": missing_counts,
        "missing_fractions": {
            field: count / len(rows) for field, count in missing_counts.items()
        },
        "split_unit": args.assay_column,
        "imputation": "none",
        "policy": "Rows are retained and marked ineligible when context is missing. No missing context is imputed, filled forward, or borrowed from another assay.",
        "required_reports": [
            "assay-held-out evaluation",
            "explicit missingness indicators",
            "no outer-test-based model selection",
        ],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "manifest": str(args.manifest),
                "rows": len(rows),
                "eligible_rows": eligible_rows,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
