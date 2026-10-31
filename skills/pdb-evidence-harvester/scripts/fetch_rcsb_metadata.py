#!/usr/bin/env python3
"""Fetch polymer entity, UniProt and sequence metadata from RCSB."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path


GRAPHQL = "https://data.rcsb.org/graphql"
QUERY = """
query($ids: [String!]!) {
  entries(entry_ids: $ids) {
    rcsb_id
    struct { title }
    exptl { method }
    rcsb_entry_info { resolution_combined }
    polymer_entities {
      rcsb_id
      entity_poly {
        pdbx_seq_one_letter_code_can
        rcsb_sample_sequence_length
        rcsb_mutation_count
      }
      rcsb_polymer_entity { pdbx_description }
      rcsb_polymer_entity_container_identifiers {
        entity_id
        uniprot_ids
      }
      rcsb_entity_source_organism {
        ncbi_scientific_name
        ncbi_taxonomy_id
      }
    }
  }
}
"""


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Codex curation"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_ids(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        token = line.strip().split(",")[0].upper()
        if token and not token.startswith("#"):
            values.append(token)
    return sorted(set(values))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdb-list", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    pdb_ids = load_ids(args.pdb_list)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    entries = {}
    errors = []
    for start in range(0, len(pdb_ids), 40):
        batch = pdb_ids[start : start + 40]
        request = urllib.request.Request(
            GRAPHQL,
            data=json.dumps(
                {"query": QUERY, "variables": {"ids": batch}}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Codex curation"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("errors"):
            errors.append({"batch": batch, "errors": payload["errors"]})
        for entry in payload.get("data", {}).get("entries") or []:
            entries[entry["rcsb_id"]] = entry

    entity_rows = []
    sequence_rows = []
    for pdb_id in pdb_ids:
        entry = entries.get(pdb_id)
        if not entry:
            entity_rows.append(
                {
                    "PDB_ID": pdb_id,
                    "status": "not_found",
                    "title": "",
                    "method": "",
                    "resolution_A": "",
                    "entity_id": "",
                    "description": "",
                    "organism": "",
                    "uniprot_ids": "",
                    "sequence": "",
                }
            )
            continue
        rest_entry = {}
        try:
            rest_entry = fetch_json(
                f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
            )
        except Exception:
            pass
        citation = rest_entry.get("rcsb_primary_citation") or {}
        methods = "; ".join(
            item.get("method") or "" for item in entry.get("exptl") or []
        )
        resolutions = [
            str(value)
            for value in (entry.get("rcsb_entry_info") or {}).get(
                "resolution_combined", []
            )
            if value is not None
        ]
        for entity in entry.get("polymer_entities") or []:
            poly = entity.get("entity_poly") or {}
            description = entity.get("rcsb_polymer_entity") or {}
            identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
            organisms = entity.get("rcsb_entity_source_organism") or []
            row = {
                "PDB_ID": pdb_id,
                "status": "found",
                "doi_from_rcsb": citation.get("pdbx_database_id_DOI") or "",
                "pmid_from_rcsb": citation.get("pdbx_database_id_PubMed") or "",
                "title": (entry.get("struct") or {}).get("title") or "",
                "method": methods,
                "resolution_A": "; ".join(resolutions),
                "entity_id": identifiers.get("entity_id") or "",
                "description": description.get("pdbx_description") or "",
                "organism": "; ".join(
                    str(item.get("ncbi_scientific_name") or "")
                    for item in organisms
                    if item.get("ncbi_scientific_name")
                ),
                "taxonomy_id": "; ".join(
                    str(item.get("ncbi_taxonomy_id") or "")
                    for item in organisms
                    if item.get("ncbi_taxonomy_id")
                ),
                "uniprot_ids": "; ".join(identifiers.get("uniprot_ids") or []),
                "sequence_length": poly.get("rcsb_sample_sequence_length") or "",
                "sequence": poly.get("pdbx_seq_one_letter_code_can") or "",
            }
            entity_rows.append(row)
            sequence_rows.append(
                {
                    "PDB_ID": pdb_id,
                    "entity_id": row["entity_id"],
                    "description": row["description"],
                    "organism": row["organism"],
                    "uniprot_ids": row["uniprot_ids"],
                    "sequence": row["sequence"],
                }
            )

    for filename, rows in (
        ("pdb_entities.csv", entity_rows),
        ("pdb_sequences.csv", sequence_rows),
    ):
        with (args.output_dir / filename).open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(rows[0]) if rows else ["PDB_ID"]
            )
            writer.writeheader()
            writer.writerows(rows)
    summary = {
        "requested_pdbs": len(pdb_ids),
        "found_pdbs": sum(1 for pdb_id in pdb_ids if pdb_id in entries),
        "missing_pdbs": sorted(set(pdb_ids) - set(entries)),
        "entities": len(entity_rows),
        "entities_with_uniprot": sum(
            1 for row in entity_rows if row.get("uniprot_ids")
        ),
        "entities_with_sequence": sum(
            1 for row in entity_rows if row.get("sequence")
        ),
        "errors": errors,
    }
    (args.output_dir / "rcsb_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
