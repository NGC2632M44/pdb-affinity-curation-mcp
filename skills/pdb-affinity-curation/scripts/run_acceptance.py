#!/usr/bin/env python3
"""Run the deterministic final acceptance gates across the four skills."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_pdb_ids_file(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        raise SystemExit(f"PDB ID file not found: {path}")
    values = []
    for token in re.split(r"[\s,;]+", path.read_text(encoding="utf-8-sig")):
        value = token.strip().upper()
        if not value:
            continue
        if not re.fullmatch(r"[0-9A-Z]{4}", value):
            raise SystemExit(f"Invalid PDB ID in {path}: {token!r}")
        values.append(value)
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument("--review-checklist", required=True, type=Path)
    parser.add_argument("--blocker-ledger", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--human-workbook", required=True, type=Path)
    parser.add_argument("--human-sheet", default="a_")
    parser.add_argument("--audit-sheet")
    parser.add_argument("--correction-log", type=Path)
    parser.add_argument("--pdb-id", action="append", default=[])
    parser.add_argument("--pdb-id-file", type=Path)
    parser.add_argument("--visual-receipts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    skills_root = Path(__file__).resolve().parents[2]
    validator = (
        skills_root
        / "pdb-affinity-output"
        / "scripts"
        / "validate_affinity_dataset.py"
    )
    comparator = (
        skills_root
        / "pdb-affinity-output"
        / "scripts"
        / "compare_to_human.py"
    )
    validation_path = args.output_dir / "validation.json"
    validation_command = [
        sys.executable,
        str(validator),
        str(args.curated),
        "--require-fulltext-acceptance",
        "--review-checklist",
        str(args.review_checklist),
        "--blocker-ledger",
        str(args.blocker_ledger),
        "--require-blocker-closure",
        "--source-root",
        str(args.source_root),
        "--output",
        str(validation_path),
    ]
    validation_run = run(validation_command)
    validation = (
        json.loads(validation_path.read_text(encoding="utf-8"))
        if validation_path.exists()
        else {"status": "FAIL", "errors": [validation_run.stderr]}
    )
    comparison_dir = args.output_dir / "human_compare"
    compare_command = [
        sys.executable,
        str(comparator),
        "--curated",
        str(args.curated),
        "--human-workbook",
        str(args.human_workbook),
        "--human-sheet",
        args.human_sheet,
        "--output-dir",
        str(comparison_dir),
    ]
    if args.audit_sheet:
        insert_at = compare_command.index("--output-dir")
        compare_command[insert_at:insert_at] = [
            "--audit-sheet",
            args.audit_sheet,
        ]
    for pdb_id in args.pdb_id:
        compare_command.extend(["--pdb-id", pdb_id])
    if args.pdb_id_file:
        compare_command.extend(["--pdb-id-file", str(args.pdb_id_file)])
    if args.correction_log:
        compare_command.extend(["--correction-log", str(args.correction_log)])
    comparison_run = run(compare_command)
    comparison_metrics = (
        json.loads(
            (comparison_dir / "human_comparison_metrics.json").read_text(
                encoding="utf-8"
            )
        )
        if (comparison_dir / "human_comparison_metrics.json").exists()
        else {"status": "FAIL", "error": comparison_run.stderr}
    )
    blockers = read_csv(args.blocker_ledger)
    open_required = [
        row
        for row in blockers
        if clean(row.get("Required_For_Acceptance")).lower() == "yes"
        and clean(row.get("Resolution_Status")).lower()
        not in {
            "resolved",
            "resolved_with_context",
            "resolved_candidate_only",
            "not_applicable",
        }
    ]
    visual_receipts = read_csv(args.visual_receipts)
    curated_rows = read_csv(args.curated)
    accepted_rows = [
        row
        for row in curated_rows
        if clean(row.get("Evidence_Status")).lower() == "accepted"
    ]
    visual_required_pdbs = {
        clean(row.get("PDB_ID")).upper()
        for row in curated_rows
        if clean(row.get("Evidence_Status")) == "accepted"
        and clean(row.get("Visual_Review_Required")).lower() == "yes"
    }
    complete_visual_pdbs = {
        clean(row.get("PDB_ID")).upper()
        for row in visual_receipts
        if clean(row.get("Review_Status")).lower() == "complete"
    }
    missing_visual_pdbs = sorted(visual_required_pdbs - complete_visual_pdbs)
    visual_incomplete = [
        row
        for row in visual_receipts
        if clean(row.get("Review_Status")).lower() != "complete"
    ]
    checks = {
        "strict_validation": validation.get("status") == "PASS",
        "accepted_rows_present": bool(accepted_rows),
        "human_comparison": comparison_metrics.get("status") == "PASS",
        "blocker_closure": not open_required,
        "visual_receipts": (
            args.visual_receipts is not None
            and args.visual_receipts.exists()
            and not visual_incomplete
            and not missing_visual_pdbs
        ),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "accepted_rows": len(accepted_rows),
        "validation_file": str(validation_path),
        "validation_summary": {
            "status": validation.get("status"),
            "rows": validation.get("rows"),
            "error_count": len(validation.get("errors") or []),
            "warning_count": len(validation.get("warnings") or []),
            "missing_strict_columns": validation.get("missing_strict_columns") or [],
            "warning_examples": (validation.get("warnings") or [])[:20],
        },
        "human_comparison": comparison_metrics,
        "open_required_blockers": open_required,
        "incomplete_visual_receipts": visual_incomplete,
        "missing_visual_receipt_pdbs": missing_visual_pdbs,
        "commands": {
            "validation": validation_command,
            "human_comparison": compare_command,
        },
    }
    (args.output_dir / "final_acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": checks,
                "accepted_rows": len(accepted_rows),
                "open_required_blockers": len(open_required),
                "incomplete_visual_receipts": len(visual_incomplete),
                "missing_visual_receipt_pdbs": len(missing_visual_pdbs),
                "final_acceptance_file": str(
                    args.output_dir / "final_acceptance.json"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
