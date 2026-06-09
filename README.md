# PDF Review Workspace

## Run

```powershell
cd pdfparser
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open in a browser:

```text
http://127.0.0.1:8000
```

## Features

1. Upload an initial report and review it in a single-pane PDF viewer.
2. Upload an updated report and run a previous/current diff.
3. Convert PDFs to markdown with `opendataloader_pdf.convert`.
4. Generate word-level diff output through `diff_extract.py`.
5. Align markdown diff segments to PDF word bounding boxes.
6. Add document-level review comments anchored to selected PDF text.
7. Store report versions under `documents/<doc_id>/runs/<run_id>`.
8. Export PDF annotations from saved reviews.

## OpenDataLoader Configuration

```python
opendataloader_pdf.convert(
    input_path=[str(pdf_path)],
    output_dir=str(output_dir),
    format="markdown",
    table_method="default",
    markdown_page_separator="--- %page-number% ---",
    reading_order="xycut",
)
```
