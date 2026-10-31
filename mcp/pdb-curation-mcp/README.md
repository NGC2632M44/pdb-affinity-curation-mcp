# PDB Curation MCP

Zero-dependency MCP server and companion Skill for traceable PDB-linked
affinity, potency, target and sequence curation.

## 中文速览

这个 MCP 把已有四个 PDB 文献/亲和力 Skill 串成可审计执行层。核心原则是
“最终数据宁缺勿错”，但每个缺失项都必须有受控原因、检索范围、查过的来源、
定位或阻断项；阻断项还必须能连回真实 blocker ledger。

本仓库以 `pdb-curation` 为 MCP 名称。工具清单或 Skill 描述变更后应重启
Codex 会话使其重新发现。

The operating rule is:

> Final data should be missing rather than wrong, while the process must remain
> traceable to where evidence is missing and why.

This server wraps the installed phase Skills:

- `pdb-evidence-harvester`
- `pdb-affinity-analysis`
- `pdb-affinity-output`
- `pdb-affinity-curation`

### Wrapped Skill map

| Skill | Scripts exposed by MCP | Responsibility |
|---|---|---|
| `pdb-evidence-harvester` | `fetch_pdb_evidence`, `resolve_local_sources`, `resolve_identity`, `resolve_blockers`, `patmap_extract` | evidence pack, local source matching, identity conflicts and access routes |
| `pdb-affinity-analysis` | `extract_document_text`, `build_visual_review` | page-aware text, OCR consensus, numeric queues and visual review |
| `pdb-affinity-output` | `validate_affinity_dataset`, `compare_to_human`, `crosscheck_sources`, `compare_curated_versions` | strict schema, source reconciliation, correction logs and comparison |
| `pdb-affinity-curation` | `run_acceptance` | joint validator, blocker, human and visual acceptance gate |

By default these Skills are resolved from `~/.codex/skills` and
`~/.agents/skills`; set `PDB_SKILL_ROOT` to an explicit installation root.

The MCP is the execution layer. The companion `pdb-affinity-mcp` Skill is the
behavior layer. No parsing or adjudication logic is duplicated in the Skill.

## What it guarantees

- Database-only hits stay leads.
- Missing data has a controlled reason code, search scope and receipt.
- Access blockers remain blockers rather than inferred values.
- Exact identity and measurement context are explicit.
- Image/OCR-derived values can require a rendered-page visual receipt.
- Database queries and exports can be logged with paths and SHA-256.
- Query and blocker ledgers carry stable `Query_ID` and `Blocker_ID` references.
- Curated versions, source files, code and outputs can be hashed in a run
  manifest.
- Human comparison reports separate coverage, value, endpoint, mutation,
  decision, evidence-correction and critical-false-accept metrics.

The server cannot prove that a human reviewer actually opened a paper. It
enforces the minimum structured evidence needed to make that claim auditable and
rejectable.

## Architecture

```text
MCP client
  -> pdb-affinity-mcp Skill
    -> PDB Curation MCP
      -> installed PDB curation Skill scripts
        -> evidence pack / extracted text / curated CSV
```

Protocol transport is stdio. Requests may be newline-delimited JSON or use
basic `Content-Length` framing. Runtime requires Python standard library.
Optional document capabilities depend on the installed Skills and local
`pypdf`, `openpyxl`, `python-docx`, Poppler, Pillow and OCR tools.

## Files

```text
server.py                       MCP server
config/databases.json           source/access inventory
config/paths.example.json       portable path template
schemas/missing_reasons.json    controlled missing taxonomy
docs/PROJECT_LESSONS.md         observed data-collection failure patterns
docs/CROSSCHECK_POLICY.md       source hierarchy and conflict rules
docs/DATABASES.md               SciFinder/Reaxys/public source notes
skill/pdb-affinity-mcp/         companion behavior Skill
tests/                          protocol, tool and traceability tests
mcp.json.example                MCP client config template
```

