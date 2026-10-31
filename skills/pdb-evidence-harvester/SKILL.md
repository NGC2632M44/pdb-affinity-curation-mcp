---
name: pdb-evidence-harvester
description: Resolve PDB accessions to original papers, OA full text, supplements, references, patents and licensed-database leads. Use before affinity curation when evidence must be acquired from primary sources rather than inferred from databases or sequences.
---

# PDB Evidence Harvester

Build an evidence pack. Do not adjudicate affinity in this skill.

## Output standard

A useful evidence pack must contain:

- RCSB citation DOI/PMID, entry title, polymer entities and non-polymer HETs;
- the original paper or an explicit `subscription_or_blocked` status;
- main text, tables/figures, methods and supplement locations;
- cited primary activity papers and author corrections;
- patent search candidates and exact patent IDs when available;
- Reaxys, SciFinder, ChEMBL or BindingDB leads with query/date/export path;
- a review checklist with every source still unread marked `no`;
- SHA-256 hashes for downloaded files.

Never claim a source was read because a search result or database record exists.

## Workflow

1. Normalize the PDB list and run:

```bash
python scripts/fetch_pdb_evidence.py \
  --pdb-list pdbs.txt \
  --output-dir evidence_pack \
  --download-oa \
  --download-supplements \
  --patent-search \
  --patent-id WO2022123158A1
```

2. Read `blockers.csv` before continuing. Paywalled, missing-main-paper,
   accession-mismatch and failed-supplement states are blockers, not blanks.
3. Use `references.csv` to follow cited primary activity papers, corrections and
   prior patents. A structure paper often cites the original activity paper.
   If a public API or OA URL is blocked, use
   [references/access-fallbacks.md](references/access-fallbacks.md). Try
   `scripts/browser_fetch.mjs` with a user-authorized profile or storage state
   before recording `needs_user_access`.
   Reuse already-downloaded papers and Zotero storage before re-fetching:

```bash
python scripts/resolve_local_sources.py \
  --source-dir "$HOME/Zotero/storage" \
  --source-dir "$HOME/Downloads" \
  --references evidence_pack/references.csv \
  --pdb-citations evidence_pack/pdb_citations.csv \
  --resolve-pmids \
  --output-dir evidence_pack
```

   The resolver follows PMID -> DOI where available, matches normalized DOI,
   PDB code and title tokens, prefers main text over SI, copies matched files
   into the evidence pack and records SHA-256.
4. After page-aware extraction, resolve stale title codes, missing citations and
   same-chemical-component aliases:

```bash
python scripts/identity_resolver.py \
  --pdb-list pdbs.txt \
  --text extracted_text \
  --output-dir identity
```

An exact-component peer is eligible only when the peer has a usable primary
citation, its title code occurs in the paper/SI, and the component name or
InChIKey is independently present. Prefer mutation/construct agreement. Keep
the original title alias in the conflict log.

5. Search patents by HET chemical name, synonym, InChIKey and exact publication
   number from Reaxys/SciFinder. Use `patent_candidates.csv` only as leads.
6. If the user has an authenticated Reaxys or SciFinder session, use the
   browser/computer-use workflow to run and export the query. Record:

```text
database, query, date, fields requested, export hash
```

7. If full patent PDFs are available and `PATMAP_API_KEY` is set, run:

```bash
python scripts/patmap_extract.py patent_1.pdf patent_2.pdf \
  --output-dir patmap_output \
  --functions table,reaction,coreferences
```

PatMap is a licensed extractor, not a patent search engine.

8. Convert raw blockers into a resolution ledger:

```bash
python scripts/resolve_blockers.py \
  --evidence-pack evidence_pack \
  --identity-resolution identity/identity_resolution.csv \
  --source-register source_register.csv \
  --database-ledger database_query_ledger.csv \
  --browser-manifest browser_evidence/browser_manifest.json \
  --output blocker_resolution.csv
```

9. Hand the evidence pack and `review_checklist.csv` to
   `$pdb-affinity-analysis`. Do not hand unread search snippets directly to an
   output table.

## Access boundaries

- Download only OA files or content the user is authorized to access.
- Do not bypass paywalls, CAPTCHAs, license terms or institutional controls.
- Do not put credentials in the skill, command history or manifest.
- API absence is a blocker record, not permission to infer the value.
- A Google Patents snippet is not patent evidence until the relevant example,
  claim or table is opened and located.

Read [references/sources.md](references/sources.md) for the source hierarchy and
[references/patmap.md](references/patmap.md) for the verified PatMap contract.
Read [references/proprietary-databases.md](references/proprietary-databases.md)
when Reaxys or SciFinder is part of the search.
Read [references/access-fallbacks.md](references/access-fallbacks.md) when a
public API or OA URL is blocked, before declaring the source unavailable.

## Deliverables

Use the generated folder as an immutable evidence pack:

```text
evidence_pack/
  evidence_manifest.json
  pdb_citations.csv
  pdb_ligands.csv
  pdb_polymers.csv
  references.csv
  patent_queries.csv
  patent_candidates.csv
  downloads.csv
  blockers.csv
  blocker_resolution.csv
  review_checklist.csv
  local_source_matches.csv
  identity/
  fulltext/
  web_pages/
```

`blockers.csv` records the raw failure. `blocker_resolution.csv` records the
route, evidence and whether the block was actually closed.
