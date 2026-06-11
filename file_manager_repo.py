from pathlib import Path

from storage_utils import iter_workspace_dirs


def _meta_path(workspace_dir: Path) -> Path:
    preferred = workspace_dir / "file_manager" / "meta.json"
    if preferred.exists():
        return preferred
    return workspace_dir / "meta.json"


def public_report_filename(meta, run_meta, *, normalize_document_title_fn):
    filename = str((run_meta or {}).get("filename") or "").strip()
    if filename.lower() in ("report.pdf", "prev_report.pdf", "old.pdf", "new.pdf", ""):
        title = normalize_document_title_fn((meta or {}).get("title") or "")
        if title:
            return title if title.lower().endswith(".pdf") else f"{title}.pdf"
        return "report.pdf"
    return filename


def list_documents_for_manager(documents_dir: Path, *, read_json_fn, normalize_document_title_fn):
    raw_items = []
    if not documents_dir.exists():
        return raw_items
    for candidate in iter_workspace_dirs(documents_dir):
        meta = read_json_fn(_meta_path(candidate), None)
        if not meta:
            continue
        # Backward compatibility: older documents may not have is_saved.
        # Treat missing flag as saved so existing user data remains visible.
        saved_flag = meta.get("is_saved")
        is_saved = True if saved_flag is None else bool(saved_flag)
        if not is_saved and not bool(meta.get("seed_key")):
            continue
        runs = meta.get("runs", []) or []
        latest = runs[-1] if runs else {}
        public_filename = public_report_filename(
            meta,
            latest,
            normalize_document_title_fn=normalize_document_title_fn,
        )
        raw_items.append({
            "workspace_id": meta.get("workspace_id") or meta.get("doc_id") or candidate.name,
            "doc_id": meta.get("doc_id") or candidate.name,
            "title": meta.get("title") or (latest.get("filename") or "Workspace"),
            "name": public_filename,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "run_count": len(runs),
            "latest_run_id": latest.get("run_id"),
            "latest_filename": public_filename,
            "latest_status": latest.get("status") or "unknown",
            "_explicit_saved": bool(saved_flag is True),
            "_seed": bool(meta.get("seed_key")),
        })
    # Deduplicate noisy legacy entries that share identical visible name/filename.
    # Keep the most likely canonical entry (explicitly saved > seeded > newest).
    dedup = {}
    for item in raw_items:
        key = (
            str(item.get("title") or "").strip().lower(),
            str(item.get("latest_filename") or "").strip().lower(),
        )
        prev = dedup.get(key)
        if not prev:
            dedup[key] = item
            continue
        prev_rank = (
            1 if prev.get("_explicit_saved") else 0,
            1 if prev.get("_seed") else 0,
            prev.get("updated_at") or prev.get("created_at") or "",
        )
        cur_rank = (
            1 if item.get("_explicit_saved") else 0,
            1 if item.get("_seed") else 0,
            item.get("updated_at") or item.get("created_at") or "",
        )
        if cur_rank > prev_rank:
            dedup[key] = item
    items = []
    for item in dedup.values():
        item.pop("_explicit_saved", None)
        item.pop("_seed", None)
        items.append(item)
    items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return items


def is_saved_or_seed_meta(meta):
    if not meta:
        return False
    saved_flag = meta.get("is_saved")
    is_saved = True if saved_flag is None else bool(saved_flag)
    return is_saved or bool(meta.get("seed_key"))


def find_document_id_by_title(title: str, documents_dir: Path, *, read_json_fn, normalize_document_title_fn, exclude_doc_id: str = None):
    wanted = normalize_document_title_fn(title).casefold()
    if not wanted or not documents_dir.exists():
        return None
    for candidate in iter_workspace_dirs(documents_dir):
        meta = read_json_fn(_meta_path(candidate), None)
        if not is_saved_or_seed_meta(meta):
            continue
        doc_id = str(meta.get("doc_id") or candidate.name)
        if exclude_doc_id and doc_id == exclude_doc_id:
            continue
        current_title = normalize_document_title_fn(meta.get("title") or "").casefold()
        if current_title and current_title == wanted:
            return doc_id
    return None


def document_title_exists(title: str, documents_dir: Path, *, read_json_fn, normalize_document_title_fn, exclude_doc_id: str = None):
    return find_document_id_by_title(
        title,
        documents_dir,
        read_json_fn=read_json_fn,
        normalize_document_title_fn=normalize_document_title_fn,
        exclude_doc_id=exclude_doc_id,
    ) is not None
