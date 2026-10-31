#!/usr/bin/env python3
"""Build a PDB-to-original-evidence pack from public metadata and OA sources.

The script discovers evidence; it does not automatically decide affinity.
Every extracted value still must be checked against the cited paper or patent.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
RCSB_NONPOLY = "https://data.rcsb.org/rest/v1/core/nonpolymer_entity/{pdb_id}/{entity_id}"
RCSB_CHEMCOMP = "https://data.rcsb.org/rest/v1/core/chemcomp/{comp_id}"
RCSB_POLY = "https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{entity_id}"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPEPMC_REFS = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{record_id}/references"
)
EUROPEPMC_XML = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
)
EUROPEPMC_SUPP = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/supplementaryFiles"
)
CROSSREF_WORK = "https://api.crossref.org/works/{doi}"
GOOGLE_PATENTS = "https://patents.google.com/xhr/query"
JINA_READER = "https://r.jina.ai/{url}"

ION_CODES = {
    "NA",
    "K",
    "CL",
    "CA",
    "MG",
    "MN",
    "ZN",
    "FE",
    "FE2",
    "CU",
    "NI",
    "CO",
}
ADDITIVE_CODES = {
    "SO4",
    "PO4",
    "GOL",
    "EDO",
    "PEG",
    "PG4",
    "PGE",
    "P6G",
    "DMS",
    "ACT",
    "ACE",
    "FMT",
    "MES",
    "TRS",
    "HEP",
    "EPE",
    "MES",
    "CIT",
    "TAR",
    "MPD",
    "DTT",
    "BME",
    "IOD",
    "BR",
}


def fetch(
    url: str,
    *,
    timeout: int = 45,
    attempts: int = 3,
    accept: str | None = None,
) -> tuple[bytes, dict[str, str], str]:
    headers = {
        "User-Agent": "pdb-affinity-curation/2.0 (Codex)",
        "Accept": accept or "*/*",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    response.read(),
                    {key.lower(): value for key, value in response.headers.items()},
                    response.geturl(),
                )
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last_error}")


def fetch_json(
    url: str,
    *,
    timeout: int = 45,
    attempts: int = 3,
) -> dict[str, Any]:
    data, _, _ = fetch(
        url,
        timeout=timeout,
        attempts=attempts,
        accept="application/json",
    )
    return json.loads(data.decode("utf-8"))


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def safe_filename(value: str, fallback: str = "file") -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return value[:120] or fallback


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pdb_ids(args: argparse.Namespace) -> list[str]:
    values = list(args.pdb_id or [])
    if args.pdb_list:
        for line in args.pdb_list.read_text(encoding="utf-8-sig").splitlines():
            token = line.strip().split(",")[0].upper()
            if token and not token.startswith("#"):
                values.append(token)
    return sorted({value.upper() for value in values})


def citation_fields(entry: dict[str, Any]) -> dict[str, str]:
    citation = entry.get("rcsb_primary_citation") or {}
    doi = clean(
        citation.get("pdbx_database_id_DOI")
        or citation.get("pdbx_database_id_doi")
    )
    pmid = clean(
        citation.get("pdbx_database_id_PubMed")
        or citation.get("pdbx_database_id_pubmed")
        or (entry.get("rcsb_entry_container_identifiers") or {}).get("pubmed_id")
    )
    return {
        "doi": doi,
        "pmid": pmid,
        "citation_title": clean(citation.get("title")),
        "journal": clean(citation.get("journal_abbrev") or citation.get("journal_id_ISSN")),
        "year": clean(citation.get("year")),
    }


def classify_chem_component(comp_id: str, name: str, weight: float | None) -> str:
    comp_id = comp_id.upper()
    lowered = name.lower()
    if comp_id in ADDITIVE_CODES or any(
        token in lowered
        for token in (
            "sulfate",
            "phosphate",
            "glycerol",
            "ethylene glycol",
            "polyethylene glycol",
            "acetate",
            "citrate",
            "buffer",
            "cryoprotect",
        )
    ):
        return "crystallization_additive"
    if comp_id in ION_CODES or any(
        token in lowered
        for token in (" ion", "manganese", "magnesium", "zinc", "calcium", "sodium")
    ):
        return "cofactor_or_metal"
    if any(token in lowered for token in ("rna", "dna", "nucleotide", "uridine", "adenosine")):
        return "rna_or_nucleotide"
    # RCSB formula_weight is reported in kDa for these entity records.
    if weight is not None and weight >= 0.15:
        return "ligand_candidate"
    return "small_component_unresolved"


def chem_component(comp_id: str, timeout: int) -> dict[str, Any]:
    try:
        return fetch_json(RCSB_CHEMCOMP.format(comp_id=comp_id), timeout=timeout)
    except Exception as error:
        return {"error": str(error), "rcsb_id": comp_id}


def entry_ligands(entry: dict[str, Any], pdb_id: str, timeout: int) -> list[dict[str, Any]]:
    container = entry.get("rcsb_entry_container_identifiers") or {}
    rows = []
    for entity_id in container.get("non_polymer_entity_ids") or []:
        try:
            entity = fetch_json(
                RCSB_NONPOLY.format(pdb_id=pdb_id, entity_id=entity_id),
                timeout=timeout,
            )
        except Exception as error:
            rows.append(
                {
                    "PDB_ID": pdb_id,
                    "entity_id": entity_id,
                    "status": "nonpolymer_fetch_failed",
                    "error": str(error),
                }
            )
            continue
        nonpoly = entity.get("pdbx_entity_nonpoly") or {}
        details = entity.get("rcsb_nonpolymer_entity") or {}
        comp_id = clean(nonpoly.get("comp_id"))
        name = clean(nonpoly.get("name") or details.get("pdbx_description"))
        weight = details.get("formula_weight")
        try:
            weight = float(weight) if weight is not None else None
        except (TypeError, ValueError):
            weight = None
        component = chem_component(comp_id, timeout) if comp_id else {}
        descriptor = component.get("rcsb_chem_comp_descriptor") or {}
        synonyms = [
            clean(item.get("name"))
            for item in component.get("rcsb_chem_comp_synonyms") or []
            if clean(item.get("name"))
        ]
        related = component.get("rcsb_chem_comp_related") or []
        rows.append(
            {
                "PDB_ID": pdb_id,
                "entity_id": entity_id,
                "comp_id": comp_id,
                "name": name,
                "formula_weight": weight or "",
                "role": classify_chem_component(comp_id, name, weight),
                "SMILES": descriptor.get("SMILES", ""),
                "InChIKey": descriptor.get("InChIKey", ""),
                "synonyms": "; ".join(dict.fromkeys(synonyms)),
                "related_database_ids": "; ".join(
                    f"{item.get('resource_name')}:{item.get('resource_accession_code')}"
                    for item in related
                    if item.get("resource_accession_code")
                ),
                "chemcomp_json": component,
            }
        )
    return rows


def entry_polymers(entry: dict[str, Any], pdb_id: str, timeout: int) -> list[dict[str, Any]]:
    container = entry.get("rcsb_entry_container_identifiers") or {}
    rows = []
    for entity_id in container.get("polymer_entity_ids") or []:
        try:
            entity = fetch_json(
                RCSB_POLY.format(pdb_id=pdb_id, entity_id=entity_id),
                timeout=timeout,
            )
        except Exception as error:
            rows.append({"PDB_ID": pdb_id, "entity_id": entity_id, "error": str(error)})
            continue
        poly = entity.get("entity_poly") or {}
        description = entity.get("rcsb_polymer_entity") or {}
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        organisms = entity.get("rcsb_entity_source_organism") or []
        rows.append(
            {
                "PDB_ID": pdb_id,
                "entity_id": entity_id,
                "description": clean(description.get("pdbx_description")),
                "organism": "; ".join(
                    clean(item.get("ncbi_scientific_name"))
                    for item in organisms
                    if clean(item.get("ncbi_scientific_name"))
                ),
                "uniprot_ids": "; ".join(identifiers.get("uniprot_ids") or []),
                "sequence": clean(poly.get("pdbx_seq_one_letter_code_can")),
            }
        )
    return rows


def europepmc_resolve(doi: str, pmid: str, timeout: int) -> dict[str, Any]:
    query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid}"
    url = (
        f"{EUROPEPMC_SEARCH}?query={urllib.parse.quote(query)}"
        "&format=json&resultType=core&pageSize=5"
    )
    try:
        payload = fetch_json(url, timeout=min(timeout, 30), attempts=2)
    except Exception as error:
        return {"error": str(error), "query": query}
    results = ((payload.get("resultList") or {}).get("result") or [])
    if not results:
        return {"query": query, "result": None}
    result = results[0]
    return {
        "query": query,
        "result": result,
        "pmid": clean(result.get("pmid")),
        "pmcid": clean(result.get("pmcid")),
        "doi": clean(result.get("doi")),
        "title": clean(result.get("title")),
        "isOpenAccess": clean(result.get("isOpenAccess")),
        "hasSuppl": clean(result.get("hasSuppl")),
        "fullTextUrlList": result.get("fullTextUrlList") or {},
    }


def europepmc_references(
    source: str,
    record_id: str,
    timeout: int,
) -> list[dict[str, Any]]:
    if not source or not record_id:
        return []
    url = (
        EUROPEPMC_REFS.format(source=source, record_id=record_id)
        + "?format=json&pageSize=1000"
    )
    try:
        payload = fetch_json(url, timeout=timeout)
    except Exception:
        return []
    return ((payload.get("referenceList") or {}).get("reference") or [])


def crossref(doi: str, timeout: int, mailto: str) -> dict[str, Any]:
    if not doi:
        return {}
    url = CROSSREF_WORK.format(doi=urllib.parse.quote(doi, safe=""))
    if mailto:
        url += f"?mailto={urllib.parse.quote(mailto)}"
    try:
        return (fetch_json(url, timeout=timeout).get("message") or {})
    except Exception as error:
        return {"error": str(error)}


def patent_queries(ligands: list[dict[str, Any]], target_hint: str) -> list[dict[str, str]]:
    queries = []
    for ligand in ligands:
        if ligand.get("role") != "ligand_candidate":
            continue
        name = clean(ligand.get("name"))
        words = re.findall(r"[A-Za-z0-9]+", name)
        normalized = " ".join(words[:10])
        query = f"{normalized} {target_hint} inhibitor".strip()
        queries.append(
            {
                "PDB_ID": clean(ligand.get("PDB_ID")),
                "comp_id": clean(ligand.get("comp_id")),
                "query": query,
                "query_basis": "RCSB ligand name + patent target terms",
            }
        )
        inchikey = clean(ligand.get("InChIKey"))
        if inchikey:
            queries.append(
                {
                    "PDB_ID": clean(ligand.get("PDB_ID")),
                    "comp_id": clean(ligand.get("comp_id")),
                    "query": f"{inchikey} {target_hint}".strip(),
                    "query_basis": "RCSB InChIKey + patent target terms",
                }
            )
    deduped = {}
    for row in queries:
        deduped[(row["PDB_ID"], row["query"])] = row
    return list(deduped.values())


def google_patents(
    query: str,
    timeout: int,
    max_results: int,
) -> tuple[list[dict[str, Any]], str]:
    encoded = urllib.parse.quote(f"q={query}", safe="")
    url = f"{GOOGLE_PATENTS}?url={encoded}"
    try:
        payload = fetch_json(url, timeout=min(timeout, 20), attempts=1)
    except Exception as error:
        return [], f"{type(error).__name__}: {error}"
    results = payload.get("results") or {}
    if results.get("user_error"):
        return [], clean(results.get("user_error"))
    output = []
    for cluster in results.get("cluster") or []:
        for item in cluster.get("result") or []:
            patent = item.get("patent") or {}
            publication = clean(patent.get("publication_number"))
            output.append(
                {
                    "query": query,
                    "patent_id": clean(item.get("id")),
                    "publication_number": publication,
                    "title": clean(patent.get("title")),
                    "snippet": clean(patent.get("snippet")),
                    "assignee": clean(patent.get("assignee")),
                    "publication_date": clean(patent.get("publication_date")),
                    "priority_date": clean(patent.get("priority_date")),
                    "pdf": clean(patent.get("pdf")),
                    "url": (
                        f"https://patents.google.com/patent/{publication}/en"
                        if publication
                        else ""
                    ),
                    "evidence_status": "candidate_only_unread",
                }
            )
            if len(output) >= max_results:
                return output, ""
    return output, ""


def supplementary_links(xml_bytes: bytes) -> list[str]:
    links = set()
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in {"supplementary-material", "media"}:
            continue
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1].lower()
            if local in {"href", "xlink:href"} and value:
                links.add(value)
    return sorted(links)


def download_url(
    url: str,
    destination_dir: Path,
    stem: str,
    timeout: int,
    max_bytes: int,
) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "pdb-affinity-curation/2.0 (Codex)"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            final_url = response.geturl()
            chunks = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return {
                        "url": url,
                        "final_url": final_url,
                        "status": "skipped_too_large",
                        "bytes_read": total,
                    }
                chunks.append(chunk)
            body = b"".join(chunks)
    except Exception as error:
        return {"url": url, "status": "failed", "error": str(error)}
    content_type = headers.get("content-type", "").lower()
    claimed_pdf = "pdf" in content_type or final_url.lower().endswith(".pdf")
    if body.startswith(b"%PDF-"):
        suffix = ".pdf"
    elif body.startswith(b"PK\x03\x04"):
        if "word" in content_type or final_url.lower().endswith((".docx", ".doc")):
            suffix = ".docx"
        elif "excel" in content_type or final_url.lower().endswith((".xlsx", ".xls")):
            suffix = ".xlsx"
        else:
            suffix = ".zip"
    elif body[:4] in {b"II*\x00", b"MM\x00*"}:
        suffix = ".tif"
    elif "xml" in content_type:
        suffix = ".xml"
    elif "html" in content_type:
        suffix = ".html"
    elif claimed_pdf:
        suffix = ".invalid-pdf-response"
    else:
        suffix = ".bin"
    path = destination_dir / f"{safe_filename(stem)}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {
        "url": url,
        "final_url": final_url,
        "path": str(path),
        "content_type": content_type,
        "bytes": len(body),
        "status": "downloaded_invalid_pdf_payload" if claimed_pdf and suffix != ".pdf" else "downloaded",
    }


def extract_zip_archive(
    archive_path: Path,
    destination_root: Path,
    max_bytes: int,
) -> list[dict[str, Any]]:
    extracted = []
    if archive_path.suffix.lower() != ".zip":
        return extracted
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for index, member in enumerate(archive.infolist()):
                if member.is_dir() or member.file_size > max_bytes:
                    continue
                member_name = Path(member.filename).name
                suffix = Path(member_name).suffix.lower()
                if suffix not in {
                    ".pdf",
                    ".docx",
                    ".xlsx",
                    ".doc",
                    ".xls",
                    ".csv",
                    ".tsv",
                    ".xml",
                    ".html",
                    ".htm",
                    ".tif",
                    ".tiff",
                    ".png",
                    ".jpg",
                    ".jpeg",
                }:
                    continue
                destination = (
                    destination_root
                    / f"{index:03d}_{safe_filename(member_name, 'supplement')}"
                )
                with archive.open(member) as source, destination.open("wb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                extracted.append(
                    {
                        "status": "extracted",
                        "path": str(destination),
                        "bytes": destination.stat().st_size,
                        "member": member.filename,
                    }
                )
    except zipfile.BadZipFile as error:
        return [{"status": "zip_extract_failed", "error": str(error)}]
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdb-id", action="append", default=[])
    parser.add_argument("--pdb-list", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--download-oa", action="store_true")
    parser.add_argument("--download-supplements", action="store_true")
    parser.add_argument("--fetch-web", action="store_true")
    parser.add_argument("--patent-search", action="store_true")
    parser.add_argument("--download-patents", action="store_true")
    parser.add_argument("--patent-id", action="append", default=[])
    parser.add_argument("--max-download-mb", type=int, default=40)
    parser.add_argument("--max-patents-per-query", type=int, default=8)
    args = parser.parse_args()

    ids = pdb_ids(args)
    if not ids:
        parser.error("provide --pdb-id and/or --pdb-list")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "fulltext").mkdir(exist_ok=True)
    (args.output_dir / "web_pages").mkdir(exist_ok=True)
    max_bytes = args.max_download_mb * 1024 * 1024
    mailto = ""

    manifest: dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "PDB evidence discovery; no automatic affinity adjudication",
        "pdb_ids": ids,
        "entries": {},
    }
    ligand_rows = []
    polymer_rows = []
    citation_rows = []
    reference_rows = []
    patent_query_rows = []
    patent_candidate_rows = []
    download_rows = []
    blockers = []
    review_checklist = []
    for patent_id in args.patent_id:
        clean_id = patent_id.strip().upper()
        url = f"https://patents.google.com/patent/{clean_id}/en"
        patent_candidate_rows.append(
            {
                "query": "exact_patent_id",
                "patent_id": f"patent/{clean_id}/en",
                "publication_number": clean_id,
                "title": "",
                "snippet": "",
                "assignee": "",
                "publication_date": "",
                "priority_date": "",
                "pdf": "",
                "url": url,
                "evidence_status": "exact_id_unread",
            }
        )
        if args.download_patents:
            jina_url = JINA_READER.format(url=urllib.parse.quote(url, safe=""))
            downloaded = download_url(
                jina_url,
                args.output_dir / "web_pages",
                f"patent_{clean_id}",
                args.timeout,
                max_bytes,
            )
            download_rows.append({"PDB_ID": "", "kind": "patent_page", **downloaded})

    for pdb_id in ids:
        try:
            entry = fetch_json(RCSB_ENTRY.format(pdb_id=pdb_id), timeout=args.timeout)
        except Exception as error:
            blockers.append({"PDB_ID": pdb_id, "blocker": "rcsb_fetch_failed", "detail": str(error)})
            continue
        fields = citation_fields(entry)
        citation_rows.append({"PDB_ID": pdb_id, **fields})
        review_checklist.append(
            {
                "PDB_ID": pdb_id,
                "Primary_DOI": fields["doi"],
                "PMID": fields["pmid"],
                "primary_citation_resolved": "yes" if fields["doi"] or fields["pmid"] else "no",
                "main_text_read": "no",
                "methods_read": "no",
                "tables_figures_read": "no",
                "supplement_read": "no",
                "reference_chain_checked": "no",
                "patent_search_checked": "no",
                "patent_fulltext_read": "no",
                "reaxys_checked": "no",
                "scifinder_checked": "no",
                "chembl_checked": "no",
                "bindingdb_checked": "no",
                "sequence_used_for_identity_only": "no",
                "mutation_supported_by_primary_source": "no",
                "visual_review_checked": "no",
                "review_status": "evidence_pack_created_not_adjudicated",
            }
        )
        ligands = entry_ligands(entry, pdb_id, args.timeout)
        polymers = entry_polymers(entry, pdb_id, args.timeout)
        ligand_rows.extend(ligands)
        polymer_rows.extend(polymers)
        europe = europepmc_resolve(fields["doi"], fields["pmid"], args.timeout)
        epmc_result = europe.get("result") or {}
        refs = europepmc_references(
            clean(epmc_result.get("source")),
            clean(epmc_result.get("id")),
            args.timeout,
        ) if epmc_result else []
        for reference in refs:
            reference_rows.append({"PDB_ID": pdb_id, **reference})
        cr = crossref(fields["doi"], args.timeout, mailto)
        if not refs:
            for index, reference in enumerate(cr.get("reference") or [], start=1):
                reference_rows.append(
                    {
                        "PDB_ID": pdb_id,
                        "source": "Crossref",
                        "id": "",
                        "citedOrder": index,
                        "title": clean(reference.get("article-title") or reference.get("unstructured")),
                        "DOI": clean(reference.get("DOI")),
                        "authorString": clean(reference.get("author")),
                        "journalAbbreviation": clean(reference.get("journal-title")),
                        "pubYear": clean(reference.get("year")),
                    }
                )
        license_rows = cr.get("license") or []
        oa_candidates = []
        for item in (
            ((europe.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
        ):
            if clean(item.get("availabilityCode")).upper() == "OA":
                oa_candidates.append(
                    {
                        "PDB_ID": pdb_id,
                        "site": clean(item.get("site")),
                        "style": clean(item.get("documentStyle")),
                        "url": clean(item.get("url")),
                    }
                )
        manifest["entries"][pdb_id] = {
            "title": clean((entry.get("struct") or {}).get("title")),
            "citation": fields,
            "europepmc": europe,
            "crossref": {
                "title": cr.get("title"),
                "reference_count": cr.get("reference-count"),
                "license": license_rows,
                "links": cr.get("link"),
            },
            "ligands": [
                {key: value for key, value in ligand.items() if key != "chemcomp_json"}
                for ligand in ligands
            ],
            "oa_candidates": oa_candidates,
            "supplementary_links": [],
        }
        if not fields["doi"] and not fields["pmid"]:
            blockers.append({"PDB_ID": pdb_id, "blocker": "no_primary_citation_identifier"})
        if fields["doi"] and not europe.get("result"):
            blockers.append({"PDB_ID": pdb_id, "blocker": "europepmc_no_match", "doi": fields["doi"]})
        if epmc_result and clean(epmc_result.get("isOpenAccess")) != "Y":
            blockers.append({"PDB_ID": pdb_id, "blocker": "subscription_or_unresolved_fulltext"})

        if args.download_oa:
            downloaded_pdf = False
            for index, candidate in enumerate(oa_candidates):
                result = download_url(
                    candidate["url"],
                    args.output_dir / "fulltext",
                    f"{pdb_id}_oa_{index}",
                    args.timeout,
                    max_bytes,
                )
                download_rows.append({"PDB_ID": pdb_id, "kind": "oa_pdf", **result})
                if result.get("status") == "downloaded" and result.get("path", "").endswith(".pdf"):
                    downloaded_pdf = True
                    break
            pmcid = clean(epmc_result.get("pmcid"))
            if pmcid:
                xml_url = EUROPEPMC_XML.format(pmcid=pmcid)
                try:
                    xml_body, _, _ = fetch(xml_url, timeout=args.timeout, accept="application/xml")
                    xml_path = args.output_dir / "fulltext" / f"{pdb_id}_{pmcid}.xml"
                    xml_path.write_bytes(xml_body)
                    links = supplementary_links(xml_body)
                    manifest["entries"][pdb_id]["supplementary_links"] = links
                    download_rows.append(
                        {
                            "PDB_ID": pdb_id,
                            "kind": "europepmc_xml",
                            "status": "downloaded",
                            "path": str(xml_path),
                            "bytes": len(xml_body),
                        }
                    )
                except Exception as error:
                    blockers.append(
                        {"PDB_ID": pdb_id, "blocker": "europepmc_xml_failed", "detail": str(error)}
                    )
                    links = []
                if args.download_supplements:
                    supp_result = download_url(
                        EUROPEPMC_SUPP.format(pmcid=pmcid),
                        args.output_dir / "fulltext",
                        f"{pdb_id}_{pmcid}_supplementaryFiles",
                        args.timeout,
                        max_bytes,
                    )
                    download_rows.append(
                        {
                            "PDB_ID": pdb_id,
                            "kind": "europepmc_supplement_zip",
                            **supp_result,
                        }
                    )
                    if supp_result.get("status") != "downloaded":
                        blockers.append(
                            {
                                "PDB_ID": pdb_id,
                                "blocker": "supplement_archive_not_downloaded",
                                "detail": supp_result.get("error")
                                or supp_result.get("status"),
                            }
                        )
                    elif supp_result.get("path", "").lower().endswith(".zip"):
                        for extracted in extract_zip_archive(
                            Path(supp_result["path"]),
                            args.output_dir / "fulltext" / f"{pdb_id}_supplement",
                            max_bytes,
                        ):
                            download_rows.append(
                                {
                                    "PDB_ID": pdb_id,
                                    "kind": "supplement_extracted",
                                    **extracted,
                                }
                            )
                            if extracted.get("status") != "extracted":
                                blockers.append(
                                    {
                                        "PDB_ID": pdb_id,
                                        "blocker": "supplement_zip_extract_failed",
                                        "detail": extracted.get("error", ""),
                                    }
                                )
                elif clean(epmc_result.get("hasSuppl")) == "Y":
                    blockers.append(
                        {
                            "PDB_ID": pdb_id,
                            "blocker": "supplement_not_downloaded",
                            "detail": "Run with --download-supplements or inspect the source page.",
                        }
                    )
            else:
                links = []
            if not downloaded_pdf and not pmcid:
                blockers.append({"PDB_ID": pdb_id, "blocker": "no_unblocked_fulltext_found"})
            for index, link in enumerate(links):
                if not link.startswith(("http://", "https://")):
                    link = urllib.parse.urljoin(
                        f"https://europepmc.org/articles/{pmcid}/",
                        link,
                    )
                result = download_url(
                    link,
                    args.output_dir / "fulltext",
                    f"{pdb_id}_supp_{index}",
                    args.timeout,
                    max_bytes,
                )
                download_rows.append({"PDB_ID": pdb_id, "kind": "supplement", **result})

        if args.fetch_web and fields["doi"]:
            jina_url = JINA_READER.format(url=urllib.parse.quote(f"https://doi.org/{fields['doi']}", safe=""))
            result = download_url(
                jina_url,
                args.output_dir / "web_pages",
                f"{pdb_id}_doi_reader",
                args.timeout,
                max_bytes,
            )
            download_rows.append({"PDB_ID": pdb_id, "kind": "jina_doi_reader", **result})

        queries = patent_queries(ligands, "influenza endonuclease")
        patent_query_rows.extend(queries)
        if args.patent_search:
            for query in queries:
                results, patent_error = google_patents(
                    query["query"],
                    args.timeout,
                    args.max_patents_per_query,
                )
                if patent_error:
                    blockers.append(
                        {
                            "PDB_ID": pdb_id,
                            "blocker": "patent_search_failed",
                            "detail": patent_error,
                            "query": query["query"],
                        }
                    )
                patent_candidate_rows.extend(
                    [{**query, **result} for result in results]
                )
                if args.download_patents:
                    for index, result in enumerate(results):
                        if not result.get("url"):
                            continue
                        jina_url = JINA_READER.format(
                            url=urllib.parse.quote(result["url"], safe="")
                        )
                        downloaded = download_url(
                            jina_url,
                            args.output_dir / "web_pages",
                            f"{pdb_id}_{result.get('publication_number') or index}_patent",
                            args.timeout,
                            max_bytes,
                        )
                        download_rows.append(
                            {"PDB_ID": pdb_id, "kind": "patent_page", **downloaded}
                        )

    for ligand in ligand_rows:
        component_json = ligand.pop("chemcomp_json", None)
        if component_json:
            write_json(
                args.output_dir
                / "metadata"
                / "chemcomp"
                / f"{ligand.get('PDB_ID', 'unknown')}_{ligand.get('comp_id', 'unknown')}.json",
                component_json,
            )

    write_csv(args.output_dir / "pdb_citations.csv", citation_rows)
    write_csv(args.output_dir / "pdb_ligands.csv", ligand_rows)
    write_csv(args.output_dir / "pdb_polymers.csv", polymer_rows)
    write_csv(args.output_dir / "references.csv", reference_rows)
    write_csv(args.output_dir / "patent_queries.csv", patent_query_rows)
    write_csv(args.output_dir / "patent_candidates.csv", patent_candidate_rows)
    write_csv(args.output_dir / "downloads.csv", download_rows)
    write_csv(args.output_dir / "blockers.csv", blockers)
    write_csv(args.output_dir / "review_checklist.csv", review_checklist)
    write_json(args.output_dir / "evidence_manifest.json", manifest)

    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "pdbs": len(ids),
                "ligands": len(ligand_rows),
                "references": len(reference_rows),
                "patent_queries": len(patent_query_rows),
                "patent_candidates": len(patent_candidate_rows),
                "downloads": len(download_rows),
                "blockers": len(blockers),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
