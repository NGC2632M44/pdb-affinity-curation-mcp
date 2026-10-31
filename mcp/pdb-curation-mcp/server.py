#!/usr/bin/env python3
"""Zero-dependency MCP server for traceable PDB affinity curation.

The server wraps the installed pdb-evidence-harvester, pdb-affinity-analysis,
pdb-affinity-output and pdb-affinity-curation skill scripts. It deliberately
uses newline-delimited JSON-RPC over stdio and has no runtime dependency beyond
the Python standard library and optional libraries already used by the skills.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SERVER_NAME = "pdb-curation"
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION}
ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs"
SCHEMAS_DIR = ROOT / "schemas"

SKILL_NAMES = (
    "pdb-evidence-harvester",
    "pdb-affinity-analysis",
    "pdb-affinity-output",
    "pdb-affinity-curation",
)

SKILL_SCRIPTS = {
    "fetch_pdb_evidence": (
        "pdb-evidence-harvester",
        "scripts/fetch_pdb_evidence.py",
    ),
    "resolve_local_sources": (
        "pdb-evidence-harvester",
        "scripts/resolve_local_sources.py",
    ),
    "identity_resolver": (
        "pdb-evidence-harvester",
        "scripts/identity_resolver.py",
    ),
    "resolve_blockers": (
        "pdb-evidence-harvester",
        "scripts/resolve_blockers.py",
    ),
    "patmap_extract": (
        "pdb-evidence-harvester",
        "scripts/patmap_extract.py",
    ),
    "extract_document_text": (
        "pdb-affinity-analysis",
        "scripts/extract_document_text.py",
    ),
    "build_visual_review": (
        "pdb-affinity-analysis",
        "scripts/build_visual_review.py",
    ),
    "validate_affinity_dataset": (
        "pdb-affinity-output",
        "scripts/validate_affinity_dataset.py",
    ),
    "compare_to_human": (
        "pdb-affinity-output",
        "scripts/compare_to_human.py",
    ),
    "crosscheck_sources": (
        "pdb-affinity-output",
        "scripts/crosscheck_sources.py",
    ),
    "compare_curated_versions": (
        "pdb-affinity-output",
        "scripts/compare_curated_versions.py",
    ),
    "run_acceptance": (
        "pdb-affinity-curation",
        "scripts/run_acceptance.py",
    ),
}

_CALL_STATE_LOCK = threading.Lock()
_CALL_CANCEL_EVENTS: dict[Any, threading.Event] = {}
_CALL_PROCESSES: dict[Any, subprocess.Popen[str]] = {}
_CURRENT_REQUEST_ID: ContextVar[Any] = ContextVar(
    "pdb_curation_request_id",
    default=None,
)
_WRITE_LOCK = threading.Lock()
_LEDGER_LOCKS_GUARD = threading.Lock()
_LEDGER_LOCKS: dict[str, threading.Lock] = {}


class ToolFailure(RuntimeError):
    """A user-actionable MCP tool failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=json_default,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def skill_roots() -> list[Path]:
    roots = []
    explicit = os.environ.get("PDB_SKILL_ROOT")
    if explicit:
        roots.append(Path(explicit).expanduser())
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    home = Path.home()
    roots.extend(
        [
            home / ".codex" / "skills",
            home / ".agents" / "skills",
        ]
    )
    output = []
    seen = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        output.append(root)
    return output


def find_skill(name: str) -> Path | None:
    for root in skill_roots():
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def find_script(tool_name: str) -> Path:
    skill_name, relative = SKILL_SCRIPTS[tool_name]
    skill = find_skill(skill_name)
    if not skill:
        raise ToolFailure(
            f"Skill not found: {skill_name}. Set PDB_SKILL_ROOT or install it "
            "under CODEX_HOME/skills, ~/.codex/skills, or ~/.agents/skills."
        )
    script = skill / relative
    if not script.exists():
        raise ToolFailure(f"Skill script missing: {script}")
    return script


