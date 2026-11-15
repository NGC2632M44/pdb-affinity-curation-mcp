# Data Collection Playbook

## Record grain

Choose one before collecting: ligand identity, target/construct, assay, measurement, PDB entity, patent example, publication or source dataset. Never aggregate rows from different grains into one flat table without explicit grain columns.

## Identifier rules

- Preserve raw and canonical identifiers.
- Canonicalize SMILES with RDKit and retain the original SMILES and hash.
- Use InChIKey for exact chemical matching when stereochemistry is stable.
- Preserve UniProt accession, construct boundaries and mutation notation separately.
- Keep DOI, PMID, PMCID and patent publication number separate.
- Record mapping confidence and ambiguity.

## Measurement rules

Store:

```text
endpoint, standard_type, standard_relation, standard_value, standard_units,
cell, mutation, assay_format, substrate, readout, temperature, incubation,
source_locator, extraction_method
```

Do not store a screening concentration as an IC50. A censored value needs a relation and bound.

## Source collection order

1. official API or structured export;
2. open-access full text or supplement;
3. authorized local file;
4. public browser page;
5. licensed database export;
6. blocker/handoff.

Every source transition needs a reason and a receipt.

## Common failure patterns

- `1a/1ab` or `Replicase polyprotein` labels being treated as a protease identity;
- review articles being treated as single assays;
- a title-level DOI being treated as an assay DOI;
- PDB ligand presence being treated as affinity evidence;
- database rows being averaged across endpoints;
- exact compounds duplicated across publications with conflicting labels;
- assay floors being treated as continuous IC50;
- scaffold overlap hidden by compound-level splits;
- context columns with zero coverage being passed to a model;
- model selection using the outer test set.
