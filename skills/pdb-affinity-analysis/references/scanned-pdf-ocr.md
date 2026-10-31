# Scanned and image-based PDFs

## Why text extraction fails

An image-only PDF has no embedded text layer. A normal `pypdf` or publisher
text extractor can return empty pages while the figures, tables and compound
numbers are still visible.

## Required fallback

1. Render every relevant PDF page to PNG.
2. OCR the image with the Windows OCR helper:

```bash
python scripts/extract_document_text.py \
  input.pdf \
  --ocr all \
  --ocr-consensus all \
  --render-pages \
  --dpi 300 \
  --terms "IC50,Kd,Table,compound" \
  --output-dir extracted_text
```

3. For native-text PDFs, use `--ocr auto --ocr-consensus auto`; for SI/patent
   table pages use `--ocr all --ocr-consensus all`.
4. Open the rendered page image and visually verify all values, units,
   qualifiers, compound positions and table headers.
5. Use `page_report.csv`, `numeric_review.csv` and
   `visual_review_queue.csv` to find numeric disagreement,
   `needs_visual_review=yes` or `ocr_quality=low`.
6. Build a contact sheet:

```bash
python scripts/build_visual_review.py extracted_text \
  --output-dir visual_review \
  --columns 3 \
  --max-pages 9
```

7. Open the contact sheet and the original full-resolution page. Record a
   receipt with the page, contact sheet, compound/row/column and observed value.
8. If Windows OCR is unavailable, use an installed Tesseract binary, then a
   multimodal page inspection or an authorized Computer Use browser session.

## Acceptance rule

An OCR result alone is not enough for a critical number. The record must state:

- rendered page path;
- OCR method and quality;
- whether a human/vision inspection was performed;
- exact table/figure locator.
- whether the OCR passes agreed and which numeric tokens disagreed;
- a visual-review receipt.

If a digit or decimal is ambiguous after visual review, leave the value
unresolved instead of choosing a plausible number.