def ensure_dir(path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    return output


def write_pdb_list(output_dir: Path, pdb_ids: list[str]) -> Path:
    normalized = []
    for value in pdb_ids:
        pdb = str(value).strip().upper()
        if not re.fullmatch(r"[0-9A-Z]{4}", pdb):
            raise ToolFailure(f"Invalid PDB ID: {value!r}")
        normalized.append(pdb)
    if not normalized:
        raise ToolFailure("pdb_ids is empty")
    path = output_dir / "pdbs.txt"
    path.write_text(
        "\n".join(dict.fromkeys(normalized)) + "\n",
        encoding="utf-8",
    )
    return path


def run_python_script(
    tool_name: str,
    arguments: list[str],
    *,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    script = find_script(tool_name)
    command = [sys.executable, str(script), *arguments]
    allowed_env = (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "TZ",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    )
    process_env = {
        key: os.environ[key]
        for key in allowed_env
        if key in os.environ
    }
    if tool_name == "patmap_extract" and os.environ.get("PATMAP_API_KEY"):
        process_env["PATMAP_API_KEY"] = os.environ["PATMAP_API_KEY"]
    process_env["PYTHONUTF8"] = "1"
    process_env["PYTHONIOENCODING"] = "utf-8"
    started = utc_now()
    request_id = _CURRENT_REQUEST_ID.get()
    with _CALL_STATE_LOCK:
        cancel_event = (
            _CALL_CANCEL_EVENTS.get(request_id)
            if request_id is not None
            else None
        )
    if cancel_event is not None and cancel_event.is_set():
        return {
            "ok": False,
            "tool": tool_name,
            "started_at": started,
            "command": command,
            "error": "cancelled_before_start",
        }
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
        )
        if request_id is not None:
            with _CALL_STATE_LOCK:
                _CALL_PROCESSES[request_id] = process
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        cancelled = False
        timed_out = False
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    process.kill()
                    stdout, stderr = process.communicate()
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    process.kill()
                    stdout, stderr = process.communicate()
                    break
    except Exception as error:
        return {
            "ok": False,
            "tool": tool_name,
            "started_at": started,
            "timeout_seconds": timeout_seconds,
            "command": command,
            "stdout": "",
            "stderr": "",
            "error": str(error),
            "error_type": type(error).__name__,
        }
    finally:
        if request_id is not None:
            with _CALL_STATE_LOCK:
                _CALL_PROCESSES.pop(request_id, None)
    if cancelled:
        return {
            "ok": False,
            "tool": tool_name,
            "started_at": started,
            "finished_at": utc_now(),
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "error": "cancelled",
        }
    if timed_out:
        return {
            "ok": False,
            "tool": tool_name,
            "started_at": started,
            "timeout_seconds": timeout_seconds,
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "error": "timeout",
        }
    return {
        "ok": process.returncode == 0,
        "tool": tool_name,
        "started_at": started,
        "finished_at": utc_now(),
        "return_code": process.returncode,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
    }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def missing_taxonomy() -> dict[str, Any]:
    return load_json(SCHEMAS_DIR / "missing_reasons.json")


def database_config() -> dict[str, Any]:
    return load_json(CONFIG_DIR / "databases.json")


def environment_check(_: dict[str, Any]) -> dict[str, Any]:
    script_status = {}
    for name in SKILL_SCRIPTS:
        try:
            path = find_script(name)
            script_status[name] = {"ok": True, "path": str(path)}
        except ToolFailure as error:
            script_status[name] = {"ok": False, "error": str(error)}
    modules = {}
    for module in (
        "openpyxl",
        "pypdf",
        "docx",
        "requests",
        "pandas",
        "PIL",
    ):
        modules[module] = bool(importlib.util.find_spec(module))
    tools = {
        name: shutil.which(name)
        for name in (
            "pdftoppm",
            "pdfinfo",
            "pdftotext",
            "powershell.exe",
            "pwsh",
            "tesseract",
        )
    }
    return {
        "ok": all(item["ok"] for item in script_status.values()),
        "server": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "skill_roots": [str(path) for path in skill_roots()],
        "skills": {
            name: str(path) if (path := find_skill(name)) else None
            for name in SKILL_NAMES
        },
        "scripts": script_status,
        "python_modules": modules,
        "external_tools": tools,
        "optional_environment": {
            "PATMAP_API_KEY": bool(os.environ.get("PATMAP_API_KEY")),
        },
        "databases": database_config(),
    }


def fetch_pdb_evidence(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    command_args = []
    pdb_ids = args.get("pdb_ids") or []
    pdb_list = args.get("pdb_list")
    if pdb_ids and pdb_list:
        raise ToolFailure("Use either pdb_ids or pdb_list, not both")
    if pdb_ids:
        pdb_list = str(write_pdb_list(output_dir, pdb_ids))
    if not pdb_list:
        raise ToolFailure("pdb_ids or pdb_list is required")
    command_args.extend(["--pdb-list", str(Path(pdb_list).resolve())])
    command_args.extend(["--output-dir", str(output_dir)])
    for key, flag in (
        ("download_oa", "--download-oa"),
        ("download_supplements", "--download-supplements"),
        ("fetch_web", "--fetch-web"),
        ("patent_search", "--patent-search"),
        ("download_patents", "--download-patents"),
    ):
        if args.get(key):
            command_args.append(flag)
    for patent_id in args.get("patent_ids") or []:
        command_args.extend(["--patent-id", str(patent_id)])
    for key, flag in (
        ("timeout_seconds", "--timeout"),
        ("max_download_mb", "--max-download-mb"),
        ("max_patents_per_query", "--max-patents-per-query"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    return run_python_script(
        "fetch_pdb_evidence",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 3600)),
    )


def resolve_local_sources(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    source_dirs = args.get("source_dirs") or []
    if not source_dirs:
        source_dirs = [
            str(Path.home() / "Zotero" / "storage"),
            str(Path.home() / "Downloads"),
        ]
    command_args = []
    for source_dir in source_dirs:
        command_args.extend(["--source-dir", str(Path(source_dir).expanduser())])
    for key, flag in (
        ("references", "--references"),
        ("pdb_citations", "--pdb-citations"),
        ("timeout_seconds", "--timeout"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    if args.get("resolve_pmids"):
        command_args.append("--resolve-pmids")
    command_args.extend(["--output-dir", str(output_dir)])
    return run_python_script(
        "resolve_local_sources",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 1800)),
    )


def extract_document_text(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    documents = [str(Path(item).expanduser().resolve()) for item in args.get("documents") or []]
    if not documents:
        raise ToolFailure("documents is required")
    command_args = [*documents, "--output-dir", str(output_dir)]
    if args.get("terms"):
        command_args.extend(["--terms", str(args["terms"])])
    for key, flag in (
        ("ocr", "--ocr"),
        ("ocr_consensus", "--ocr-consensus"),
        ("dpi", "--dpi"),
        ("page_limit", "--page-limit"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    if args.get("render_pages"):
        command_args.append("--render-pages")
    return run_python_script(
        "extract_document_text",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 7200)),
    )


def build_visual_review(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    queues = [str(Path(item).expanduser().resolve()) for item in args.get("queues") or []]
    if not queues:
        raise ToolFailure("queues is required")
    command_args = [*queues, "--output-dir", str(output_dir)]
    for key, flag in (
        ("columns", "--columns"),
        ("max_pages", "--max-pages"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    return run_python_script(
        "build_visual_review",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 1800)),
    )


def resolve_identity(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    command_args = []
    pdb_ids = args.get("pdb_ids") or []
    pdb_list = args.get("pdb_list")
    provided_ids = sum(
        bool(value) for value in (args.get("pdb_id"), pdb_ids, pdb_list)
    )
    if provided_ids > 1:
        raise ToolFailure("Use exactly one of pdb_id, pdb_ids or pdb_list")
    if pdb_ids:
        pdb_list = str(write_pdb_list(output_dir, pdb_ids))
    if pdb_list:
        command_args.extend(["--pdb-list", str(Path(pdb_list).resolve())])
    elif not args.get("pdb_id"):
        raise ToolFailure("pdb_id, pdb_ids, or pdb_list is required")
    if args.get("pdb_id"):
        command_args.extend(["--pdb-id", str(args["pdb_id"]).upper()])
    if args.get("text"):
        command_args.extend(["--text", str(Path(args["text"]).resolve())])
    for key, flag in (
        ("timeout_seconds", "--timeout"),
        ("peer_limit", "--peer-limit"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    command_args.extend(["--output-dir", str(output_dir)])
    return run_python_script(
        "identity_resolver",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 1800)),
    )


def resolve_blockers(args: dict[str, Any]) -> dict[str, Any]:
    evidence_pack = Path(args["evidence_pack"]).expanduser().resolve()
    output = Path(args["output"]).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command_args = ["--evidence-pack", str(evidence_pack)]
    for key, flag in (
        ("identity_resolution", "--identity-resolution"),
        ("source_register", "--source-register"),
        ("database_ledger", "--database-ledger"),
        ("browser_manifest", "--browser-manifest"),
    ):
        if args.get(key):
            command_args.extend([flag, str(Path(args[key]).expanduser().resolve())])
    command_args.extend(["--output", str(output)])
    return run_python_script(
        "resolve_blockers",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 600)),
    )


def patmap_extract(args: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("PATMAP_API_KEY"):
        raise ToolFailure(
            "PATMAP_API_KEY is not configured; use public patent text or an "
            "authorized manual export and record the blocker/result."
        )
    output_dir = ensure_dir(args["output_dir"])
    pdfs = [str(Path(item).expanduser().resolve()) for item in args.get("pdfs") or []]
    if not pdfs:
        raise ToolFailure("pdfs is required")
    command_args = [*pdfs, "--output-dir", str(output_dir)]
    for key, flag in (
        ("functions", "--functions"),
        ("version", "--version"),
        ("timeout_seconds", "--timeout"),
        ("interval", "--interval"),
        ("max_wait", "--max-wait"),
    ):
        if args.get(key) is not None:
            command_args.extend([flag, str(args[key])])
    return run_python_script(
        "patmap_extract",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 3600)),
    )


def validate_affinity_dataset(args: dict[str, Any]) -> dict[str, Any]:
    csv_path = str(Path(args["csv_path"]).expanduser().resolve())
    command_args = [csv_path]
    if args.get("output"):
        command_args.extend(["--output", str(Path(args["output"]).expanduser().resolve())])
    if args.get("require_fulltext_acceptance"):
        command_args.append("--require-fulltext-acceptance")
    for key, flag in (
        ("review_checklist", "--review-checklist"),
        ("blocker_ledger", "--blocker-ledger"),
        ("source_root", "--source-root"),
    ):
        if args.get(key):
            command_args.extend([flag, str(Path(args[key]).expanduser().resolve())])
    if args.get("require_blocker_closure"):
        command_args.append("--require-blocker-closure")
    return run_python_script(
        "validate_affinity_dataset",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 600)),
    )


def curated_pdb_ids(path: str | Path) -> list[str]:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ToolFailure(f"Curated CSV not found: {source}")
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        normalized = {header.lower(): header for header in headers}
        pdb_header = next(
            (
                normalized[key]
                for key in ("pdb_id", "pdb", "pdbid", "pdb_code")
                if key in normalized
            ),
            None,
        )
        if not pdb_header:
            raise ToolFailure(
                f"No PDB identifier column found in curated CSV: {source}"
            )
        pdbs = []
        for row in reader:
            value = str(row.get(pdb_header) or "").strip().upper()
            if re.fullmatch(r"[0-9A-Z]{4}", value):
                pdbs.append(value)
    return list(dict.fromkeys(pdbs))


def comparison_pdb_ids(
    args: dict[str, Any],
    curated_path: str | Path,
) -> tuple[list[str], str]:
    explicit = [
        str(value).strip().upper()
        for value in args.get("pdb_ids") or []
        if str(value).strip()
    ]
    if explicit:
        return explicit, "explicit"
    if args.get("use_all_human_rows"):
        return [], "all_human_rows"
    inferred = curated_pdb_ids(curated_path)
    if not inferred:
        raise ToolFailure("No valid PDB IDs found in curated CSV")
    return inferred, "inferred_from_curated_csv"


def compare_to_human(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    curated = str(Path(args["curated"]).expanduser().resolve())
    pdb_ids, pdb_id_source = comparison_pdb_ids(args, curated)
    command_args = [
        "--curated",
        curated,
        "--human-workbook",
        str(Path(args["human_workbook"]).expanduser().resolve()),
    ]
    for key, flag in (
        ("human_sheet", "--human-sheet"),
        ("audit_sheet", "--audit-sheet"),
        ("correction_log", "--correction-log"),
    ):
        if args.get(key):
            value = (
                str(Path(args[key]).expanduser().resolve())
                if key == "correction_log"
                else str(args[key])
            )
            command_args.extend([flag, value])
    for pdb_id in pdb_ids:
        command_args.extend(["--pdb-id", str(pdb_id).upper()])
    command_args.extend(["--output-dir", str(output_dir)])
    result = run_python_script(
        "compare_to_human",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 1200)),
    )
    result["pdb_ids_used"] = pdb_ids
    result["pdb_id_source"] = pdb_id_source
    return result


def crosscheck_sources(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    command_args = [
        "--curated",
        str(Path(args["curated"]).expanduser().resolve()),
    ]
    for name, path in (args.get("sources") or {}).items():
        command_args.extend(
            [
                "--source",
                f"{name}={Path(path).expanduser().resolve()}",
            ]
        )
    command_args.extend(["--output-dir", str(output_dir)])
    return run_python_script(
        "crosscheck_sources",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 1200)),
    )


def compare_curated_versions(args: dict[str, Any]) -> dict[str, Any]:
    command_args = [
        "--old",
        str(Path(args["old"]).expanduser().resolve()),
        "--new",
        str(Path(args["new"]).expanduser().resolve()),
    ]
    for key in args.get("keys") or ["PDB_ID"]:
        command_args.extend(["--key", str(key)])
    command_args.extend(
        ["--output", str(Path(args["output"]).expanduser().resolve())]
    )
    return run_python_script(
        "compare_curated_versions",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 600)),
    )


