import shutil
import uuid

import run_layout


def create_seed_document_from_pdf(
    pdf_path,
    *,
    seed_key,
    document_run_dir_fn,
    utc_now_fn,
    save_document_meta_fn,
):
    doc_id = uuid.uuid4().hex[:12]
    run_id = uuid.uuid4().hex[:12]
    run_dir = document_run_dir_fn(doc_id, run_id)
    run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
    report_pdf = run_layout.source_pdf(run_dir, "current")
    shutil.copy2(pdf_path, report_pdf)
    created_at = utc_now_fn()
    meta = {
        "doc_id": doc_id,
        "title": pdf_path.stem,
        "seed_key": seed_key,
        "is_saved": True,
        "created_at": created_at,
        "updated_at": created_at,
        "runs": [
            {
                "run_id": run_id,
                "kind": "initial",
                "status": "ready",
                "created_at": created_at,
                "filename": pdf_path.name,
                "has_diff": False,
                "has_ai_assessment": False,
            }
        ],
    }
    save_document_meta_fn(meta)


def ensure_default_file_manager_documents(
    *,
    already_seeded,
    input_dir,
    documents_dir,
    default_file_manager_inputs,
    read_json_fn,
    create_seed_document_from_pdf_fn,
):
    if already_seeded:
        return True
    if not input_dir.exists():
        return True
    existing_seed_keys = set()
    existing_filenames = set()
    for candidate in documents_dir.iterdir():
        if not candidate.is_dir():
            continue
        meta = read_json_fn(candidate / "meta.json", None) or {}
        key = str(meta.get("seed_key") or "").strip()
        if key:
            existing_seed_keys.add(key)
        runs = meta.get("runs", []) or []
        if runs:
            filename = str((runs[-1] or {}).get("filename") or "").strip()
            if filename:
                existing_filenames.add(filename)
    for filename in default_file_manager_inputs:
        if filename in existing_seed_keys or filename in existing_filenames:
            continue
        src = input_dir / filename
        if not src.exists():
            continue
        create_seed_document_from_pdf_fn(src, seed_key=filename)
    return True
