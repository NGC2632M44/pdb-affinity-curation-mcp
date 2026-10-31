# DOI map

Many PDB/affinity exports omit paper provenance. Do not infer a DOI from a
compound name, PDB title, or nearby folder.

Before extraction, create:

| Column | Meaning |
|---|---|
| `PDB_ID` | Structure accession |
| `DOI` | Structure/association paper |
| `Evidence_DOI` | DOI of the numeric evidence |
| `Evidence_Source` | Paper title or database record |
| `Mapping_Note` | Exact HET/compound/series evidence |

If a source CSV lacks DOI columns, supply this map as a separate input and join
on stable identifiers. A missing DOI is a schema failure until the map is
provided; it is not evidence that the affinity is absent.

For source tables with shifted value/unit/uncertainty fields, compare the CSV
header with the printed table and recompute the typed value explicitly.

Do not reinterpret a hyphenated PDB range such as `1ABC-1ABD` as an author chain
label. If a chain label is intended, record it as a separate `auth_asym_id`
field and verify it against RCSB.
