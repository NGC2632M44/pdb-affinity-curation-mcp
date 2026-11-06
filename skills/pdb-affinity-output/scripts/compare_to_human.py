#!/usr/bin/env python3
"""Compare independently curated records with a human review workbook.

The human workbook is treated as a draft plus verified exception ledger, not as
infallible ground truth. A disagreement passes only when a correction-log entry
identifies the PDB and gives an evidence-backed reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any


UNIT_TO_NM = {
    "pm": 1e-3,
    "nm": 1.0,
    "um": 1e3,
    "µm": 1e3,
    "μm": 1e3,
    "mm": 1e6,
    "m": 1e9,
}


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {clean(key): clean(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def workbook_rows(
    path: Path,
    sheet_match: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = None
    if sheet_match:
        sheet = next(
            (
                item
                for item in workbook.worksheets
                if item.title.startswith(sheet_match)
            ),
            None,
        )
        if sheet is None:
            sheet = next(
                (
                    item
                    for item in workbook.worksheets
                    if sheet_match.lower() in item.title.lower()
                ),
                None,
            )
    if sheet is None:
        raise SystemExit(f"worksheet not found in {path}: {sheet_match}")
    headers = [clean(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    output = []
    for row_number, cells in enumerate(sheet.iter_rows(min_row=2), start=2):
        values = [cell.value for cell in cells]
        if not any(value not in (None, "") for value in values):
            continue
        record = {
            header or f"Column_{index + 1}": values[index] if index < len(values) else None
            for index, header in enumerate(headers)
        }
        record["_row"] = row_number
        output.append(record)
    return sheet.title, output


def endpoint_family(value: str) -> str:
    lowered = value.lower()
    if "single-concentration" in lowered or "complete inhibition" in lowered:
        return "concentration"
    if "qualitative" in lowered or ("gel" in lowered and "inhib" in lowered):
        return "qualitative_inhibition"
    if "δtm" in lowered or "delta-tm" in lowered or re.search(r"\btm\b", lowered):
        return "Tm"
    if "cc50" in lowered or "cytotox" in lowered:
        return "CC50"
    if "ec50" in lowered or "cpe" in lowered:
        return "EC50"
    if "ic50" in lowered:
        return "IC50"
    if re.search(r"\bki\b", lowered):
        return "Ki"
    if (
        re.search(r"(?<![a-z])k\s*d\s*\d?(?![a-z])", lowered)
        or "dissociation constant" in lowered
    ):
        return "Kd"
    if any(token in lowered for token in ("concentration", "single-point", "inhibition")):
        return "concentration"
    return "other"


def measurement_context(row: dict[str, Any], prefix: str = "") -> str:
    method = clean(row.get(f"{prefix}Method")).lower()
    assay = clean(row.get(f"{prefix}Assay_System")).lower()
    cell_line = clean(row.get(f"{prefix}Cell_Line")).lower()
    endpoint = clean(
        row.get(f"{prefix}Endpoint_As_Defined_By_Paper")
        or row.get(f"{prefix}Assay_Type")
    ).lower()
    combined = " ".join((method, assay, cell_line, endpoint))
    if "itc" in combined or "isothermal" in combined:
        return "ITC"
    if "mst" in combined or "thermophoresis" in combined:
        return "MST"
    if "fp" in combined or "polarization" in combined:
        return "FP"
    if "dsf" in combined or "thermal shift" in combined or "apparent" in combined:
        return "DSF_model"
    if "mdck" in combined or "plaque" in combined or "cell culture" in combined:
        return "cell"
    if "gel" in combined and "concentration" in combined:
        return "gel_single_concentration"
    if "metal" in combined or "manganese" in combined or "magnesium" in combined:
        return "metal"
    if "rna" in combined and "kd" in combined:
        return "RNA"
    if "fret" in combined or "quench" in combined or "maldi" in combined:
        return "biochemical"
    return "unknown"


def mutation_profile(row: dict[str, Any], prefix: str = "") -> str:
    value = clean(
        row.get(f"{prefix}Mutation_Profile_per_paper")
        or row.get(f"{prefix}Mutation")
        or row.get(f"{prefix}Construct")
    )
    matches = re.findall(r"\b[A-Z]\d{1,4}[A-Z]\b", value.upper())
    if matches:
        return ";".join(sorted(set(matches)))
    if re.search(r"\bWT\b|wild[- ]?type", value, re.IGNORECASE):
        return "WT"
    return ""


def compound_token(value: str) -> str:
    text = clean(value)
    match = re.search(r"compound\s*([0-9]+[a-z-]*)", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if re.fullmatch(r"[0-9]+[a-z-]*", text, re.IGNORECASE):
        return text.lower()
    return normalize_token(text)


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).lower())


def parse_quantity(value: str, unit: str) -> dict[str, Any]:
    original = clean(value).replace(",", "")
    original_unit = clean(unit)
    qualifier = ""
    for candidate in ("<=", ">=", "≤", "≥", "<", ">", "~"):
        if original.startswith(candidate):
            qualifier = candidate
            original = original[len(candidate) :].strip()
            break
    number_match = re.search(r"-?\d+(?:\.\d+)?", original)
    if not number_match:
        return {
            "qualifier": qualifier,
            "number": None,
            "unit": original_unit,
            "nM": None,
            "normalized": "",
        }
    number = float(number_match.group(0))
    unit_key = original_unit.lower().replace("μ", "u").replace("µ", "u")
    if not unit_key:
        embedded = re.search(r"(pm|nm|um|mm|m)\b", original.lower(), re.IGNORECASE)
        unit_key = embedded.group(1).lower() if embedded else ""
    factor = UNIT_TO_NM.get(unit_key)
    nm = number * factor if factor is not None else None
    return {
        "qualifier": qualifier,
        "number": number,
        "unit": original_unit,
        "nM": nm,
        "normalized": (
            f"{qualifier}{nm:.8g}"
            if nm is not None
            else f"{qualifier}{number:.8g}"
        ),
    }


def values_agree(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["number"] is None or right["number"] is None:
        return clean(left["normalized"]) == clean(right["normalized"])
    if left["qualifier"] != right["qualifier"]:
        return False
    if left["nM"] is not None and right["nM"] is not None:
        return math.isclose(left["nM"], right["nM"], rel_tol=0.01, abs_tol=1e-9)
    return math.isclose(left["number"], right["number"], rel_tol=0.01, abs_tol=1e-9)


def status_family(value: str) -> str:
    lowered = value.lower()
    if lowered == "accepted":
        return "accepted"
    if "blocked" in lowered or "unresolved" in lowered:
        return "blocked"
    if "conflict" in lowered:
        return "conflict"
    if "needs" in lowered:
        return "needs_source"
    if lowered in {"rejected", "not_applicable"}:
        return "rejected"
    return lowered or "unknown"


def correction_index(path: Path | None) -> dict[str, list[dict[str, str]]]:
    if not path or not path.exists():
        return {}
    rows = read_csv(path)
    output: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        pdb_id = clean(row.get("PDB_ID")).upper()
        reason = clean(row.get("Reason"))
        old_value = clean(row.get("Old_Value")).lower()
        new_value = clean(row.get("New_Value")).lower()
        if pdb_id and reason and (
            old_value in {"", "blocked", "no_value", "human_no_value"}
            or "human" in reason.lower()
            or "correction" in reason.lower()
            or "primary" in reason.lower()
        ):
            output.setdefault(pdb_id, []).append(row)
    return output


def correction_receipts(
    corrections: list[dict[str, str]],
    failed_fields: set[str],
    curated: dict[str, Any],
) -> tuple[list[dict[str, str]], set[str]]:
    if not failed_fields:
        return [], set()
    aliases = {
        "value": {"value", "quantity", "result"},
        "endpoint": {"endpoint", "assay"},
        "mutation": {"mutation", "construct", "genotype"},
        "compound": {"compound", "compound_label", "identity", "ligand"},
        "decision": {"decision", "status", "disposition"},
    }
    output = []
    covered = set()
    for row in corrections:
        field_text = clean(row.get("Field")).lower()
        for field in failed_fields:
            if not any(alias in field_text for alias in aliases.get(field, {field})):
                continue
            new_value = clean(row.get("New_Value"))
            matches = False
            if field == "value":
                matches = values_agree(
                    parse_quantity(new_value, curated["unit_original"]),
                    curated["quantity"],
                )
            elif field == "compound":
                matches = compound_token(new_value) == curated["compound_label"]
            elif field == "mutation":
                matches = normalize_token(new_value) == normalize_token(
                    curated["mutation"]
                )
            elif field == "decision":
                new_status = clean(new_value).lower()
                matches = new_status in {
                    clean(curated.get("affinity_status")).lower(),
                    clean(curated.get("evidence_status")).lower(),
                    status_family(new_status),
                } or status_family(new_status) == status_family(
                    curated.get("evidence_status", "")
                )
            elif field == "endpoint":
                matches = (
                    endpoint_family(new_value) == curated["endpoint_family"]
                )
            if matches:
                output.append(row)
                covered.add(field)
    return output, covered


def human_rows(path: Path, sheet_match: str, target_pdbs: set[str]) -> tuple[str, list[dict[str, Any]]]:
    title, records = workbook_rows(path, sheet_match)
    output = []
    for record in records:
        pdb_id = clean(record.get("PDB_ID")).upper()
        if target_pdbs and pdb_id not in target_pdbs:
            continue
        endpoint = " ".join(
            item
            for item in (
                clean(record.get("Assay_Type")),
                clean(record.get("Endpoint_As_Defined_By_Paper")),
                clean(record.get("Metric")),
            )
            if item
        )
        value = clean(record.get("Value_Original"))
        unit = clean(record.get("Unit_Original"))
        mutation = mutation_profile(record)
        note = clean(record.get("Note"))
        side_note = clean(record.get("Column_28"))
        gold_status = "verified"
        if "此处有误" in note or "under review" in note.lower():
            gold_status = "draft_known_incomplete"
        if not value:
            gold_status = "draft_no_value"
        output.append(
            {
                "human_id": f"{title}:{record['_row']}",
                "sheet": title,
                "row": record["_row"],
                "PDB_ID": pdb_id,
                "DOI": clean(record.get("DOI")),
                "endpoint_raw": endpoint,
                "endpoint_family": endpoint_family(endpoint),
                "context": measurement_context(record),
                "value_original": value,
                "unit_original": unit,
                "quantity": parse_quantity(value, unit),
                "mutation": mutation,
                "compound_label": compound_token(
                    clean(
                        record.get("Paper_Compound_Label")
                        or record.get("Compound")
                    )
                ),
                "method": clean(record.get("Method")),
                "note": note,
                "side_note": side_note,
                "gold_status": gold_status,
                "source": "manual_review_sheet",
            }
        )
    return title, output


def audit_rows(path: Path, sheet_match: str, target_pdbs: set[str]) -> list[dict[str, Any]]:
    title, records = workbook_rows(path, sheet_match)
    output = []
    for record in records:
        pdb_id = clean(record.get("pdb_id")).upper()
        if target_pdbs and pdb_id not in target_pdbs:
            continue
        endpoint = clean(record.get("endpoint"))
        value = clean(record.get("value_as_reported"))
        unit = clean(record.get("unit"))
        output.append(
            {
                "human_id": f"{title}:{record['_row']}",
                "sheet": title,
                "row": record["_row"],
                "PDB_ID": pdb_id,
                "DOI": clean(record.get("source_doi")),
                "endpoint_raw": endpoint,
                "endpoint_family": endpoint_family(endpoint),
                "context": measurement_context(record),
                "value_original": "" if value.lower().startswith("not ") else value,
                "unit_original": "" if unit.lower().startswith("not ") else unit,
                "quantity": parse_quantity(
                    "" if value.lower().startswith("not ") else value,
                    "" if unit.lower().startswith("not ") else unit,
                ),
                "mutation": mutation_profile(record),
                "compound_label": compound_token(clean(record.get("dubois_compound"))),
                "method": clean(record.get("notes")),
                "note": clean(record.get("notes")),
                "side_note": "",
                "gold_status": "verified_exception_ledger",
                "source": "verified_exception_sheet",
            }
        )
    return output


def curated_rows(path: Path, target_pdbs: set[str]) -> list[dict[str, Any]]:
    output = []
    for index, row in enumerate(read_csv(path), start=2):
        pdb_id = clean(row.get("PDB_ID")).upper()
        if target_pdbs and pdb_id not in target_pdbs:
            continue
        endpoint = " ".join(
            item
            for item in (
                clean(row.get("Assay_Type")),
                clean(row.get("Endpoint_As_Defined_By_Paper")),
            )
            if item
        )
        output.append(
            {
                "curated_id": clean(row.get("Correction_ID")) or f"row:{index}",
                "row": index,
                "PDB_ID": pdb_id,
                "DOI": clean(row.get("DOI")),
                "Evidence_DOI": clean(row.get("Evidence_DOI")),
                "endpoint_raw": endpoint,
                "endpoint_family": endpoint_family(endpoint),
                "context": measurement_context(row),
                "value_original": clean(row.get("Value_Original")),
                "unit_original": clean(row.get("Unit_Original")),
                "quantity": parse_quantity(
                    clean(row.get("Value_Original")),
                    clean(row.get("Unit_Original")),
                ),
                "mutation": mutation_profile(row),
                "compound_label": compound_token(
                    clean(row.get("Paper_Compound_Label"))
                ),
                "method": clean(row.get("Method")),
                "provenance": clean(row.get("Provenance_Class")),
                "evidence_status": clean(row.get("Evidence_Status")),
                "affinity_status": clean(row.get("Affinity_Status")),
                "conflict_status": clean(row.get("Conflict_Status")),
                "mapping_evidence": clean(row.get("Mapping_Evidence")),
                "row": row,
            }
        )
    return output


def row_score(human: dict[str, Any], curated: dict[str, Any]) -> float:
    if human["PDB_ID"] != curated["PDB_ID"]:
        return -1
    score = 100.0
    if human["endpoint_family"] == curated["endpoint_family"]:
        score += 30
    elif human["endpoint_family"] == "other":
        score += 3
    if human["mutation"] and human["mutation"] == curated["mutation"]:
        score += 15
    if human["compound_label"] and human["compound_label"] == curated["compound_label"]:
        score += 10
    if human["context"] == curated["context"]:
        score += 15
    if human["quantity"]["number"] is not None and values_agree(
        human["quantity"], curated["quantity"]
    ):
        score += 30
    return score


def match_rows(
    humans: list[dict[str, Any]],
    curated: list[dict[str, Any]],
    corrections: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    pairs = []
    for human in humans:
        for cur in curated:
            pairs.append((row_score(human, cur), human, cur))
    pairs.sort(key=lambda item: (-item[0], item[1]["PDB_ID"], item[2]["curated_id"]))
    assigned_human = set()
    assigned_curated = set()
    output = []
    for score, human, cur in pairs:
        if score < 100 or human["human_id"] in assigned_human or cur["curated_id"] in assigned_curated:
            continue
        assigned_human.add(human["human_id"])
        assigned_curated.add(cur["curated_id"])
        value_ok = (
            human["quantity"]["number"] is None
            or values_agree(human["quantity"], cur["quantity"])
        )
        endpoint_ok = human["endpoint_family"] in {
            "other",
            cur["endpoint_family"],
        }
        mutation_ok = not human["mutation"] or human["mutation"] == cur["mutation"]
        compound_ok = (
            not human["compound_label"]
            or human["compound_label"] == cur["compound_label"]
        )
        human_has_value = (
            human["quantity"]["number"] is not None
            or bool(human["value_original"])
        )
        curated_has_value = (
            cur["quantity"]["number"] is not None
            or bool(cur["value_original"])
        )
        status_ok = (
            human["gold_status"] != "draft_no_value"
            and cur["evidence_status"] == "accepted"
        ) or (
            human["gold_status"] == "draft_no_value"
            and cur["evidence_status"] in {"accepted", "blocked"}
        )
        if (
            human["gold_status"] == "draft_no_value"
            and not human_has_value
            and curated_has_value
            and cur["evidence_status"] == "accepted"
        ):
            status_ok = False
        correction = corrections.get(human["PDB_ID"], [])
        failed_fields = set()
        if not value_ok:
            failed_fields.add("value")
        if not endpoint_ok:
            failed_fields.add("endpoint")
        if not mutation_ok:
            failed_fields.add("mutation")
        if not compound_ok:
            failed_fields.add("compound")
        if not status_ok:
            failed_fields.add("decision")
        supporting_corrections, covered_fields = correction_receipts(
            correction,
            failed_fields,
            cur,
        )
        evidence_corrected = bool(
            supporting_corrections
            and failed_fields <= covered_fields
        )
        output.append(
            {
                "PDB_ID": human["PDB_ID"],
                "Human_ID": human["human_id"],
                "Human_Source": human["source"],
                "Human_Gold_Status": human["gold_status"],
                "Human_Endpoint": human["endpoint_raw"],
                "Human_Value": human["value_original"],
                "Human_Unit": human["unit_original"],
                "Human_nM": human["quantity"]["normalized"],
                "Human_Mutation": human["mutation"],
                "Human_Compound": human["compound_label"],
                "Curated_ID": cur["curated_id"],
                "Curated_Endpoint": cur["endpoint_raw"],
                "Curated_Value": cur["value_original"],
                "Curated_Unit": cur["unit_original"],
                "Curated_nM": cur["quantity"]["normalized"],
                "Curated_Mutation": cur["mutation"],
                "Curated_Compound": cur["compound_label"],
                "Curated_Status": cur["evidence_status"],
                "Curated_Provenance": cur["provenance"],
                "Value_Agreement": "yes" if value_ok else "no",
                "Endpoint_Agreement": "yes" if endpoint_ok else "no",
                "Mutation_Agreement": "yes" if mutation_ok else "no",
                "Compound_Agreement": "yes" if compound_ok else "no",
                "Decision_Agreement": "yes" if status_ok else "no",
                "Evidence_Backed_Correction": "yes" if evidence_corrected else "no",
                "Correction_Reason": (
                    " | ".join(
                        clean(item.get("Reason"))
                        for item in supporting_corrections[:3]
                    )
                    if evidence_corrected
                    else ""
                ),
                "Overall": (
                    "match"
                    if value_ok and endpoint_ok and mutation_ok and compound_ok and status_ok
                    else "evidence_corrected"
                    if evidence_corrected
                    else "unresolved_disagreement"
                ),
            }
        )
    for human in humans:
        if human["human_id"] not in assigned_human:
            supporting_corrections, _ = correction_receipts(
                corrections.get(human["PDB_ID"], []),
                {"decision", "compound"},
                {
                    "unit_original": "",
                    "quantity": parse_quantity("", ""),
                    "compound_label": human["compound_label"],
                    "mutation": human["mutation"],
                    "affinity_status": "",
                    "evidence_status": "accepted",
                    "endpoint_family": human["endpoint_family"],
                },
            )
            output.append(
                {
                    "PDB_ID": human["PDB_ID"],
                    "Human_ID": human["human_id"],
                    "Human_Source": human["source"],
                    "Human_Gold_Status": human["gold_status"],
                    "Human_Endpoint": human["endpoint_raw"],
                    "Human_Value": human["value_original"],
                    "Human_Unit": human["unit_original"],
                    "Human_nM": human["quantity"]["normalized"],
                    "Human_Mutation": human["mutation"],
                    "Human_Compound": human["compound_label"],
                    "Curated_ID": "",
                    "Overall": (
                        "evidence_corrected"
                        if supporting_corrections
                        else "missing_in_curated"
                    ),
                    "Evidence_Backed_Correction": (
                        "yes" if supporting_corrections else "no"
                    ),
                    "Correction_Reason": (
                        " | ".join(
                            clean(item.get("Reason"))
                            for item in supporting_corrections[:3]
                        )
                        if supporting_corrections
                        else ""
                    ),
                }
            )
    for cur in curated:
        if cur["curated_id"] not in assigned_curated:
            supporting_corrections, _ = correction_receipts(
                corrections.get(cur["PDB_ID"], []),
                {"value", "decision", "compound"},
                cur,
            )
            output.append(
                {
                    "PDB_ID": cur["PDB_ID"],
                    "Human_ID": "",
                    "Curated_ID": cur["curated_id"],
                    "Curated_Endpoint": cur["endpoint_raw"],
                    "Curated_Value": cur["value_original"],
                    "Curated_Unit": cur["unit_original"],
                    "Curated_nM": cur["quantity"]["normalized"],
                    "Curated_Mutation": cur["mutation"],
                    "Curated_Compound": cur["compound_label"],
                    "Curated_Status": cur["evidence_status"],
                    "Curated_Provenance": cur["provenance"],
                    "Overall": (
                        "evidence_added"
                        if supporting_corrections
                        else "extra_in_curated"
                    ),
                    "Evidence_Backed_Correction": (
                        "yes" if supporting_corrections else "no"
                    ),
                    "Correction_Reason": (
                        " | ".join(
                            clean(item.get("Reason"))
                            for item in supporting_corrections[:3]
                        )
                        if supporting_corrections
                        else ""
                    ),
                }
            )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument("--human-workbook", required=True, type=Path)
    parser.add_argument("--human-sheet", default="a_")
    parser.add_argument("--audit-sheet")
    parser.add_argument("--pdb-id", action="append", default=[])
    parser.add_argument("--pdb-id-file", type=Path)
    parser.add_argument("--correction-log", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    target_values = [*args.pdb_id, *read_pdb_ids_file(args.pdb_id_file)]
    targets = {clean(value).upper() for value in target_values if clean(value)}
    title, humans = human_rows(args.human_workbook, args.human_sheet, targets)
    audits = (
        audit_rows(args.human_workbook, args.audit_sheet, targets)
        if args.audit_sheet
        else []
    )
    audit_pdbs = {row["PDB_ID"] for row in audits}
    humans = [row for row in humans if row["PDB_ID"] not in audit_pdbs]
    humans.extend(audits)
    curated = curated_rows(args.curated, targets)
    corrections = correction_index(args.correction_log)
    comparison = match_rows(humans, curated, corrections)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "human_comparison.csv", comparison)
    matched = [row for row in comparison if row.get("Human_ID") and row.get("Curated_ID")]
    corrected = [row for row in comparison if row.get("Overall") == "evidence_corrected"]
    missing = [row for row in comparison if row.get("Overall") == "missing_in_curated"]
    unresolved = [
        row for row in comparison if row.get("Overall") == "unresolved_disagreement"
    ]
    valuemetrics = [
        row
        for row in matched
        if clean(row.get("Human_nM")) or clean(row.get("Human_Value"))
    ]
    metrics = {
        "curated_file": str(args.curated),
        "human_workbook": str(args.human_workbook),
        "human_sheet": title,
        "target_pdbs": sorted(targets),
        "human_evaluation_rows": len(humans),
        "curated_evaluation_rows": len(curated),
        "matched_rows": len(matched),
        "exact_match_rows": sum(
            row.get("Overall") == "match" for row in comparison
        ),
        "evidence_corrected_rows": len(corrected),
        "missing_in_curated": len(missing),
        "unresolved_disagreements": len(unresolved),
        "value_comparisons": len(valuemetrics),
        "value_agreement": sum(
            row.get("Value_Agreement") == "yes" for row in valuemetrics
        ),
        "endpoint_agreement": sum(
            row.get("Endpoint_Agreement") == "yes" for row in matched
        ),
        "mutation_agreement": sum(
            row.get("Mutation_Agreement") == "yes" for row in matched
        ),
        "compound_agreement": sum(
            row.get("Compound_Agreement") == "yes" for row in matched
        ),
        "decision_agreement": sum(
            row.get("Decision_Agreement") == "yes" for row in matched
        ),
        "critical_false_accepts": sum(
            row.get("Overall") == "extra_in_curated"
            and clean(row.get("Curated_Status")) == "accepted"
            for row in comparison
        ),
        "coverage_rate": (
            len(matched) / max(1, len(humans))
        ),
        "status": (
            "PASS"
            if len(missing) == 0 and len(unresolved) == 0
            else "FAIL"
        ),
        "interpretation": (
            "Evidence-backed corrections are reported separately from exact "
            "agreement. This avoids treating known-incomplete human draft rows "
            "as infallible gold."
        ),
    }
    (args.output_dir / "human_comparison_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    gold_manifest = {
        "human_sheet": title,
        "audit_sheet_rows": len(audits),
        "audit_sheet_priority_pdbs": sorted(audit_pdbs),
        "rows": humans,
    }
    (args.output_dir / "human_gold_manifest.json").write_text(
        json.dumps(gold_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    raise SystemExit(0 if metrics["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
