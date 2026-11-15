#!/usr/bin/env python3
"""Audit metadata coverage and required-field gates from a registry JSON or CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def load_rows(path: Path) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("groups", []), payload
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle)), None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-fields", required=True, help="Comma-separated fields")
    parser.add_argument("--minimum-fraction", type=float, default=0.8)
    parser.add_argument("--source-field", default="sources")
    args = parser.parse_args()
    required = [item.strip() for item in args.required_fields.split(",") if item.strip()]
    rows, registry = load_rows(args.input)
    if registry is not None:
        coverage_map = registry.get("field_coverage", {})
        group_results = []
        for group in rows:
            key = group.get("key", {})
            present = set(group.get("fields_present", []))
            missing = [field for field in required if field not in present]
            group_results.append({"key": key, "missing_required": missing, "ok": not missing})
        field_coverage = {
            field: float(coverage_map.get(field, {}).get("fraction", 0.0))
            for field in required
        }
        sources = sorted({source for group in rows for source in group.get("sources", [])})
    else:
        group_results = []
        field_coverage = {field: 0.0 for field in required}
        sources = sorted({str(row.get(args.source_field, "")) for row in rows if nonempty(row.get(args.source_field))})
        for index, row in enumerate(rows, 1):
            missing = [field for field in required if not nonempty(row.get(field))]
            group_results.append({"key": {"row": index}, "missing_required": missing, "ok": not missing})
        if rows:
            for field in required:
                field_coverage[field] = sum(nonempty(row.get(field)) for row in rows) / len(rows)
    gate_failures = [field for field, value in field_coverage.items() if value < args.minimum_fraction]
    failing_groups = [group for group in group_results if not group["ok"]]
    result = {
        "ok": not gate_failures and not failing_groups,
        "input": str(args.input.resolve()),
        "rows_or_groups": len(rows),
        "required_fields": required,
        "minimum_fraction": args.minimum_fraction,
        "field_coverage": field_coverage,
        "gate_failures": gate_failures,
        "failing_groups": len(failing_groups),
        "sources": sources,
        "group_results": group_results,
        "rule": "A failed gate blocks model training for the affected context field; it does not block source discovery.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("ok", "rows_or_groups", "field_coverage", "gate_failures", "failing_groups")}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
