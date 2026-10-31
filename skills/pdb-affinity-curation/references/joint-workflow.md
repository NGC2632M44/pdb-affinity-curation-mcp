# Joint workflow contract

This is the only supported route to a final accepted dataset.

## 1. Harvest

Run `$pdb-evidence-harvester`:

```bash
python scripts/fetch_pdb_evidence.py \
  --pdb-list pdbs.txt \
  --output-dir evidence_pack \
  --download-oa \
  --download-supplements \
  --patent-search
```

Resolve every blocker through the access-fallback ladder. Use
`browser_fetch.mjs` for public or user-authorized browser-only pages, and
Computer Use only when available for an authorized UI workflow.

Before declaring a cited activity paper missing, run the local-source resolver
against Zotero and the user's download/source directories. It resolves PMID to
DOI, matches DOI/PDB/title tokens, prefers main text, copies the source into the
evidence pack and records a hash.

```bash
python ../pdb-evidence-harvester/scripts/resolve_local_sources.py \
  --source-dir "$HOME/Zotero/storage" \
  --source-dir "$HOME/Downloads" \
  --references evidence_pack/references.csv \
  --pdb-citations evidence_pack/pdb_citations.csv \
  --resolve-pmids \
  --output-dir evidence_pack
```

## 2. Extract

Run `$pdb-affinity-analysis`:

```bash
python scripts/extract_document_text.py \
  evidence_pack/fulltext/*.pdf evidence_pack/fulltext/*.tif \
  --ocr all \
  --ocr-consensus all \
  --render-pages \
  --dpi 300 \
  --terms "IC50,EC50,Kd,Ki,Table,Figure,compound,mutation" \
  --output-dir extracted_text
```

Inspect every page where `needs_visual_review=yes`; low OCR quality blocks
acceptance. Build the contact sheet with
`$pdb-affinity-analysis/scripts/build_visual_review.py` and record
`visual_review_receipts.csv`.

## 3. Resolve identity

Run `$pdb-evidence-harvester/scripts/identity_resolver.py` with the extracted
text. It may resolve a stale title code only when an exact chemical-component
peer has a primary citation, its paper code occurs in the text, and the
component name/InChIKey is independently present. Preserve the original alias.

## 4. Adjudicate

Apply the analysis skill's entity-role, endpoint, mutation/accession,
provenance and evidence-tier rules. Update `review_checklist.csv` after actual
inspection. Database and patent hits remain leads until their primary record is
read.

## 5. Close blockers

Run `$pdb-evidence-harvester/scripts/resolve_blockers.py`. Every
acceptance-required blocker must be `resolved`, `resolved_with_context` or
`not_applicable`. A licensed source that is unavailable is recorded as
`requires_authorized_session`; it cannot be silently converted into a success.

## 6. Validate and export

Run `$pdb-affinity-output`:

```bash
python scripts/validate_affinity_dataset.py \
  curated_records.csv \
  --require-fulltext-acceptance \
  --review-checklist evidence_pack/review_checklist.csv \
  --blocker-ledger blocker_resolution.csv \
  --require-blocker-closure \
  --source-root . \
  --output validation.json
```

Then run the source crosscheck, version diff and human comparison. Export only
rows that pass the strict gate; blocked rows remain visible in the
conflict/blocker files.

## 7. Final joint acceptance

Run `$pdb-affinity-curation/scripts/run_acceptance.py` with the curated file,
checklist, blocker ledger, human workbook, correction log and visual receipts.
The generated `final_acceptance.json` must be `PASS`.

## Final proof

An independent acceptance run must:

- start from PDB IDs and available original sources;
- not use prior corrected values as truth;
- resolve or explicitly block every source;
- pass strict validation for accepted rows;
- compare status, value, provenance and conflict decisions against the human
  review sheet;
- report any disagreement rather than silently overwriting it.