def run_acceptance(args: dict[str, Any]) -> dict[str, Any]:
    output_dir = ensure_dir(args["output_dir"])
    curated = str(Path(args["curated"]).expanduser().resolve())
    pdb_ids, pdb_id_source = comparison_pdb_ids(args, curated)
    missing_ledger_value = str(
        args.get("missing_disposition_ledger") or ""
    ).strip()
    if not missing_ledger_value:
        raise ToolFailure(
            "missing_disposition_ledger is required for MCP final acceptance"
        )
    missing_ledger = Path(missing_ledger_value).expanduser().resolve()
    coverage_table = Path(
        str(args.get("missing_coverage_table") or curated)
    ).expanduser().resolve()
    traceability = audit_missing_traceability(
        {
            "ledger_path": str(missing_ledger),
            "blocker_ledger": args.get("missing_blocker_ledger"),
            "database_ledger": args.get("database_ledger"),
            "output_path": str(output_dir / "missing_traceability.json"),
        }
    )
    coverage = audit_missing_coverage(
        {
            "table_path": str(coverage_table),
            "sheet": args.get("missing_coverage_sheet"),
            "pdb_column": args.get("missing_pdb_column") or "PDB_ID",
            "ligand_column": (
                args.get("missing_ligand_column")
                or "Ligand_ID"
            ),
            "value_column": args.get("missing_value_column") or "Value_nM",
            "context_column": args.get("missing_context_column"),
            "disposition_ledger": str(missing_ledger),
            "output_path": str(output_dir / "missing_coverage.json"),
        }
    )
    command_args = [
        "--curated",
        curated,
        "--review-checklist",
        str(Path(args["review_checklist"]).expanduser().resolve()),
        "--blocker-ledger",
        str(Path(args["blocker_ledger"]).expanduser().resolve()),
        "--source-root",
        str(Path(args["source_root"]).expanduser().resolve()),
        "--human-workbook",
        str(Path(args["human_workbook"]).expanduser().resolve()),
    ]
    for key, flag in (
        ("human_sheet", "--human-sheet"),
        ("audit_sheet", "--audit-sheet"),
        ("correction_log", "--correction-log"),
        ("visual_receipts", "--visual-receipts"),
    ):
        if args.get(key):
            value = (
                str(Path(args[key]).expanduser().resolve())
                if key in {"correction_log", "visual_receipts"}
                else str(args[key])
            )
            command_args.extend([flag, value])
    for pdb_id in pdb_ids:
        command_args.extend(["--pdb-id", str(pdb_id).upper()])
    command_args.extend(["--output-dir", str(output_dir)])
    result = run_python_script(
        "run_acceptance",
        command_args,
        timeout_seconds=int(args.get("process_timeout_seconds", 3600)),
    )
    final_path = output_dir / "final_acceptance.json"
    if final_path.exists():
        payload = load_json(final_path)
        checks = payload.setdefault("checks", {})
        checks["missing_traceability"] = traceability["ok"]
        checks["missing_coverage"] = coverage["ok"]
        payload["missing_traceability"] = traceability
        payload["missing_coverage"] = coverage
        payload["mcp_wrapper_version"] = SERVER_VERSION
        payload["status"] = (
            "PASS" if all(bool(value) for value in checks.values()) else "FAIL"
        )
        final_path.write_text(compact_json(payload), encoding="utf-8")
        result["final_acceptance_status"] = payload["status"]
        result["ok"] = result.get("ok") is True and payload["status"] == "PASS"
        result["return_code"] = 0 if result["ok"] else 1
    result["pdb_ids_used"] = pdb_ids
    result["pdb_id_source"] = pdb_id_source
    return result


def classify_missing_reason(args: dict[str, Any]) -> dict[str, Any]:
    taxonomy = missing_taxonomy()
    code = str(args.get("code") or "").strip()
    if code:
        if code not in taxonomy:
            raise ToolFailure(f"Unknown missing reason code: {code}")
        return {"ok": True, "taxonomy_version": 1, **taxonomy[code]}
    return {
        "ok": True,
        "taxonomy_version": 1,
        "reasons": taxonomy,
        "rule": (
            "Missing is a disposition, not a zero. Record both the reason code "
            "and the evidence or blocker that supports it."
        ),
    }


def _iter_rows_from_file(
    path: Path,
    sheet: str | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or []), list(reader)
    if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        try:
            import openpyxl
        except ImportError as error:
            raise ToolFailure("openpyxl is required to audit Excel files") from error
        workbook = openpyxl.load_workbook(
            path, read_only=True, data_only=True
        )
        if not sheet:
            sheet = workbook.sheetnames[0]
        if sheet not in workbook.sheetnames:
            raise ToolFailure(f"Sheet not found: {sheet}")
        worksheet = workbook[sheet]
        iterator = worksheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(iterator)]
        rows = [
            dict(zip(headers, values))
            for values in iterator
            if any(value not in (None, "") for value in values)
        ]
        return headers, rows
    raise ToolFailure("audit_missing_reasons supports CSV and XLSX files")


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def audit_missing_reasons(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["path"]).expanduser().resolve()
    if not path.exists():
        raise ToolFailure(f"Input file not found: {path}")
    sheet = args.get("sheet")
    headers, rows = _iter_rows_from_file(path, sheet)
    ligand_column = args.get("ligand_column")
    value_column = args.get("value_column")
    reason_column = args.get("reason_column")
    pdb_column = args.get("pdb_column")
    if not all((ligand_column, value_column, reason_column)):
        raise ToolFailure(
            "ligand_column, value_column and reason_column are required"
        )
    missing_reason_rows = []
    stats = Counter()
    for index, row in enumerate(rows, start=2):
        ligand = row.get(ligand_column)
        value = row.get(value_column)
        reason = str(row.get(reason_column) or "")
        missing_ligand = _is_blank(ligand)
        missing_value = _is_blank(value)
        has_ligand_reason = (
            "无配体" in reason or "无抑制剂配体" in reason
        )
        has_value_reason = "无数值" in reason
        if missing_ligand:
            stats["missing_ligand"] += 1
        if missing_value:
            stats["missing_value"] += 1
        if missing_ligand and not has_ligand_reason:
            stats["missing_ligand_without_reason"] += 1
            missing_reason_rows.append(
                {
                    "row": index,
                    "PDB": row.get(pdb_column) if pdb_column else "",
                    "issue": "missing_ligand_without_chinese_reason",
                }
            )
        if missing_value and not has_value_reason:
            stats["missing_value_without_reason"] += 1
            missing_reason_rows.append(
                {
                    "row": index,
                    "PDB": row.get(pdb_column) if pdb_column else "",
                    "issue": "missing_value_without_chinese_reason",
                }
            )
    output_path = args.get("output_path")
    report = {
        "ok": not missing_reason_rows,
        "path": str(path),
        "sheet": sheet,
        "columns": {
            "pdb": pdb_column,
            "ligand": ligand_column,
            "value": value_column,
            "reason": reason_column,
        },
        "rows": len(rows),
        "stats": dict(stats),
        "problems": missing_reason_rows[: int(args.get("max_report_rows", 100))],
    }
    if output_path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            compact_json(report), encoding="utf-8"
        )
        report["output_path"] = str(destination)
    return report


