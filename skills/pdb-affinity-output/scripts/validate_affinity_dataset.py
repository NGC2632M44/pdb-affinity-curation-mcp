#!/usr/bin/env python3
"""Validate a normalized PDB-affinity CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


STATUSES = {
    "direct_affinity",
    "direct_affinity_context_only",
    "functional_inhibition_ic50",
    "censored_functional_inhibition",
    "model_defined_affinity",
    "model_extrapolated_ic50",
    "cell_phenotype_not_target_affinity",
    "metal_ion_affinity",
    "rna_binding_affinity",
    "no_pdb_specific_affinity",
    "unresolved_identity",
}
ENTITY_ROLES = {
    "inhibitor_candidate",
    "substrate_or_nucleotide_analogue",
    "cofactor_or_metal",
    "rna_or_dna",
    "crystallization_additive",
    "buffer_or_cryoprotectant",
    "unknown",
}
MEASUREMENT_KINDS = {
    "binding",
    "functional",
    "cell",
    "cytotoxicity",
    "Tm",
    "concentration",
    "other",
}
MUTATION_EVIDENCE = {
    "paper_and_sequence",
    "paper_only",
    "sequence_inference_only",
    "not_applicable",
}
EVIDENCE_TIERS = {
    "A_primary_fulltext_si",
    "B_primary_fulltext",
    "C_patent_plus_paper",
    "D_secondary_only",
    "U_unresolved",
}
READ_LEVELS = {"fulltext_si", "fulltext", "patent_fulltext", "abstract", "metadata_only", "none"}
EVIDENCE_STATUSES = {
    "accepted",
    "needs_fulltext",
    "needs_primary_source",
    "conflict",
    "rejected",
    "blocked",
}
SOURCE_TYPES = {
    "paper_main",
    "paper_si",
    "patent",
    "rcsb",
    "reaxys",
    "scifinder",
    "chembl",
    "bindingdb",
    "pubchem",
}
SOURCE_STATUSES = {
    "available",
    "hashed",
    "downloaded",
    "manual_access",
    "blocked",
    "missing",
    "not_found",
}
NUMERIC_STATUSES = {
    "direct_affinity",
    "direct_affinity_context_only",
    "functional_inhibition_ic50",
    "model_defined_affinity",
    "model_extrapolated_ic50",
    "metal_ion_affinity",
}


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--require-fulltext-acceptance", action="store_true")
    parser.add_argument("--review-checklist", type=Path)
    parser.add_argument("--blocker-ledger", type=Path)
    parser.add_argument("--require-blocker-closure", action="store_true")
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    with args.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    checklist = {}
    checklist_duplicates = set()
    if args.review_checklist:
        if not args.review_checklist.exists():
            raise SystemExit(f"review checklist does not exist: {args.review_checklist}")
        with args.review_checklist.open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for item in csv.DictReader(handle):
                pdb_id = clean(item.get("PDB_ID")).upper()
                if pdb_id:
                    if pdb_id in checklist:
                        checklist_duplicates.add(pdb_id)
                    checklist[pdb_id] = item
    errors = []
    warnings = []
    missing_strict_columns = set()
    keys = set()
    blocker_rows = []
    if args.blocker_ledger:
        if not args.blocker_ledger.exists():
            errors.append(
                f"blocker ledger does not exist: {args.blocker_ledger}"
            )
        else:
            with args.blocker_ledger.open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                blocker_rows = list(csv.DictReader(handle))
    source_root = (
        args.source_root.resolve()
        if args.source_root
        else args.csv_path.resolve().parent
    )
    if args.require_fulltext_acceptance:
        required_strict_columns = {
            "Source_Type",
            "Source_Status",
            "Artifact_SHA256",
            "Source_File",
            "Supplement_File",
            "Supplement_SHA256",
            "Locator",
            "Evidence_Relation",
            "Entity_Role",
            "Measurement_Kind",
            "Mutation_Evidence_Class",
            "Construct_Scope",
            "Mapping_Evidence",
            "Evidence_Tier",
            "Read_Level",
            "Evidence_Status",
            "Conflict_Status",
            "Ligand_InChIKey",
            "Artifact_Type",
            "Visual_Review_Required",
            "Visual_Review_Status",
            "OCR_Quality",
        }
        if rows:
            missing_strict_columns = required_strict_columns - set(rows[0])
            if missing_strict_columns:
                errors.append(
                    "strict columns missing: "
                    + ", ".join(sorted(missing_strict_columns))
                )
    for index, row in enumerate(rows, start=2):
        pdb = clean(row.get("PDB_ID")).upper()
        status = clean(row.get("Affinity_Status"))
        applies = clean(row.get("Affinity_Applies_To_PDB")).lower()
        provenance = clean(row.get("Provenance_Class")).lower()
        value = clean(row.get("Value_Original"))
        value_nm = clean(row.get("Value_nM"))
        doi = clean(row.get("DOI"))
        evidence_doi = clean(row.get("Evidence_DOI"))
        source_type = clean(row.get("Source_Type"))
        source_type_tokens = {
            token.strip().lower()
            for token in re.split(r"[,;|+]", source_type)
            if token.strip()
        }
        source_status = clean(row.get("Source_Status")).lower()
        artifact_sha256 = clean(row.get("Artifact_SHA256"))
        locator = clean(row.get("Locator"))
        entity_role = clean(row.get("Entity_Role"))
        measurement_kind = clean(row.get("Measurement_Kind"))
        mutation_evidence = clean(row.get("Mutation_Evidence_Class"))
        mapping_evidence = clean(row.get("Mapping_Evidence"))
        evidence_tier = clean(row.get("Evidence_Tier"))
        read_level = clean(row.get("Read_Level"))
        evidence_status = clean(row.get("Evidence_Status"))
        source_file = clean(row.get("Source_File"))
        supplement_file = clean(row.get("Supplement_File"))
        artifact_type = clean(row.get("Artifact_Type")).lower()
        visual_required = clean(row.get("Visual_Review_Required")).lower()
        visual_status = clean(row.get("Visual_Review_Status")).lower()
        ocr_quality = clean(row.get("OCR_Quality")).lower()
        if not re.fullmatch(r"[0-9A-Z]{4}", pdb):
            errors.append(f"row {index}: invalid PDB_ID {pdb!r}")
        if not re.match(r"^10\.\d{4,9}/", doi):
            errors.append(f"row {index}: invalid or missing DOI {doi!r}")
        if status not in STATUSES:
            errors.append(f"row {index}: unknown Affinity_Status {status!r}")
        if status in NUMERIC_STATUSES and not value_nm:
            errors.append(f"row {index}: {status} requires numeric Value_nM")
        if status == "censored_functional_inhibition" and value_nm:
            errors.append(
                f"row {index}: censored status must not carry exact Value_nM"
            )
        if status == "cell_phenotype_not_target_affinity" and "compound" in applies:
            errors.append(
                f"row {index}: cell phenotype incorrectly marked compound affinity"
            )
        if (
            status == "metal_ion_affinity"
            and "inhibitor" in applies
            and "not_inhibitor" not in applies
        ):
            errors.append(
                f"row {index}: metal-ion affinity marked as inhibitor affinity"
            )
        if "cited" in provenance and not evidence_doi:
            errors.append(f"row {index}: cited row lacks Evidence_DOI")
        if value.startswith(("<", ">", "~", "approx")) and value_nm:
            errors.append(f"row {index}: qualifier/value conflict")
        if not value and not clean(row.get("Why_No_Value")):
            warnings.append(f"row {index}: no value and no Why_No_Value")
        if args.require_fulltext_acceptance and evidence_status == "accepted":
            if not source_type_tokens:
                errors.append(f"row {index}: accepted row lacks Source_Type")
            if not source_status:
                errors.append(f"row {index}: accepted row lacks Source_Status")
            if evidence_tier not in {
                "A_primary_fulltext_si",
                "B_primary_fulltext",
                "C_patent_plus_paper",
            }:
                errors.append(
                    f"row {index}: accepted evidence has invalid tier {evidence_tier!r}"
                )
            if read_level not in {
                "fulltext_si",
                "fulltext",
                "patent_fulltext",
            }:
                errors.append(
                    f"row {index}: accepted evidence has insufficient Read_Level {read_level!r}"
                )
            if evidence_tier == "A_primary_fulltext_si" and read_level != "fulltext_si":
                errors.append(
                    f"row {index}: A-tier evidence must use Read_Level=fulltext_si"
                )
            if (
                evidence_tier == "A_primary_fulltext_si"
                and "paper_si" not in source_type_tokens
            ):
                errors.append(
                    f"row {index}: A-tier evidence must include Source_Type=paper_si"
                )
            conflict_status = clean(row.get("Conflict_Status")).lower()
            if evidence_tier == "B_primary_fulltext" and conflict_status not in {
                "",
                "none",
            }:
                errors.append(
                    f"row {index}: conflict resolution requires A-tier SI evidence"
                )
            if (
                conflict_status not in {"", "none"}
                and not conflict_status.endswith("_resolved")
            ):
                errors.append(
                    f"row {index}: unresolved Conflict_Status {conflict_status!r}"
                )
            if not locator:
                errors.append(f"row {index}: accepted evidence lacks Locator")
            if source_status in {"missing", "blocked"}:
                errors.append(
                    f"row {index}: accepted evidence has blocked source status"
                )
            if visual_status != "yes":
                errors.append(
                    f"row {index}: accepted evidence requires Visual_Review_Status=yes"
                )
            if (
                visual_required == "yes"
                and visual_status != "yes"
            ):
                errors.append(
                    f"row {index}: required visual review was not completed"
                )
            if ocr_quality not in {"high", "medium", "not_applicable"}:
                errors.append(
                    f"row {index}: accepted evidence has insufficient OCR_Quality {ocr_quality!r}"
                )
            if (
                ("scanned" in artifact_type or "image" in artifact_type)
                and ocr_quality not in {"high", "medium"}
            ):
                errors.append(
                    f"row {index}: scanned/image evidence needs high or medium OCR quality"
                )
            if mapping_evidence in {"name_only", "unresolved", ""}:
                errors.append(
                    f"row {index}: accepted identity mapping is insufficient"
                )
            if mutation_evidence == "sequence_inference_only":
                errors.append(
                    f"row {index}: sequence-only mutation inference is forbidden"
                )
            if entity_role in {
                "cofactor_or_metal",
                "rna_or_dna",
                "crystallization_additive",
                "buffer_or_cryoprotectant",
            } and status == "direct_affinity":
                errors.append(
                    f"row {index}: entity role {entity_role} cannot be direct inhibitor affinity"
                )
            if source_type_tokens and not source_type_tokens.issubset(SOURCE_TYPES):
                errors.append(
                    f"row {index}: unknown Source_Type tokens {sorted(source_type_tokens - SOURCE_TYPES)}"
                )
            if source_status and source_status not in SOURCE_STATUSES:
                errors.append(
                    f"row {index}: unknown Source_Status {source_status!r}"
                )
            if source_type_tokens & {
                "reaxys",
                "scifinder",
                "chembl",
                "bindingdb",
                "pubchem",
            }:
                errors.append(
                    f"row {index}: secondary database row cannot be accepted alone"
                )
            if source_type_tokens & {"paper_main", "paper_si", "patent"}:
                if not re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha256):
                    errors.append(
                        f"row {index}: accepted paper/patent source has invalid SHA-256"
                    )
                source_paths = [
                    Path(part.strip())
                    for part in re.split(r"[;|]", source_file)
                    if part.strip()
                ]
                existing = [
                    path if path.is_absolute() else source_root / path
                    for path in source_paths
                ]
                existing = [path for path in existing if path.exists()]
                if not existing:
                    errors.append(
                        f"row {index}: accepted source file does not exist under source root"
                    )
                elif re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha256):
                    actual_hash = hashlib.sha256(existing[0].read_bytes()).hexdigest()
                    if actual_hash.lower() != artifact_sha256.lower():
                        errors.append(
                            f"row {index}: Artifact_SHA256 does not match source file"
                        )
            if evidence_tier == "A_primary_fulltext_si":
                supplement_hash = clean(row.get("Supplement_SHA256"))
                if not supplement_file:
                    errors.append(
                        f"row {index}: A-tier evidence lacks Supplement_File"
                    )
                elif not re.fullmatch(r"[0-9a-fA-F]{64}", supplement_hash):
                    errors.append(
                        f"row {index}: A-tier evidence lacks valid Supplement_SHA256"
                    )
                else:
                    supp_path = Path(supplement_file)
                    if not supp_path.is_absolute():
                        supp_path = source_root / supp_path
                    if not supp_path.exists():
                        errors.append(
                            f"row {index}: Supplement_File does not exist"
                        )
                    else:
                        actual_hash = hashlib.sha256(supp_path.read_bytes()).hexdigest()
                        if actual_hash.lower() != supplement_hash.lower():
                            errors.append(
                                f"row {index}: Supplement_SHA256 does not match supplement"
                            )
        if clean(row.get("Entity_Role")) and entity_role not in ENTITY_ROLES:
            errors.append(f"row {index}: unknown Entity_Role {entity_role!r}")
        if clean(row.get("Measurement_Kind")) and measurement_kind not in MEASUREMENT_KINDS:
            errors.append(
                f"row {index}: unknown Measurement_Kind {measurement_kind!r}"
            )
        if clean(row.get("Evidence_Tier")) and evidence_tier not in EVIDENCE_TIERS:
            errors.append(f"row {index}: unknown Evidence_Tier {evidence_tier!r}")
        if clean(row.get("Read_Level")) and read_level not in READ_LEVELS:
            errors.append(f"row {index}: unknown Read_Level {read_level!r}")
        if clean(row.get("Evidence_Status")) and evidence_status not in EVIDENCE_STATUSES:
            errors.append(
                f"row {index}: unknown Evidence_Status {evidence_status!r}"
            )
        if clean(row.get("Mutation_Evidence_Class")) and mutation_evidence not in MUTATION_EVIDENCE:
            errors.append(
                f"row {index}: unknown Mutation_Evidence_Class {mutation_evidence!r}"
            )
        key = (
            pdb,
            clean(row.get("Paper_Compound_Label")).lower(),
            status,
            clean(row.get("Assay_Type")).lower(),
            value,
            clean(row.get("Unit_Original")).lower(),
        )
        if key in keys:
            warnings.append(f"row {index}: possible duplicate {key}")
        keys.add(key)

    if args.require_fulltext_acceptance:
        if not args.review_checklist:
            errors.append(
                "strict acceptance requires --review-checklist from the evidence pack"
            )
        accepted_pdbs = {
            clean(row.get("PDB_ID")).upper()
            for row in rows
            if clean(row.get("Evidence_Status")) == "accepted"
            and clean(row.get("PDB_ID"))
        }
        for pdb_id in sorted(accepted_pdbs):
            if pdb_id in checklist_duplicates:
                errors.append(f"{pdb_id}: duplicate rows in review checklist")
            check = checklist.get(pdb_id)
            if not check:
                errors.append(f"{pdb_id}: missing review checklist row")
                continue
            required_yes = {
                "main_text_read",
                "methods_read",
                "tables_figures_read",
                "reference_chain_checked",
                "patent_search_checked",
                "visual_review_checked",
            }
            accepted_rows = [
                row
                for row in rows
                if clean(row.get("PDB_ID")).upper() == pdb_id
                and clean(row.get("Evidence_Status")) == "accepted"
            ]
            if any(
                clean(row.get("Evidence_Tier")) == "A_primary_fulltext_si"
                for row in accepted_rows
            ):
                required_yes.add("supplement_read")
            if any(
                clean(row.get("Evidence_Tier")) == "C_patent_plus_paper"
                for row in accepted_rows
            ):
                required_yes.add("patent_fulltext_read")
            if any(
                clean(row.get("Mutation_Evidence_Class")) not in {
                    "",
                    "not_applicable",
                }
                for row in accepted_rows
            ):
                required_yes.add("mutation_supported_by_primary_source")
            for flag in sorted(required_yes):
                if clean(check.get(flag)).lower() != "yes":
                    errors.append(f"{pdb_id}: checklist {flag} must be yes")
            if (
                clean(check.get("reaxys_checked")).lower() != "yes"
                and clean(check.get("scifinder_checked")).lower() != "yes"
            ):
                errors.append(
                    f"{pdb_id}: at least one of Reaxys/SciFinder must be checked"
                )
            if args.require_blocker_closure:
                pdb_blockers = [
                    item
                    for item in blocker_rows
                    if clean(item.get("PDB_ID")).upper() == pdb_id
                    and clean(item.get("Required_For_Acceptance")).lower() == "yes"
                ]
                unresolved = [
                    item
                    for item in pdb_blockers
                    if clean(item.get("Resolution_Status")).lower()
                    not in {
                        "resolved",
                        "resolved_with_context",
                        "resolved_candidate_only",
                        "not_applicable",
                    }
                ]
                if unresolved:
                    errors.append(
                        f"{pdb_id}: required blocker(s) unresolved: "
                        + ", ".join(
                            sorted(
                                {
                                    clean(item.get("Blocker_Type"))
                                    or "unknown"
                                    for item in unresolved
                                }
                            )
                        )
                    )
    result = {
        "csv": str(args.csv_path),
        "rows": len(rows),
        "errors": errors,
        "warnings": warnings,
        "strict_fulltext_acceptance": args.require_fulltext_acceptance,
        "missing_strict_columns": sorted(missing_strict_columns),
        "status": "PASS" if not errors else "FAIL",
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
