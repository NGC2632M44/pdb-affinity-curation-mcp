# Database Access and Export Notes

The machine-readable inventory is `config/databases.json`. The MCP does not
store credentials or bypass paywalls, CAPTCHAs or entitlement controls.

## Public sources

| Source | Endpoint | Use |
|---|---|---|
| RCSB PDB | `https://data.rcsb.org/graphql` | citation, title, entities, HETs, sequence |
| Europe PMC | `https://www.ebi.ac.uk/europepmc/webservices/rest` | citation and OA full text |
| ChEMBL | `https://www.ebi.ac.uk/chembl/api/data` | molecule, target and assay crosscheck |
| BindingDB | `https://bindingdb.org/` | activity crosscheck |
| PubChem | `https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest` | chemical identity and synonyms |

Public data are still discovery or identity sources. Exact affinity acceptance
requires the primary evidence and locator.

## Licensed sources

### SciFinder-n

- URL: `https://scifinder-n.cas.org/`
- Access: institution login and entitlement.
- API documentation: `https://github.com/acs-cas/scifinder-api`
- API status: requires CAS `client_id`, redirect URI and entitlement. A normal
  username/password does not automatically create API access.
- Preferred workflow: exact structure or exact substance, inspect target and
  assay, then `Pharmacology Data -> Download Assay Results`.
- Preserve the structured export even if the ACS PDF viewer cannot be
  automated. The viewer may use a closed shadow root.

### CAS BioFinder

- URL: `https://biofinder.cas.org/`
- Access: institution login.
- Use for substance identity, pharmacology screens and cross-reference
  discovery.

### Reaxys

- URL: `https://www.reaxys.com/`
- Access: institution login, IP/entitlement or approved API agreement.
- API note:
  `https://www.elsevier.support/reaxys/answer/required-login-customers-using-api-with-ip-access`
- Use exact structure and retain registry number, reference and export.
- Never transfer analogue activity to an exact deposited fragment.

## Patent sources

- Google Patents: `https://patents.google.com/`
- WIPO Patentscope: `https://patentscope.wipo.int/`
- Espacenet: `https://worldwide.espacenet.com/`

A search result is a candidate, not evidence. Preserve exact publication
number, example/table/claim, page, rendered receipt and source hash. PatMap is an
authorized patent-PDF extractor when `PATMAP_API_KEY` is configured; it is not a
patent search engine.

## Local source search paths

The resolver checks configured directories and prefers already-downloaded files:

```text
/path/to/Zotero/storage
/path/to/Downloads
```

Run-specific evidence may also live under:

```text
run/documents/
run/fulltext/
run/supplements/
run/patents/
run/database_exports/
run/source_files/
```

Do not assume a folder name is an exact DOI. Resolve aliases and record the
mapping.

## Query ledger

Use `record_database_query` or an equivalent audited ledger. At minimum record:

```text
Date, Database, PDB_ID, Ligand, Query, Filters, Result_Count,
Export_Path, Export_SHA256, Checked_Status, Access_Status, Notes
```

For each reported hit, preserve the exact result row or locator. Distinguish:

- live query performed;
- archived export inspected;
- API unavailable;
- entitlement missing;
- authenticated browser session required;
- no hit in the declared scope.

## Credentials

Never place credentials, session cookies, bearer tokens or institution secrets
in MCP config, prompts, source registers, manifests or Skill files. Use the
host's authorized browser session or an institution-managed credential store.

## Failure handling

Escalate in order:

1. Public metadata/OA endpoint.
2. Existing local Zotero/Downloads/project file.
3. Public browser fetch.
4. Authorized browser/Playwright or computer-use session.
5. Licensed structured export or user handoff.

Record an unresolved route as a blocker. Do not convert an access failure into
an inferred value.