def _append_csv_row_unlocked(
    output: Path,
    fields: list[str],
    row: dict[str, Any],
    *,
    allow_added_columns: bool = False,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 0:
        with output.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing = list(reader.fieldnames or [])
            existing_rows = list(reader)
        if existing != fields:
            if not allow_added_columns or not set(existing).issubset(fields):
                raise ToolFailure(
                    "Existing ledger columns do not match this tool version: "
                    f"{output}. Expected {fields}; found {existing}. Use a new "
                    "ledger or migrate the columns explicitly."
                )
            temporary = output.with_name(output.name + ".tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for existing_row in existing_rows:
                    writer.writerow(
                        {field: existing_row.get(field, "") for field in fields}
                    )
            temporary.replace(output)
    exists = output.exists() and output.stat().st_size > 0
    with output.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _append_csv_row(
    output: Path,
    fields: list[str],
    row: dict[str, Any],
    *,
    allow_added_columns: bool = False,
) -> None:
    output_key = str(output.resolve())
    with _LEDGER_LOCKS_GUARD:
        ledger_lock = _LEDGER_LOCKS.setdefault(
            output_key,
            threading.Lock(),
        )
    with ledger_lock:
        _append_csv_row_unlocked(
            output,
            fields,
            row,
            allow_added_columns=allow_added_columns,
        )


def missing_disposition_fields() -> list[str]:
    return [
        "Record_ID",
        "PDB_ID",
        "Ligand_ID",
        "Context",
        "Field_Name",
        "Disposition",
        "Reason_Code",
        "Reason_Label_ZH",
        "Search_Scope",
        "Sources_Checked",
        "Source_Locator",
        "Evidence_Path",
        "Evidence_SHA256",
        "Evidence_Text",
        "Blocker_ID",
        "Query_ID",
        "Status",
        "Checked_At",
        "Reviewer",
        "Notes",
    ]


def record_missing_disposition(args: dict[str, Any]) -> dict[str, Any]:
    taxonomy = missing_taxonomy()
    reason_code = str(args["reason_code"]).strip()
    if reason_code not in taxonomy:
        raise ToolFailure(
            f"Unknown missing reason code: {reason_code}. "
            "Call classify_missing_reason to inspect the taxonomy."
        )
    pdb_id = str(args["pdb_id"]).strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4}", pdb_id):
        raise ToolFailure(f"Invalid PDB ID: {pdb_id!r}")
    field_name = str(args["field_name"]).strip()
    if field_name not in {"ligand", "affinity_value", "target", "sequence", "other"}:
        raise ToolFailure(
            "field_name must be ligand, affinity_value, target, sequence or other"
        )
    expected_disposition = (
        "no_ligand"
        if field_name == "ligand"
        else "no_value"
        if field_name == "affinity_value"
        else "not_applicable"
    )
    requested_disposition = str(args.get("disposition") or "").strip()
    if requested_disposition and requested_disposition != expected_disposition:
        raise ToolFailure(
            f"disposition is derived from field_name; expected "
            f"{expected_disposition}, got {requested_disposition}"
        )
    disposition = expected_disposition
    allowed_fields = set(
        taxonomy[reason_code].get("applies_to_fields") or []
    )
    if not allowed_fields:
        raise ToolFailure(
            f"Missing taxonomy has no applies_to_fields for {reason_code}"
        )
    if field_name not in allowed_fields:
        raise ToolFailure(
            f"reason_code {reason_code} applies to fields "
            f"{sorted(allowed_fields)}, not {field_name}"
        )
    taxonomy_status = taxonomy[reason_code]["default_status"]
    requested_status = str(args.get("status") or "").strip()
    if requested_status and requested_status != taxonomy_status:
        raise ToolFailure(
            f"status is controlled by reason_code {reason_code}; expected "
            f"{taxonomy_status}, got {requested_status}"
        )
    status = taxonomy_status
    search_scope = str(args.get("search_scope") or "").strip()
    sources_checked = str(args.get("sources_checked") or "").strip()
    source_locator = str(args.get("source_locator") or "").strip()
    evidence_path_value = str(args.get("evidence_path") or "").strip()
    evidence_text = str(args.get("evidence_text") or "").strip()
    blocker_id = str(args.get("blocker_id") or "").strip()
    query_id = str(args.get("query_id") or "").strip()
    if not search_scope:
        raise ToolFailure("search_scope is required")
    if not sources_checked:
        raise ToolFailure("sources_checked is required")
    if status.startswith("blocked") and not blocker_id:
        raise ToolFailure("A blocked disposition requires blocker_id")
    if (
        status.startswith("accepted")
        or status == "rejected_non_inhibitor"
    ) and not source_locator:
        raise ToolFailure(
            "Accepted or rejected dispositions require an exact source_locator"
        )
    if status == "needs_primary_source" and not source_locator:
        raise ToolFailure(
            "database_only_lead dispositions require the database result locator"
        )
    if reason_code == "database_only_lead" and not query_id:
        raise ToolFailure(
            "database_only_lead dispositions require a database Query_ID"
        )
    checked_at = str(args.get("checked_at") or utc_now())
    reviewer = str(args.get("reviewer") or "").strip()
    evidence_path = (
        Path(evidence_path_value).expanduser().resolve()
        if evidence_path_value
        else None
    )
    evidence_hash = str(args.get("evidence_sha256") or "").strip()
    if evidence_path:
        if not evidence_path.exists() or not evidence_path.is_file():
            raise ToolFailure(f"Evidence path not found: {evidence_path}")
        actual_hash = sha256_file(evidence_path)
        if evidence_hash and evidence_hash.lower() != actual_hash.lower():
            raise ToolFailure(
                "evidence_sha256 does not match the evidence file"
            )
        evidence_hash = actual_hash
    record_seed = "\x1f".join(
        (
            pdb_id,
            str(args.get("ligand_id") or ""),
            str(args.get("context") or ""),
            field_name,
            reason_code,
            search_scope,
            checked_at,
        )
    )
    record_id = str(args.get("record_id") or "").strip()
    if not record_id:
        record_id = "MD-" + hashlib.sha256(
            record_seed.encode("utf-8")
        ).hexdigest()[:16].upper()
    row = {
        "Record_ID": record_id,
        "PDB_ID": pdb_id,
        "Ligand_ID": args.get("ligand_id", ""),
        "Context": args.get("context", ""),
        "Field_Name": field_name,
        "Disposition": disposition,
        "Reason_Code": reason_code,
        "Reason_Label_ZH": taxonomy[reason_code]["label_zh"],
        "Search_Scope": search_scope,
        "Sources_Checked": sources_checked,
        "Source_Locator": source_locator,
        "Evidence_Path": str(evidence_path) if evidence_path else "",
        "Evidence_SHA256": evidence_hash,
        "Evidence_Text": evidence_text,
        "Blocker_ID": blocker_id,
        "Query_ID": query_id,
        "Status": status,
        "Checked_At": checked_at,
        "Reviewer": reviewer,
        "Notes": args.get("notes", ""),
    }
    output = Path(args["ledger_path"]).expanduser().resolve()
    _append_csv_row(
        output,
        missing_disposition_fields(),
        row,
        allow_added_columns=True,
    )
    return {
        "ok": True,
        "ledger_path": str(output),
        "row": row,
        "traceability_contract": (
            "The disposition records why the field is missing, the query scope, "
            "the sources actually checked, and either a locator/receipt or a "
            "blocker identifier."
        ),
    }


def audit_missing_traceability(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["ledger_path"]).expanduser().resolve()
    if not path.exists():
        raise ToolFailure(f"Missing-disposition ledger not found: {path}")
    blocker_ledger_path = args.get("blocker_ledger")
    blocker_pdbs: dict[str, str] | None = None
    if blocker_ledger_path:
        blocker_path = Path(blocker_ledger_path).expanduser().resolve()
        if not blocker_path.exists():
            raise ToolFailure(f"Blocker ledger not found: {blocker_path}")
        with blocker_path.open(encoding="utf-8-sig", newline="") as handle:
            blocker_reader = csv.DictReader(handle)
            if "Blocker_ID" not in (blocker_reader.fieldnames or []):
                raise ToolFailure(
                    f"Blocker ledger has no Blocker_ID column: {blocker_path}"
                )
            blocker_pdbs = {}
            for blocker_row in blocker_reader:
                blocker_id = str(
                    blocker_row.get("Blocker_ID") or ""
                ).strip()
                if blocker_id:
                    blocker_pdbs[blocker_id] = str(
                        blocker_row.get("PDB_ID") or ""
                    ).strip().upper()
    database_ledger_path = args.get("database_ledger")
    query_pdbs: dict[str, str] | None = None
    if database_ledger_path:
        database_path = Path(database_ledger_path).expanduser().resolve()
        if not database_path.exists():
            raise ToolFailure(f"Database ledger not found: {database_path}")
        with database_path.open(encoding="utf-8-sig", newline="") as handle:
            database_reader = csv.DictReader(handle)
            if "Query_ID" not in (database_reader.fieldnames or []):
                raise ToolFailure(
                    f"Database ledger has no Query_ID column: {database_path}"
                )
            query_pdbs = {}
            for query_row in database_reader:
                query_id = str(query_row.get("Query_ID") or "").strip()
                if query_id:
                    query_pdbs[query_id] = str(
                        query_row.get("PDB_ID") or ""
                    ).strip().upper()
    required = set(missing_disposition_fields())
    problems: list[dict[str, Any]] = []
    stats = Counter()
    seen_keys: dict[tuple[str, str, str, str], int] = {}
    pdbs: set[str] = set()
    blocked_rows = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing_columns = sorted(required - headers)
        if missing_columns:
            raise ToolFailure(
                "Missing-disposition ledger is missing columns: "
                + ", ".join(missing_columns)
            )
        for line_number, row in enumerate(reader, start=2):
            stats["rows"] += 1
            pdb_id = str(row.get("PDB_ID") or "").strip().upper()
            if re.fullmatch(r"[0-9A-Z]{4}", pdb_id):
                pdbs.add(pdb_id)
            else:
                problems.append(
                    {"row": line_number, "issue": "invalid_pdb_id", "value": pdb_id}
                )
            reason_code = str(row.get("Reason_Code") or "").strip()
            taxonomy = missing_taxonomy()
            if reason_code not in taxonomy:
                problems.append(
                    {
                        "row": line_number,
                        "issue": "unknown_reason_code",
                        "value": reason_code,
                    }
                )
            field_name = str(row.get("Field_Name") or "").strip()
            allowed_fields = set(
                taxonomy[reason_code].get("applies_to_fields") or []
            ) if reason_code in taxonomy else set()
            if allowed_fields and field_name not in allowed_fields:
                problems.append(
                    {
                        "row": line_number,
                        "issue": "reason_field_mismatch",
                        "expected": sorted(allowed_fields),
                        "value": field_name,
                    }
                )
                stats["reason_field_mismatch"] += 1
            expected_disposition = (
                "no_ligand"
                if field_name == "ligand"
                else "no_value"
                if field_name == "affinity_value"
                else "not_applicable"
            )
            if str(row.get("Disposition") or "").strip() != expected_disposition:
                problems.append(
                    {
                        "row": line_number,
                        "issue": "disposition_field_mismatch",
                        "expected": expected_disposition,
                    }
                )
                stats["disposition_field_mismatch"] += 1
            required_text = {
                "Search_Scope": "blank_search_scope",
                "Sources_Checked": "blank_sources_checked",
            }
            for column, issue in required_text.items():
                if _is_blank(row.get(column)):
                    problems.append(
                        {"row": line_number, "issue": issue}
                    )
                    stats[issue] += 1
            status = str(row.get("Status") or "").strip()
            evidence_path_value = str(row.get("Evidence_Path") or "").strip()
            source_locator = str(row.get("Source_Locator") or "").strip()
            evidence_text = str(row.get("Evidence_Text") or "").strip()
            blocker_id = str(row.get("Blocker_ID") or "").strip()
            query_id = str(row.get("Query_ID") or "").strip()
            if reason_code in taxonomy:
                expected_status = taxonomy[reason_code]["default_status"]
                if status != expected_status:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "status_reason_mismatch",
                            "expected": expected_status,
                            "value": status,
                        }
                    )
                    stats["status_reason_mismatch"] += 1
            if status.startswith("blocked") and not blocker_id:
                problems.append(
                    {"row": line_number, "issue": "blocked_without_blocker_id"}
                )
                stats["blocked_without_blocker_id"] += 1
            if status.startswith("blocked"):
                blocked_rows += 1
                if blocker_pdbs is None:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "blocker_ledger_not_supplied",
                        }
                    )
                    stats["blocker_ledger_not_supplied"] += 1
                elif blocker_id and blocker_id not in blocker_pdbs:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "blocker_id_not_found",
                            "value": blocker_id,
                        }
                    )
                    stats["blocker_id_not_found"] += 1
                elif (
                    blocker_id
                    and blocker_pdbs[blocker_id] not in {pdb_id, "ALL"}
                ):
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "blocker_pdb_mismatch",
                            "blocker_pdb": blocker_pdbs[blocker_id],
                            "disposition_pdb": pdb_id,
                        }
                    )
                    stats["blocker_pdb_mismatch"] += 1
            if reason_code == "database_only_lead" and not query_id:
                problems.append(
                    {
                        "row": line_number,
                        "issue": "database_lead_without_query_id",
                    }
                )
                stats["database_lead_without_query_id"] += 1
            if query_id:
                if query_pdbs is None:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "database_ledger_not_supplied",
                        }
                    )
                    stats["database_ledger_not_supplied"] += 1
                elif query_id not in query_pdbs:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "query_id_not_found",
                            "value": query_id,
                        }
                    )
                    stats["query_id_not_found"] += 1
                elif query_pdbs[query_id] not in {pdb_id, "ALL"}:
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "query_pdb_mismatch",
                            "query_pdb": query_pdbs[query_id],
                            "disposition_pdb": pdb_id,
                        }
                    )
                    stats["query_pdb_mismatch"] += 1
            if (
                status.startswith("accepted")
                or status == "rejected_non_inhibitor"
            ) and not source_locator:
                problems.append(
                    {"row": line_number, "issue": "accepted_without_receipt"}
                )
                stats["accepted_without_receipt"] += 1
            if status == "needs_primary_source" and not source_locator:
                problems.append(
                    {"row": line_number, "issue": "database_lead_without_receipt"}
                )
                stats["database_lead_without_receipt"] += 1
            if evidence_path_value:
                evidence_path = Path(evidence_path_value).expanduser()
                if not evidence_path.is_absolute():
                    evidence_path = path.parent / evidence_path
                evidence_path = evidence_path.resolve()
                if not evidence_path.exists() or not evidence_path.is_file():
                    problems.append(
                        {
                            "row": line_number,
                            "issue": "evidence_path_not_found",
                            "path": str(evidence_path),
                        }
                    )
                    stats["evidence_path_not_found"] += 1
                else:
                    expected_hash = str(
                        row.get("Evidence_SHA256") or ""
                    ).strip()
                    if not expected_hash:
                        problems.append(
                            {"row": line_number, "issue": "evidence_hash_missing"}
                        )
                        stats["evidence_hash_missing"] += 1
                    else:
                        actual_hash = sha256_file(evidence_path)
                        if actual_hash.lower() != expected_hash.lower():
                            problems.append(
                                {
                                    "row": line_number,
                                    "issue": "evidence_hash_mismatch",
                                    "path": str(evidence_path),
                                }
                            )
                            stats["evidence_hash_mismatch"] += 1
            duplicate_key = (
                pdb_id,
                str(row.get("Ligand_ID") or "").strip(),
                str(row.get("Context") or "").strip(),
                str(row.get("Field_Name") or "").strip(),
                str(row.get("Reason_Code") or "").strip(),
                str(row.get("Search_Scope") or "").strip(),
            )
            if duplicate_key in seen_keys:
                problems.append(
                    {
                        "row": line_number,
                        "issue": "duplicate_disposition_scope",
                        "first_row": seen_keys[duplicate_key],
                    }
                )
                stats["duplicate_disposition_scope"] += 1
            else:
                seen_keys[duplicate_key] = line_number
    required_pdbs = {
        str(value).strip().upper()
        for value in args.get("required_pdb_ids") or []
    }
    missing_pdbs = sorted(required_pdbs - pdbs)
    if missing_pdbs:
        problems.append(
            {
                "issue": "required_pdbs_without_disposition",
                "pdbs": missing_pdbs,
            }
        )
        stats["required_pdbs_without_disposition"] = len(missing_pdbs)
    report = {
        "ok": not problems,
        "ledger_path": str(path),
        "stats": dict(stats),
        "pdb_count": len(pdbs),
        "blocked_rows": blocked_rows,
        "missing_required_pdbs": missing_pdbs,
        "problems": problems[: int(args.get("max_report_rows", 200))],
    }
    output_path = args.get("output_path")
    if output_path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(compact_json(report), encoding="utf-8")
        report["output_path"] = str(destination)
    return report


