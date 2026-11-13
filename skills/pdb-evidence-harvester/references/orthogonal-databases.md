# Orthogonal Interaction And Complex Sources

These sources answer contextual questions. They do not create affinity values.

## Routing

| Question | Source | Output |
|---|---|---|
| Is there an experimentally supported molecular interaction? | IntAct or BioGRID | interaction candidate with evidence code |
| Is the protein a member of a curated mammalian complex? | CORUM | complex-context candidate |
| What is the broader network neighbourhood or enrichment? | STRING | aggregated association context |

## IntAct

- Query by exact UniProt or another stable identifier.
- Preserve the interaction accession, participant accessions, MI evidence code
  and species.
- Do not infer affinity, direction, stoichiometry or a viral construct from an
  edge alone.

## CORUM

- Search with exact UniProt, gene symbol or complex name.
- Record the complex ID, source release and member identifiers.
- CORUM is mammalian-complex oriented. A host complex is not a viral target
  construct.

## BioGRID

- Prefer the official API with `BIOGRID_ACCESS_KEY` or an official release file.
- Keep `Physical` and `Genetic` interaction evidence separate.
- Record the BioGRID interaction ID, experimental system, organism and source
  publication.
- Never use an interaction score as a binding constant.

## STRING

- Resolve identifiers first with `get_string_ids` and record the taxid.
- `interaction_partners` returns channel scores, not Kd, Ki, IC50 or EC50.
- A predicted or transferred association remains contextual evidence.

## Cross-source rule

An affinity record may cite an interaction source only as supporting biological
context. The affinity value itself still requires the primary paper, supplement
or patent and an exact compound/construct/assay locator.