## Run

This repository is intentionally source-run; it does not publish a standalone
wheel because runtime assets and companion Skills are resolved from the cloned
repository.

```powershell
python -m pip install -r requirements-optional.txt
python server.py
```

Self-check:

```powershell
python server.py --self-test
```

The server finds installed curation Skills in:

```text
PDB_SKILL_ROOT
$CODEX_HOME/skills
~/.codex/skills
~/.agents/skills
```

Example client entry:

```json
{
  "mcpServers": {
    "pdb-curation": {
      "command": "python",
      "args": [
        "/path/to/pdb-affinity-curation-mcp/mcp/pdb-curation-mcp/server.py"
      ],
      "env": {
        "PDB_SKILL_ROOT": "/path/to/pdb-affinity-curation-mcp/skills",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

## Tool groups

### Environment

- `environment_check`: installed Skills/scripts, Python modules, Poppler/OCR
  tools and database capability inventory.

### Evidence acquisition

- `fetch_pdb_evidence`: RCSB citations, OA text, supplements, references,
  patent candidates.
- `resolve_local_sources`: match citations to Zotero, Downloads and project
  files.
- `resolve_identity`: stale title codes, missing citation and exact-component
  peers.
- `patmap_extract`: authorized patent PDFs when `PATMAP_API_KEY` exists.

### Analysis

- `extract_document_text`: page-aware text, OCR consensus, numeric queues and
  rendered pages.
- `build_visual_review`: contact sheets for page and table review.

### Crosscheck and output

- `crosscheck_sources`: exact Reaxys/SciFinder/ChEMBL/BindingDB comparison.
- `compare_to_human`: field-level human review comparison.
- `validate_affinity_dataset`: normal and strict acceptance validation.
- `compare_curated_versions`: field-level correction log.
- `run_acceptance`: joint schema, blocker, human and visual gate.

### Traceability

- `prepare_database_queries`: exact identity query templates.
- `record_database_query`: append a query/export receipt.
- `record_blocker`: append access and source blockers with a stable `Blocker_ID`.
- `classify_missing_reason`: controlled missing taxonomy.
- `audit_missing_reasons`: lightweight Chinese note audit for CSV/XLSX.
- `record_missing_disposition`: structured reason, search scope, sources and
  receipt/blocker.
- `audit_missing_traceability`: audit reasons, receipts, hashes, referenced
  blockers, duplicates and PDB coverage.
- `audit_missing_coverage`: join the actual CSV/XLSX to the disposition ledger
  and require one matching PDB+ligand+field disposition for every blank.
- `create_run_manifest`: SHA-256 inventory of a run.

## Recommended sequence

1. `environment_check`.
2. `fetch_pdb_evidence`.
3. `resolve_local_sources` and `resolve_identity`.
4. `extract_document_text`.
5. `build_visual_review` for table, figure or scanned pages.
6. `prepare_database_queries`, then `record_database_query` after authorized
   query/export.
7. `record_blocker` for unresolved access or evidence failures.
8. Read the primary evidence and adjudicate exact identity, construct, endpoint
   and locator.
9. `record_missing_disposition` for every missing ligand/value/target/sequence.
10. `audit_missing_traceability` with the blocker ledger when blocked rows
    exist and the database ledger when query references exist.
11. `audit_missing_coverage` against the final tabular dataset.
12. `record_database_query` and `crosscheck_sources`.
13. `validate_affinity_dataset`.
14. `compare_to_human`.
15. `run_acceptance` with the missing-disposition ledger and final-table
    coverage columns.
16. `create_run_manifest`.

Do not skip from an evidence pack to an accepted table.

## Missing-data workflow

First inspect the taxonomy:

```text
classify_missing_reason
```

Then append a structured disposition. Example:

```json
{
  "ledger_path": "run/missing_dispositions.csv",
  "pdb_id": "8VGZ",
  "ligand_id": "",
  "field_name": "affinity_value",
  "reason_code": "no_modeled_ligand_apo",
  "status": "accepted_no_affinity_context",
  "search_scope": "RCSB entry; primary paper; SI; cited activity sources; patent search",
  "sources_checked": "RCSB; DOI 10.xxxx/example; SI; Reaxys export 2026-10-30",
  "source_locator": "Paper Results, structure table 1; RCSB polymer/HET entities",
  "evidence_path": "run/source_register.csv",
  "reviewer": "curation-run",
  "notes": "Apo structure; no modeled inhibitor."
}
```

Audit the ledger:

```text
audit_missing_traceability
```

The audit rejects unknown reason codes, missing search scope, missing source
locator/blocker, reason/status/field mismatches, missing evidence files, hash
mismatches, blocker IDs absent from the blocker ledger, and Query IDs absent
from or linked to a different PDB in the database ledger.
`ok=false` is a blocker, not a warning to ignore.

Then join the ledger back to the actual table:

```text
audit_missing_coverage
```

This rejects a blank ligand or value unless a disposition exists for the same
`PDB_ID`, `Ligand_ID` and `Field_Name`.

## Traceability artifacts

Recommended run layout:

```text
run/
  evidence_pack/
  extracted_text/
  visual_review/
  visual_review_receipts.csv
  database_query_ledger.csv
  blocker_resolution.csv
  missing_dispositions.csv
  curated_records.csv
  source_register.csv
  conflicts.csv
  correction_log.csv
  validation.json
  human_compare/
  final_acceptance.json
  run_manifest.json