def audit_missing_coverage(args: dict[str, Any]) -> dict[str, Any]:
    table_path = Path(args["table_path"]).expanduser().resolve()
    ledger_path = Path(args["disposition_ledger"]).expanduser().resolve()
    if not table_path.exists():
        raise ToolFailure(f"Input table not found: {table_path}")
    if not ledger_path.exists():
        raise ToolFailure(f"Missing-disposition ledger not found: {ledger_path}")
    _, table_rows = _iter_rows_from_file(table_path, args.get("sheet"))
    required_ledger_columns = {
        "PDB_ID",
        "Ligand_ID",
        "Context",
        "Field_Name",
        "Reason_Code",
    }
    dispositions: set[tuple[str, str, str, str]] = set()
    dispositions_without_context: set[tuple[str, str, str]] = set()
    with ledger_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_columns = sorted(
            required_ledger_columns - set(reader.fieldnames or [])
        )
        if missing_columns:
            raise ToolFailure(
                "Missing-disposition ledger lacks coverage columns: "
                + ", ".join(missing_columns)
            )
        for ledger_row in reader:
            pdb_id = str(ledger_row.get("PDB_ID") or "").strip().upper()
            ligand_id = str(ledger_row.get("Ligand_ID") or "").strip()
            context = str(ledger_row.get("Context") or "").strip()
            field_name = str(ledger_row.get("Field_Name") or "").strip()
            if context:
                dispositions.add((pdb_id, ligand_id, context, field_name))
            else:
                dispositions_without_context.add(
                    (pdb_id, ligand_id, field_name)
                )
    pdb_column = args["pdb_column"]
    ligand_column = args["ligand_column"]
    value_column = args["value_column"]
    context_column = args.get("context_column")
    stats = Counter()
    problems: list[dict[str, Any]] = []
    for line_number, row in enumerate(table_rows, start=2):
        pdb_id = str(row.get(pdb_column) or "").strip().upper()
        ligand_id = str(row.get(ligand_column) or "").strip()
        context = (
            str(row.get(context_column) or "").strip()
            if context_column
            else ""
        )
        checks = []
        if _is_blank(row.get(ligand_column)):
            stats["missing_ligand"] += 1
            checks.append("ligand")
        if _is_blank(row.get(value_column)):
            stats["missing_value"] += 1
            checks.append("affinity_value")
        for field_name in checks:
            base_key = (pdb_id, ligand_id, field_name)
            context_key = (pdb_id, ligand_id, context, field_name)
            if context_column:
                covered = context_key in dispositions
            else:
                covered = (
                    base_key in dispositions_without_context
                    or any(
                        key[0] == pdb_id
                        and key[1] == ligand_id
                        and key[3] == field_name
                        for key in dispositions
                    )
                )
            if covered:
                stats["covered_missing_fields"] += 1
                continue
            stats["uncovered_missing_fields"] += 1
            problems.append(
                {
                    "row": line_number,
                    "PDB_ID": pdb_id,
                    "Ligand_ID": ligand_id,
                    "Context": context,
                    "Field_Name": field_name,
                    "issue": "missing_disposition_not_found",
                }
            )
    report = {
        "ok": not problems,
        "table_path": str(table_path),
        "disposition_ledger": str(ledger_path),
        "rows": len(table_rows),
        "stats": dict(stats),
        "problems": problems[: int(args.get("max_report_rows", 200))],
    }
    output_path = args.get("output_path")
    if output_path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(compact_json(report), encoding="utf-8")
        report["output_path"] = str(destination)
    return report


def prepare_database_queries(args: dict[str, Any]) -> dict[str, Any]:
    pdb_id = str(args.get("pdb_id") or "").strip().upper()
    ligand_id = str(args.get("ligand_id") or "").strip()
    ligand_name = str(args.get("ligand_name") or "").strip()
    inchi_key = str(args.get("inchi_key") or "").strip()
    doi = str(args.get("doi") or "").strip()
    if not any((pdb_id, ligand_id, inchi_key, ligand_name)):
        raise ToolFailure("Provide at least one PDB, ligand, or InChIKey field")
    exact_terms = [item for item in (inchi_key, ligand_id, ligand_name) if item]
    return {
        "ok": True,
        "identity_precedence": [
            "exact InChIKey",
            "exact HET plus primary citation",
            "exact DOI plus compound code",
            "exact CAS",
            "normalized chemical name",
        ],
        "queries": [
            {
                "database": "SciFinder-n",
                "url": "https://scifinder-n.cas.org/",
                "query": " AND ".join(
                    item for item in (inchi_key, doi, pdb_id) if item
                )
                or " | ".join(exact_terms),
                "required_scope": "Exact structure or exact substance; inspect target, construct, mutation and assay.",
                "export_route": "Pharmacology Data -> Download Assay Results",
            },
            {
                "database": "Reaxys",
                "url": "https://www.reaxys.com/",
                "query": " AND ".join(
                    item for item in (inchi_key, pdb_id) if item
                )
                or " | ".join(exact_terms),
                "required_scope": "Exact structure; never merge analogue activity.",
                "export_route": "Bioactivity export, retaining RN and reference.",
            },
            {
                "database": "ChEMBL",
                "url": "https://www.ebi.ac.uk/chembl/api/data",
                "query": inchi_key or ligand_id,
                "required_scope": "Exact molecule plus target and assay.",
            },
            {
                "database": "BindingDB",
                "url": "https://bindingdb.org/",
                "query": inchi_key or ligand_name,
                "required_scope": "Exact molecule plus target and assay.",
            },
        ],
        "policy": (
            "A database hit is a lead. Acceptance still requires the primary "
            "paper, supplement, or patent full text and a locator."
        ),
    }


