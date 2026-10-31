#!/usr/bin/env python3
"""Reconcile curated records with Reaxys, SciFinder and other exports.

Database records are leads. They do not become accepted affinity records until
the primary paper, supplement or patent has been read.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {clean(key): clean(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pick(row: dict[str, str], *names: str) -> str:
    lookup = {key.lower().replace(" ", "_"): value for key, value in row.items()}
    for name in names:
        value = lookup.get(name.lower().replace(" ", "_"))
        if clean(value):
            return clean(value)
    return ""


def numeric(value: str) -> float | None:
    value = clean(value).replace(",", "")
    if not value or value.startswith(("<", ">", "~", "approx")):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def normalize_endpoint(value: str) -> str:
    lowered = clean(value).lower()
    if "ic50" in lowered:
        return "IC50"
    if re.search(r"\bkd\b|dissociation constant", lowered):
        return "Kd"
    if re.search(r"\bki\b", lowered):
        return "Ki"
    if "ec50" in lowered:
        return "EC50"
    if "cc50" in lowered:
        return "CC50"
    if "tm" in lowered or "melting" in lowered:
        return "Tm"
    return clean(value)


def normalize_source_row(
    source_name: str,
    row: dict[str, str],
    index: int,
) -> dict[str, Any]:
    value_text = pick(row, "Value", "Value_Text", "Value_nM", "activity_value")
    unit = pick(row, "Unit", "Unit_Original", "units")
    relation = pick(row, "Relation", "qualifier")
    if not relation:
        if value_text.startswith("<"):
            relation = "<"
        elif value_text.startswith(">"):
            relation = ">"
    target = pick(row, "Target", "Target_Enzyme", "target_pref_name")
    reference = pick(row, "Reference", "Source", "Data_Source", "document")
    record_id = pick(
        row,
        "Reaxys_Registry_Number",
        "CAS",
        "record_id",
        "id",
    )
    return {
        "source_database": source_name,
        "source_row": index,
        "PDB_ID": pick(row, "PDB ID", "PDB_ID").upper(),
        "DOI": pick(row, "DOI", "doi"),
        "InChIKey": pick(row, "InChIKey", "inchi_key"),
        "CAS": pick(row, "CAS", "cas"),
        "Target": target,
        "Mutation_Text": "I38T" if "i38t" in target.lower() else "",
        "Endpoint": normalize_endpoint(
            pick(row, "Parameter", "Assay_Type", "endpoint", "type")
        ),
        "Value_Text": value_text,
        "Value_Numeric": numeric(value_text),
        "Unit": unit,
        "Relation": relation,
        "Reference": reference,
        "Source_Record_ID": record_id,
        "Evidence_Role": "secondary_database_lead",
        "Patent_Candidate": (
            "yes"
            if re.search(r"\b(?:WO|US|EP|CN|JP)\d{6,}", reference, re.I)
            else "no"
        ),
    }


def curated_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        clean(row.get("PDB_ID")).upper(),
        normalize_endpoint(clean(row.get("Assay_Type"))),
        clean(row.get("Ligand_InChIKey") or row.get("InChIKey")),
    )


def source_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        clean(row.get("PDB_ID")).upper(),
        normalize_endpoint(clean(row.get("Endpoint"))),
        clean(row.get("InChIKey")),
    )


def values_overlap(
    left: dict[str, str],
    right: dict[str, Any],
) -> bool:
    a = numeric(clean(left.get("Value_nM")))
    b = right.get("Value_Numeric")
    if a is None or b is None:
        return False
    unit = clean(right.get("Unit")).lower().replace("μ", "u")
    if unit.startswith("u") and "m" in unit:
        b *= 1000
    elif unit.startswith("p") and "m" in unit:
        b /= 1000
    tolerance = max(0.01, 0.02 * max(abs(a), abs(float(b))))
    return abs(a - float(b)) <= tolerance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME=C:/path/export.csv",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    curated_rows = read_csv(args.curated)
    curated_by_pdb: dict[str, list[dict[str, str]]] = defaultdict(list)
    curated_by_doi: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in curated_rows:
        curated_by_pdb[clean(row.get("PDB_ID")).upper()].append(row)
        doi = clean(row.get("Evidence_DOI") or row.get("DOI")).lower()
        if doi:
            curated_by_doi[doi].append(row)

    source_rows: list[dict[str, Any]] = []
    for spec in args.source:
        if "=" not in spec:
            parser.error(f"--source must be NAME=PATH, got {spec!r}")
        name, path_text = spec.split("=", 1)
        path = Path(path_text)
        if not path.exists():
            parser.error(f"source file does not exist: {path}")
        for index, row in enumerate(read_csv(path), start=2):
            source_rows.append(normalize_source_row(name, row, index))

    source_by_pdb: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        if row["PDB_ID"]:
            source_by_pdb[row["PDB_ID"]].append(row)
        elif row["DOI"]:
            source_by_doi[row["DOI"].lower()].append(row)

    crosschecks = []
    conflicts = []
    status_counts: Counter[str] = Counter()
    orphan_source_rows = []
    for doi, rows in source_by_doi.items():
        doi_curated = curated_by_doi.get(doi.lower(), [])
        for candidate in rows:
            matching = [
                row
                for row in doi_curated
                if normalize_endpoint(clean(row.get("Assay_Type")))
                == normalize_endpoint(clean(candidate.get("Endpoint")))
                and (
                    not clean(candidate.get("InChIKey"))
                    or not clean(
                        row.get("Ligand_InChIKey") or row.get("InChIKey")
                    )
                    or clean(candidate.get("InChIKey")).lower()
                    == clean(
                        row.get("Ligand_InChIKey") or row.get("InChIKey")
                    ).lower()
                )
                and values_overlap(row, candidate)
            ]
            matched_pdbs = {
                clean(row.get("PDB_ID")).upper()
                for row in matching
                if clean(row.get("PDB_ID"))
            }
            if len(matched_pdbs) == 1:
                candidate["PDB_ID"] = next(iter(matched_pdbs))
                source_by_pdb[candidate["PDB_ID"]].append(candidate)
            else:
                orphan_source_rows.append(
                    {
                        **candidate,
                        "assignment_status": (
                            "doi_ambiguous_multiple_pdb"
                            if len(matched_pdbs) > 1
                            else "database_only_no_pdb_assignment"
                        ),
                    }
                )

    for pdb_id in sorted(set(curated_by_pdb) | set(source_by_pdb)):
        curated = curated_by_pdb.get(pdb_id, [])
        candidates = source_by_pdb.get(pdb_id, [])
        if not candidates:
            row = {
                "PDB_ID": pdb_id,
                "status": "no_secondary_source_record",
                "database_evidence_role": "none",
                "needs_primary_fulltext": "yes",
                "detail": "No supplied Reaxys/SciFinder/other record matched this PDB.",
            }
            crosschecks.append(row)
            status_counts[row["status"]] += 1
            conflicts.append(row)
            continue
        for candidate in candidates:
            matches = [
                row
                for row in curated
                if normalize_endpoint(clean(row.get("Assay_Type")))
                == normalize_endpoint(clean(candidate.get("Endpoint")))
                and (
                    not clean(candidate.get("InChIKey"))
                    or clean(candidate.get("InChIKey")).lower()
                    == clean(
                        row.get("Ligand_InChIKey") or row.get("InChIKey")
                    ).lower()
                )
            ]
            exact_value_match = any(
                values_overlap(row, candidate) for row in matches
            )
            if "i38t" in clean(candidate.get("Mutation_Text")).lower() and not any(
                "i38t" in clean(row.get("Mutation_Profile_per_paper")).lower()
                for row in curated
            ):
                status = "genotype_conflict"
            elif not matches:
                status = "database_only_identity_or_endpoint_mismatch"
            elif exact_value_match:
                status = "cross_source_value_agreement"
            else:
                status = "cross_source_value_conflict"
            row = {
                "PDB_ID": pdb_id,
                "status": status,
                "source_database": candidate["source_database"],
                "source_record_id": candidate["Source_Record_ID"],
                "source_doi": candidate["DOI"],
                "source_inchikey": candidate["InChIKey"],
                "source_cas": candidate["CAS"],
                "source_endpoint": candidate["Endpoint"],
                "source_value_text": candidate["Value_Text"],
                "source_unit": candidate["Unit"],
                "source_relation": candidate["Relation"],
                "source_target": candidate["Target"],
                "source_reference": candidate["Reference"],
                "database_evidence_role": "lead_requires_primary_source_review",
                "patent_candidate": candidate["Patent_Candidate"],
                "needs_primary_fulltext": (
                    "no"
                    if status == "cross_source_value_agreement"
                    else "yes"
                ),
            }
            crosschecks.append(row)
            status_counts[status] += 1
            if status != "cross_source_value_agreement":
                conflicts.append(row)

    for candidate in orphan_source_rows:
        row = {
            "PDB_ID": "",
            "status": candidate["assignment_status"],
            "source_database": candidate["source_database"],
            "source_record_id": candidate["Source_Record_ID"],
            "source_doi": candidate["DOI"],
            "source_inchikey": candidate["InChIKey"],
            "source_cas": candidate["CAS"],
            "source_endpoint": candidate["Endpoint"],
            "source_value_text": candidate["Value_Text"],
            "source_unit": candidate["Unit"],
            "source_relation": candidate["Relation"],
            "source_target": candidate["Target"],
            "source_reference": candidate["Reference"],
            "database_evidence_role": "lead_requires_pdb_assignment_and_primary_source",
            "patent_candidate": candidate["Patent_Candidate"],
            "needs_primary_fulltext": "yes",
        }
        crosschecks.append(row)
        conflicts.append(row)
        status_counts[row["status"]] += 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "crosscheck_records.csv", crosschecks)
    write_csv(args.output_dir / "conflicts.csv", conflicts)
    summary = {
        "curated_rows": len(curated_rows),
        "source_rows": len(source_rows),
        "status_counts": dict(status_counts),
        "policy": (
            "Secondary database records are discovery leads only. Even an "
            "agreement requires source-aware review before Evidence_Status=accepted."
        ),
    }
    (args.output_dir / "crosscheck_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
