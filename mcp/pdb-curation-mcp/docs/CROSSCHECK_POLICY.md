# Crosscheck Policy

## Governing rule

Final data should be missing rather than wrong. Every accepted value must be
traceable to exact identity, context and a locator. Every missing value must be
traceable to a search scope and a reason.

## Source hierarchy

Use sources in this order:

1. RCSB/PDB metadata for entry, citation, polymer, HET and sequence facts.
2. Primary paper full text for design, constructs, methods and values.
3. Peer-reviewed supplement for tables, assay details and compound mapping.
4. Cited primary activity paper for a value not measured in the structure paper.
5. Patent full text for examples, tables and compound/property ties.
6. Reaxys, SciFinder and CAS BioFinder for discovery and cross-reference.
7. ChEMBL, BindingDB and PubChem for public cross-check and chemical identity.

Lower tiers may identify a lead or conflict. They do not override a directly
read primary source.

## Exact-identity gate

Before comparing values across sources, require at least one strong anchor:

- exact InChIKey or equivalent structure;
- exact HET plus primary citation and compound code;
- exact DOI plus compound code;
- exact CAS plus target and construct;
- normalized name only with a documented reason stronger anchors are absent.

Record ambiguous salts, stereochemistry, mixtures, fragments and compound-code
collisions as conflicts. Do not average structures.

## Context gate

An affinity value applies to a context, not just a PDB ID. Compare:

- polymer and species;
- construct boundaries;
- mutation and engineered state;
- ligand entity and role;
- assay target;
- measurement endpoint;
- unit, qualifier and censoring;
- assay conditions that materially affect interpretation.

Wild type and mutant values are not interchangeable. A cellular phenotype is
not a target-affinity value. Metal or RNA binding is not inhibitor potency.

## Database-only leads

- A database-only record remains `D_secondary_only` and
  `needs_primary_source`.
- A value appearing in two secondary databases is still a lead.
- A database "no hit" means no hit in that query/export scope.
- Record database, exact query, filters, date, result count, result locator,
  export path and SHA-256.
- Keep live access status separate from archived-export verification.

## Conflict classes

Emit a conflict row rather than selecting silently:

- title or compound-code mismatch;
- DOI/PDB/citation mismatch;
- HET, InChIKey, CAS or name identity conflict;
- mutation, construct, species or accession conflict;
- endpoint or measurement-kind conflict;
- unit, value, sign or censoring conflict;
- cited-versus-measured provenance ambiguity;
- database result that cannot be traced to primary evidence.

Do not average conflicting values. Re-read the source chain and preserve the
unresolved row until a stronger locator resolves it.

## Required crosscheck record

For each accepted PDB context, retain:

- PDB ID and exact ligand ID;
- primary DOI and cited evidence DOI when different;
- source path and SHA-256;
- page/table/figure/paragraph locator;
- compound identity anchors;
- construct and mutation evidence;
- endpoint and value with unit and qualifier;
- evidence tier and acceptance status;
- database query/export ledger references;
- conflict IDs and reviewer note.

## Acceptance order

1. Resolve PDB, paper and supplement.
2. Resolve chemical identity and entity role.
3. Resolve construct, mutation and target.
4. Extract endpoint and value with page-aware evidence.
5. Crosscheck patents and licensed/public databases.
6. Validate strict schema and source gates.
7. Audit every missing disposition.
8. Compare to human review with explicit denominators.
9. Freeze the curated output before opening the human workbook when a forward
   test is being measured.
10. Run the joint acceptance gate.

Never report a PASS based only on schema validity if source, blocker, visual or
human-comparison gates were not run.

