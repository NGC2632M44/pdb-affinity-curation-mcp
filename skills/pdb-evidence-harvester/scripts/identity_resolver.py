#!/usr/bin/env python3
"""Resolve PDB ligand-code/DOI mismatches using exact chemical-component peers.

This script addresses a common deposition failure: an entry title or citation
contains a stale compound code, while the deposited chemical component is
shared with another PDB entry that has an explicit citation and the paper code.

The resolver does not decide affinity. It produces identity evidence that the
analysis skill can consume.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable


RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
RCSB_POLY = "https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{entity_id}"
RCSB_NONPOLY = "https://data.rcsb.org/rest/v1/core/nonpolymer_entity/{pdb_id}/{entity_id}"
RCSB_CHEMCOMP = "https://data.rcsb.org/rest/v1/core/chemcomp/{comp_id}"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")[:160] or "file"


def request_json(
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 45,
    attempts: int = 3,
) -> dict[str, Any]:
    payload = None
    headers = {
        "User-Agent": "pdb-affinity-curation/2.1 (Codex)",
        "Accept": "application/json",
    }
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                headers=headers,
                method="POST" if body is not None else "GET",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {last_error}")


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def extract_compound_codes(value: str) -> list[str]:
    """Extract long alphanumeric compound codes without treating PDB IDs as codes."""

    candidates = re.findall(r"\b[A-Z]{1,8}[A-Z0-9-]*\d{4,}[A-Z0-9-]*\b", value.upper())
    output: list[str] = []
    for candidate in candidates:
        token = candidate.strip("-")
        if re.fullmatch(r"[0-9][A-Z0-9]{3}", token):
            continue
        if token.count("-") > 2:
            continue
        if token not in output:
            output.append(token)
    return output


def entry_metadata(pdb_id: str, timeout: int) -> dict[str, Any]:
    entry = request_json(RCSB_ENTRY.format(pdb_id=pdb_id), timeout=timeout)
    citation = entry.get("rcsb_primary_citation") or {}
    title = clean((entry.get("struct") or {}).get("title"))
    doi = clean(
        citation.get("pdbx_database_id_DOI")
        or citation.get("pdbx_database_id_doi")
    )
    pmid = clean(
        citation.get("pdbx_database_id_PubMed")
        or citation.get("pdbx_database_id_pubmed")
    )
    container = entry.get("rcsb_entry_container_identifiers") or {}
    nonpoly_ids = [clean(item) for item in container.get("non_polymer_entity_ids") or []]
    polymer_ids = [clean(item) for item in container.get("polymer_entity_ids") or []]
    entities = []
    for entity_id in nonpoly_ids:
        entity = request_json(
            RCSB_NONPOLY.format(pdb_id=pdb_id, entity_id=entity_id),
            timeout=timeout,
        )
        nonpoly = entity.get("pdbx_entity_nonpoly") or {}
        details = entity.get("rcsb_nonpolymer_entity") or {}
        annotations = entity.get("rcsb_nonpolymer_entity_annotation") or []
        entities.append(
            {
                "entity_id": entity_id,
                "comp_id": clean(nonpoly.get("comp_id")),
                "name": clean(nonpoly.get("name") or details.get("pdbx_description")),
                "formula_weight": details.get("formula_weight"),
                "annotations": annotations,
            }
        )
    polymers = []
    for entity_id in polymer_ids:
        entity = request_json(
            RCSB_POLY.format(pdb_id=pdb_id, entity_id=entity_id),
            timeout=timeout,
        )
        polymers.append(
            {
                "entity_id": entity_id,
                "sequence": clean(
                    (entity.get("entity_poly") or {}).get(
                        "pdbx_seq_one_letter_code_can"
                    )
                ),
                "description": clean(
                    (entity.get("rcsb_polymer_entity") or {}).get("pdbx_description")
                ),
                "mutations": (
                    (entity.get("entity_poly") or {}).get("rcsb_mutation_count")
                ),
            }
        )
    return {
        "pdb_id": pdb_id,
        "title": title,
        "title_codes": extract_compound_codes(title),
        "citation_title": clean(citation.get("title")),
        "doi": doi,
        "pmid": pmid,
        "entities": entities,
        "polymers": polymers,
    }


def component_metadata(comp_id: str, timeout: int) -> dict[str, Any]:
    if not comp_id:
        return {}
    payload = request_json(RCSB_CHEMCOMP.format(comp_id=comp_id), timeout=timeout)
    descriptor = payload.get("rcsb_chem_comp_descriptor") or {}
    return {
        "comp_id": comp_id,
        "name": clean((payload.get("chem_comp") or {}).get("name")),
        "formula": clean((payload.get("chem_comp") or {}).get("formula")),
        "smiles": clean(descriptor.get("SMILES")),
        "smiles_stereo": clean(descriptor.get("SMILES_stereo")),
        "inchi_key": clean(descriptor.get("InChIKey")),
    }


def component_peers(comp_id: str, timeout: int) -> list[str]:
    query = {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": (
                    "rcsb_nonpolymer_entity_container_identifiers.nonpolymer_comp_id"
                ),
                "operator": "exact_match",
                "value": comp_id,
            },
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": 200}},
    }
    payload = request_json(RCSB_SEARCH, body=query, timeout=timeout)
    return sorted(
        {
            clean(item.get("identifier")).upper()
            for item in payload.get("result_set") or []
            if re.fullmatch(r"[0-9A-Z]{4}", clean(item.get("identifier")).upper())
        }
    )


def read_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError:
        return ""
    document = Document(path)
    text = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            text.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(text)


def read_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    workbook = load_workbook(path, read_only=True, data_only=True)
    output = []
    for sheet in workbook.worksheets:
        output.append(f"=== {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            values = [clean(value) for value in row if clean(value)]
            if values:
                output.append(" | ".join(values))
    return "\n".join(output)


def read_text_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm"}:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".docx":
            return read_docx(path)
        if suffix == ".xlsx":
            return read_xlsx(path)
    except Exception:
        return ""
    return ""


def collect_text(paths: Iterable[Path], output_dir: Path, timeout: int) -> tuple[str, list[dict[str, Any]]]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            files.append(path)
    chunks = []
    manifest = []
    cache_dir = output_dir / "identity_text"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        suffix = path.suffix.lower()
        text = ""
        extracted_from = ""
        if suffix == ".pdf":
            # PDF text should normally come from extract_document_text.py. Keep a
            # clear receipt instead of silently treating a binary PDF as text.
            manifest.append(
                {
                    "path": str(path),
                    "status": "requires_page_aware_extraction",
                    "chars": 0,
                }
            )
            continue
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        member_name = safe_filename(Path(member.filename).name)
                        destination = cache_dir / f"{safe_filename(path.stem)}_{member_name}"
                        if not destination.exists():
                            with archive.open(member) as source, destination.open("wb") as target:
                                shutil.copyfileobj(source, target)
                        text += "\n" + read_text_file(destination)
                extracted_from = "zip"
            except Exception as error:
                manifest.append({"path": str(path), "status": "zip_failed", "error": str(error)})
                continue
        else:
            text = read_text_file(path)
        if text:
            chunks.append(text)
            manifest.append(
                {
                    "path": str(path),
                    "status": "read",
                    "chars": len(text),
                    "via": extracted_from or suffix.lstrip("."),
                }
            )
        else:
            manifest.append({"path": str(path), "status": "unsupported_or_empty", "chars": 0})
    return "\n\n".join(chunks), manifest


def code_presence(codes: list[str], normalized_text: str) -> dict[str, bool]:
    return {code: normalize_text(code) in normalized_text for code in codes}


def fuzzy_code_name_match(
    normalized_text: str,
    code: str,
    name: str,
) -> tuple[float, int]:
    normalized_code = normalize_text(code)
    normalized_name = normalize_text(name)
    if not normalized_code or len(normalized_name) < 20:
        return 0.0, -1
    best = 0.0
    best_position = -1
    window = max(80, len(normalized_name) * 3)
    width = len(normalized_name)
    for occurrence in re.finditer(re.escape(normalized_code), normalized_text):
        start = max(0, occurrence.start() - window)
        end = min(len(normalized_text), occurrence.end() + window)
        segment = normalized_text[start:end]
        stride = max(5, width // 12)
        for offset in range(0, max(1, len(segment) - width + 1), stride):
            candidate = segment[offset : offset + width]
            ratio = difflib.SequenceMatcher(None, normalized_name, candidate).ratio()
            if ratio > best:
                best = ratio
                best_position = start + offset
    return best, best_position


def mutation_tokens(value: str) -> list[str]:
    tokens = re.findall(r"\b[A-Z]\d{1,4}[A-Z]\b", value.upper())
    if re.search(r"\bwild[- ]?type\b|\bWT\b", value, re.IGNORECASE):
        tokens.append("WT")
    return sorted(set(tokens))


def ligand_candidate_entity(entity: dict[str, Any]) -> bool:
    annotations = entity.get("annotations") or []
    if any(
        clean(item.get("type")).upper() == "SUBJECT_OF_INVESTIGATION"
        for item in annotations
    ):
        return True
    name = clean(entity.get("name")).lower()
    excluded = (
        "vinylpyrrolidone",
        "polyethylene glycol",
        "glycerol",
        "sulfate",
        "phosphate",
        "acetate",
        "citrate",
        "buffer",
        "cryoprotect",
        "manganese",
        "magnesium",
        "zinc",
        "calcium",
        "sodium",
        "chloride",
    )
    return not any(token in name for token in excluded)


def resolve_one(
    pdb_id: str,
    text: str,
    timeout: int,
    peer_limit: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_text = normalize_text(text)
    target = entry_metadata(pdb_id, timeout)
    candidate_rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "target": target,
        "components": [],
        "candidates": [],
    }
    seed_components = [
        entity
        for entity in target["entities"]
        if clean(entity.get("comp_id"))
        and ligand_candidate_entity(entity)
        and clean(entity.get("formula_weight"))
    ]
    target_mutations = mutation_tokens(target["title"])
    target_sequences = {
        clean(item.get("sequence"))
        for item in target.get("polymers", [])
        if clean(item.get("sequence"))
    }
    for entity in seed_components:
        comp_id = clean(entity["comp_id"])
        component = component_metadata(comp_id, timeout)
        component["name_in_text"] = bool(
            normalize_text(component.get("name", ""))
            and normalize_text(component.get("name", "")) in normalized_text
        )
        component["inchi_key_in_text"] = bool(
            component.get("inchi_key")
            and normalize_text(component["inchi_key"]) in normalized_text
        )
        peers = component_peers(comp_id, timeout)
        evidence["components"].append(
            {**component, "target_entity": entity, "peers": peers}
        )
        for peer_id in peers:
            if peer_id == pdb_id or len(evidence["candidates"]) >= peer_limit:
                continue
            peer = entry_metadata(peer_id, timeout)
            peer_presence = code_presence(peer["title_codes"], normalized_text)
            peer["title_code_presence_in_text"] = peer_presence
            peer["component_name_similarity"] = {}
            for code, present in peer_presence.items():
                if present:
                    ratio, position = fuzzy_code_name_match(
                        normalized_text,
                        code,
                        clean(component.get("name")),
                    )
                    peer["component_name_similarity"][code] = {
                        "ratio": round(ratio, 4),
                        "normalized_position": position,
                    }
            peer["same_component"] = comp_id
            peer["has_primary_citation"] = bool(peer["doi"] or peer["pmid"])
            peer["mutation_tokens"] = mutation_tokens(peer["title"])
            peer["mutation_match"] = bool(
                target_mutations
                and set(target_mutations).intersection(peer["mutation_tokens"])
            )
            peer_sequences = {
                clean(item.get("sequence"))
                for item in peer.get("polymers", [])
                if clean(item.get("sequence"))
            }
            peer["exact_sequence_match"] = bool(
                target_sequences.intersection(peer_sequences)
            )
            peer["eligible_peer"] = bool(
                peer["has_primary_citation"]
                and any(peer_presence.values())
                and (
                    any(
                        item.get("ratio", 0) >= 0.9
                        for item in peer["component_name_similarity"].values()
                    )
                    or component["inchi_key_in_text"]
                )
                and (peer["mutation_match"] or peer["exact_sequence_match"])
            )
            evidence["candidates"].append(peer)
            candidate_rows.append(
                {
                    "PDB_ID": pdb_id,
                    "Component_ID": comp_id,
                    "Component_Name": component.get("name", ""),
                    "InChIKey": component.get("inchi_key", ""),
                    "Current_Title_Codes": "; ".join(target["title_codes"]),
                    "Peer_PDB_ID": peer_id,
                    "Peer_Title_Codes": "; ".join(peer["title_codes"]),
                    "Codes_Present_In_Text": "; ".join(
                        code for code, present in peer_presence.items() if present
                    ),
                    "Component_Name_In_Text": "yes" if component["name_in_text"] else "no",
                    "Best_Name_Similarity_Near_Peer_Code": max(
                        [
                            item.get("ratio", 0)
                            for item in peer["component_name_similarity"].values()
                        ]
                        or [0]
                    ),
                    "InChIKey_In_Text": "yes" if component["inchi_key_in_text"] else "no",
                    "Peer_DOI": peer["doi"],
                    "Peer_PMID": peer["pmid"],
                    "Peer_Title": peer["title"],
                    "Peer_Mutations": "; ".join(peer["mutation_tokens"]),
                    "Mutation_Match": "yes" if peer["mutation_match"] else "no",
                    "Exact_Sequence_Match": (
                        "yes" if peer["exact_sequence_match"] else "no"
                    ),
                    "Eligible_Peer": "yes" if peer["eligible_peer"] else "no",
                }
            )
    current_codes = code_presence(target["title_codes"], normalized_text)
    direct = bool(
        (target["doi"] or target["pmid"])
        and (not target["title_codes"] or any(current_codes.values()))
    )
    eligible = [item for item in evidence["candidates"] if item.get("eligible_peer")]
    if direct:
        resolution = "direct_citation_and_code"
        mapped = target
        summary = "RCSB citation and a title compound code are supported by the text."
    elif eligible:
        eligible.sort(
            key=lambda item: (
                not bool(item.get("mutation_match")),
                not bool(item.get("exact_sequence_match")),
                not any(item.get("title_code_presence_in_text", {}).values()),
                not item.get("doi"),
                item["pdb_id"],
            )
        )
        mapped = eligible[0]
        resolution = "resolved_component_peer"
        summary = (
            f"Exact component {clean(mapped.get('same_component'))} is shared with "
            f"{mapped['pdb_id']}; its title code "
            f"{', '.join(mapped.get('title_codes') or [])} occurs in the paper/SI, "
            f"and its RCSB citation is {mapped.get('doi') or mapped.get('pmid')}."
        )
    elif any(
        component.get("name_in_text") or component.get("inchi_key_in_text")
        for component in evidence["components"]
    ):
        mapped = {}
        resolution = "component_name_only_unresolved"
        summary = (
            "The chemical component occurs in the text, but no peer with a usable "
            "primary citation and paper code was found."
        )
    else:
        mapped = {}
        resolution = "unresolved"
        summary = "No direct or same-component peer identity chain was established."
    row = {
        "PDB_ID": pdb_id,
        "Resolution": resolution,
        "Mapped_DOI": clean(mapped.get("doi")),
        "Mapped_PMID": clean(mapped.get("pmid")),
        "Mapped_Peer_PDB_ID": clean(mapped.get("pdb_id")),
        "Mapped_Title_Codes": "; ".join(mapped.get("title_codes") or []),
        "Conflict": (
            "title_code_citation_conflict"
            if resolution == "resolved_component_peer"
            else ""
        ),
        "Evidence_Summary": summary,
    }
    return row, evidence


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdb-id", action="append", default=[])
    parser.add_argument("--pdb-list", type=Path)
    parser.add_argument("--text", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--peer-limit", type=int, default=20)
    args = parser.parse_args()

    ids = [clean(value).upper() for value in args.pdb_id if clean(value)]
    if args.pdb_list:
        ids.extend(
            line.strip().split(",")[0].upper()
            for line in args.pdb_list.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    ids = sorted({value for value in ids if re.fullmatch(r"[0-9A-Z]{4}", value)})
    if not ids:
        parser.error("provide --pdb-id or --pdb-list")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    text, text_manifest = collect_text(args.text, args.output_dir, args.timeout)
    write_csv(args.output_dir / "identity_text_manifest.csv", text_manifest)
    if not text.strip():
        parser.error("no readable text was supplied; run extract_document_text.py first")

    rows = []
    evidence = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "text_manifest": text_manifest,
        "resolutions": {},
    }
    for pdb_id in ids:
        try:
            row, detail = resolve_one(pdb_id, text, args.timeout, args.peer_limit)
        except Exception as error:
            row = {
                "PDB_ID": pdb_id,
                "Resolution": "unresolved_error",
                "Mapped_DOI": "",
                "Mapped_PMID": "",
                "Mapped_Peer_PDB_ID": "",
                "Mapped_Title_Codes": "",
                "Conflict": "",
                "Evidence_Summary": f"{type(error).__name__}: {error}",
            }
            detail = {"error": str(error)}
        rows.append(row)
        evidence["resolutions"][pdb_id] = {"result": row, "detail": detail}
        print(
            f"{pdb_id}: {row['Resolution']} "
            f"({row['Mapped_Peer_PDB_ID'] or 'no peer'}, {row['Mapped_DOI'] or 'no DOI'})"
        )
    write_csv(args.output_dir / "identity_resolution.csv", rows)
    (args.output_dir / "identity_resolution_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "resolved": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
