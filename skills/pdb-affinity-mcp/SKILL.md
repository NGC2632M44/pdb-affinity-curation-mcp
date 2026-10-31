---
name: pdb-affinity-mcp
description: Curate PDB-linked affinity, potency, target and sequence data through the PDB Curation MCP. Use when a task requires full-text-first evidence, exact compound and construct matching, multi-source crosschecks, or auditable explanations for missing data.
---

# PDB Affinity MCP

Use the `pdb-curation` MCP as the execution layer. The governing rule is:

> Final data should be missing rather than wrong, while the process must remain
> traceable to where evidence is missing and why.

Do not infer an affinity value from a PDB HET, sequence alignment, database row,
paper title or search snippet.

## Start

1. Call `environment_check`.
2. Read `pdb-curation://project-lessons`,
   `pdb-curation://source-policy` and `pdb-curation://databases`.
3. If a required installed skill or document tool is missing, record a blocker
   and continue only through an available route.

## Evidence acquisition

1. Call `fetch_pdb_evidence` for the PDB set.
2. Call `resolve_local_sources` before downloading duplicates.
3. Call `resolve_identity` for stale title codes, missing citations or
   same-chemical-component peer evidence.
4. Call `extract_document_text` with page-aware OCR and rendered pages for
   scanned or image-heavy sources.
5. Call `build_visual_review` for tables, figures and ambiguous decimals.

Reuse authorized local files. Do not bypass paywalls, CAPTCHAs or entitlement
controls. When a licensed source lacks API access, use an already-authorized
browser/computer-use session or return an access blocker.

## Adjudication

For each PDB, resolve in this order:

1. structural state and citation;
2. exact ligand identity;
3. entity role;
4. target, construct and mutation;
5. measurement class and endpoint;
6. value, unit and qualifier;
7. primary-source locator;
8. secondary-database and patent crosscheck.

Keep the structure paper, cited activity paper and current measurement separate
in provenance. Do not transfer analogue activity to an exact deposited fragment.
Do not assign wild-type potency to a mutant or a cellular phenotype to a target
affinity field.

## Missing data

For every missing ligand, value, target or sequence:

1. Call `classify_missing_reason`.
2. Call `record_missing_disposition` with the controlled code, search scope,
   sources actually checked and either a locator/receipt or blocker ID.
3. For access failures, also call `record_blocker`.
4. Call `audit_missing_traceability` with the blocker ledger whenever any row
   is blocked and with the database ledger whenever a `Query_ID` is referenced.
5. Call `audit_missing_coverage` against the final table so every blank has a
   matching PDB+ligand+field disposition.

`ok=false` from the audit blocks completion. Do not replace it with a blank,
zero or narrative "not available".

The reason code controls the allowed field and status. Do not override either
one. A blocked disposition must reference a `Blocker_ID` that actually exists
in the blocker ledger. A database-only lead must reference a real `Query_ID`
linked to the same PDB.

Use `audit_missing_reasons` only as a lightweight workbook annotation check. It
does not prove that a source was searched.

## Database crosschecks

Call `prepare_database_queries`, perform only authorized searches or inspect
authorized exports, then call `record_database_query`.

Record exact structure/InChIKey, query, filters, date, result locator, export
path and SHA-256. Keep live access status separate from archived-export
verification. A database value remains a lead until primary evidence is read.

Call `crosscheck_sources` after curated rows exist. Preserve conflicts; do not
average values.

## Output and acceptance

1. Call `validate_affinity_dataset` with strict full-text acceptance.
2. Call `compare_to_human` with explicit human and audit sheets.
3. Call `run_acceptance` with `missing_disposition_ledger` and the final table's
   PDB, ligand, value and context columns.
4. Call `create_run_manifest`.

When the target PDB list is not supplied, the MCP infers PDB IDs from the
curated CSV. Do not use all human rows unless that whole-workbook denominator is
the intended evaluation.

Report numerators and denominators separately for coverage, exact agreement,
value, endpoint, mutation, compound identity, evidence corrections, missing
recall and critical false accepts.

Do not call a run `accepted` when any of these remain:

- missing main paper;
- unresolved identity or construct conflict;
- database-only evidence;
- missing primary-source locator;
- required blocker without a resolution receipt;
- image-derived value without visual receipt;
- missing-disposition audit failure.

## Stop conditions

Stop and return a structured blocker when:

- no authorized source route exists;
- exact chemical identity remains ambiguous;
- the deposited construct cannot be matched to the measurement;
- the decimal/table cell remains ambiguous after visual review;
- a required client capability or MCP tool is unavailable;
- human acceptance cannot be compared with a declared denominator.

Do not fill the gap with a plausible guess.

