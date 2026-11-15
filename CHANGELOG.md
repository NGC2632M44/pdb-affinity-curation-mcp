# Changelog

## [0.6.0] - 2026-11-16

### Added

- Assay-level registry with row counts, compound counts, field coverage,
  context flags and explicit missing-field paths.
- Source-acquisition queue generation with stable queue IDs, priority scores,
  next-source routes and source hints.
- Context-aware model handoff with `missing_model_fields`, `model_eligible` and
  an assay-level split manifest. Missing values are never imputed.

## [Unreleased]

### Added

- General biomedical database evidence collection scope alongside PDB-linked
  affinity curation.
- `database-evidence-curation` Skill with metadata registry, ChEMBL assay
  harvesting, public DOI/PMID/PMCID resolution, assay-text enrichment and
  coverage-audit scripts.
- MCP tools `build_metadata_registry`, `fetch_chembl_assay_metadata`,
  `resolve_public_metadata`, `enrich_assay_text` and
  `audit_metadata_coverage`.
- MCP tools `build_assay_registry`, `build_source_acquisition_queue` and
  `build_context_model_dataset`.
- Crossref, DataCite, Europe PMC fullTextXML, Semantic Scholar and Unpaywall
  source inventory entries.
- Resources for the database-collection skill, playbook and modeling handoff.

### Added

- Official orthogonal-source routes for IntAct, CORUM, BioGRID and STRING.
- `query_orthogonal_sources` with bounded payload summaries, query/result
  provenance, explicit evidence roles and prohibited-use rules.
- `pdb-curation://orthogonal-sources` resource documentation.

### Safety

- BioGRID without `BIOGRID_ACCESS_KEY` is recorded as a blocker rather than an
  inferred result.
- Interaction, complex and association context can never create affinity,
  potency, construct identity or causal direction.

## [0.3.1] - 2026-11-06

### Fixed

- Pass large inferred PDB sets through a UTF-8 ID file instead of hundreds or
  thousands of repeated `--pdb-id` command-line arguments. This removes the
  Windows `WinError 206` failure observed during joint acceptance.
- Return a bounded acceptance summary instead of embedding the full strict
  validation warning payload in stdout and MCP responses.

### Changed

- Final acceptance now explicitly requires at least one accepted curated row.
  Schema validity and an empty visual-review set can no longer be mistaken for
  scientific acceptance.
- Keep full validation evidence in `validation.json` while exposing file paths,
  counts and the first 20 warnings in `final_acceptance.json`.

### Tests

- Added a 2,863-PDB regression test proving that inferred IDs are written to a
  file rather than expanded inline.
