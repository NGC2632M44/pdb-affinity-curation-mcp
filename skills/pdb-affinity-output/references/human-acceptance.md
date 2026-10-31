# Human-review acceptance

The human workbook has three different roles and they must not be mixed:

1. A full draft sheet made by an AI pass and later edited.
2. An explicitly named verified exception sheet.
3. Notes and adjacent annotations that identify known errors or incomplete
   review.

Use the verified exception sheet first for its PDBs. For other rows, treat the
main review sheet as a draft unless the workflow has marked it verified.

## Agreement classes

- `match`: value, endpoint, mutation and decision agree.
- `evidence_corrected`: the human draft is incomplete or wrong and a correction
  row cites a primary locator and evidence DOI.
- `evidence_added`: the independent run adds a legitimate context/no-affinity
  row absent from the human draft, with a locator.
- `missing_in_curated`: the curated file lacks a human-reviewed row.
- `unresolved_disagreement`: no exact agreement and no evidence-backed
  correction.

## Release rule

Do not report one “AI accuracy” number. Report:

- PDB coverage;
- matched human rows;
- normalized-value agreement;
- endpoint agreement;
- mutation/construct agreement;
- available compound-label agreement;
- acceptance-decision agreement;
- evidence-backed corrections;
- unexplained disagreements;
- critical false accepts.

The default release gate is:

- every requested PDB has an explicit disposition;
- every comparable human row is matched or evidence-corrected;
- `missing_in_curated = 0`;
- `unresolved_disagreements = 0`;
- `critical_false_accepts = 0`;
- no accepted crystallization additive, metal, RNA or cell-phenotype value is
  mislabeled as inhibitor affinity.

Exact agreement is computed only after evidence-backed corrections are
separated. Otherwise a known-wrong draft row would reward the model for copying
the error.