def record_database_query(args: dict[str, Any]) -> dict[str, Any]:
    output = Path(args["ledger_path"]).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Date",
        "Database",
        "PDB_ID",
        "Ligand",
        "Query",
        "Filters",
        "Result_Count",
        "Export_Path",
        "Export_SHA256",
        "Checked_Status",
        "Access_Status",
        "Notes",
        "Query_ID",
        "Result_Locator",
    ]
    export_path = args.get("export_path")
    export_hash = ""
    if export_path:
        path = Path(export_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ToolFailure(f"Database export not found: {path}")
        export_hash = sha256_file(path)
    expected_hash = str(args.get("export_sha256") or "").strip()
    if (
        expected_hash
        and export_hash
        and expected_hash.lower() != export_hash.lower()
    ):
        raise ToolFailure("export_sha256 does not match the export file")
    export_hash = export_hash or expected_hash
    query_seed = "\x1f".join(
        (
            str(args.get("database", "")),
            str(args.get("pdb_id", "")).upper(),
            str(args.get("ligand", "")),
            str(args.get("query", "")),
            str(args.get("filters", "")),
            str(args.get("date") or datetime.now().date().isoformat()),
        )
    )
    query_id = str(args.get("query_id") or "").strip()
    if not query_id:
        query_id = "DQ-" + hashlib.sha256(
            query_seed.encode("utf-8")
        ).hexdigest()[:16].upper()
    row = {
        "Date": args.get("date") or datetime.now().date().isoformat(),
        "Database": args.get("database", ""),
        "PDB_ID": str(args.get("pdb_id", "")).upper(),
        "Ligand": args.get("ligand", ""),
        "Query": args.get("query", ""),
        "Filters": args.get("filters", ""),
        "Result_Count": args.get("result_count", ""),
        "Export_Path": export_path or "",
        "Export_SHA256": export_hash,
        "Checked_Status": args.get("checked_status", "not_checked"),
        "Access_Status": args.get("access_status", "unknown"),
        "Notes": args.get("notes", ""),
        "Query_ID": query_id,
        "Result_Locator": args.get("result_locator", ""),
    }
    _append_csv_row(output, fields, row, allow_added_columns=True)
    return {"ok": True, "ledger_path": str(output), "row": row}


def record_blocker(args: dict[str, Any]) -> dict[str, Any]:
    output = Path(args["ledger_path"]).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "PDB_ID",
        "Blocker_Type",
        "Original_Detail",
        "Resolution_Status",
        "Resolution_Route",
        "Resolution_Evidence",
        "Evidence_SHA256",
        "Required_For_Acceptance",
        "Remaining_Action",
        "Blocker_ID",
    ]
    evidence_path = args.get("resolution_evidence")
    evidence_hash = args.get("evidence_sha256", "")
    if (
        not evidence_hash
        and evidence_path
        and Path(evidence_path).expanduser().exists()
        and Path(evidence_path).expanduser().is_file()
    ):
        evidence_hash = sha256_file(Path(evidence_path).expanduser().resolve())
    row = {
        "PDB_ID": str(args.get("pdb_id", "")).upper(),
        "Blocker_Type": args.get("blocker_type", ""),
        "Original_Detail": args.get("original_detail", ""),
        "Resolution_Status": args.get("resolution_status", "open"),
        "Resolution_Route": args.get("resolution_route", ""),
        "Resolution_Evidence": evidence_path or args.get("evidence_text", ""),
        "Evidence_SHA256": evidence_hash,
        "Required_For_Acceptance": args.get("required_for_acceptance", "yes"),
        "Remaining_Action": args.get("remaining_action", ""),
        "Blocker_ID": "",
    }
    blocker_seed = "\x1f".join(
        (
            row["PDB_ID"],
            str(row["Blocker_Type"]),
            str(row["Original_Detail"]),
            str(row["Resolution_Status"]),
            str(row["Remaining_Action"]),
        )
    )
    row["Blocker_ID"] = str(args.get("blocker_id") or "").strip()
    if not row["Blocker_ID"]:
        row["Blocker_ID"] = "BL-" + hashlib.sha256(
            blocker_seed.encode("utf-8")
        ).hexdigest()[:16].upper()
    _append_csv_row(output, fields, row, allow_added_columns=True)
    return {"ok": True, "ledger_path": str(output), "row": row}


