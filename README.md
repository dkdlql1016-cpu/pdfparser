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

For maintainers/handoff, see `ARCHITECTURE.md`, `RUN_LAYOUT.md`, and `CHARS_API_CONTRACT.md`.

On file-manager bootstrap, the app uses only one default seed source (`DEFAULT_FILE_MANAGER_INPUTS` first existing file). If a matching seed document already exists, it appends a new run to that same seed document instead of creating another document folder.

## Project Structure

- `app.py`: Flask entrypoint and composition root (wires blueprints and services).
- `routes/`: HTTP layer grouped by domain (`document`, `assessment`, `snapshot`, `file_manager`).
- `document_*_service.py`: document-domain business logic (pipeline, IO, diff analysis, review projection, snapshot, bootstrap).
- `assessment_service.py`, `ai_callers.py`, `ai_runtime_utils.py`: AI assessment orchestration and tool-calling runtime.
- `storage_utils.py`, `text_utils.py`, `section_context_utils.py`: shared helpers for storage/text/markdown section context.

## Features

1. Upload an initial report and review it in a single-pane PDF viewer.
2. Upload an updated report and run a previous/current diff.
3. Convert PDFs to markdown with `opendataloader_pdf.convert`.
4. Generate word-level diff output through `document_diff_extract.py`.
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
