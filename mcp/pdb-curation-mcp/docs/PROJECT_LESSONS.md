# Project Lessons

This document captures operational lessons from literature-backed PDB affinity
curation. They are decision rules, not claims that every dataset contains every
failure mode.

## Core separation of objects

Keep these objects separate throughout the pipeline:

1. PDB structural state: entry, polymer construct, mutation, HET, sequence.
2. Chemical identity: HET, compound code, structure, InChIKey, CAS, name.
3. Measurement: Kd, Ki, IC50, EC50, Tm, single-point inhibition, phenotype.
4. Provenance: primary paper, supplement, cited activity paper, patent,
   database export, model output.

A HET exists because a chemical component was modeled. It does not prove that
the compound is an inhibitor and it does not create an affinity value.

## Accepted evidence

- A database row is a discovery lead. It becomes accepted evidence only after
  the primary paper, supplement or patent is opened and a locator is recorded.
- Full-text-first means the main paper, supplement, corrections, data
  availability and cited activity source are checked as a chain.
- A structure paper may legitimately cite an earlier activity paper. The value
  is then `provenance=cited`, not a measurement newly performed by the structure
  paper.
- The PDB-first paper and the affinity-source paper can describe different
  compound numbering. Resolve exact chemistry before transferring a value.
- Record a negative finding within its search scope. "No hit in this export"
  does not mean "no activity exists".

## Identity precedence

Use the strongest available exact identity anchor:

1. Exact InChIKey or equivalent structure.
2. Exact HET plus primary citation and paper compound code.
3. Exact DOI plus paper compound code.
4. Exact CAS registry number plus target and construct.
5. Normalized chemical name only when no stronger anchor exists.

Normalized names alone are insufficient for final acceptance when isomers,
salts, mixtures, fragments or compound-number collisions are possible.

## Mutation, construct and sequence

- Use sequence to verify an identity that the paper or PDB metadata has already
  named.
- Never infer an undocumented mutation from a sequence alignment.
- Keep wild type and engineered mutants on separate evidence rows.
- A matching sequence can support a context decision, but it does not create a
  potency value.
- If paper, PDB title, HET, accession and sequence disagree, preserve a conflict
  record and do not choose a winner silently.

## Measurement classes

Do not collapse or transfer these endpoints:

- direct binding: Kd or Ki by a stated biophysical method;
- target inhibition: target-assay IC50;
- cell phenotype: antiviral IC50, EC50 or CPE;
- cytotoxicity: CC50;
- stabilization: Tm or Delta-Tm;
- model estimate: extrapolated pIC50, fitted relation or docking score;
- single concentration, percent inhibition or qualitative gel;
- metal-ion or RNA binding;
- peptide-protein or protein-protein binding.

Cell activity is not automatically target affinity. A single concentration is
not an IC50. Tm is not a binding constant. A model estimate is not a direct
measurement.

## Human-review failure modes

- potency assigned to an apo structure;
- activity assigned to the wrong mutation or construct;
- a cited analogue value mixed with a series compound;
- a cellular phenotype reported as target affinity;
- single-concentration inhibition reported as IC50;
- Tm, Delta-Tm or model output reported as a direct binding constant;
- a crystallization additive, detergent or buffer ion treated as an inhibitor;
- a stale title code or table-header compound number overriding exact chemistry;
- analogue potency inherited by an exact but different deposited fragment.

Common crystallization additives, ions, buffers and cryoprotectants include
sulfate, phosphate, acetate, glycerol, PEG, EDO, DMS, GOL and `9HD`. Confirm the
role in paper context; component identity alone is not enough.

## PDF and supplement handling

- Treat a PDF as a page-aware source, not one undifferentiated text blob.
- Preserve page numbers, table names, figure panels and supplement labels.
- Use OCR and multi-pass consensus for image-only pages, but OCR is a retrieval
  aid. Visually verify critical decimals, signs, units, qualifiers and row/column
  alignment.