def create_run_manifest(args: dict[str, Any]) -> dict[str, Any]:
    root = Path(args["root"]).expanduser().resolve()
    if not root.exists():
        raise ToolFailure(f"Root path not found: {root}")
    output = Path(args["output"]).expanduser().resolve()
    max_file_mb = float(args.get("max_file_mb", 512))
    files = []
    total_bytes = 0
    paths = (
        [root]
        if root.is_file()
        else sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.resolve() != output.resolve()
        )
    )
    for path in paths:
        size = path.stat().st_size
        total_bytes += size
        entry = {
            "path": str(path),
            "bytes": size,
            "sha256": "",
        }
        if size <= max_file_mb * 1024 * 1024:
            entry["sha256"] = sha256_file(path)
        else:
            entry["sha256"] = "skipped_large_file"
        files.append(entry)
    manifest = {
        "created_at": utc_now(),
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "root": str(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "environment": environment_check({})["server"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(compact_json(manifest), encoding="utf-8")
    manifest["output"] = str(output)
    return manifest


def _property(
    type_name: str,
    description: str,
    *,
    items: dict[str, Any] | None = None,
    default: Any = None,
    enum: list[Any] | None = None,
    pattern: str | None = None,
    min_length: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"type": type_name, "description": description}
    if items is not None:
        value["items"] = items
    if default is not None:
        value["default"] = default
    if enum is not None:
        value["enum"] = enum
    if pattern is not None:
        value["pattern"] = pattern
    if min_length is not None:
        value["minLength"] = min_length
    return value


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    read_only: bool = False,
    open_world: bool = False,
    any_of: list[dict[str, Any]] | None = None,
    one_of: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    input_schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if any_of:
        input_schema["anyOf"] = any_of
    if one_of:
        input_schema["oneOf"] = one_of
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": open_world,
        },
    }


MISSING_REASON_CODES = list(missing_taxonomy())
MISSING_STATUSES = sorted(
    {
        str(item["default_status"])
        for item in missing_taxonomy().values()
    }
)


TOOLS = [
    _tool(
        "environment_check",
        "Check installed source-curation skills, required scripts, optional PDF/OCR tools, and configured database capabilities.",
        {},
        [],
        read_only=True,
    ),
    _tool(
        "fetch_pdb_evidence",
        "Build a PDB evidence pack from RCSB citations, OA full text, supplements, reference chains, and optional patent candidates.",
        {
            "pdb_ids": _property("array", "Four-character PDB IDs.", items={"type": "string"}),
            "pdb_list": _property("string", "Path to a one-ID-per-line PDB list."),
            "output_dir": _property("string", "Evidence-pack output directory."),
            "download_oa": _property("boolean", "Download open-access full text."),
            "download_supplements": _property("boolean", "Download available supplements."),
            "fetch_web": _property("boolean", "Fetch browser-accessible metadata/pages."),
            "patent_search": _property("boolean", "Create patent-search candidates."),
            "download_patents": _property("boolean", "Download authorized patent PDFs."),
            "patent_ids": _property("array", "Exact patent publication IDs.", items={"type": "string"}),
            "timeout_seconds": _property("integer", "Network timeout per request."),
            "max_download_mb": _property("integer", "Maximum file size to download."),
            "max_patents_per_query": _property("integer", "Patent candidate cap."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=3600),
        },
        ["output_dir"],
        open_world=True,
        one_of=[{"required": ["pdb_ids"]}, {"required": ["pdb_list"]}],
    ),
    _tool(
        "resolve_local_sources",
        "Match cited DOIs, PMIDs, title tokens and PDB codes to already-downloaded Zotero, Downloads or project files.",
        {
            "source_dirs": _property("array", "Directories to search.", items={"type": "string"}),
            "references": _property("string", "references.csv from the evidence pack."),
            "pdb_citations": _property("string", "pdb_citations.csv from the evidence pack."),
            "output_dir": _property("string", "Output directory."),
            "resolve_pmids": _property("boolean", "Resolve PMIDs to DOI values before matching."),
            "timeout_seconds": _property("integer", "Network timeout."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=1800),
        },
        ["output_dir"],
        open_world=True,
    ),
    _tool(
        "extract_document_text",
        "Create page-aware text, numeric review queues, OCR consensus and rendered-page queues from PDF, DOCX, TIFF, ZIP, XLSX and CSV sources.",
        {
            "documents": _property("array", "Document paths.", items={"type": "string"}),
            "output_dir": _property("string", "Extraction output directory."),
            "terms": _property("string", "Comma-separated target terms."),
            "ocr": _property("string", "OCR mode: off, auto or all.", default="auto"),
            "ocr_consensus": _property("string", "OCR consensus mode: off, auto or all.", default="auto"),
            "render_pages": _property("boolean", "Render page images for visual review."),
            "dpi": _property("integer", "Rendered-page DPI.", default=300),
            "page_limit": _property("integer", "Optional page limit per document."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=7200),
        },
        ["documents", "output_dir"],
    ),
    _tool(
        "build_visual_review",
        "Build contact sheets from visual-review queues for rendered scientific pages.",
        {
            "queues": _property("array", "visual_review_queue.csv files or extraction directories.", items={"type": "string"}),
            "output_dir": _property("string", "Review output directory."),
            "columns": _property("integer", "Contact-sheet columns.", default=3),
            "max_pages": _property("integer", "Maximum pages per contact sheet.", default=9),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=1800),
        },
        ["queues", "output_dir"],
    ),
    _tool(
        "resolve_identity",
        "Resolve stale PDB title codes, missing citations or same-chemical-component aliases with exact-component evidence.",
        {
            "pdb_id": _property("string", "Single PDB ID."),
            "pdb_ids": _property("array", "PDB IDs.", items={"type": "string"}),
            "pdb_list": _property("string", "Path to PDB list."),
            "text": _property("string", "Extracted-text directory."),
            "output_dir": _property("string", "Identity-resolution output directory."),
            "timeout_seconds": _property("integer", "Network timeout."),
            "peer_limit": _property("integer", "Maximum component peers."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=1800),
        },
        ["output_dir"],
        open_world=True,
        one_of=[
            {"required": ["pdb_id"]},
            {"required": ["pdb_ids"]},
            {"required": ["pdb_list"]},
        ],
    ),
    _tool(
        "resolve_blockers",
        "Convert raw evidence blockers into a resolution ledger without treating missing access as success.",
        {
            "evidence_pack": _property("string", "Evidence-pack directory."),
            "identity_resolution": _property("string", "Identity-resolution CSV."),
            "source_register": _property("string", "Source register CSV."),
            "database_ledger": _property("string", "Database query ledger CSV."),
            "browser_manifest": _property("string", "Browser evidence manifest."),
            "output": _property("string", "blocker_resolution.csv output path."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=600),
        },
        ["evidence_pack", "output"],
    ),
    _tool(
        "patmap_extract",
        "Extract tables, reactions and co-references from authorized patent PDFs when PATMAP_API_KEY is configured.",
        {
            "pdfs": _property("array", "Patent PDF paths.", items={"type": "string"}),
            "output_dir": _property("string", "Output directory."),
            "functions": _property("string", "Comma-separated PatMap functions."),
            "version": _property("string", "PatMap API version."),
            "timeout_seconds": _property("integer", "Request timeout."),
            "interval": _property("integer", "Polling interval seconds."),
            "max_wait": _property("integer", "Maximum polling seconds."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=3600),
        },
        ["pdfs", "output_dir"],
        open_world=True,
    ),
    _tool(
        "validate_affinity_dataset",
        "Run schema and optional strict full-text acceptance validation on a normalized affinity CSV.",
        {
            "csv_path": _property("string", "Normalized record CSV."),
            "output": _property("string", "Validation JSON path."),
            "require_fulltext_acceptance": _property("boolean", "Enforce full-text acceptance gates."),
            "review_checklist": _property("string", "Review checklist CSV."),
            "blocker_ledger": _property("string", "Blocker ledger CSV."),
            "require_blocker_closure": _property("boolean", "Fail on open acceptance blockers."),
            "source_root": _property("string", "Root for source-path resolution."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=600),
        },
        ["csv_path"],
    ),
    _tool(
        "compare_to_human",
        "Compare curated records with a human-review workbook and report exact matches, evidence corrections, missing rows and critical false accepts.",
        {
            "curated": _property("string", "Curated CSV."),
            "human_workbook": _property("string", "Human-review XLSX."),
            "human_sheet": _property("string", "Human sheet name or prefix."),
            "audit_sheet": _property("string", "Verified exception sheet."),
            "pdb_ids": _property("array", "Optional PDB subset.", items={"type": "string"}),
            "correction_log": _property("string", "Correction log CSV."),
            "use_all_human_rows": _property("boolean", "Compare every human row instead of inferring target PDBs from the curated CSV.", default=False),
            "output_dir": _property("string", "Comparison output directory."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=1200),
        },
        ["curated", "human_workbook", "output_dir"],
    ),
    _tool(
        "crosscheck_sources",
        "Cross-check curated records against exact Reaxys, SciFinder, ChEMBL or BindingDB exports using identical chemical identity.",
        {
            "curated": _property("string", "Curated CSV."),
            "sources": _property("object", "Map of source name to local export path."),
            "output_dir": _property("string", "Crosscheck output directory."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=1200),
        },
        ["curated", "sources", "output_dir"],
    ),
    _tool(
        "compare_curated_versions",
        "Create a field-level correction log between two curated CSV versions.",
        {
            "old": _property("string", "Old CSV."),
            "new": _property("string", "New CSV."),
            "keys": _property("array", "Stable key columns.", items={"type": "string"}),
            "output": _property("string", "Correction-log CSV."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=600),
        },
        ["old", "new", "output"],
    ),
    _tool(
        "run_acceptance",
        "Run the joint strict validator, blocker closure, human comparison and visual-receipt gate.",
        {
            "curated": _property("string", "Curated CSV."),
            "review_checklist": _property("string", "Review checklist CSV."),
            "blocker_ledger": _property("string", "Blocker ledger CSV."),
            "source_root": _property("string", "Root for resolved source paths."),
            "human_workbook": _property("string", "Human-review XLSX."),
            "human_sheet": _property("string", "Human sheet name or prefix."),
            "audit_sheet": _property("string", "Verified exception sheet."),
            "correction_log": _property("string", "Correction log CSV."),
            "pdb_ids": _property("array", "Optional PDB subset.", items={"type": "string"}),
            "use_all_human_rows": _property("boolean", "Compare every human row instead of inferring target PDBs from the curated CSV.", default=False),
            "visual_receipts": _property("string", "Visual receipt CSV."),
            "missing_disposition_ledger": _property("string", "Structured missing-disposition CSV ledger required by the MCP acceptance gate."),
            "missing_coverage_table": _property("string", "CSV/XLSX table audited for uncovered blanks; defaults to curated CSV."),
            "missing_coverage_sheet": _property("string", "Worksheet name when the coverage table is XLSX."),
            "missing_pdb_column": _property("string", "PDB column in the coverage table.", default="PDB_ID"),
            "missing_ligand_column": _property("string", "Ligand column in the coverage table.", default="Ligand_ID"),
            "missing_value_column": _property("string", "Affinity value column in the coverage table.", default="Value_nM"),
            "missing_context_column": _property("string", "Optional assay/endpoint context column used to distinguish multiple blanks."),
            "missing_blocker_ledger": _property("string", "Optional MCP blocker ledger used to validate blocked missing dispositions; normally distinct from the acceptance blocker-resolution CSV."),
            "database_ledger": _property("string", "Database query ledger used to validate Query_ID references."),
            "output_dir": _property("string", "Acceptance output directory."),
            "process_timeout_seconds": _property("integer", "Overall subprocess timeout.", default=3600),
        },
        [
            "curated",
            "review_checklist",
            "blocker_ledger",
            "source_root",
            "human_workbook",
            "missing_disposition_ledger",
            "output_dir",
        ],
    ),
    _tool(
        "classify_missing_reason",
        "Return a controlled missing-data reason and the evidence required to support it.",
        {
            "code": _property(
                "string",
                "Optional reason code. Omit to return the complete taxonomy.",
            )
        },
        [],
        read_only=True,
    ),
    _tool(
        "audit_missing_reasons",
        "Audit a CSV or XLSX table so every missing ligand and missing value has a concise Chinese reason.",
        {
            "path": _property("string", "CSV or XLSX path."),
            "sheet": _property("string", "Worksheet name for XLSX."),
            "pdb_column": _property("string", "PDB identifier column name."),
            "ligand_column": _property("string", "Ligand column name."),
            "value_column": _property("string", "Numeric value column name."),
            "reason_column": _property("string", "Reason/note column name."),
            "output_path": _property("string", "Optional JSON report path."),
            "max_report_rows": _property("integer", "Maximum examples returned.", default=100),
        },
        ["path", "ligand_column", "value_column", "reason_column"],
    ),
    _tool(
        "record_missing_disposition",
        "Append a structured, auditable reason for a missing ligand, value, target or sequence to a disposition ledger.",
        {
            "ledger_path": _property("string", "Missing-disposition CSV ledger."),
            "pdb_id": _property("string", "Four-character PDB ID.", pattern="^[0-9A-Za-z]{4}$"),
            "ligand_id": _property("string", "Ligand, HET or compound ID."),
            "context": _property("string", "Measurement or construct context used to distinguish blanks for the same PDB and ligand."),
            "field_name": _property("string", "ligand, affinity_value, target, sequence or other.", enum=["ligand", "affinity_value", "target", "sequence", "other"]),
            "disposition": _property("string", "Optional assertion; the server derives it from field_name.", enum=["no_ligand", "no_value", "not_applicable"]),
            "reason_code": _property("string", "Controlled code from classify_missing_reason.", enum=MISSING_REASON_CODES),
            "status": _property("string", "Optional assertion; must equal the reason taxonomy status.", enum=MISSING_STATUSES),
            "search_scope": _property("string", "Databases, papers, supplements, patents and date range searched.", min_length=1),
            "sources_checked": _property("string", "Semicolon-separated sources actually opened or exported.", min_length=1),
            "source_locator": _property("string", "Exact page, table, figure, paragraph or database result locator."),
            "evidence_path": _property("string", "Local receipt or export file."),
            "evidence_sha256": _property("string", "Optional expected file hash; verified when provided."),
            "evidence_text": _property("string", "Short evidence description when a file is not available."),
            "blocker_id": _property("string", "Blocker identifier for blocked dispositions."),
            "query_id": _property("string", "Database Query_ID for database-only leads."),
            "checked_at": _property("string", "ISO timestamp; defaults to now."),
            "reviewer": _property("string", "Reviewer or process identifier."),
            "notes": _property("string", "Optional notes."),
            "record_id": _property("string", "Optional stable record ID."),
        },
        [
            "ledger_path",
            "pdb_id",
            "field_name",
            "reason_code",
            "search_scope",
            "sources_checked",
        ],
    ),
    _tool(
        "audit_missing_traceability",
        "Audit a missing-disposition ledger for controlled reasons, search scope, receipts, hashes, blockers, duplicates and required-PDB coverage.",
        {
            "ledger_path": _property("string", "Missing-disposition CSV ledger."),
            "blocker_ledger": _property("string", "Blocker ledger used to verify referenced Blocker_ID values."),
            "database_ledger": _property("string", "Database query ledger used to verify referenced Query_ID values."),
            "required_pdb_ids": _property("array", "PDB IDs that must have a disposition.", items={"type": "string"}),
            "output_path": _property("string", "Optional JSON report path."),
            "max_report_rows": _property("integer", "Maximum returned problems.", default=200),
        },
        ["ledger_path"],
    ),
    _tool(
        "audit_missing_coverage",
        "Join a CSV/XLSX table to a missing-disposition ledger and require every blank ligand and affinity value to have a matching PDB, ligand and field disposition.",
        {
            "table_path": _property("string", "CSV or XLSX table to audit."),
            "sheet": _property("string", "Worksheet name for XLSX."),
            "pdb_column": _property("string", "PDB identifier column."),
            "ligand_column": _property("string", "Ligand identifier column."),
            "value_column": _property("string", "Affinity value column."),
            "context_column": _property("string", "Optional context column such as assay type or endpoint."),
            "disposition_ledger": _property("string", "Structured missing-disposition CSV ledger."),
            "output_path": _property("string", "Optional JSON report path."),
            "max_report_rows": _property("integer", "Maximum returned uncovered rows.", default=200),
        },
        [
            "table_path",
            "pdb_column",
            "ligand_column",
            "value_column",
            "disposition_ledger",
        ],
    ),
    _tool(
        "prepare_database_queries",
        "Build exact-identity query templates for SciFinder, Reaxys, ChEMBL and BindingDB.",
        {
            "pdb_id": _property("string", "PDB ID."),
            "ligand_id": _property("string", "HET or internal ligand ID."),
            "ligand_name": _property("string", "Chemical name."),
            "inchi_key": _property("string", "Exact InChIKey."),
            "doi": _property("string", "Primary citation DOI."),
        },
        [],
        read_only=True,
        any_of=[
            {"required": ["pdb_id"]},
            {"required": ["ligand_id"]},
            {"required": ["ligand_name"]},
            {"required": ["inchi_key"]},
        ],
    ),
    _tool(
        "record_database_query",
        "Append a licensed or public database query to an auditable CSV ledger.",
        {
            "ledger_path": _property("string", "Database ledger CSV."),
            "database": _property("string", "Database name."),
            "pdb_id": _property("string", "PDB ID."),
            "ligand": _property("string", "Compound or ligand identifier."),
            "query": _property("string", "Exact query string."),
            "filters": _property("string", "Search scope and filters."),
            "result_count": _property("integer", "Number of returned records."),
            "export_path": _property("string", "Local structured export path."),
            "export_sha256": _property("string", "Optional expected export hash; verified when provided."),
            "result_locator": _property("string", "Exact row, page or result identifier supporting the query outcome."),
            "checked_status": _property("string", "Checked status."),
            "access_status": _property("string", "Access/licensing status."),
            "notes": _property("string", "Review notes."),
            "date": _property("string", "Query date in YYYY-MM-DD."),
            "query_id": _property("string", "Optional stable query ID."),
        },
        ["ledger_path", "database", "query"],
    ),
    _tool(
        "record_blocker",
        "Append a blocker and its resolution route to a blocker ledger.",
        {
            "ledger_path": _property("string", "Blocker ledger CSV."),
            "pdb_id": _property("string", "PDB ID or 'all'."),
            "blocker_type": _property("string", "Controlled blocker type."),
            "original_detail": _property("string", "What failed."),
            "resolution_status": _property("string", "open, resolved, resolved_with_context, evidence_exhausted_no_hit, or requires_authorized_session."),
            "resolution_route": _property("string", "API, OA, browser, exact-component peer, patent, or licensed database."),
            "resolution_evidence": _property("string", "Evidence path."),
            "evidence_text": _property("string", "Evidence description when no file exists."),
            "evidence_sha256": _property("string", "Evidence SHA-256 if known."),
            "required_for_acceptance": _property("string", "yes or no."),
            "remaining_action": _property("string", "Next required action."),
            "blocker_id": _property("string", "Optional stable blocker ID; generated when omitted."),
        },
        ["ledger_path", "pdb_id", "blocker_type", "original_detail"],
    ),
    _tool(
        "create_run_manifest",
        "Hash and inventory inputs, evidence, code and outputs for a reproducible run.",
        {
            "root": _property("string", "File or directory to inventory."),
            "output": _property("string", "Manifest JSON output path."),
            "max_file_mb": _property("number", "Maximum file size to hash.", default=512),
        },
        ["root", "output"],
    ),
]


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "environment_check": environment_check,
    "fetch_pdb_evidence": fetch_pdb_evidence,
    "resolve_local_sources": resolve_local_sources,
    "extract_document_text": extract_document_text,
    "build_visual_review": build_visual_review,
    "resolve_identity": resolve_identity,
    "resolve_blockers": resolve_blockers,
    "patmap_extract": patmap_extract,
    "validate_affinity_dataset": validate_affinity_dataset,
    "compare_to_human": compare_to_human,
    "crosscheck_sources": crosscheck_sources,
    "compare_curated_versions": compare_curated_versions,
    "run_acceptance": run_acceptance,
    "classify_missing_reason": classify_missing_reason,
    "audit_missing_reasons": audit_missing_reasons,
    "record_missing_disposition": record_missing_disposition,
    "audit_missing_traceability": audit_missing_traceability,
    "audit_missing_coverage": audit_missing_coverage,
    "prepare_database_queries": prepare_database_queries,
    "record_database_query": record_database_query,
    "record_blocker": record_blocker,
    "create_run_manifest": create_run_manifest,
}


