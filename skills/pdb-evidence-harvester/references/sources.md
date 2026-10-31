# Evidence source hierarchy

## Primary

1. Original research article main text and figures.
2. Original supplement and author correction/erratum.
3. Original patent examples, tables, claims and family members.
4. Deposited PDB entry and RCSB chemical-component records.
5. Direct author-provided data or repository record.

## Navigation and secondary

- Europe PMC, PubMed and Crossref for bibliographic links.
- Unpaywall/OpenAlex for authorized OA locations.
- Reaxys and SciFinder for discovering papers, patents and activity series.
- ChEMBL, BindingDB and PubChem for records that point back to an assay or paper.

A secondary record is a lead. It becomes evidence only after the primary source
and exact compound/target context are checked.

## Public API patterns

- RCSB entry: `https://data.rcsb.org/rest/v1/core/entry/{PDB_ID}`
- RCSB non-polymer: `https://data.rcsb.org/rest/v1/core/nonpolymer_entity/{PDB_ID}/{ENTITY_ID}`
- RCSB chemical component: `https://data.rcsb.org/rest/v1/core/chemcomp/{COMP_ID}`
- Europe PMC search: `https://www.ebi.ac.uk/europepmc/webservices/rest/search`
- Europe PMC references: `/MED/{PMID}/references?format=json`
- Europe PMC full text: `/PMC.../fullTextXML`
- Europe PMC supplements: `/PMC.../supplementaryFiles`
- Crossref: `https://api.crossref.org/works/{DOI}`
- Google Patents query: `https://patents.google.com/xhr/query?url=q%3D...`
- Jina reader fallback: `https://r.jina.ai/{URL}`

Record date, endpoint and response hash whenever the result enters an audit.
