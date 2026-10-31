# Decision rules

## PDB versus measurement

Ask three questions separately:

1. What chemical or biological entity is modeled?
2. What measurement was made, on which construct and under which conditions?
3. What evidence authorizes joining the two?

If any answer is missing, use `unresolved_identity` or `no_pdb_specific_affinity`.

## Citation chains

A later structural paper may cite an older affinity. Keep the structure paper
as `DOI` and the measurement source as `Evidence_DOI`. Do not average values
from different assay definitions or source studies.

## Common false affinities

- Working concentrations used to prepare crystals.
- Metal-ion Kd values from ITC.
- RNA-binding Kd values from a different construct.
- Tm or Delta-Tm.
- Single-point percent inhibition.
- Cellular EC50 or CPE IC50.
- DSF extrapolation from a pIC50 regression.
- A ligand code appearing in a PDB without a matching activity record.

## Mutant handling

Store WT and mutant measurements separately. If only a WT value exists, place
it in `Note` or a WT context row. Never populate an I38T PDB with a WT value as
if it were the mutant affinity.

Sequence similarity and mutation counts are identity-verification aids only.
Do not infer that a construct contains an engineered mutation from sequence
alignment, a database annotation or a natural variant.

## Chemical identity

Prefer the deposited HET component and explicit activity-series table. A PDB
title may contain a stale precursor name. Flag title/HET/SI conflicts instead
of silently choosing one.

If the paper contains a compound-label typo but the HET and figure unambiguously
identify the chemical entity, retain the typo in `Conflict_Status` and use the
chemical identity for the record. Never silently rewrite the paper.

When a summary table header conflicts with the exact deposited chemistry plus
the synthesis/characterization and a targeted data table, exact chemistry wins
for the identity join; the conflicting header remains in the correction/conflict
log. A matching numeric value does not resolve two different compound labels.

## Censored values

Retain `<`, `>`, `approx` and ranges in `Value_Original`. Leave `Value_nM`
blank unless the source provides an exact numeric value. Use a `censored_*`
status.

## No-value records

A no-affinity row needs:

- the structure state;
- the modeled ligands or ions;
- why no PDB-specific affinity is available;
- the exact source location.

This prevents missing evidence from becoming a zero or an empty success cell.

## Entity role gate

Check `Entity_Role` before affinity:

- metal ion and buffer components cannot be inhibitor rows;
- RNA/DNA binding is `rna_binding_affinity`, not inhibitor affinity;
- crystallization additives are not inhibitor ligands;
- substrate analogues require their own endpoint type;
- unknown role requires manual review.

## Full-text gate

A database row, search snippet or abstract cannot set
`Evidence_Status=accepted`. Accepted direct values require a primary full-text
locator and exact compound/target context. Missing main text is a blocker.

## PDB coverage versus measurement

A PDB may have:

- a structure but no affinity;
- a structure and a cited activity from a different construct;
- a structure and a current activity on a matched construct;
- an ambiguous accession/title mapping.

Store the relationship, not just one numerical column.
