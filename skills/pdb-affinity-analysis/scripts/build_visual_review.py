#!/usr/bin/env python3
"""Build page-crop contact sheets for required visual evidence review."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_queue(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        if path.is_dir():
            candidates = list(path.rglob("visual_review_queue.csv"))
        else:
            candidates = [path]
        for candidate in candidates:
            with candidate.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if clean(row.get("review_status")).lower() == "complete":
                        continue
                    image_path = Path(clean(row.get("image_path")))
                    if not image_path.is_absolute():
                        candidate_path = (
                            image_path
                            if image_path.exists()
                            else candidate.parent / image_path
                        )
                        image_path = candidate_path.resolve()
                    if not image_path.exists():
                        continue
                    rows.append(
                        {
                            **row,
                            "queue": str(candidate),
                            "resolved_image_path": str(image_path),
                        }
                    )
    return rows


def make_sheets(rows: list[dict[str, Any]], output_dir: Path, columns: int, max_pages: int) -> list[dict[str, Any]]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as error:
        raise SystemExit("Pillow is required to build visual review sheets") from error
    output_dir.mkdir(parents=True, exist_ok=True)
    thumb_width = 720
    thumb_height = 940
    label_height = 70
    margin = 24
    output = []
    for batch_index in range(0, len(rows), max_pages):
        batch = rows[batch_index : batch_index + max_pages]
        row_count = (len(batch) + columns - 1) // columns
        width = margin + columns * (thumb_width + margin)
        height = margin + row_count * (thumb_height + label_height + margin)
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        for local_index, item in enumerate(batch):
            source = Image.open(item["resolved_image_path"]).convert("RGB")
            source = ImageOps.contain(source, (thumb_width, thumb_height))
            column = local_index % columns
            row = local_index // columns
            x = margin + column * (thumb_width + margin)
            y = margin + row * (thumb_height + label_height + margin)
            sheet.paste(
                source,
                (
                    x + (thumb_width - source.width) // 2,
                    y + (thumb_height - source.height) // 2,
                ),
            )
            label = (
                f"{Path(item.get('document', '')).name} | page {item.get('page', '?')} "
                f"| {item.get('priority', '')}"
            )
            draw.text((x, y + thumb_height + 8), label[:120], fill="black", font=font)
            draw.text(
                (x, y + thumb_height + 28),
                clean(item.get("reason"))[:120],
                fill="#333333",
                font=font,
            )
            draw.text(
                (x, y + thumb_height + 48),
                Path(item["resolved_image_path"]).name[:120],
                fill="#555555",
                font=font,
            )
        destination = output_dir / f"visual_review_{batch_index // max_pages + 1:03d}.png"
        sheet.save(destination)
        output.append(
            {
                "contact_sheet": str(destination),
                "pages": len(batch),
                "items": batch,
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("queue", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=9)
    args = parser.parse_args()
    rows = read_queue(args.queue)
    if not rows:
        print("No pending rendered pages with visual_review_queue.csv entries were found.")
        return
    sheets = make_sheets(rows, args.output_dir, args.columns, args.max_pages)
    with (args.output_dir / "contact_sheet_index.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["contact_sheet", "page_index", "document", "page", "priority", "reason", "image_path"],
        )
        writer.writeheader()
        for sheet in sheets:
            for item in sheet["items"]:
                writer.writerow(
                    {
                        "contact_sheet": sheet["contact_sheet"],
                        "page_index": clean(item.get("page")),
                        "document": clean(item.get("document")),
                        "page": clean(item.get("page")),
                        "priority": clean(item.get("priority")),
                        "reason": clean(item.get("reason")),
                        "image_path": clean(item.get("resolved_image_path")),
                    }
                )
    print(f"{len(rows)} pages -> {len(sheets)} contact sheet(s) in {args.output_dir}")


if __name__ == "__main__":
    main()
