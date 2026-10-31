---
name: pdb-affinity-analysis
description: Read collected primary papers, supplements, patents and database leads and adjudicate PDB-linked affinity evidence. Use after evidence acquisition to resolve compound identity, mutation/accession conflicts, endpoint type, provenance and whether a value applies to the deposited state.
---

# PDB Affinity Analysis

This is the semantic review layer. It must read primary evidence; it must not
turn a database row, sequence alignment or search snippet into an affinity.

## Mandatory reading pass

For each PDB:

1. Read the abstract, results, discussion, methods and every relevant figure
   legend and table.
2. Read the supplement, especially crystallographic, assay and compound tables.
3. Read author corrections/errata and Data Availability.
4. Follow cited references that contain the activity value or original series.
5. Search for the same compound in patents and proprietary databases.
6. Verify HET/compound identity with at least two independent anchors where
   possible: PDB component, paper table, exact chemical structure/InChIKey or a
   patent example.
7. Record the exact page, table, figure or patent paragraph for every value.

If the main paper is missing, stop on that PDB. A supplement alone is not a
complete source unless it independently defines the compound, target, mutation
and assay.

Create page-aware searchable text for every authorized document:

```bash
python scripts/extract_document_text.py \
  main.pdf supplement.pdf patent.pdf \
  --ocr auto \
  --ocr-consensus auto \
  --render-pages \
  --terms "IC50,EC50,Kd,compound 2,mutation,Table S" \
  --output-dir extracted_text
```

Use the extracted text to build locators. Still render and inspect the relevant
pages for tables, figures, compound numbers, footnotes and structure images.
For image-only or scanned files, read
[references/scanned-pdf-ocr.md](references/scanned-pdf-ocr.md) and use
`--ocr all --render-pages`. OCR is a retrieval aid, not authority for an
ambiguous decimal, table cell or label.

For image-only pages and assay/table pages, run the stronger path:

```bash
python scripts/extract_document_text.py \
  main.pdf supplement.tif supplementary.zip \
  --ocr all \
  --ocr-consensus all \
  --render-pages \
  --dpi 300 \
  --terms "IC50,EC50,Kd,Ki,compound,Table,Model" \
  --output-dir extracted_text
```

This produces `visual_review_queue.csv`, `numeric_review.csv`,
`ocr_consensus`, and rendered page images. Build review sheets with:

```bash
python scripts/build_visual_review.py extracted_text \
  --output-dir visual_review \
  --columns 3 \
  --max-pages 9
```

Open the contact sheet or individual rendered page and record a receipt. For a
critical number, confirm the row, column, compound, unit, qualifier and exact
decimal. If the decimal remains ambiguous, leave it unresolved.

## Entity roles

Assign one role before assigning affinity:

- `inhibitor_candidate`
- `substrate_or_nucleotide_analogue`
- `cofactor_or_metal`
- `rna_or_dna`
- `crystallization_additive`
- `buffer_or_cryoprotectant`
- `unknown`

Only an inhibitor candidate or explicitly measured biological ligand can enter
an inhibitor-affinity field. A HET code in a PDB is not sufficient.

## Mutation and accession rules

- Use sequences to verify identity after the paper/PDB names the construct.
- Never infer an engineered mutation from an alignment alone.
- Treat natural variants, construct boundaries and engineered mutants as
  different evidence classes.
- Keep WT and mutant records separate.
- If the paper, PDB title, HET and accession list disagree, emit
  `accession_or_identity_conflict`; do not choose a winner silently.
- A title typo can be corrected only if independent Table/HET/figure evidence
  identifies the actual chemical entity. Preserve the typo in the conflict log.

## Activity adjudication

Read the source and select the endpoint matrix:

- direct binding: Kd/Ki by a stated biophysical method;
- functional inhibition: IC50 from a target activity assay;
- cell phenotype: antiviral IC50/EC50/CPE;
- cytotoxicity: CC50/MTT/CellTiter-Glo;
- biophysical stabilization: Tm or Delta-Tm;
- model estimate: DSF extrapolation, fitted pIC50 relation;
- single concentration or qualitative gel;
- metal-ion or RNA-binding Kd.

Never convert one endpoint into another without an explicit source model.

## Evidence grade

Assign `Evidence_Tier`:

- `A_primary_fulltext_si`: main text + SI + exact identity and locator.
- `B_primary_fulltext`: main text + exact identity and locator; SI not needed or
  unavailable but limitation recorded.
- `C_patent_plus_paper`: patent example/table + paper context are both read.
- `D_secondary_only`: database/abstract/search result only, not acceptable for
  final affinity acceptance.
- `U_unresolved`: blocker.

Assign `Evidence_Status=accepted` only for A/B/C with no unresolved identity or
scope conflict. D remains a lead.

## Human-review failure modes

Read [references/human-review-patterns.md](references/human-review-patterns.md).
It records the actual error classes found in the user's reviewed workbook,
including RNA Kd leakage, metal Kd leakage, I38T/WT mismatch, DSF extrapolation,
single-point concentrations, crystallization additives and accession typos.

Read [references/decision-rules.md](references/decision-rules.md) for the
row-level adjudication rules.
Read [references/full-text-protocol.md](references/full-text-protocol.md) for
the page-order, locator and visual-check protocol.

## Handoff

Update the evidence pack's `review_checklist.csv` manually after each source is
actually inspected. Set a flag to `yes` only when the document was opened and
the relevant section located. Mark `patent_search_checked`, `reaxys_checked`
and `scifinder_checked` only after the query or export was performed. If a
licensed source is unavailable, add the blocker to the audit log and do not set
the row to accepted.

Also update `visual_review_queue.csv` after viewing the rendered page. Record
the contact sheet/page path and a short receipt in
`visual_review_receipts.csv`.

Return records ready for `$pdb-affinity-output`:

- one measurement or explicit no-affinity row per exact context;
- identity evidence and conflict status;
- entity role and measurement kind;
- evidence tier, read level and source locator;
- primary DOI plus cited Evidence_DOI;
- database and patent crosschecks;
- reviewer note explaining the decision.