```

## Resources

The MCP exposes:

- `pdb-curation://readme`
- `pdb-curation://project-lessons`
- `pdb-curation://source-policy`
- `pdb-curation://databases`
- `pdb-curation://missing-reasons`
- `pdb-curation://skill`

The server enforces reason/status/field consistency at runtime and exposes
controlled enums to MCP clients. It cannot prove subjective interpretation.
The strict validator remains the authority for whether a row is scientifically
accepted; the missing ledger proves what was searched and why the field remains
missing.

## Database access

SciFinder and Reaxys APIs require entitlements beyond ordinary web login. API
absence is a blocker. Use an authorized browser/computer-use session or
structured export, and record the scope and hash. See `docs/DATABASES.md`.

Do not store credentials in MCP configuration, scripts, prompts, ledgers or
manifests.

## Testing

```powershell
python -m py_compile mcp\pdb-curation-mcp\server.py
python mcp\pdb-curation-mcp\server.py --self-test
python -m unittest discover mcp\pdb-curation-mcp\tests -v
```

The current suite covers protocol negotiation, newline and `Content-Length`
framing, tool schemas, traceability enforcement, blocker references, ledger
migration, concurrency, cancellation and human-comparison PDB inference.

## Validation context

The implementation was exercised on literature-backed affinity curation
workflows. Aggregated research results, human-review workbooks and licensed
exports are not bundled because they may contain local paths, personal review
notes or content governed by third-party terms.

When `compare_to_human` or `run_acceptance` is called without `pdb_ids`, the MCP
infers the target PDB set from the curated CSV. This prevents unrelated rows in
a large workbook from distorting the denominator. Set `use_all_human_rows=true`
only for an intentional whole-workbook comparison.

The MCP `run_acceptance` wrapper adds `missing_traceability` and
`missing_coverage` checks to the underlying acceptance script. A blank value
without a matching disposition keeps the final status at `FAIL`, even when the
normalized CSV passes schema validation.

## Non-goals

- bypassing paywalls, CAPTCHAs or entitlements;
- treating sequence alignment as mutation evidence;
- treating a database hit as primary evidence;
- replacing a pathologist/scientist reviewer for ambiguous images;
- claiming universal accuracy from one human-reviewed project;
- forcing all source documents into one vendor-specific parser.

