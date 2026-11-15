#!/usr/bin/env python3
"""Build a provenance-aware metadata registry and coverage summary from a CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--id-columns", required=True, help="Comma-separated key columns")
    parser.add_argument("--source-column", default="source")
    parser.add_argument("--title-column", default="title")
    parser.add_argument("--required-fields", default="", help="Comma-separated required fields")
    parser.add_argument("--max-groups", type=int, default=0, help="0 keeps all groups")
    args = parser.parse_args()
    id_columns = [item.strip() for item in args.id_columns.split(",") if item.strip()]
    required_fields = [item.strip() for item in args.required_fields.split(",") if item.strip()]
    if not id_columns:
        raise SystemExit("--id-columns is empty")
    rows = list(csv.DictReader(args.input.open(encoding="utf-8-sig", newline="")))
    if not rows:
        raise SystemExit("input CSV has no data rows")
    fields = list(rows[0].keys())
    missing_key_fields = [field for field in id_columns if field not in fields]
    if missing_key_fields:
        raise SystemExit(f"missing key columns: {missing_key_fields}")

    field_counts = {field: sum(nonempty(row.get(field)) for row in rows) for field in fields}
    groups: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        key = tuple(str(row.get(field, "")).strip() for field in id_columns)
        if not any(key):
            continue
        group = groups.setdefault(
            key,
            {
                "key": dict(zip(id_columns, key)),
                "row_count": 0,
                "sources": set(),
                "fields_present": set(),
                "fields_missing": set(),
            },
        )
        group["row_count"] = int(group["row_count"]) + 1
        source = str(row.get(args.source_column, "")).strip()
        if source:
            group["sources"].add(source)
        for field in fields:
            if nonempty(row.get(field)):
                group["fields_present"].add(field)
            else:
                group["fields_missing"].add(field)

    group_rows = []
    for group in groups.values():
        missing_required = sorted(set(required_fields) & set(group["fields_missing"]))
        group_rows.append(
            {
                "key": group["key"],
                "row_count": group["row_count"],
                "sources": sorted(group["sources"]),
                "fields_present": sorted(group["fields_present"]),
                "fields_missing": sorted(group["fields_missing"]),
                "required_fields_missing": missing_required,
                "metadata_status": "complete_required" if not missing_required else "partial",
            }
        )
    group_rows.sort(key=lambda item: tuple(item["key"].get(field, "") for field in id_columns))
    if args.max_groups > 0:
        group_rows = group_rows[: args.max_groups]
    payload = {
        "ok": True,
        "schema_version": 1,
        "input": {"path": str(args.input.resolve()), "sha256": sha256_file(args.input)},
        "record_grain": id_columns,
        "row_count": len(rows),
        "group_count": len(groups),
        "fields": fields,
        "field_coverage": {
            field: {"present": count, "total": len(rows), "fraction": count / len(rows)}
            for field, count in field_counts.items()
        },
        "required_fields": required_fields,
        "groups": group_rows,
        "warnings": [
            "A registry hit is not accepted evidence until exact identity, context and primary-source locator are verified."
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output), "groups": len(groups), "rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
