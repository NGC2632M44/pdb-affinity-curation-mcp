#!/usr/bin/env python3
"""Create a row/field-level change log between two curated CSV versions."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        rows = [
            {clean(key): clean(value) for key, value in row.items()}
            for row in reader
        ]
    return headers, rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def group_rows(
    rows: list[dict[str, str]],
    key_columns: list[str],
) -> dict[tuple[str, ...], list[dict[str, str]]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(clean(row.get(column)) for column in key_columns)].append(row)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True, type=Path)
    parser.add_argument("--new", required=True, type=Path)
    parser.add_argument("--key", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    old_headers, old_rows = read_csv(args.old)
    new_headers, new_rows = read_csv(args.new)
    old_grouped = group_rows(old_rows, args.key)
    new_grouped = group_rows(new_rows, args.key)
    all_headers = list(dict.fromkeys(old_headers + new_headers))
    changes = []

    for key in sorted(set(old_grouped) | set(new_grouped)):
        old_records = old_grouped.get(key, [])
        new_records = new_grouped.get(key, [])
        key_text = " | ".join(f"{column}={value}" for column, value in zip(args.key, key))
        if not old_records:
            changes.append(
                {
                    "Change_Type": "record_added",
                    "Record_Key": key_text,
                    "Old_Row_Count": 0,
                    "New_Row_Count": len(new_records),
                    "Field": "",
                    "Old_Value": "",
                    "New_Value": "",
                }
            )
            continue
        if not new_records:
            changes.append(
                {
                    "Change_Type": "record_removed",
                    "Record_Key": key_text,
                    "Old_Row_Count": len(old_records),
                    "New_Row_Count": 0,
                    "Field": "",
                    "Old_Value": "",
                    "New_Value": "",
                }
            )
            continue
        old = old_records[0]
        new = new_records[0]
        for field in all_headers:
            old_value = clean(old.get(field))
            new_value = clean(new.get(field))
            if old_value != new_value:
                changes.append(
                    {
                        "Change_Type": "field_modified",
                        "Record_Key": key_text,
                        "Old_Row_Count": len(old_records),
                        "New_Row_Count": len(new_records),
                        "Field": field,
                        "Old_Value": old_value,
                        "New_Value": new_value,
                    }
                )

    write_csv(args.output, changes)
    print(
        f"{len(changes)} change rows written to {args.output}; "
        f"old={len(old_rows)}, new={len(new_rows)}"
    )


if __name__ == "__main__":
    main()
