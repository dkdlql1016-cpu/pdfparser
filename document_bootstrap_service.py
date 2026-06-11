import shutil
import uuid

import run_layout
from storage_utils import iter_workspace_dirs


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
        "workspace_id": doc_id,
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
    upsert_seed_document_from_pdf_fn,
):
    if already_seeded:
        return True
    if not input_dir.exists():
        return True
    selected_seed = None
    for filename in default_file_manager_inputs:
        src = input_dir / filename
        if src.exists():
            selected_seed = (filename, src)
            break
    if not selected_seed:
        return True
    seed_key, seed_src = selected_seed
    existing_doc_id = None
    for candidate in iter_workspace_dirs(documents_dir):
        meta_path = candidate / "file_manager" / "meta.json"
        if not meta_path.exists():
            meta_path = candidate / "meta.json"
        meta = read_json_fn(meta_path, None) or {}
        if not meta:
            continue
        key = str(meta.get("seed_key") or "").strip()
        if key == seed_key:
            existing_doc_id = str(meta.get("doc_id") or candidate.name)
            break
    upsert_seed_document_from_pdf_fn(seed_src, seed_key=seed_key, existing_doc_id=existing_doc_id)
    return True