RESOURCE_FILES = {
    "pdb-curation://readme": ROOT / "README.md",
    "pdb-curation://project-lessons": DOCS_DIR / "PROJECT_LESSONS.md",
    "pdb-curation://source-policy": DOCS_DIR / "CROSSCHECK_POLICY.md",
    "pdb-curation://databases": DOCS_DIR / "DATABASES.md",
    "pdb-curation://missing-reasons": SCHEMAS_DIR / "missing_reasons.json",
    "pdb-curation://skill": ROOT / "skill" / "pdb-affinity-mcp" / "SKILL.md",
}


def resources_list() -> dict[str, Any]:
    resources = []
    for uri, path in RESOURCE_FILES.items():
        resources.append(
            {
                "uri": uri,
                "name": path.stem,
                "title": path.name,
                "mimeType": (
                    "application/json"
                    if path.suffix == ".json"
                    else "text/markdown"
                ),
            }
        )
    return {"resources": resources}


def resources_read(params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri")
    path = RESOURCE_FILES.get(uri)
    if not path:
        raise ToolFailure(f"Unknown resource URI: {uri}")
    if not path.exists():
        raise ToolFailure(f"Resource file missing: {path}")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": (
                    "application/json"
                    if path.suffix == ".json"
                    else "text/markdown"
                ),
                "text": path.read_text(encoding="utf-8"),
            }
        ]
    }


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def process_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}
    if method in {
        "notifications/initialized",
        "notifications/cancelled",
    }:
        return None
    if method == "initialize":
        requested_version = params.get("protocolVersion", PROTOCOL_VERSION)
        negotiated_version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION
        )
        return rpc_result(
            request_id,
            {
                "protocolVersion": negotiated_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "Final data should be missing rather than wrong. Preserve "
                    "source paths, hashes, blockers, missing reasons and human "
                    "comparison denominators for every accepted or missing row."
                ),
            },
        )
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        return rpc_result(request_id, {"tools": TOOLS})
    if method == "resources/list":
        return rpc_result(request_id, resources_list())
    if method == "resources/read":
        try:
            return rpc_result(request_id, resources_read(params))
        except ToolFailure as error:
            return rpc_error(request_id, -32002, str(error))
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return rpc_error(
                request_id,
                -32602,
                f"Unknown tool: {name}",
            )
        try:
            payload = handler(arguments)
            is_error = payload.get("ok") is False
            return rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": compact_json(payload),
                        }
                    ],
                    "structuredContent": payload,
                    "isError": is_error,
                },
            )
        except Exception as error:
            payload = {
                "ok": False,
                "tool": name,
                "error": str(error),
                "error_type": type(error).__name__,
            }
            return rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": compact_json(payload),
                        }
                    ],
                    "structuredContent": payload,
                    "isError": True,
                },
            )
    return rpc_error(request_id, -32601, f"Method not found: {method}")


def read_message() -> tuple[dict[str, Any] | None, str]:
    line = sys.stdin.buffer.readline()
    if not line:
        return None, "newline"
    if line.lower().startswith(b"content-length:"):
        length = int(line.split(b":", 1)[1].strip())
        while True:
            header = sys.stdin.buffer.readline()
            if header in {b"\n", b"\r\n", b""}:
                break
        body = sys.stdin.buffer.read(length)
        return json.loads(body.decode("utf-8")), "content-length"
    stripped = line.strip()
    if not stripped:
        return {}, "newline"
    return json.loads(stripped.decode("utf-8")), "newline"


def write_message(message: dict[str, Any], framing: str = "newline") -> None:
    body = json.dumps(
        message,
        ensure_ascii=False,
        default=json_default,
        separators=(",", ":"),
    ).encode("utf-8")
    with _WRITE_LOCK:
        if framing == "content-length":
            sys.stdout.buffer.write(
                f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
            )
        else:
            sys.stdout.buffer.write(body + b"\n")
        sys.stdout.buffer.flush()


def cancel_request(request_id: Any) -> bool:
    with _CALL_STATE_LOCK:
        event = _CALL_CANCEL_EVENTS.get(request_id)
        process = _CALL_PROCESSES.get(request_id)
    if event is None:
        return False
    event.set()
    if process is not None and process.poll() is None:
        process.kill()
    return True


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    if "--self-test" in sys.argv:
        print(
            compact_json(
                {
                    "server": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "tools": [tool["name"] for tool in TOOLS],
                    "resources": list(RESOURCE_FILES),
                    "environment": environment_check({}),
                }
            )
        )
        return
    workers: list[threading.Thread] = []

    def run_tool_request(
        request: dict[str, Any],
        request_id: Any,
        framing: str,
        cancel_event: threading.Event,
    ) -> None:
        _CURRENT_REQUEST_ID.set(request_id)
        try:
            response = process_request(request)
            if response is not None:
                write_message(response, framing)
        except Exception as error:
            write_message(
                rpc_error(
                    request_id,
                    -32603,
                    f"Tool worker failure: {error}",
                ),
                framing,
            )
        finally:
            with _CALL_STATE_LOCK:
                _CALL_CANCEL_EVENTS.pop(request_id, None)
                _CALL_PROCESSES.pop(request_id, None)

    while True:
        try:
            request, framing = read_message()
        except Exception as error:
            write_message(
                rpc_error(None, -32700, f"Parse error: {error}"),
                "newline",
            )
            continue
        if request is None:
            break
        if not request:
            continue
        if request.get("method") == "notifications/cancelled":
            params = request.get("params") or {}
            request_id = params.get("requestId")
            cancel_request(request_id)
            continue
        if request.get("method") == "tools/call" and request.get("id") is not None:
            request_id = request["id"]
            cancel_event = threading.Event()
            with _CALL_STATE_LOCK:
                _CALL_CANCEL_EVENTS[request_id] = cancel_event
            worker = threading.Thread(
                target=run_tool_request,
                args=(request, request_id, framing, cancel_event),
                daemon=True,
            )
            workers.append(worker)
            worker.start()
            continue
        response = process_request(request)
        if response is not None:
            write_message(response, framing)
    with _CALL_STATE_LOCK:
        active_ids = list(_CALL_CANCEL_EVENTS)
    for request_id in active_ids:
        cancel_request(request_id)
    for worker in workers:
        worker.join()


if __name__ == "__main__":
    main()
