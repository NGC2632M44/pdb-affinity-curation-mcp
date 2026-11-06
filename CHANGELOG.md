# Changelog

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
