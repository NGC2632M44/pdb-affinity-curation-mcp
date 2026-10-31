# Reaxys and SciFinder workflow

Reaxys and SciFinder provide licensed, authenticated access. There is no
universal public API contract. Do not claim a live query or a direct API call
unless the current environment has an authenticated session and the response is
recorded.

## Query anchors

Use multiple anchors where available:

- exact InChIKey
- CAS number
- DOI or patent publication number
- HET chemical name and synonyms
- target plus organism plus mutation

Do not search with a bare article-local compound number.

## Required provenance

For each query, record:

```text
database, query, date, non-secret session label, filters, result count,
export format, export path, export SHA-256
```

## Reaxys export

Useful fields include:

- registry number
- CAS
- InChIKey
- target
- parameter/endpoint
- value, unit and relation
- assay condition
- reference title/patent/publication

Preserve the export's exact column names in the query ledger.

## SciFinder export

Useful fields include:

- CAS
- InChIKey
- target and biological system
- assay/procedure
- function and parameter
- value, unit and relation
- condition
- source paper or patent

Preserve the export's exact column names and result locator.

## Interpretation

Reaxys and SciFinder are powerful discovery and crosscheck sources, but a hit
still requires:

1. exact compound identity;
2. exact target/mutation/construct;
3. primary paper or patent in the evidence pack;
4. source-aware endpoint classification.

Pass exported files to `$pdb-affinity-output` for reconciliation. Do not copy
their values into accepted rows without reading the primary source.
