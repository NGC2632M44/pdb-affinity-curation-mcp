---
name: database-evidence-curation
description: Build traceable biomedical database datasets from PDB, ChEMBL, BindingDB, PubChem, Europe PMC, Crossref/DataCite and local sources. Use when designing schemas, harvesting assay or target metadata, auditing coverage, resolving identifiers, or creating reusable source-to-dataset pipelines.
---

# Database Evidence Curation

This skill generalizes the data-collection rules learned from PDB affinity curation and viral-protease assay work. It is a data-engineering workflow, not a claim generator.

## Governing rule

> Store missing before wrong. Every accepted value needs provenance; every blank needs a search scope, a reason, and a blocker or receipt when applicable.

Do not infer a construct, assay condition, endpoint, affinity value, sequence or legal status from a title, search snippet or database convenience field.

## Workflow

1. **Define the record grain.** Decide whether one row is a measurement, compound, assay, construct, target, patent example or PDB entity. Never mix grains silently.
2. **Inventory sources.** Record official endpoints, access status, evidence role, prohibited uses and credentials policy in a machine-readable database inventory.
3. **Normalize identifiers.** Preserve source ID, canonical ID, InChIKey, UniProt, PDB ID, assay ID, DOI, PMID, PMCID, patent number and mapping confidence.
4. **Separate facts from metadata.** A structure paper, cited activity paper, assay record and database export are different evidence objects.
5. **Normalize endpoint and context.** Keep endpoint, relation, unit, construct, mutation, cell, readout, substrate, temperature, assay floor and batch separate.
6. **Audit coverage before modeling.** Compute field coverage by source and by group. Do not train on a context column whose coverage is effectively zero.
7. **Create an acquisition queue.** For every missing priority field, record the next source route, reason and source hint. A queue item is a task, not evidence.
8. **Export a context handoff.** Add explicit `missing_model_fields` and `model_eligible` flags. Never impute missing context or borrow it from another assay.
9. **Freeze the dataset.** Hash the source snapshot, schema, split files, normalization code and evaluation code before model selection.
10. **Evaluate at the correct unit.** Use assay/publication/construct or family as the independent unit; compounds inside an assay are not independent experimental replicates.

## Tool scripts

Use the scripts in `scripts/`; keep network access optional and explicit.

```text
build_metadata_registry.py       group CSV rows and compute field coverage
build_assay_registry.py          build one assay-level record with explicit gaps
fetch_chembl_assay_metadata.py   fetch assay, document and target metadata
resolve_public_metadata.py       resolve DOI, PMID, PMCID and dataset records
enrich_assay_text.py             extract readout, expression, tag and substrate text
audit_metadata_coverage.py       enforce required-field thresholds by group/source
build_source_acquisition_queue.py turn missing assay fields into a prioritized queue
build_context_model_dataset.py   export rows with missingness flags and no imputation
```

Run `python <script> --help` for exact arguments. Prefer a cache directory and a manifest for every run.

## Source hierarchy

- Primary source: paper, supplement, patent example, official assay protocol or PDB entry.
- Structured source: ChEMBL, BindingDB, PubChem BioAssay, RCSB PDB, Crossref, DataCite, Europe PMC.
- Discovery source: search engine, abstract, review, database title or vendor page.

A structured or discovery hit is a lead. It becomes accepted evidence only after exact identity and context matching to the intended record grain.

## Mandatory separation

Keep these fields distinct:

```text
structure_paper
activity_paper
assay_id
assay_description
construct
mutation
cell_or_format
substrate
readout
endpoint
relation
unit
value
censoring
source_locator
database_query
blocker
```

The first paper that solved a structure need not be the paper that measured the ligand's activity. Preserve both citations and do not transfer an analogue's value to a deposited compound.

## Metadata acquisition rules

- ChEMBL assay endpoint supplies description, type, target and document linkage; do not assume empty cell/tissue/parameter fields mean wild-type or cell-free.
- Europe PMC fullTextXML is useful when the publisher page is blocked. Record PMID/PMCID and the exact section.
- Crossref supplies bibliographic metadata, not assay facts.
- DataCite identifies datasets; it does not prove the assay protocol.
- PubChem BioAssay can supply AID protocol and controls when the AID mapping is exact.
- Patents require publication number, example/table/claim, page and rendered receipt.

## Coverage gate

Before a context-aware model is trained, require explicit coverage for the fields used by that model. Example minimum gate:

- 100% group identifier and source locator;
- 100% endpoint and unit for accepted activity rows;
- at least 80% construct or target sequence for target-aware models;
- at least 60% readout/substrate or assay-format context for assay-aware models;
- 0 silent missing values: every blank is in the missing-disposition ledger.

Lower coverage is allowed only as an explicit limitation or after the field is removed from the model.

## Modeling handoff

Create separate files for:

```text
measurements.csv       accepted values and provenance
assay_metadata.csv     one row per assay/context
missing_ledger.csv     one row per missing field
blocker_ledger.csv     access or identity blockers
source_manifest.json   URLs, dates, hashes, licenses
split_manifest.json    scaffold/assay/target holdout definitions
```

Do not merge unrelated assays into a single label scale. If cross-assay modeling is intended, retain assay offsets and ligand-assay interaction features; within-assay rank can ignore an additive offset but not an interaction.

## Stop conditions

Stop and report a blocker when:

- the record grain is ambiguous;
- exact compound, construct or assay identity is unresolved;
- a metric is from a different endpoint or cell format;
- only a review aggregation is available for a claimed single assay;
- a test label or held-out target influenced preprocessing or parameter choice;
- a source is accessible only through an unapproved login, paywall or CAPTCHA bypass.

## References

- `references/data-collection-playbook.md` for field definitions and failure patterns.
- `references/modeling-handoff.md` for normalization, split and context-model rules.
