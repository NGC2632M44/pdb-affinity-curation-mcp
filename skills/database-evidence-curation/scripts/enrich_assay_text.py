#!/usr/bin/env python3
"""Extract deterministic assay context from descriptions without inventing values."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


READOUTS = [
    ("rapidfire_or_ms", r"rapidfire|mass spectrom|mass spec|q-tof"),
    ("fret", r"\bfret\b|fluorescence resonance energy transfer"),
    ("fluorescence", r"fluorescen|fluorogenic"),
    ("absorbance", r"absorbance|optical density"),
    ("alphascreen", r"alphascreen"),
]
EXPRESSION = [
    ("e_coli", r"escherichia coli|\be\.\s*coli\b"),
    ("hek293", r"hek\s*293"),
    ("mammalian", r"mammalian"),
]
TAGS = [("his", r"his\s*6|his-tag|his tag|his-tagged|his/"), ("sumo", r"\bsumo\b"), ("gst", r"\bgst\b")]


def first_match(patterns: list[tuple[str, str]], text: str) -> str:
    for label, pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return "unknown"


def extract_substrate(text: str) -> str:
    patterns = [
        r"using\s+(.{3,180}?)\s+as\s+(?:a\s+)?substrate",
        r"with\s+(.{3,180}?)\s+as\s+(?:a\s+)?substrate",
        r"substrate\s+(.{3,180}?)(?:\s+and|\s+by|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .;")
    return ""


def nonempty(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "nan", "none", "null", "<na>"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--description-column", default="description")
    parser.add_argument("--readout-column", default="readout_class")
    parser.add_argument("--expression-column", default="expression_system")
    parser.add_argument("--tag-column", default="tag_class")
    parser.add_argument("--substrate-column", default="substrate_text")
    args = parser.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if args.description_column not in fields:
        raise SystemExit(f"missing description column: {args.description_column}")
    for field in (args.readout_column, args.expression_column, args.tag_column, args.substrate_column):
        if field not in fields:
            fields.append(field)
    for row in rows:
        text = str(row.get(args.description_column, ""))
        row[args.readout_column] = first_match(READOUTS, text)
        row[args.expression_column] = first_match(EXPRESSION, text)
        row[args.tag_column] = first_match(TAGS, text)
        row[args.substrate_column] = extract_substrate(text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "ok": True,
        "rows": len(rows),
        "description_present": sum(nonempty(row.get(args.description_column)) for row in rows),
        "readout_present": sum(nonempty(row.get(args.readout_column)) and row.get(args.readout_column) != "unknown" for row in rows),
        "expression_present": sum(nonempty(row.get(args.expression_column)) and row.get(args.expression_column) != "unknown" for row in rows),
        "tag_present": sum(nonempty(row.get(args.tag_column)) and row.get(args.tag_column) != "unknown" for row in rows),
        "substrate_present": sum(nonempty(row.get(args.substrate_column)) for row in rows),
        "output": str(args.output.resolve()),
    }
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
