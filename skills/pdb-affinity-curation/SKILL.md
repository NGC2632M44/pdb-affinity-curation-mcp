---
name: pdb-affinity-curation
description: Orchestrate full-text-first curation of PDB-to-compound affinity, potency, target and sequence data. Use when a task spans evidence acquisition, primary-source analysis, database crosschecks and final validation or export.
---

# PDB Affinity Curation

This is the orchestration and acceptance skill. Do not collapse it into one
database join or one language-model extraction pass.

Use [references/joint-workflow.md](references/joint-workflow.md) for the exact
cross-skill command and acceptance sequence.

## Core invariant

The PDB model and the biological measurement are separate objects.

A PDB file defines a structural state. Kd, Ki, IC50, EC50, Tm, cell phenotype,
metal binding and RNA binding are different measurement classes under different
constructs and conditions. The existence of a HET code does not create affinity.

## Route the work

Use the specialized skill that owns the current phase:

1. `$pdb-evidence-harvester`
   - PDB citation resolution
   - main paper and supplement acquisition
   - reference-chain discovery
   - OA PDFs, patent candidates and licensed-database leads
   - PatMap processing of patent PDFs the user is authorized to use
2. `$pdb-affinity-analysis`
   - full-text reading and evidence adjudication
   - entity role, assay domain, mutation, accession and provenance decisions
   - human-review failure patterns
3. `$pdb-affinity-output`
   - schema and strict full-text acceptance gate
   - Reaxys/SciFinder/ChEMBL/BindingDB reconciliation
   - correction logs, CSV/Excel and reporting metrics

Do not skip from harvester output to output table. The evidence pack contains
leads, not accepted affinity.

## Required joint sequence

The supported sequence is:

1. harvest PDB metadata, full text, SI, references, patents and database leads;
2. create page-aware text/OCR and visual-review queue;
3. resolve missing citation/title-code conflicts with
   `identity_resolver.py` using exact chemical-component peers;
4. adjudicate the primary evidence;
5. close every blocker in `blocker_resolution.csv`;
6. strict-validate the rows;
7. compare field-by-field with the human review workbook;
8. run `scripts/run_acceptance.py` to require all gates together.

An image-only numeric table is not complete until the rendered page was visually
checked. OCR agreement is a retrieval signal, not authority for a decimal.

## Acceptance gates

For every quantitative direct value:

- primary paper or patent full text was read;
- relevant supplement and correction were checked;
- compound identity is exact or explicitly mapped through HET/structure/series;
- target, mutation and construct are explicit;
- the source locator is recorded;
- database-only records remain leads;
- sequence is not used to infer an undocumented mutation;
- a missing main paper blocks final acceptance.
- a title/code mismatch is resolved only through an auditable exact-component,
  construct and citation chain; preserve the original alias;
- image/scanned evidence has a rendered page, OCR quality and a visual receipt;
- every acceptance-required blocker has a resolution receipt.

For a no-affinity structure:

- state the modeled entity roles;
- state why no quantitative affinity exists;
- cite the exact paper/SI location;
- retain it as evidence, not as a zero.

## Human-review lessons

Read the analysis skill's human-review pattern file before major batches. The
reviewed workbook demonstrates:

- RNA-binding Kd mislabeled as inhibitor potency;
- metal Kd mislabeled as ligand affinity;
- WT potency assigned to I38T;
- DSF extrapolation treated as measured IC50;
- single working concentrations treated as IC50;
- crystallization additives treated as inhibitors;
- cited prior values labeled as current measurements;
- wrong analogue or compound-number matches;
- accession/title typos and missing main papers.

## Final contract

Do not call a phase complete without:

- evidence pack and blockers;
- full-text review checklist;
- curated rows and source conflicts;
- strict validator result;
- explicit denominators for any reported accuracy;
- a statement of sources not accessed.
- a human-workbook comparison with coverage, normalized value, endpoint,
  mutation, decision, evidence-corrected and critical-false-accept metrics.

The final command is:

```bash
python scripts/run_acceptance.py \
  --curated curated_records.csv \
  --review-checklist evidence_pack/review_checklist.csv \
  --blocker-ledger blocker_resolution.csv \
  --source-root . \
  --human-workbook human_review.xlsx \
  --human-sheet "a_" \
  --audit-sheet "audit" \
  --correction-log correction_log.csv \
  --visual-receipts visual_review_receipts.csv \
  --output-dir final_acceptance
```

`final_acceptance.json` must be `PASS`.

When sources are inaccessible, return `blocked` or `needs_primary_source`.
Do not fill the gap with sequence, database or common-knowledge guesses.

## Blocker handling

Do not stop at the first missing API. Escalate through:

1. alternate public metadata/OA endpoint;
2. public browser fetch;
3. user-authorized Playwright profile/storage state;
4. Computer Use when available for an already-authorized desktop session;
5. licensed-database export or user handoff.

A block is closed only by a located source, OCR/rendered page, patent example or
an explicit user-access handoff. A licensed system that is unavailable is
recorded as `requires_authorized_session`; it is never represented as a
successful search.
