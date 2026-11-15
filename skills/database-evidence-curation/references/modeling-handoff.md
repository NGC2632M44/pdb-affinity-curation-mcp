# Modeling Handoff

## Within-assay normalization

Use robust within-assay centering or scaling for ranking:

```text
z_ij = (pIC50_ij - median_j) / (1.4826 * MAD_j)
```

An additive assay offset cancels for within-assay Spearman; it does not cancel across assays.

## Cross-assay decomposition

```text
y_ij = mu + assay_j + ligand_i + interaction_ij + error_ij
```

Do not remove `interaction_ij` unless an interaction model shows it is negligible. Do not use an outer test assay to estimate its normalization parameters.

## Splits

- within-assay: scaffold-disjoint;
- cross-assay: leave-one-assay-out;
- cross-target: leave-one-target-out;
- external: locked prospective assay, opened once.

## Context model minima

At minimum, a context-aware model should see ligand representation; target sequence or descriptor; assay format/readout; construct/mutation when available; and explicit missingness indicators. A one-hot assay gate is not a substitute for assay descriptors because it cannot generalize to a new assay.

## Negative controls

- support-label permutation;
- assay-label permutation;
- scaffold/per-assay leakage audit;
- random split as an upper bound, not a primary result;
- target-held-out and assay-held-out tests separately.

## Reporting

Report all attempted methods and the pre-specified selection rule. Do not retain only runs that cross an outer-test threshold.
