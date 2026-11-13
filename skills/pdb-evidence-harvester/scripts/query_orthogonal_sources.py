#!/usr/bin/env python3
"""Query official interaction/complex APIs without creating affinity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USER_AGENT = "pdb-affinity-curation-mcp/orthogonal-sources-1.0"

SOURCE_META = {
    "intact": {
        "evidence_role": "experimental_interaction_context",
        "allowed_use": ["binary interaction context", "experimental evidence codes", "host-virus network clues"],
        "prohibited_use": ["affinity", "stoichiometry without evidence", "causal direction without evidence"],
    },
    "corum": {
        "evidence_role": "complex_context",
        "allowed_use": ["manually curated mammalian complex membership", "host complex context"],
        "prohibited_use": ["viral affinity", "binary interaction direction", "non-mammalian complex inference"],
    },
    "string": {
        "evidence_role": "aggregated_association_context",
        "allowed_use": ["network neighbourhood", "identifier mapping", "functional enrichment"],
        "prohibited_use": ["affinity", "binary experimental evidence", "causal direction"],
    },
    "biogrid": {
        "evidence_role": "curated_interaction_context",
        "allowed_use": ["curated physical or genetic interaction context", "orthogonal interaction lookup"],
        "prohibited_use": ["affinity", "potency transfer", "inferring construct identity"],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str, timeout: int, *, allow_insecure_tls: bool = False) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl._create_unverified_context() if allow_insecure_tls else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read()
            text = raw.decode("utf-8", "replace")
            return {
                "status": "ok",
                "http_status": response.status,
                "payload": json.loads(text) if text.strip() else None,
                "payload_sha256": hashlib.sha256(raw).hexdigest(),
                "payload_bytes": len(raw),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": "blocked_http_error",
            "http_status": error.code,
            "error": error.read().decode("utf-8", "replace")[:1000],
        }
    except Exception as error:  # noqa: BLE001 - recorded as a source blocker
        return {
            "status": "blocked_request_error",
            "http_status": None,
            "error": f"{type(error).__name__}: {error}",
        }


def redacted_url(url: str, secret: str) -> str:
    return url.replace(secret, "***") if secret else url


def query_intact(identifier: str, limit: int, timeout: int) -> dict[str, Any]:
    url = (
        "https://www.ebi.ac.uk/intact/ws/interaction/findInteractions/"
        f"{urllib.parse.quote(identifier, safe='')}?page=0&pageSize={limit}"
    )
    result = fetch_json(url, timeout)
    payload = result.get("payload")
    count = len(payload.get("content", [])) if isinstance(payload, dict) else None
    return {"source": "IntAct", "query": identifier, "url": url, "result_count": count, **result}


def query_string(identifier: str, taxid: str | None, limit: int, timeout: int) -> dict[str, Any]:
    if not taxid:
        return {
            "source": "STRING",
            "query": identifier,
            "status": "blocked_missing_taxid",
            "error": "STRING queries require an NCBI taxid for the selected organism.",
            "result_count": None,
        }
    params = urllib.parse.urlencode({
        "identifiers": identifier,
        "species": taxid,
        "limit": limit,
        "caller_identity": "pdb-affinity-curation-mcp",
    })
    url = f"https://string-db.org/api/json/get_string_ids?{params}"
    result = fetch_json(url, timeout)
    payload = result.get("payload")
    count = len(payload) if isinstance(payload, list) else None
    return {"source": "STRING", "query": identifier, "taxid": taxid, "url": url, "result_count": count, **result}


def query_corum(identifier: str, limit: int, timeout: int, allow_insecure_tls: bool) -> dict[str, Any]:
    params = urllib.parse.urlencode({"value": identifier, "page": 1, "size": limit})
    url = f"https://mips.helmholtz-muenchen.de/fastapi-corum/public/search?{params}"
    result = fetch_json(url, timeout, allow_insecure_tls=allow_insecure_tls)
    payload = result.get("payload")
    count = payload.get("total") if isinstance(payload, dict) else None
    return {"source": "CORUM", "query": identifier, "url": url, "result_count": count, **result}


def query_biogrid(identifier: str, taxid: str | None, limit: int, timeout: int) -> dict[str, Any]:
    access_key = os.environ.get("BIOGRID_ACCESS_KEY", "").strip()
    if not access_key:
        return {
            "source": "BioGRID",
            "query": identifier,
            "status": "blocked_missing_api_key",
            "error": "BIOGRID_ACCESS_KEY is not configured; use the official bulk download or an authorized export.",
            "result_count": None,
        }
    params = {"geneList": identifier, "accesskey": access_key, "format": "json", "max": limit}
    if taxid:
        params["taxId"] = taxid
    url = "https://webservice.thebiogrid.org/interactions/?" + urllib.parse.urlencode(params)
    result = fetch_json(url, timeout)
    payload = result.get("payload")
    count = len(payload) if isinstance(payload, dict) else None
    return {
        "source": "BioGRID",
        "query": identifier,
        "taxid": taxid,
        "url": redacted_url(url, access_key),
        "result_count": count,
        **result,
    }


def summarize_payload(source: str, result: dict[str, Any], include_raw: bool) -> dict[str, Any]:
    output = dict(result)
    payload = output.get("payload")
    if include_raw or payload is None:
        return output
    output.pop("payload", None)
    items = []
    if source == "IntAct" and isinstance(payload, dict):
        output["total_count"] = payload.get("totalElements")
        for item in payload.get("content", []):
            items.append({
                "interaction_id": item.get("ac"),
                "binary_interaction_id": item.get("binaryInteractionId"),
                "molecule_a": item.get("moleculeA"),
                "molecule_b": item.get("moleculeB"),
                "accession_a": item.get("uniqueIdA"),
                "accession_b": item.get("uniqueIdB"),
                "taxid_a": item.get("taxIdA"),
                "taxid_b": item.get("taxIdB"),
                "mutation_a": item.get("mutationA"),
                "mutation_b": item.get("mutationB"),
            })
    elif source == "CORUM" and isinstance(payload, dict):
        output["total_count"] = payload.get("total")
        items = payload.get("items", [])
    elif source == "STRING" and isinstance(payload, list):
        output["total_count"] = len(payload)
        items = [
            {
                "query_item": item.get("queryItem"),
                "string_id": item.get("stringId"),
                "preferred_name": item.get("preferredName"),
                "ncbi_taxon_id": item.get("ncbiTaxonId"),
            }
            for item in payload
        ]
    else:
        output["total_count"] = len(payload) if isinstance(payload, (dict, list)) else None
    output["items"] = items
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifier", action="append", default=[])
    parser.add_argument("--identifier-file", type=Path)
    parser.add_argument("--taxid")
    parser.add_argument("--sources", default="intact,corum,string,biogrid")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--allow-insecure-tls", action="store_true")
    parser.add_argument("--include-raw-payloads", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    identifiers = [item.strip() for item in args.identifier if item.strip()]
    if args.identifier_file:
        identifiers.extend(
            line.strip()
            for line in args.identifier_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers:
        raise SystemExit("at least one identifier is required")

    sources = [item.strip().lower() for item in args.sources.split(",") if item.strip()]
    unknown = [source for source in sources if source not in SOURCE_META]
    if unknown:
        raise SystemExit(f"unknown sources: {', '.join(unknown)}")

    results = []
    for identifier in identifiers:
        for source in sources:
            if source == "intact":
                result = query_intact(identifier, args.limit, args.timeout)
            elif source == "corum":
                result = query_corum(identifier, args.limit, args.timeout, args.allow_insecure_tls)
            elif source == "string":
                result = query_string(identifier, args.taxid, args.limit, args.timeout)
            else:
                result = query_biogrid(identifier, args.taxid, args.limit, args.timeout)
            results.append({
                **summarize_payload(result.get("source", ""), result, args.include_raw_payloads),
                **SOURCE_META[source],
            })

    manifest = {
        "schema_version": "orthogonal-sources-v1",
        "fetched_at": utc_now(),
        "principle": "Interaction and complex context are not affinity or potency evidence.",
        "identifiers": identifiers,
        "taxid": args.taxid,
        "sources": sources,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