- When `pypdf` fails on malformed fonts or encodings, fall back to `pdftotext`
  and record the extraction route.
- Do not let one unsupported `.doc` file abort a batch. Isolate it, attempt a
  documented conversion, or record it as a blocker.
- Closed-shadow-root PDF viewers may block automated download. Use an authorized
  structured export, browser/computer-use session, or user handoff; do not claim
  a downloaded PDF exists when it does not.
- An accepted image-derived number needs a rendered page and a visual receipt.

## Folder and source resolution

- Folder names may omit DOI prefixes, punctuation or article suffixes.
- Resolve aliases and record the mapping. Do not silently merge ambiguous
  folders.
- Prefer local Zotero and Downloads files before re-downloading.
- Hash every source used for acceptance and preserve the original file.
- Keep one main paper per DOI explicit when a folder contains main text, SI and
  unrelated files.

## Missing-data contract

Missing is a disposition, not zero and not a blank. Every missing ligand,
affinity value, target or sequence must record:

- controlled reason code;
- exact field and record key;
- measurement or construct context when the same PDB and ligand have multiple
  blank endpoints;
- search scope and sources checked;
- source locator or evidence receipt;
- blocker ID when access failed;
- status, timestamp and reviewer;
- whether the item is unmeasured, inaccessible, non-inhibitor,
  identity-ambiguous or outside scope.

`audit_missing_reasons` checks human-readable annotations in a workbook. The
stronger `record_missing_disposition` and `audit_missing_traceability` tools
create and audit the structured evidence ledger.

`audit_missing_coverage` then joins the ledger back to the actual table. A blank
is covered only when PDB, ligand, context and field match. Database-only leads
also require a `Query_ID` that exists in the database ledger and belongs to the
same PDB.

## Database and licensed-source handling

- Use exact structure or exact substance first in SciFinder and Reaxys.
- Keep SciFinder, Reaxys, CAS BioFinder, ChEMBL and BindingDB records distinct
  until exact identity and context agree.
- Save structured exports, dates, queries, filters and hashes.
- Never store usernames, passwords, session cookies or tokens in a manifest.
- API absence is a blocker. It is not permission to infer the result.
- Browser/computer-use is allowed only in an authorized session and must leave a
  manifest of actions and evidence.

## Spreadsheet editing

- Preserve unrelated sheets, formulas, formatting and existing examples.
- Verify a minimal cell edit did not alter untouched columns or sheets.
- Some bulk spreadsheet writers normalize blanks or formulas. Prefer minimal
  cell edits and verify untouched columns, sheets and protected ranges.
- A literal cell-edit agreement rate is not a semantic accuracy rate.

## Reporting

Always publish numerators and denominators. Separate:

- historical AI baseline from current Skill verification;
- exact agreement from evidence-backed correction;
- missing recall from precision among accepted values;
- critical false accepts from ordinary disagreements;
- strict accepted data from candidates that still need visual review.

Do not describe a candidate-seeded verification exercise as a fully blind
discovery benchmark.

## Operational failure modes

- Do not expand thousands of PDB IDs into repeated command-line arguments.
  Windows can fail with `WinError 206` before the acceptance script starts.
  Store the normalized IDs in a file and pass the file path.
- Do not print or return the full strict-validation payload from acceptance.
  Preserve it on disk and return counts, statuses and a bounded warning excerpt.
- Strict schema validation is not acceptance. The joint gate must explicitly
  require at least one `Evidence_Status=accepted` row.
- A visual-receipt check can be vacuously satisfied when there are zero accepted
  rows. Keep it, but combine it with the explicit accepted-row gate.
- Long-running evidence and extraction calls need external progress, timeout and
  cancellation. A client timeout is not permission to rerun the same batch.
- Verify that the installed Skill script hashes match the source repository
  before claiming that a source fix is active in the MCP runtime.

