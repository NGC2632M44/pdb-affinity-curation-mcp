#!/usr/bin/env python3
"""Extract page-aware text, rendered pages and OCR fallback for evidence files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from html import unescape
from pathlib import Path


ASSAY_TERMS = (
    "ic50",
    "ec50",
    "cc50",
    "ki",
    "kd",
    "km",
    "tm",
    "table",
    "figure",
    "compound",
    "inhibitor",
    "mutation",
)


def clean_text(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def ocr_quality(text: str) -> str:
    if not text:
        return "low"
    compact = re.sub(r"\s+", "", text)
    alphanumeric = sum(char.isalnum() for char in compact)
    ratio = alphanumeric / max(1, len(compact))
    if len(text) >= 1000 and ratio >= 0.55:
        return "high"
    if len(text) >= 250 and ratio >= 0.4:
        return "medium"
    return "low"


def numeric_tokens(text: str, assay_context: bool = False) -> list[str]:
    output = []
    for line in text.splitlines():
        lowered = line.lower()
        if assay_context and not any(term in lowered for term in ASSAY_TERMS):
            continue
        for match in re.finditer(
            r"(?<![\w.])[<>≤≥~]?\s*\d+(?:[.,]\d+)?"
            r"(?:\s*(?:±|\+/-)\s*\d+(?:[.,]\d+)?)?",
            line,
        ):
            token = re.sub(r"\s+", "", match.group(0))
            token = token.replace(",", ".")
            if token and token not in output:
                output.append(token)
    return output


def numeric_agreement(texts: list[str]) -> tuple[str, list[str]]:
    sets = [
        set(numeric_tokens(text, assay_context=True))
        for text in texts
        if text.strip()
    ]
    sets = [item for item in sets if item]
    if len(sets) < 2:
        return "single_pass", sorted(set().union(*sets)) if sets else []
    shared = set.intersection(*sets)
    union = set.union(*sets)
    missing = sorted(union - shared)
    if not missing:
        return "agree", sorted(shared)
    ratio = len(shared) / max(1, len(union))
    return ("disagree" if ratio < 0.9 else "agree_with_minor_differences"), missing


def tool_paths() -> tuple[str | None, str | None, Path]:
    pdftoppm = shutil.which("pdftoppm")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    ocr_script = Path(__file__).with_name("windows_ocr.ps1")
    return pdftoppm, powershell, ocr_script


def render_pdf_page(
    pdf: Path,
    page_number: int,
    dpi: int,
    destination_dir: Path,
) -> tuple[Path | None, str]:
    pdftoppm, _, _ = tool_paths()
    if not pdftoppm:
        return None, "pdftoppm_not_found"
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", pdf.stem)
    prefix = destination_dir / f"{stem}_page_{page_number:04d}"
    destination = prefix.with_suffix(".png")
    result = subprocess.run(
        [
            pdftoppm,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-singlefile",
            "-r",
            str(dpi),
            "-png",
            str(pdf),
            str(prefix),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not destination.exists():
        return None, f"render_failed:{result.stderr.strip()[:300]}"
    return destination, "rendered"


def windows_ocr(image: Path) -> tuple[str, str]:
    _, powershell, script = tool_paths()
    if not powershell:
        return "", "powershell_not_found"
    if not script.exists():
        return "", "windows_ocr_script_not_found"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Path",
            str(image.resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return "", f"ocr_failed:{result.stderr.strip()[:500]}"
    return clean_text(result.stdout), "windows_ocr"


def preprocess_image(image: Path) -> tuple[Path | None, str]:
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError:
        return None, "pillow_not_found"
    try:
        with Image.open(image) as source:
            converted = source.convert("L")
            width = max(converted.width, 2200)
            if converted.width != width:
                height = round(converted.height * width / converted.width)
                converted = converted.resize((width, height), Image.Resampling.LANCZOS)
            converted = ImageOps.autocontrast(converted, cutoff=1)
            converted = ImageEnhance.Contrast(converted).enhance(1.35)
            converted = converted.filter(ImageFilter.SHARPEN)
            destination = image.with_name(f"{image.stem}_ocr_enhanced.png")
            converted.save(destination)
            return destination, "preprocessed"
    except Exception as error:
        return None, f"preprocess_failed:{error}"


def tesseract_ocr(image: Path) -> tuple[str, str]:
    executable = shutil.which("tesseract")
    if not executable:
        return "", "tesseract_not_found"
    result = subprocess.run(
        [executable, str(image), "stdout"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return "", f"tesseract_failed:{result.stderr.strip()[:300]}"
    return clean_text(result.stdout), "tesseract"


def ocr_image(image: Path) -> tuple[str, str]:
    text, method = windows_ocr(image)
    if text:
        return text, method
    fallback_text, fallback_method = tesseract_ocr(image)
    if fallback_text:
        return fallback_text, fallback_method
    return "", method if method != "windows_ocr" else fallback_method


def ocr_variants(image: Path, consensus: bool) -> tuple[list[tuple[str, str]], list[str]]:
    results = []
    notes = []
    text, method = windows_ocr(image)
    if text:
        results.append((text, method))
    else:
        notes.append(method)
    if consensus:
        processed, process_status = preprocess_image(image)
        if processed:
            processed_text, processed_method = windows_ocr(processed)
            if processed_text:
                results.append((processed_text, f"{processed_method}_enhanced"))
            else:
                notes.append(processed_method)
            fallback_text, fallback_method = tesseract_ocr(processed)
            if fallback_text:
                results.append((fallback_text, f"{fallback_method}_enhanced"))
            else:
                notes.append(fallback_method)
        else:
            notes.append(process_status)
    if not results:
        fallback_text, fallback_method = tesseract_ocr(image)
        if fallback_text:
            results.append((fallback_text, fallback_method))
        else:
            notes.append(fallback_method)
    return results, notes


def best_ocr_result(results: list[tuple[str, str]]) -> tuple[str, str]:
    if not results:
        return "", "ocr_unavailable"
    quality_rank = {"low": 0, "medium": 1, "high": 2}
    text, method = max(
        results,
        key=lambda item: (quality_rank[ocr_quality(item[0])], len(item[0])),
    )
    return text, method


def native_pdf_text(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise SystemExit("pypdf is required for PDF extraction") from error
    try:
        reader = PdfReader(path)
        return [clean_text(page.extract_text() or "") for page in reader.pages]
    except Exception:
        # Some PDFs contain malformed font matrices that pypdf cannot parse.
        # Poppler's pdftotext is read-only and preserves page boundaries with \f.
        executable = shutil.which("pdftotext")
        if not executable:
            raise
        result = subprocess.run(
            [executable, "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pdftotext fallback failed: {result.stderr.strip()[:500]}"
            )
        pages = result.stdout.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        return [clean_text(page) for page in pages]


def extract_pdf(
    path: Path,
    output_dir: Path,
    ocr_mode: str,
    ocr_consensus: str,
    render_pages: bool,
    dpi: int,
    page_limit: int | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    native = native_pdf_text(path)
    if page_limit:
        native = native[:page_limit]
    page_dir = output_dir / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for index, text in enumerate(native, start=1):
        render_needed = render_pages or ocr_mode == "all" or (
            ocr_mode == "auto" and len(text) < 80
        )
        image = None
        render_status = "not_requested"
        if render_needed:
            image, render_status = render_pdf_page(path, index, dpi, page_dir)
        ocr_text = ""
        ocr_method = "not_requested"
        ocr_texts: list[str] = []
        ocr_notes: list[str] = []
        should_ocr = image is not None and (
            ocr_mode == "all" or (ocr_mode == "auto" and len(text) < 80)
        )
        if should_ocr:
            wants_consensus = ocr_consensus == "all" or (
                ocr_consensus == "auto"
                and (
                    not text
                    or any(term in text.lower() for term in ASSAY_TERMS)
                )
            )
            results, ocr_notes = ocr_variants(image, wants_consensus)
            ocr_text, ocr_method = best_ocr_result(results)
            ocr_texts = [item[0] for item in results]
        elif render_needed and image is None:
            ocr_method = render_status
        final_text = text
        text_method = "native_pdf_text"
        if ocr_text and (not text or len(ocr_text) > len(text) * 1.15):
            final_text = ocr_text
            text_method = ocr_method
        elif text and ocr_text:
            final_text = f"{text}\n\n=== OCR SUPPLEMENT ===\n{ocr_text}"
            text_method = f"native_plus_{ocr_method}"
        elif not text and ocr_text:
            final_text = ocr_text
            text_method = ocr_method
        lowered = final_text.lower()
        consensus_status, numeric_differences = numeric_agreement(ocr_texts)
        native_numbers = numeric_tokens(text, assay_context=True)
        ocr_numbers = numeric_tokens(ocr_text, assay_context=True)
        native_ocr_missing = sorted(set(native_numbers) ^ set(ocr_numbers))
        visual_reasons = []
        if any(term in lowered for term in ASSAY_TERMS):
            visual_reasons.append("assay_table_figure_or_identity_term")
        if not text and ocr_text:
            visual_reasons.append("image_only_source")
        if ocr_quality(ocr_text) == "low":
            visual_reasons.append("low_ocr_quality")
        if consensus_status in {"disagree", "single_pass"} and should_ocr:
            visual_reasons.append(f"ocr_numeric_{consensus_status}")
        if text and ocr_text and native_ocr_missing:
            visual_reasons.append("native_ocr_numeric_difference")
        pages.append(
            {
                "page": index,
                "text": final_text,
                "text_method": text_method,
                "ocr_method": ocr_method,
                "render_status": render_status,
                "image_path": str(image) if image else "",
                "needs_visual_review": (
                    "yes" if visual_reasons else "no"
                ),
                "visual_review_reason": "; ".join(visual_reasons),
                "ocr_quality": (
                    ocr_quality(ocr_text)
                    if "windows_ocr" in ocr_method or "tesseract" in ocr_method
                    else "not_applicable"
                ),
                "ocr_consensus": consensus_status,
                "numeric_differences": "; ".join(
                    dict.fromkeys(numeric_differences + native_ocr_missing)
                ),
                "ocr_notes": "; ".join(dict.fromkeys(ocr_notes)),
                "assay_numeric_tokens": "; ".join(ocr_numbers or native_numbers),
            }
        )
    tables = []
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        with pdfplumber.open(path) as document:
            for index, page in enumerate(document.pages, start=1):
                if page_limit and index > page_limit:
                    break
                for table_number, table in enumerate(page.extract_tables() or [], start=1):
                    tables.append(
                        {
                            "page": index,
                            "table": table_number,
                            "rows": table,
                            "extractor": "pdfplumber",
                        }
                    )
    return pages, tables


def docx_pages(path: Path) -> list[dict[str, object]]:
    try:
        from docx import Document
    except ImportError as error:
        raise SystemExit("python-docx is required for DOCX extraction") from error
    document = Document(path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            paragraphs.append(" | ".join(cell.text for cell in row.cells))
    paragraph_text = "\n".join(paragraphs)
    pages = [
        {
            "page": 1,
            "text": clean_text(paragraph_text),
            "text_method": "docx_text",
            "ocr_method": "not_requested",
            "render_status": "not_applicable",
            "image_path": "",
            "needs_visual_review": "yes",
            "visual_review_reason": "docx_tables_or_figures_require_locator",
            "ocr_quality": "not_applicable",
            "ocr_consensus": "not_applicable",
            "numeric_differences": "",
            "ocr_notes": "",
            "assay_numeric_tokens": "; ".join(
                numeric_tokens(paragraph_text, assay_context=True)
            ),
        }
    ]
    return pages


def xlsx_pages(path: Path) -> list[dict[str, object]]:
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise SystemExit("openpyxl is required for XLSX extraction") from error
    workbook = load_workbook(path, read_only=True, data_only=True)
    pages = []
    for index, sheet in enumerate(workbook.worksheets, start=1):
        lines = []
        for row in sheet.iter_rows(values_only=True):
            values = [clean_text(str(value)) for value in row if value is not None]
            if values:
                lines.append(" | ".join(values))
        text = "\n".join(lines)
        pages.append(
            {
                "page": index,
                "sheet": sheet.title,
                "text": text,
                "text_method": "xlsx_text",
                "ocr_method": "not_requested",
                "render_status": "not_applicable",
                "image_path": "",
                "needs_visual_review": "yes",
                "visual_review_reason": "spreadsheet_table_requires_targeted_review",
                "ocr_quality": "not_applicable",
                "ocr_consensus": "not_applicable",
                "numeric_differences": "",
                "ocr_notes": "",
                "assay_numeric_tokens": "; ".join(
                    numeric_tokens(text, assay_context=True)
                ),
            }
        )
    return pages


def tabular_pages(path: Path) -> list[dict[str, object]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return [
        {
            "page": 1,
            "text": text,
            "text_method": "tsv_text" if delimiter == "\t" else "csv_text",
            "ocr_method": "not_requested",
            "render_status": "not_applicable",
            "image_path": "",
            "needs_visual_review": "yes",
            "visual_review_reason": "tabular_data_requires_targeted_review",
            "ocr_quality": "not_applicable",
            "ocr_consensus": "not_applicable",
            "numeric_differences": "",
            "ocr_notes": "",
            "assay_numeric_tokens": "; ".join(
                numeric_tokens(text, assay_context=True)
            ),
        }
    ]


def xml_pages(path: Path) -> list[dict[str, object]]:
    root = ET.fromstring(path.read_bytes())
    paragraphs = []
    for element in root.iter():
        text = clean_text(" ".join(element.itertext()))
        if text and element.tag.rsplit("}", 1)[-1] in {
            "p",
            "title",
            "caption",
            "table-wrap",
            "sec",
        }:
            paragraphs.append(text)
    return [
        {
            "page": 1,
            "text": "\n\n".join(dict.fromkeys(paragraphs)),
            "text_method": "jats_xml_text",
            "ocr_method": "not_requested",
            "render_status": "not_applicable",
            "image_path": "",
            "needs_visual_review": "yes",
            "visual_review_reason": "jats_structure_requires_locator",
            "ocr_quality": "not_applicable",
            "ocr_consensus": "not_applicable",
            "numeric_differences": "",
            "ocr_notes": "",
            "assay_numeric_tokens": "; ".join(
                numeric_tokens("\n\n".join(paragraphs), assay_context=True)
            ),
        }
    ]


def html_pages(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return [
        {
            "page": 1,
            "text": clean_text(text),
            "text_method": "html_text",
            "ocr_method": "not_requested",
            "render_status": "not_applicable",
            "image_path": "",
            "needs_visual_review": "yes",
            "visual_review_reason": "html_requires_source_locator",
            "ocr_quality": "not_applicable",
            "ocr_consensus": "not_applicable",
            "numeric_differences": "",
            "ocr_notes": "",
            "assay_numeric_tokens": "; ".join(
                numeric_tokens(clean_text(text), assay_context=True)
            ),
        }
    ]


def text_pages(path: Path) -> list[dict[str, object]]:
    return [
        {
            "page": 1,
            "text": clean_text(path.read_text(encoding="utf-8", errors="replace")),
            "text_method": "plain_text",
            "ocr_method": "not_requested",
            "render_status": "not_applicable",
            "image_path": "",
            "needs_visual_review": "no",
            "visual_review_reason": "",
            "ocr_quality": "not_applicable",
            "ocr_consensus": "not_applicable",
            "numeric_differences": "",
            "ocr_notes": "",
            "assay_numeric_tokens": "; ".join(
                numeric_tokens(clean_text(text), assay_context=True)
            ),
        }
    ]


def image_pages(path: Path) -> list[dict[str, object]]:
    results, notes = ocr_variants(path, consensus=True)
    text, method = best_ocr_result(results)
    if not text:
        text = ""
        method = method or "ocr_unavailable"
    consensus_status, numeric_differences = numeric_agreement(
        [item[0] for item in results]
    )
    reasons = ["image_source_requires_visual_verification"]
    if consensus_status != "agree":
        reasons.append(f"ocr_numeric_{consensus_status}")
    if ocr_quality(text) == "low":
        reasons.append("low_ocr_quality")
    return [
        {
            "page": 1,
            "text": text,
            "text_method": method,
            "ocr_method": method,
            "render_status": "source_is_image",
            "image_path": str(path),
            "needs_visual_review": "yes",
            "visual_review_reason": "; ".join(reasons),
            "ocr_quality": ocr_quality(text),
            "ocr_consensus": consensus_status,
            "numeric_differences": "; ".join(numeric_differences),
            "ocr_notes": "; ".join(dict.fromkeys(notes)),
            "assay_numeric_tokens": "; ".join(
                numeric_tokens(text, assay_context=True)
            ),
        }
    ]


def extract(
    path: Path,
    output_dir: Path,
    ocr_mode: str,
    ocr_consensus: str,
    render_pages: bool,
    dpi: int,
    page_limit: int | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(
            path,
            output_dir,
            ocr_mode,
            ocr_consensus,
            render_pages,
            dpi,
            page_limit,
        )
    if suffix == ".docx":
        return docx_pages(path), []
    if suffix == ".xlsx":
        return xlsx_pages(path), []
    if suffix in {".csv", ".tsv"}:
        return tabular_pages(path), []
    if suffix in {".xml", ".nxml"}:
        return xml_pages(path), []
    if suffix in {".html", ".htm"}:
        return html_pages(path), []
    if suffix in {".txt", ".md"}:
        return text_pages(path), []
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return image_pages(path), []
    raise SystemExit(f"unsupported file type: {path}")


def expand_archives(paths: list[Path], output_dir: Path) -> list[Path]:
    expanded: list[Path] = []
    supported = {
        ".pdf",
        ".docx",
        ".xlsx",
        ".csv",
        ".tsv",
        ".xml",
        ".nxml",
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
    }
    for path in paths:
        if path.suffix.lower() != ".zip":
            expanded.append(path)
            continue
        destination_root = output_dir / "_archives" / re.sub(
            r"[^0-9A-Za-z._-]+", "_", path.stem
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(path) as archive:
                for index, member in enumerate(archive.infolist()):
                    if member.is_dir():
                        continue
                    member_name = Path(member.filename).name
                    suffix = Path(member_name).suffix.lower()
                    if suffix not in supported:
                        continue
                    destination = destination_root / f"{index:03d}_{safe_name(member_name)}"
                    with archive.open(member) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    expanded.append(destination)
        except zipfile.BadZipFile as error:
            raise SystemExit(f"invalid ZIP archive: {path}: {error}") from error
    return expanded


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")[:180] or "file"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--terms", default="")
    parser.add_argument("--ocr", choices=["off", "auto", "all"], default="auto")
    parser.add_argument(
        "--ocr-consensus",
        choices=["off", "auto", "all"],
        default="auto",
        help="Run a second enhanced OCR pass for numeric cross-checking.",
    )
    parser.add_argument("--render-pages", action="store_true")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--page-limit", type=int)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    terms = [term.strip() for term in args.terms.split(",") if term.strip()]
    hit_rows = []
    report_rows = []
    numeric_rows = []
    visual_rows = []

    documents = expand_archives(args.documents, args.output_dir)
    for document in documents:
        pages, tables = extract(
            document,
            args.output_dir,
            args.ocr,
            args.ocr_consensus,
            args.render_pages,
            args.dpi,
            args.page_limit,
        )
        stem = re.sub(r"[^0-9A-Za-z._-]+", "_", document.stem)
        jsonl_path = args.output_dir / f"{stem}_pages.jsonl"
        text_path = args.output_dir / f"{stem}.txt"
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for page in pages:
                handle.write(json.dumps(page, ensure_ascii=False) + "\n")
                report_rows.append(
                    {
                        "document": str(document),
                        "page": page["page"],
                        "chars": len(clean_text(str(page["text"]))),
                        "text_method": page["text_method"],
                        "ocr_method": page["ocr_method"],
                        "render_status": page["render_status"],
                        "image_path": page["image_path"],
                        "needs_visual_review": page["needs_visual_review"],
                        "visual_review_reason": page.get("visual_review_reason", ""),
                        "ocr_quality": page["ocr_quality"],
                        "ocr_consensus": page.get("ocr_consensus", "not_applicable"),
                        "numeric_differences": page.get("numeric_differences", ""),
                        "ocr_notes": page.get("ocr_notes", ""),
                    }
                )
                if page.get("numeric_differences"):
                    numeric_rows.append(
                        {
                            "document": str(document),
                            "page": page["page"],
                            "text_method": page["text_method"],
                            "ocr_method": page["ocr_method"],
                            "ocr_consensus": page.get("ocr_consensus", ""),
                            "numeric_differences": page.get("numeric_differences", ""),
                            "image_path": page.get("image_path", ""),
                        }
                    )
                if page.get("needs_visual_review") == "yes":
                    visual_rows.append(
                        {
                            "document": str(document),
                            "page": page["page"],
                            "priority": (
                                "high"
                                if (
                                    page.get("ocr_consensus") == "disagree"
                                    or page["ocr_quality"] == "low"
                                    or "native_ocr_numeric_difference"
                                    in str(page.get("visual_review_reason", ""))
                                )
                                else "medium"
                            ),
                            "reason": page.get("visual_review_reason", ""),
                            "image_path": page.get("image_path", ""),
                            "review_status": "pending",
                            "reviewer_locator": "",
                            "reviewer_note": "",
                        }
                    )
        text_path.write_text(
            "\n\n".join(
                f"=== PAGE {page['page']} [{page['text_method']}] ===\n{page['text']}"
                for page in pages
            ),
            encoding="utf-8",
        )
        if tables:
            with (args.output_dir / f"{stem}_tables.jsonl").open(
                "w", encoding="utf-8"
            ) as handle:
                for table in tables:
                    handle.write(json.dumps(table, ensure_ascii=False) + "\n")
        for page in pages:
            text = str(page["text"])
            lowered = text.lower()
            for term in terms:
                if term.lower() in lowered:
                    index = lowered.find(term.lower())
                    hit_rows.append(
                        {
                            "document": str(document),
                            "page": page["page"],
                            "term": term,
                            "text_method": page["text_method"],
                            "context": text[max(0, index - 160) : index + len(term) + 240],
                        }
                    )
    if hit_rows:
        with (args.output_dir / "term_hits.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(hit_rows[0]))
            writer.writeheader()
            writer.writerows(hit_rows)
    if report_rows:
        with (args.output_dir / "page_report.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(report_rows[0]))
            writer.writeheader()
            writer.writerows(report_rows)
    if numeric_rows:
        with (args.output_dir / "numeric_review.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(numeric_rows[0]))
            writer.writeheader()
            writer.writerows(numeric_rows)
    if visual_rows:
        with (args.output_dir / "visual_review_queue.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(visual_rows[0]))
            writer.writeheader()
            writer.writerows(visual_rows)
    print(
        json.dumps(
            {
                "documents": len(documents),
                "pages": len(report_rows),
                "term_hits": len(hit_rows),
                "numeric_review_pages": len(numeric_rows),
                "visual_review_pages": len(visual_rows),
                "ocr_pages": sum(
                    "windows_ocr" in row["ocr_method"]
                    or "tesseract" in row["ocr_method"]
                    for row in report_rows
                ),
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
