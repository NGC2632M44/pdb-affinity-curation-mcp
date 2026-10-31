# Full-text evidence protocol

## Reading order

1. Data Availability and accession table.
2. Abstract and results.
3. Every figure legend and table footnote.
4. Methods, especially construct, mutation, protein preparation and assay.
5. Supplement crystallography and activity tables.
6. Author correction/erratum.
7. Cited primary activity paper and preceding patent/patent family.
8. Database record only to locate the primary source.

## Locator format

Use a source locator that another reviewer can reopen:

```text
main.pdf Table 1 p.9440
SI_02.xlsx Table S4 row 7
patent WO2022123158A1 Example 42
Europe PMC PMC1234567 Methods section
```

## Required extraction

Record:

- exact compound label and independent chemical identity;
- PDB/construct, virus/strain and mutation;
- endpoint name, value, unit, relation and uncertainty;
- assay system, method, cell line and model;
- current/cited/predicted/qualitative provenance;
- page/table/figure/patent paragraph;
- every conflicting label/accession/value.

## Visual checks

Render the relevant PDF pages. Text extraction can miss:

- superscripts and footnotes;
- ">", "<", "+/-" and ranges;
- merged table cells;
- shared legends;
- compound-position numbers;
- chemical structures and HET labels.

## Failure behavior

If the main text is missing, the source is inaccessible, or the compound cannot
be tied to the measured entity, return `blocked` or `needs_primary_source`.
Never fill the gap from a database, sequence alignment or common analogue.
