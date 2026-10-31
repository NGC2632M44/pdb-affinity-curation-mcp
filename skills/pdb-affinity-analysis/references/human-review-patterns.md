# Common Human-Review Failure Modes

These patterns are generic checks derived from PDB affinity review practice.
They are not a ground-truth dataset and do not replace reading the source.

## Multi-endpoint rows

Separate target binding, biochemical inhibition, antiviral phenotype,
cytotoxicity and biophysical stabilization into different rows. A broad
"IC50" column is not enough; the endpoint domain must be explicit.

## RNA and metal binding

RNA-binding or metal-ion-binding constants are not automatically inhibitor
potency. Record the measured entity and state whether it applies to the
deposited complex.

## Single concentration

A working concentration, complete inhibition at one dose or a qualitative gel
is not a fitted IC50 or Kd. Keep it as non-numeric context.

## Crystallization additives

Trehalose, sulfate, phosphate, PEG, glycerol, buffer ions, detergents and
cryoprotectants may be modeled in a structure without being inhibitors.
Confirm the entity role in the paper before assigning any activity.

## Cited versus measured

Keep values measured by the structure paper separate from values cited from an
earlier activity paper. Do not relabel a cited value as a new measurement.

## Construct and mutation mismatch

Wild-type and engineered-mutant values are separate evidence classes. A
matching sequence can support a context decision but does not create a value.
Never assign a mutation from sequence alignment alone.

## Model estimates

DSF extrapolation, fitted relations, docking scores and other model-derived
estimates are not direct affinity measurements. Preserve the model source and
calibration.

## Original-source blockers

If the main paper is unavailable, a citation, supplement or database row alone
does not establish the complete context. Record the blocker instead of guessing.

## Title and accession conflicts

Resolve stale title codes, HET aliases, accession mismatches and genotype
conflicts through exact chemistry, construct identity, paper/SI locators and
sequence verification after the source names the construct. Preserve the
conflict.

## Table-header traps

A supplement table header can disagree with exact structure. Prefer exact
InChIKey/HET chemistry plus synthesis and assay context; retain the conflicting
header in the conflict log.

## Wrong analogue

Sharing a chemical series is not identity. Do not transfer an analogue's
potency to a different deposited fragment or compound.

## Uncertainty type

Do not relabel confidence intervals as standard deviations or vice versa.
Preserve the authors' uncertainty definition and source locator.

