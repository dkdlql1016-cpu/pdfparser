import json
import subprocess
import sys
from pathlib import Path

import fitz

from text_utils import keep_token, norm_token


def extract_pdf_words(pdf_path: Path):
    words = []
    page_sizes = []
    with fitz.open(str(pdf_path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            page_sizes.append({"width": float(page.rect.width), "height": float(page.rect.height)})
            raw_words = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7], w[1], w[0]))
            for w in raw_words:
                x0, y0, x1, y1, text, block, line, word_no = w[:8]
                if not keep_token(text):
                    continue
                words.append({
                    "idx": len(words),
                    "text": text,
                    "norm": norm_token(text),
                    "page": page_no,
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                    "block": int(block),
                    "line": int(line),
                    "word_no": int(word_no),
                })
    return words, page_sizes


def render_pdf_page(pdf_path: Path, page_no: int, zoom: float):
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def run_opendataloader_to_markdown(pdf_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import opendataloader_pdf
    except Exception as e:
        raise RuntimeError("opendataloader_pdf import failed. Cause: " + str(e))
    kwargs = dict(
        input_path=[str(pdf_path)],
        output_dir=str(output_dir),
        format="markdown",
        table_method="default",
        reading_order="xycut",
        markdown_page_separator="--- %page-number% ---",
    )
    try:
        opendataloader_pdf.convert(**kwargs, include_header_footer=False)
    except TypeError:
        opendataloader_pdf.convert(**kwargs)
    candidates = []
    for pattern in ("*.md", "*.markdown", "**/*.md", "**/*.markdown"):
        candidates.extend(output_dir.glob(pattern))
    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"OpenDataLoader ran but markdown file was not found: {output_dir}")
    return candidates[0]


def run_diff_extract(old_md: Path, new_md: Path, result_json: Path, *, base_dir: Path):
    cmd = [
        sys.executable,
        str(base_dir / "document_diff_extract.py"),
        str(old_md),
        str(new_md),
        "-o",
        str(result_json),
        "--unit",
        "word",
        "--engine",
        "auto",
        "--diff-algorithm",
        "histogram",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="backslashreplace")
    if proc.returncode != 0 or not result_json.exists():
        raise RuntimeError(proc.stderr or proc.stdout or "document_diff_extract.py failed")
    return json.loads(result_json.read_text(encoding="utf-8"))
