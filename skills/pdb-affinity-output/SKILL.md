---
name: pdb-affinity-output
description: Validate, reconcile and export adjudicated PDB affinity evidence. Use after full-text review to enforce source grading, resolve Reaxys/SciFinder/ChEMBL/BindingDB conflicts, produce correction logs and build CSV or Excel outputs.
---

# PDB Affinity Output

This skill owns schema, acceptance gates, reconciliation and delivery.

## Required gate

Do not export a record as `accepted` unless:

- `Evidence_Tier` is A, B or C;
- `Read_Level` is `fulltext_si`, `fulltext` or `patent_fulltext`;
- `Evidence_Status` is `accepted`;
- `Source_Location` identifies the table/figure/page/paragraph;
- compound identity is exact or explicitly mapped, never `name_only`;
- mutation evidence is not `sequence_inference_only`;
- entity role and measurement kind are resolved;
- no unresolved accession/title/series conflict exists.
- `Visual_Review_Status=yes` for every accepted record;
- scanned/image evidence has high or medium OCR quality and a rendered page.

Database-only rows remain `D_secondary_only` with `Evidence_Status=needs_primary_source`.

## Validate

Run normal validation for row structure and strict validation for acceptance:

```bash
python scripts/validate_affinity_dataset.py curated.csv \
  --output validation.json \
  --require-fulltext-acceptance \
  --review-checklist evidence_pack/review_checklist.csv \
  --blocker-ledger blocker_resolution.csv \
  --require-blocker-closure \
  --source-root .
```

Strict validation fails on full-text gate violations, not merely missing optional
metadata.

## Compare with the human review

The human workbook is a draft plus exception ledger, not automatic truth. The
comparator overlays a user-specified verified exception sheet, normalizes
values and units, and treats an independently corrected draft row separately
from an unexplained disagreement.

```bash
python scripts/compare_to_human.py \
  --curated curated_records.csv \
  --human-workbook human_review.xlsx \
  --human-sheet "a_" \
  --audit-sheet "audit" \
  --correction-log correction_log.csv \
  --output-dir human_compare
```

Required interpretation:

- `missing_in_curated = 0`;
- `unresolved_disagreements = 0`;
- `critical_false_accepts = 0`;
- value, endpoint, mutation and compound-label agreement reported on their own
  denominators;
- evidence-backed corrections reported separately and never counted as exact
  human agreement.

Read [references/human-acceptance.md](references/human-acceptance.md) before
using human accuracy as a release metric.

## Reconcile databases

```bash
python scripts/crosscheck_sources.py \
  --curated curated.csv \
  --source reaxys=reaxys_export.csv \
  --source scifinder=scifinder_export.csv \
  --source chembl=chembl_assays.csv \
  --source bindingdb=bindingdb.csv \
  --output-dir crosscheck
```

Rules:

- use InChIKey first, CAS second, exact DOI+compound third, name last;
- record missing/ambiguous keys;
- mark `patent_candidate=yes` when a Reaxys/SciFinder reference contains a
  patent publication number;
- a value agreement across secondary databases does not replace primary reading;
- a value disagreement creates a conflict row, not an average.

## Output contract

Produce:

- `curated_records.csv`;
- `correction_log.csv`;
- `source_register.csv`;
- `conflicts.csv`;
- `crosscheck/`;
- `validation.json`;
- optionally an Excel review workbook with separate source, analysis and output
  regions.

Create a field-level correction log between versions:

```bash
python scripts/compare_curated_versions.py \
  --old old.csv \
  --new new.csv \
  --key PDB_ID \
  --key Correction_ID \
  --output correction_log.csv
```

Keep raw source files and database exports unchanged. Every transformed value
must retain a locator and evidence DOI.

## Metrics

Report separate denominators for:

- PDB/DOI mapping;
- entity and compound identity;
- endpoint/status;
- provenance/read level;
- full five-field acceptance;
- rows excluded or blocked.

Never call a literal cell-edit rate a semantic affinity accuracy.

Read [references/schema.md](references/schema.md) before authoring.
