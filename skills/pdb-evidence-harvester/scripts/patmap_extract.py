#!/usr/bin/env python3
"""Submit PDFs to AlphaMa PatMap and download structured outputs.

PatMap is a licensed service. Set PATMAP_API_KEY in the environment and use the
account only within its subscription terms. This script does not search for
patents; it extracts structures, reactions and coreferences from patent or
paper PDFs that the user is authorized to process.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import zipfile
from pathlib import Path

import requests


BASE = "https://patmap.alphama.com.cn/prod-api/apps/api/patmap"


def api_headers() -> dict[str, str]:
    token = os.environ.get("PATMAP_API_KEY", "").strip()
    if not token:
        raise SystemExit("PATMAP_API_KEY is not set")
    return {"X-Auth-Token": token}


def create_task(
    pdf: Path,
    functions: list[str],
    version: str,
    headers: dict[str, str],
    timeout: int,
) -> str:
    with pdf.open("rb") as handle:
        response = requests.post(
            f"{BASE}/execute",
            headers=headers,
            files={"file": (pdf.name, handle, "application/pdf")},
            data={
                "version": version,
                "compute_function": ",".join(functions),
            },
            timeout=timeout,
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"PatMap submit failed: {payload}")
    task_id = payload.get("taskId") or (payload.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"PatMap response has no taskId: {payload}")
    return str(task_id)


def wait_for_task(
    task_id: str,
    headers: dict[str, str],
    interval: int,
    max_wait: int,
) -> dict:
    elapsed = 0
    last_status = ""
    while elapsed <= max_wait:
        response = requests.get(
            f"{BASE}/task_status",
            headers=headers,
            params={"taskId": task_id},
            timeout=180,
        )
        response.raise_for_status()
        raw = response.json()
        data = raw.get("data") or {}
        status = data.get("status") or ""
        if status != last_status:
            print(f"[{elapsed:4d}s] {task_id}: {status}", flush=True)
            last_status = status
        if status == "SUCCESS":
            return data
        if status in {"FAILED", "ERROR"}:
            raise RuntimeError(f"PatMap task failed: {raw}")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"PatMap task timed out after {max_wait}s: {task_id}")


def safe_extract(zip_path: Path, destination: Path) -> list[str]:
    extracted = []
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"unsafe zip member: {member.filename}")
            if member.is_dir():
                continue
            archive.extract(member, destination)
            extracted.append(str(target))
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--functions", default="table,reaction,coreferences")
    parser.add_argument("--version", default="1.6")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--max-wait", type=int, default=3600)
    args = parser.parse_args()

    headers = api_headers()
    functions = [item.strip() for item in args.functions.split(",") if item.strip()]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    zip_dir = args.output_dir / "zip"
    extracted_dir = args.output_dir / "extracted"
    zip_dir.mkdir(exist_ok=True)
    extracted_dir.mkdir(exist_ok=True)
    manifest = []

    for pdf in args.pdfs:
        if not pdf.exists():
            manifest.append(
                {"pdf": str(pdf), "status": "missing_input", "task_id": ""}
            )
            continue
        try:
            task_id = create_task(
                pdf,
                functions,
                args.version,
                headers,
                args.timeout,
            )
            data = wait_for_task(
                task_id,
                headers,
                args.interval,
                args.max_wait,
            )
            download_url = data.get("downloadUrl")
            if not download_url:
                manifest.append(
                    {
                        "pdf": str(pdf),
                        "status": "success_no_download_url",
                        "task_id": task_id,
                        "functions": functions,
                    }
                )
                continue
            response = requests.get(download_url, timeout=1800)
            response.raise_for_status()
            zip_path = zip_dir / f"{task_id}.zip"
            zip_path.write_bytes(response.content)
            task_dir = extracted_dir / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            extracted = safe_extract(zip_path, task_dir)
            manifest.append(
                {
                    "pdf": str(pdf),
                    "status": "success",
                    "task_id": task_id,
                    "functions": functions,
                    "zip_path": str(zip_path),
                    "extracted": extracted,
                }
            )
        except Exception as error:
            manifest.append(
                {
                    "pdf": str(pdf),
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    (args.output_dir / "patmap_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
