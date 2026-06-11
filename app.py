import importlib
import importlib.util
import logging
import os
import shutil
import uuid
from pathlib import Path

import flask.cli
from flask import Flask, Response, jsonify, request, send_file

import document_semantic as semantic
import run_layout
from ai_config_utils import load_system_prompt, required_ai_api_key, required_ai_api_key_name, resolve_ai_model
from ai_callers import (
    call_ai_assessment as callers_call_ai_assessment,
    call_ai_assessment_batch as callers_call_ai_assessment_batch,
    call_ai_change_assessment as callers_call_ai_change_assessment,
    call_ai_change_assessment_batch as callers_call_ai_change_assessment_batch,
)
from assessment_service import (
    forward_change_assessment_to_review_service,
    run_ai_assessment_service,
    run_change_ai_assessment_service,
)
from assessment_prompt_builders import (
    build_assessment_prompt,
    build_change_assessment_prompt,
    review_thread_for_prompt,
    review_threads_for_prompt_from_projection,
)
from assessment_normalizers import (
    anchor_section_id,
    harmonize_change_items,
    run_meta_for,
)
from file_manager_repo import (
    ensure_default_workspace as repo_ensure_default_workspace,
    find_file_by_id as repo_find_file_by_id,
    find_file_in_meta as repo_find_file_in_meta,
    list_documents_for_manager as repo_list_documents_for_manager,
    list_workspace_files as repo_list_workspace_files,
    migrate_workspace_files as repo_migrate_workspace_files,
    prune_ephemeral_workspaces as repo_prune_ephemeral_workspaces,
    resolve_workspace_file_pdf as repo_resolve_workspace_file_pdf,
    workspace_file_title_exists as repo_workspace_file_title_exists,
    workspace_title_exists as repo_workspace_title_exists,
)
from file_manager_service import (
    canonical_review_from_anchor as service_canonical_review_from_anchor,
    delete_workspace_file as service_delete_workspace_file,
    open_workspace_saved_file as service_open_workspace_saved_file,
    overwrite_saved_document as service_overwrite_saved_document,
    rename_workspace_file as service_rename_workspace_file,
    run_side_pdf_info as service_run_side_pdf_info,
    save_run_side_file as service_save_run_side_file,
)
from document_pipeline_service import (
    build_chars_from_words as pipeline_build_chars_from_words,
    copy_if_exists as pipeline_copy_if_exists,
    estimated_char_count as pipeline_estimated_char_count,
    filter_words_for_chars as pipeline_filter_words_for_chars,
    process_document_diff_run as pipeline_process_document_diff_run,
    process_single_document_run as pipeline_process_single_document_run,
    write_unmarked_md_copy as pipeline_write_unmarked_md_copy,
)
from document_io_service import (
    extract_pdf_words as io_extract_pdf_words,
    render_pdf_page as io_render_pdf_page,
    run_diff_extract as io_run_diff_extract,
    run_opendataloader_to_markdown as io_run_opendataloader_to_markdown,
)
from document_diff_analysis_service import (
    alignment_report as analysis_alignment_report,
    align_stream_to_pdf_words as analysis_align_stream_to_pdf_words,
    attach_nearest_equal_anchors as analysis_attach_nearest_equal_anchors,
    build_side_stream as analysis_build_side_stream,
    context_signature as analysis_context_signature,
    is_weak_move_token as analysis_is_weak_move_token,
    make_highlights_and_changes as analysis_make_highlights_and_changes,
    map_result_segments_to_pdf_indices as analysis_map_result_segments_to_pdf_indices,
    merge_highlight_rects_server as analysis_merge_highlight_rects_server,
    move_norm_token as analysis_move_norm_token,
    near_equal_anchor_score as analysis_near_equal_anchor_score,
    suppress_layout_moves as analysis_suppress_layout_moves,
    weighted_jaccard as analysis_weighted_jaccard,
)
from document_index_service import (
    build_new_pdf_index as index_build_new_pdf_index,
    clean_structural_text as index_clean_structural_text,
    detect_notes_start_page as index_detect_notes_start_page,
    extract_fs_from_pdf_visual_lines as index_extract_fs_from_pdf_visual_lines,
    extract_note_headings_from_md as index_extract_note_headings_from_md,
    find_note_anchor_in_words as index_find_note_anchor_in_words,
    find_section_heading_line as index_find_section_heading_line,
    first_line_on_page as index_first_line_on_page,
    fs_section_id as index_fs_section_id,
    group_pdf_visual_lines as index_group_pdf_visual_lines,
    inject_section_markers as index_inject_section_markers,
    is_amount_like_token as index_is_amount_like_token,
    line_bbox as index_line_bbox,
    line_text as index_line_text,
    md_line_pages as index_md_line_pages,
    normalize_title_for_index as index_normalize_title_for_index,
    note_heading_candidate as index_note_heading_candidate,
    numeric_density_from_text as index_numeric_density_from_text,
    refine_title_with_pdf_line as index_refine_title_with_pdf_line,
    search_sequence as index_search_sequence,
    section_id_for_index_item as index_section_id_for_index_item,
    slug_for_section_id as index_slug_for_section_id,
    title_match_score as index_title_match_score,
)
from document_export_service import export_annotated_pdf as service_export_annotated_pdf
from document_review_store_service import (
    append_review_for_run as store_append_review_for_run,
    attach_run_reviews_to_file as store_attach_run_reviews_to_file,
    delete_review_in_run_storage as store_delete_review_in_run_storage,
    load_all_workspace_reviews as store_load_all_workspace_reviews,
    load_file_reviews as store_load_file_reviews,
    load_reviews_for_run as store_load_reviews_for_run,
    migrate_legacy_workspace_reviews as store_migrate_legacy_workspace_reviews,
    primary_file_id_for_run_session as store_primary_file_id_for_run_session,
    resolve_file_ids_for_run_session as store_resolve_file_ids_for_run_session,
    save_file_reviews as store_save_file_reviews,
    storage_file_id_for_run_session as store_storage_file_id_for_run_session,
    save_reviews_for_run as store_save_reviews_for_run,
    update_review_in_run_storage as store_update_review_in_run_storage,
)
from document_review_projection_service import (
    anchor_for_selection as projection_anchor_for_selection,
    apply_md_metadata_to_anchor as projection_apply_md_metadata_to_anchor,
    assessment_anchor_for_previous_side as projection_assessment_anchor_for_previous_side,
    build_anchor_md_metadata as projection_build_anchor_md_metadata,
    compact_equal_ref as projection_compact_equal_ref,
    copied_comments_for_forward as projection_copied_comments_for_forward,
    current_word_ids_from_anchor_md as projection_current_word_ids_from_anchor_md,
    current_word_ids_from_md_equal_anchor as projection_current_word_ids_from_md_equal_anchor,
    document_reviews_for_run as projection_document_reviews_for_run,
    equal_refs_for_selection as projection_equal_refs_for_selection,
    find_word_sequence_by_text as projection_find_word_sequence_by_text,
    map_anchor_to_current_md_anchor as projection_map_anchor_to_current_md_anchor,
    md_equal_entries_for_anchor as projection_md_equal_entries_for_anchor,
    migrate_previous_review_to_current as projection_migrate_previous_review_to_current,
    review_projection_for_anchor as projection_review_projection_for_anchor,
    run_side_anchor_key as projection_run_side_anchor_key,
    word_ids_in_page_bbox as projection_word_ids_in_page_bbox,
)
from document_assessment_context_service import (
    assessment_reviews_for_run as assess_assessment_reviews_for_run,
    build_assessment_context_pack as assess_build_assessment_context_pack,
    build_change_assessment_context_pack as assess_build_change_assessment_context_pack,
    build_change_groups_for_run as assess_build_change_groups_for_run,
    change_by_id as assess_change_by_id,
    group_assessment_entries_by_report_section as assess_group_assessment_entries_by_report_section,
    nearest_words_for_anchor as assess_nearest_words_for_anchor,
    new_word_ids_for_change as assess_new_word_ids_for_change,
    related_diff_changes_for_review as assess_related_diff_changes_for_review,
    reviews_for_sections as assess_reviews_for_sections,
    section_id_for_change_anchor as assess_section_id_for_change_anchor,
)
from document_snapshot_service import (
    backfill_snapshot_change_counts as snapshot_backfill_snapshot_change_counts,
    create_run_snapshot as snapshot_create_run_snapshot,
    delete_document_snapshot as snapshot_delete_document_snapshot,
    prune_document_snapshots as snapshot_prune_document_snapshots,
    rename_document_snapshot as snapshot_rename_document_snapshot,
    snapshot_artifacts as snapshot_snapshot_artifacts,
    snapshot_counts as snapshot_snapshot_counts,
    snapshot_side_file as snapshot_snapshot_side_file,
)
from routes.assessment_routes import create_assessment_blueprint
from routes.document_routes import create_document_blueprint
from routes.file_manager_routes import create_file_manager_blueprint
from routes.snapshot_routes import create_snapshot_blueprint
from section_context_utils import (
    available_assessment_sections,
    infer_section_id_for_anchor,
    read_md_lines,
    section_entries,
    strip_section_markers,
)
from storage_utils import (
    document_dir as _document_dir,
    document_meta_path as _document_meta_path,
    document_reviews_path as _document_reviews_path,
    document_run_dir as _document_run_dir,
    document_snapshot_dir as _document_snapshot_dir,
    document_snapshots_dir as _document_snapshots_dir,
    migrate_legacy_workspaces as _migrate_legacy_workspaces,
    iter_workspace_dirs,
    read_json as _read_json,
    utc_now as _utc_now,
    workspace_file_dir as _workspace_file_dir,
    workspace_stored_pdf_path as _workspace_stored_pdf_path,
    write_json as _write_json,
)
from text_utils import keep_token, norm_token, normalize_document_title, trim_for_prompt

BASE_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("dotenv"):
    importlib.import_module("dotenv").load_dotenv(BASE_DIR / ".env")
DOCUMENTS_DIR = BASE_DIR / "documents"
DOCUMENTS_DIR.mkdir(exist_ok=True)
_migrate_legacy_workspaces(DOCUMENTS_DIR)

# Fixed test workspace: the app always boots into this workspace. Its file_manager
# files, per-file reviews, and snapshots persist across sessions; analysis runs do not.
DEFAULT_WORKSPACE_ID = os.environ.get("DEFAULT_WORKSPACE_ID", "57199b22be39")

app = Flask(__name__)


# =========================================================
# PDF extraction / rendering
# =========================================================

def extract_pdf_words(pdf_path: Path):
    return io_extract_pdf_words(pdf_path)


def render_pdf_page(pdf_path: Path, page_no: int, zoom: float):
    return io_render_pdf_page(pdf_path, page_no, zoom)


# =========================================================
# OpenDataLoader / diff extraction
# =========================================================

def run_opendataloader_to_markdown(pdf_path: Path, output_dir: Path) -> Path:
    return io_run_opendataloader_to_markdown(pdf_path, output_dir)


def run_diff_extract(old_md: Path, new_md: Path, result_json: Path):
    return io_run_diff_extract(old_md, new_md, result_json, base_dir=BASE_DIR)


# =========================================================
# Result segment to PDF word alignment
# =========================================================

def build_side_stream(segments, side):
    return analysis_build_side_stream(segments, side)


def align_stream_to_pdf_words(stream, pdf_words):
    return analysis_align_stream_to_pdf_words(stream, pdf_words)


def map_result_segments_to_pdf_indices(segments, old_words, new_words):
    return analysis_map_result_segments_to_pdf_indices(segments, old_words, new_words)


# =========================================================
# Layout/read-order move suppression
# =========================================================

def move_norm_token(value):
    return analysis_move_norm_token(value)


def is_weak_move_token(n):
    return analysis_is_weak_move_token(n)


def context_signature(segments, idx, radius=50):
    return analysis_context_signature(segments, idx, radius=radius)


def weighted_jaccard(a, b):
    return analysis_weighted_jaccard(a, b)


def near_equal_anchor_score(segments, di, ai, radius=14):
    return analysis_near_equal_anchor_score(segments, di, ai, radius=radius)


def suppress_layout_moves(segments, window=220):
    return analysis_suppress_layout_moves(segments, window=window)


# =========================================================
# Index extraction - MD first, PDF anchor second
# =========================================================

def line_bbox(words):
    return index_line_bbox(words)


def line_text(words):
    return index_line_text(words)


def is_amount_like_token(text):
    return index_is_amount_like_token(text)


def numeric_density_from_text(text):
    return index_numeric_density_from_text(text)


def clean_structural_text(s):
    return index_clean_structural_text(s)


def normalize_title_for_index(title):
    return index_normalize_title_for_index(title)


def slug_for_section_id(value):
    return index_slug_for_section_id(value)


def fs_section_id(label):
    return index_fs_section_id(label)


def section_id_for_index_item(item):
    return index_section_id_for_index_item(item)


def md_line_pages(lines):
    return index_md_line_pages(lines)


def title_match_score(text, title):
    return index_title_match_score(text, title)


def note_heading_candidate(lines, idx, note_no, title):
    return index_note_heading_candidate(lines, idx, note_no, title)


def find_section_heading_line(lines, line_pages, item):
    return index_find_section_heading_line(lines, line_pages, item)


def inject_section_markers(md_path: Path, index_items):
    return index_inject_section_markers(md_path, index_items)


def group_pdf_visual_lines(words, y_tolerance=3.2):
    return index_group_pdf_visual_lines(words, y_tolerance=y_tolerance)


def first_line_on_page(visual_lines, page_no):
    return index_first_line_on_page(visual_lines, page_no)


def detect_notes_start_page(new_md_path: Path, visual_lines):
    return index_detect_notes_start_page(new_md_path, visual_lines)


def search_sequence(words, needle_tokens):
    return index_search_sequence(words, needle_tokens)


def find_note_anchor_in_words(note_no, title, new_words, preferred_page=None, notes_start_page=None):
    return index_find_note_anchor_in_words(note_no, title, new_words, preferred_page=preferred_page, notes_start_page=notes_start_page)


def refine_title_with_pdf_line(note_no, md_title, visual_lines, preferred_page=None):
    return index_refine_title_with_pdf_line(note_no, md_title, visual_lines, preferred_page=preferred_page)


def extract_note_headings_from_md(new_md_path: Path, notes_start_page):
    return index_extract_note_headings_from_md(new_md_path, notes_start_page)


def extract_fs_from_pdf_visual_lines(visual_lines):
    return index_extract_fs_from_pdf_visual_lines(visual_lines)


def build_new_pdf_index(new_md_path: Path, new_words, page_count):
    return index_build_new_pdf_index(new_md_path, new_words, page_count)


# =========================================================
# Highlight / change generation
# =========================================================

def attach_nearest_equal_anchors(changes, mapped, old_words, new_words):
    return analysis_attach_nearest_equal_anchors(changes, mapped, old_words, new_words)


def make_highlights_and_changes(segments, old_words, new_words):
    return analysis_make_highlights_and_changes(segments, old_words, new_words)


def merge_highlight_rects_server(highlights, y_tolerance=5.5, max_gap=42, pad_x=0.8, pad_y=0.9):
    return analysis_merge_highlight_rects_server(highlights, y_tolerance=y_tolerance, max_gap=max_gap, pad_x=pad_x, pad_y=pad_y)


def alignment_report(segments, old_words, new_words):
    return analysis_alignment_report(segments, old_words, new_words)


# =========================================================
# PDF annotation export
# =========================================================

def export_annotated_pdf(run_dir: Path, side: str) -> bytes:
    return service_export_annotated_pdf(run_dir, side)


# =========================================================
# Document / run pipeline (Phase 2)
# =========================================================

def utc_now():
    return _utc_now()


def read_json(path: Path, default=None):
    return _read_json(path, default)


def write_json(path: Path, data):
    _write_json(path, data)


def document_dir(doc_id):
    return _document_dir(DOCUMENTS_DIR, doc_id)


def document_run_dir(doc_id, run_id):
    return _document_run_dir(DOCUMENTS_DIR, doc_id, run_id)


def document_snapshots_dir(doc_id):
    return _document_snapshots_dir(DOCUMENTS_DIR, doc_id)


def document_snapshot_dir(doc_id, snapshot_id):
    return _document_snapshot_dir(DOCUMENTS_DIR, doc_id, snapshot_id)


def workspace_file_dir(doc_id, file_id):
    return _workspace_file_dir(DOCUMENTS_DIR, doc_id, file_id)


def workspace_file_pdf_path(doc_id, file_id, filename=None):
    return _workspace_stored_pdf_path(DOCUMENTS_DIR, doc_id, file_id, filename)


def resolve_workspace_file_pdf(doc_id, file_id, entry=None):
    return repo_resolve_workspace_file_pdf(DOCUMENTS_DIR, doc_id, file_id, entry=entry)


def document_meta_path(doc_id):
    return _document_meta_path(DOCUMENTS_DIR, doc_id)


def load_document_meta(doc_id):
    workspace_dir = document_dir(doc_id)
    from storage_utils import ensure_canonical_meta_path

    ensure_canonical_meta_path(workspace_dir)
    meta = read_json(document_meta_path(doc_id))
    if meta:
        meta["workspace_id"] = str(meta.get("workspace_id") or doc_id)
    return meta


def save_document_meta(meta):
    canonical_id = str(meta.get("workspace_id") or "")
    if not canonical_id:
        raise ValueError("workspace_id is required")
    meta["workspace_id"] = canonical_id
    meta["updated_at"] = utc_now()
    write_json(document_meta_path(canonical_id), meta)


def migrate_all_workspace_storage():
    from storage_utils import ensure_canonical_meta_path, prune_stale_temp_dirs

    prune_stale_temp_dirs(DOCUMENTS_DIR)
    repo_prune_ephemeral_workspaces(DOCUMENTS_DIR, read_json_fn=read_json)
    if not DOCUMENTS_DIR.exists():
        return
    for candidate in iter_workspace_dirs(DOCUMENTS_DIR):
        ensure_canonical_meta_path(candidate)
        doc_id = candidate.name
        meta = read_json(document_meta_path(doc_id), None)
        if not meta:
            continue
        if meta.get("storage_layout_v2"):
            continue
        canonical_id = str(meta.get("workspace_id") or doc_id)
        meta["workspace_id"] = canonical_id
        if repo_migrate_workspace_files(meta, DOCUMENTS_DIR, canonical_id):
            meta["storage_layout_v2"] = True
            save_document_meta(meta)
        elif not any(r.get("kind") == "file" for r in (meta.get("runs") or [])):
            meta["storage_layout_v2"] = True
            save_document_meta(meta)


def purge_ephemeral_analysis_runs():
    """Analysis is session-scoped.

    File Manager files, their per-file reviews, and snapshots are durable and survive
    restarts. Analysis runs (``runs/``) are not: unless a run was frozen into a snapshot,
    it is dropped on each startup so a fresh session begins with no live analysis.
    """
    if not DOCUMENTS_DIR.exists():
        return
    for candidate in iter_workspace_dirs(DOCUMENTS_DIR):
        runs_dir = candidate / "runs"
        if runs_dir.exists():
            shutil.rmtree(runs_dir, ignore_errors=True)
        doc_id = candidate.name
        meta = read_json(document_meta_path(doc_id), None)
        if not meta or not meta.get("runs"):
            continue
        meta["workspace_id"] = str(meta.get("workspace_id") or doc_id)
        meta["runs"] = []
        save_document_meta(meta)


def prune_dangling_file_entries():
    """Drop File Manager file entries whose backing directory no longer exists.

    A file entry must always have a directory holding its PDF (and reviews.json). A phantom
    entry -- meta record with no backing dir -- can still WIN run->file resolution by an exact
    filename match and then surface zero reviews, making a re-uploaded report look empty even
    though the real file's reviews sit safely on disk under a different (stem-matched) entry.
    Removing these dangling entries keeps resolution pointed at files that actually hold data.
    """
    if not DOCUMENTS_DIR.exists():
        return
    for candidate in iter_workspace_dirs(DOCUMENTS_DIR):
        doc_id = candidate.name
        meta = read_json(document_meta_path(doc_id), None)
        if not meta:
            continue
        files = meta.get("files") or []
        kept = [f for f in files if f.get("file_id") and workspace_file_dir(doc_id, f["file_id"]).exists()]
        if len(kept) != len(files):
            meta["files"] = kept
            save_document_meta(meta)


migrate_all_workspace_storage()
purge_ephemeral_analysis_runs()
prune_dangling_file_entries()


def list_documents_for_manager():
    return repo_list_documents_for_manager(
        DOCUMENTS_DIR,
        read_json_fn=read_json,
        normalize_document_title_fn=normalize_document_title,
    )


def ensure_default_workspace():
    return repo_ensure_default_workspace(
        DOCUMENTS_DIR,
        read_json_fn=read_json,
        write_json_fn=write_json,
        normalize_document_title_fn=normalize_document_title,
        utc_now_fn=utc_now,
        preferred_workspace_id=DEFAULT_WORKSPACE_ID,
    )


def list_workspace_files(doc_id):
    return repo_list_workspace_files(
        doc_id,
        DOCUMENTS_DIR,
        read_json_fn=read_json,
        normalize_document_title_fn=normalize_document_title,
    )


def workspace_file_title_exists(doc_id, title):
    return repo_workspace_file_title_exists(
        doc_id,
        title,
        DOCUMENTS_DIR,
        read_json_fn=read_json,
        normalize_document_title_fn=normalize_document_title,
    )


def workspace_title_exists(title: str, *, exclude_workspace_id: str = None):
    return repo_workspace_title_exists(
        title,
        DOCUMENTS_DIR,
        read_json_fn=read_json,
        normalize_document_title_fn=normalize_document_title,
        exclude_workspace_id=exclude_workspace_id,
    )


def overwrite_saved_document(source_workspace_id, source_run_id, target_workspace_id):
    return service_overwrite_saved_document(
        source_workspace_id,
        source_run_id,
        target_workspace_id,
        documents_dir=DOCUMENTS_DIR,
        document_dir_fn=document_dir,
        load_document_meta_fn=load_document_meta,
        read_json_fn=read_json,
        utc_now_fn=utc_now,
        write_json_fn=write_json,
    )


def run_side_pdf_info(doc_id, run_id, run_dir: Path, side: str):
    return service_run_side_pdf_info(
        doc_id,
        run_id,
        run_dir,
        side,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        read_json_fn=read_json,
    )


def canonical_review_from_anchor(doc_id, run_id, anchor, *, status="open", comments=None, text="", source_review_id=None, source_side=None, created_at=None):
    return service_canonical_review_from_anchor(
        doc_id,
        run_id,
        anchor,
        utc_now_fn=utc_now,
        status=status,
        comments=comments,
        text=text,
        source_review_id=source_review_id,
        source_side=source_side,
        created_at=created_at,
    )


def save_run_side_file(workspace_id, run_id, side, *, title=None, overwrite_existing=False):
    # A plain Save (no explicit new name) updates the File Manager file this run is already bound
    # to -- the same file its reviews live on -- instead of forking a duplicate entry. Without
    # this, an upload that resolves to an existing file (e.g. re-uploading v1) saves to a brand
    # new same-named file with no reviews, which then shadows the real file on the next upload.
    # Save As (explicit title) still creates a new file.
    target_file_id = None
    if not (title and str(title).strip()):
        meta = load_document_meta(workspace_id)
        if meta:
            target_file_id = store_primary_file_id_for_run_session(
                meta, run_id, find_file_by_id_fn=find_file_by_id
            )
    payload, status = service_save_run_side_file(
        workspace_id,
        run_id,
        side,
        document_run_dir_fn=document_run_dir,
        run_side_pdf_info_fn=run_side_pdf_info,
        load_document_meta_fn=load_document_meta,
        find_file_in_workspace_fn=find_file_in_workspace,
        normalize_document_title_fn=normalize_document_title,
        workspace_file_title_exists_fn=workspace_file_title_exists,
        save_document_meta_fn=save_document_meta,
        workspace_stored_pdf_fn=workspace_file_pdf_path,
        utc_now_fn=utc_now,
        title=title,
        overwrite_existing=overwrite_existing,
        target_file_id=target_file_id,
    )
    # Save / Save As persists only the PDF + meta entry. Carry the current run's reviews onto
    # the saved file so they survive the startup purge of ephemeral runs (reviews are stored
    # per report). Best-effort: a review-copy failure must not fail the file save itself.
    if status in (200, 201) and payload.get("file_id"):
        try:
            store_attach_run_reviews_to_file(
                workspace_id,
                run_id,
                payload["file_id"],
                side,
                documents_dir=DOCUMENTS_DIR,
                load_document_meta_fn=load_document_meta,
                find_file_by_id_fn=find_file_by_id,
                read_json_fn=read_json,
                write_json_fn=write_json,
                migrate_legacy_fn=migrate_workspace_reviews,
            )
        except Exception:
            pass
    return payload, status


def delete_workspace_file(workspace_id, file_id):
    return service_delete_workspace_file(
        workspace_id,
        file_id,
        load_document_meta_fn=load_document_meta,
        workspace_file_dir_fn=workspace_file_dir,
        save_document_meta_fn=save_document_meta,
        find_file_by_id_fn=find_file_by_id,
        utc_now_fn=utc_now,
    )


def rename_workspace_file(workspace_id, file_id, title):
    return service_rename_workspace_file(
        workspace_id,
        file_id,
        title,
        load_document_meta_fn=load_document_meta,
        save_document_meta_fn=save_document_meta,
        find_file_by_id_fn=find_file_by_id,
        normalize_document_title_fn=normalize_document_title,
        workspace_file_title_exists_fn=workspace_file_title_exists,
        workspace_stored_pdf_fn=workspace_file_pdf_path,
        utc_now_fn=utc_now,
    )


def open_workspace_saved_file(workspace_id, file_id):
    return service_open_workspace_saved_file(
        workspace_id,
        file_id,
        document_run_dir_fn=document_run_dir,
        load_document_meta_fn=load_document_meta,
        find_file_by_id_fn=find_file_by_id,
        resolve_workspace_file_pdf_fn=resolve_workspace_file_pdf,
        process_single_document_run_fn=process_single_document_run,
        read_json_fn=read_json,
        document_reviews_for_run_fn=document_reviews_for_run,
        semantic_save_reviews_fn=semantic.save_reviews,
    )


def find_file_in_workspace(meta, title):
    return repo_find_file_in_meta(
        meta,
        title,
        normalize_document_title_fn=normalize_document_title,
    )


def find_file_by_id(meta, file_id):
    return repo_find_file_by_id(meta, file_id)



def get_uploaded_pdf(field_names=("pdf", "report_pdf", "file")):
    for name in field_names:
        if name in request.files:
            return request.files[name]
    return None


def copy_if_exists(src: Path, dst: Path):
    pipeline_copy_if_exists(src, dst)


def write_unmarked_md_copy(src: Path, dst: Path):
    return pipeline_write_unmarked_md_copy(
        src,
        dst,
        strip_section_markers_fn=strip_section_markers,
        read_md_lines_fn=read_md_lines,
    )


def build_chars_from_words(words):
    return pipeline_build_chars_from_words(words)


def filter_words_for_chars(words, *, page=None, page_start=None, page_end=None):
    return pipeline_filter_words_for_chars(words, page=page, page_start=page_start, page_end=page_end)


def estimated_char_count(words):
    return pipeline_estimated_char_count(words)


def process_single_document_run(run_dir: Path, pdf_path: Path, *, doc_id=None, run_id=None, filename=None):
    return pipeline_process_single_document_run(
        run_dir,
        pdf_path,
        extract_pdf_words_fn=extract_pdf_words,
        write_json_fn=write_json,
        run_opendataloader_to_markdown_fn=run_opendataloader_to_markdown,
        build_new_pdf_index_fn=build_new_pdf_index,
        inject_section_markers_fn=inject_section_markers,
        doc_id=doc_id,
        run_id=run_id,
        filename=filename,
    )


def process_document_diff_run(run_dir: Path, *, doc_id=None, run_id=None):
    return pipeline_process_document_diff_run(
        run_dir,
        read_json_fn=read_json,
        write_json_fn=write_json,
        write_unmarked_md_copy_fn=write_unmarked_md_copy,
        run_diff_extract_fn=run_diff_extract,
        suppress_layout_moves_fn=suppress_layout_moves,
        make_highlights_and_changes_fn=make_highlights_and_changes,
        merge_highlight_rects_server_fn=merge_highlight_rects_server,
        alignment_report_fn=alignment_report,
        build_new_pdf_index_fn=build_new_pdf_index,
        inject_section_markers_fn=inject_section_markers,
        map_result_segments_to_pdf_indices_fn=map_result_segments_to_pdf_indices,
        document_reviews_for_run_fn=document_reviews_for_run,
        semantic_module=semantic,
        doc_id=doc_id,
        run_id=run_id,
    )


def document_semantic_map(doc_id, run_id):
    p = run_layout.viewer_path(document_run_dir(doc_id, run_id))
    return (read_json(p, {}) or {}).get("semantic_map", {})


def _review_store_kwargs():
    return {
        "documents_dir": DOCUMENTS_DIR,
        "load_document_meta_fn": load_document_meta,
        "find_file_by_id_fn": find_file_by_id,
        "read_json_fn": read_json,
        "write_json_fn": write_json,
    }


def migrate_workspace_reviews(doc_id):
    return store_migrate_legacy_workspace_reviews(doc_id, **_review_store_kwargs())


def load_reviews_for_run(doc_id, run_id):
    return store_load_reviews_for_run(
        doc_id,
        run_id,
        migrate_legacy_fn=migrate_workspace_reviews,
        **_review_store_kwargs(),
    )


def load_document_reviews(doc_id):
    # load_all_workspace_reviews accepts only a subset of the shared store kwargs
    # (no find_file_by_id_fn / write_json_fn), so pass them explicitly.
    return store_load_all_workspace_reviews(
        doc_id,
        documents_dir=DOCUMENTS_DIR,
        load_document_meta_fn=load_document_meta,
        read_json_fn=read_json,
        migrate_legacy_fn=migrate_workspace_reviews,
    )


def save_reviews_for_run(doc_id, run_id, reviews):
    store_save_reviews_for_run(doc_id, run_id, reviews, **_review_store_kwargs())


def save_document_reviews(doc_id, run_id, reviews):
    save_reviews_for_run(doc_id, run_id, reviews)


def mutate_document_review(doc_id, review_id, mutator):
    migrate_workspace_reviews(doc_id)
    meta = load_document_meta(doc_id) or {}
    for entry in meta.get("files") or []:
        file_id = entry.get("file_id")
        if not file_id:
            continue
        reviews = store_load_file_reviews(DOCUMENTS_DIR, doc_id, file_id, read_json_fn=read_json)
        updated = None
        for review in reviews:
            if review.get("review_id") == review_id:
                mutator(review)
                updated = review
                break
        if updated:
            store_save_file_reviews(DOCUMENTS_DIR, doc_id, file_id, reviews, write_json_fn=write_json)
            return updated
    return None


def update_review_in_run(doc_id, run_id, review_id, mutator):
    migrate_workspace_reviews(doc_id)
    return store_update_review_in_run_storage(
        doc_id,
        run_id,
        review_id,
        mutator,
        migrate_legacy_fn=migrate_workspace_reviews,
        **_review_store_kwargs(),
    )


def delete_review_in_run(doc_id, run_id, review_id):
    migrate_workspace_reviews(doc_id)
    return store_delete_review_in_run_storage(
        doc_id,
        run_id,
        review_id,
        migrate_legacy_fn=migrate_workspace_reviews,
        **_review_store_kwargs(),
    )


def delete_document_review(doc_id, review_id):
    migrate_workspace_reviews(doc_id)
    meta = load_document_meta(doc_id) or {}
    deleted = False
    for entry in meta.get("files") or []:
        file_id = entry.get("file_id")
        if not file_id:
            continue
        reviews = store_load_file_reviews(DOCUMENTS_DIR, doc_id, file_id, read_json_fn=read_json)
        kept = [r for r in reviews if r.get("review_id") != review_id]
        if len(kept) != len(reviews):
            store_save_file_reviews(DOCUMENTS_DIR, doc_id, file_id, kept, write_json_fn=write_json)
            deleted = True
    return deleted


def words_bbox(words, word_ids):
    selected = [words[i] for i in word_ids if isinstance(i, int) and 0 <= i < len(words)]
    if not selected:
        return [0, 0, 1, 1]
    return line_bbox(selected)


def first_word_page(words, word_ids):
    for wid in word_ids:
        if isinstance(wid, int) and 0 <= wid < len(words):
            return words[wid].get("page")
    return None


def text_for_word_ids(words, word_ids):
    return " ".join(str(words[i].get("text", "")) for i in word_ids if isinstance(i, int) and 0 <= i < len(words))


def semantic_map_for_run_dir(run_dir: Path):
    return (read_json(run_layout.viewer_path(run_dir), {}) or {}).get("semantic_map", {}) or {}


def section_context_for_side(run_dir: Path, side):
    return run_layout.source_md(run_dir, side), read_json(run_layout.sections_path(run_dir, side), {}) or {}


def equal_refs_for_selection(semantic_map, side, word_ids):
    return projection_equal_refs_for_selection(semantic_map, side, word_ids)


def compact_equal_ref(entry):
    return projection_compact_equal_ref(entry)


def build_anchor_md_metadata(run_dir: Path, side, anchor, semantic_map=None):
    return projection_build_anchor_md_metadata(
        run_dir,
        side,
        anchor,
        semantic_map_for_run_dir_fn=semantic_map_for_run_dir,
        section_context_for_side_fn=section_context_for_side,
        infer_section_id_for_anchor_fn=infer_section_id_for_anchor,
        semantic_map=semantic_map,
    )


def apply_md_metadata_to_anchor(anchor, md_meta):
    return projection_apply_md_metadata_to_anchor(anchor, md_meta)


def anchor_for_selection(run_dir: Path, run_id, side, word_ids, rect=None):
    return projection_anchor_for_selection(
        run_dir,
        run_id,
        side,
        word_ids,
        read_json_fn=read_json,
        first_word_page_fn=first_word_page,
        words_bbox_fn=words_bbox,
        text_for_word_ids_fn=text_for_word_ids,
        build_anchor_md_metadata_fn=build_anchor_md_metadata,
        apply_md_metadata_to_anchor_fn=apply_md_metadata_to_anchor,
        rect=rect,
    )


def review_projection_for_anchor(review, anchor_run_id, display_side=None):
    return projection_review_projection_for_anchor(review, anchor_run_id, display_side=display_side)


def run_side_anchor_key(run_id, side):
    return projection_run_side_anchor_key(run_id, side)


def copied_comments_for_forward(review):
    return projection_copied_comments_for_forward(review, utc_now_fn=utc_now)


def assessment_anchor_for_previous_side(review, prev_run_id, run_id):
    return projection_assessment_anchor_for_previous_side(review, prev_run_id, run_id)


def document_reviews_for_run(doc_id, run_id):
    return projection_document_reviews_for_run(
        doc_id,
        run_id,
        load_document_meta_fn=load_document_meta,
        load_reviews_for_run_fn=load_reviews_for_run,
        resolve_file_ids_for_run_session_fn=lambda meta, rid: store_resolve_file_ids_for_run_session(
            meta, rid, find_file_by_id_fn=find_file_by_id
        ),
        load_file_reviews_fn=lambda doc, fid: store_load_file_reviews(
            DOCUMENTS_DIR, doc, fid, read_json_fn=read_json
        ),
    )


def create_document_level_review(doc_id, run_id, run_dir: Path, data):
    anchor = anchor_for_selection(run_dir, run_id, data.get("side", "new"), data.get("word_ids", []), data.get("rect"))
    if not anchor:
        return {"error": "no_word_selected"}
    meta = load_document_meta(doc_id) or {}
    side = data.get("side", "new")
    storage_id = store_storage_file_id_for_run_session(meta, run_id, side, find_file_by_id_fn=find_file_by_id) or run_id
    now = utc_now()
    comment_text = data.get("comment", "")
    review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "workspace_id": doc_id,
        "status": data.get("status", "open"),
        "created_run_id": storage_id,
        "is_floating": False,
        "text": anchor.get("text", ""),
        "comments": ([{"comment_id": "c-" + uuid.uuid4().hex[:8], "author": data.get("author", "user"), "text": comment_text, "created_at": now}] if comment_text else []),
        "anchors": {storage_id: anchor},
        "created_at": now,
        "updated_at": now,
    }
    store_append_review_for_run(doc_id, run_id, review, side=side, **_review_store_kwargs())
    # The anchor is keyed by storage_id (the bound File Manager file_id for a plain upload, or the
    # run_id when ephemeral). Project by that same key so the lookup finds the anchor; using run_id
    # here returns None when the run is bound to a file (storage_id != run_id) and 500s the request.
    return review_projection_for_anchor(review, storage_id)


def find_word_sequence_by_text(words, text):
    return projection_find_word_sequence_by_text(words, text, norm_token_fn=norm_token)


def md_equal_entries_for_anchor(old_ids, semantic_map, max_nearby_distance=120):
    return projection_md_equal_entries_for_anchor(old_ids, semantic_map, max_nearby_distance=max_nearby_distance)


def current_word_ids_from_md_equal_anchor(old_ids, semantic_map, new_words):
    return projection_current_word_ids_from_md_equal_anchor(old_ids, semantic_map, new_words)


def current_word_ids_from_anchor_md(prev_anchor, semantic_map, new_words):
    return projection_current_word_ids_from_anchor_md(prev_anchor, semantic_map, new_words)


def word_ids_in_page_bbox(words, page, bbox, pad=18):
    return projection_word_ids_in_page_bbox(words, page, bbox, pad=pad)


def fallback_current_word_ids_from_diff(run_dir, prev_anchor, new_words):
    viewer_data = read_json(run_layout.viewer_path(run_dir), {}) or {}
    semantic_map = viewer_data.get("semantic_map", {}) or {}
    candidates = related_diff_changes_for_review(viewer_data, prev_anchor, semantic_map, limit=3)
    candidate_ids = {c.get("change_id") for c in candidates}
    for change in viewer_data.get("changes", []) or []:
        if change.get("id") not in candidate_ids:
            continue
        anchor = change.get("new_anchor") or {}
        ids = word_ids_in_page_bbox(new_words, anchor.get("page") or change.get("new_page"), anchor.get("bbox"))
        if ids:
            return ids
    return []


def migrate_previous_review_to_current(doc_id, run_id, review_id):
    return projection_migrate_previous_review_to_current(
        doc_id,
        run_id,
        review_id,
        load_document_meta_fn=load_document_meta,
        update_run_meta_fn=update_run_meta,
        document_run_dir_fn=document_run_dir,
        load_document_reviews_fn=load_reviews_for_run,
        load_reviews_for_run_fn=load_reviews_for_run,
        read_json_fn=read_json,
        document_semantic_map_fn=document_semantic_map,
        map_anchor_to_current_md_anchor_fn=map_anchor_to_current_md_anchor,
        find_word_sequence_by_text_fn=find_word_sequence_by_text,
        fallback_current_word_ids_from_diff_fn=fallback_current_word_ids_from_diff,
        first_word_page_fn=first_word_page,
        words_bbox_fn=words_bbox,
        text_for_word_ids_fn=text_for_word_ids,
        utc_now_fn=utc_now,
        copied_comments_for_forward_fn=copied_comments_for_forward,
        save_document_reviews_fn=save_document_reviews,
        document_reviews_for_run_fn=document_reviews_for_run,
        semantic_module=semantic,
    )


SNAPSHOT_LIMIT = 30


def snapshot_artifacts(run_dir):
    return snapshot_snapshot_artifacts(run_dir)


def snapshot_counts(reviews):
    return snapshot_snapshot_counts(reviews)


def prune_document_snapshots(doc_id, meta):
    return snapshot_prune_document_snapshots(
        doc_id,
        meta,
        snapshot_limit=SNAPSHOT_LIMIT,
        document_snapshot_dir_fn=document_snapshot_dir,
    )


def create_run_snapshot(doc_id, run_id, label=""):
    return snapshot_create_run_snapshot(
        doc_id,
        run_id,
        label=label,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        document_run_dir_fn=document_run_dir,
        read_json_fn=read_json,
        document_snapshot_dir_fn=document_snapshot_dir,
        document_reviews_for_run_fn=document_reviews_for_run,
        semantic_module=semantic,
        snapshot_artifacts_fn=snapshot_artifacts,
        write_json_fn=write_json,
        utc_now_fn=utc_now,
        snapshot_counts_fn=snapshot_counts,
        prune_document_snapshots_fn=prune_document_snapshots,
        save_document_meta_fn=save_document_meta,
    )


def delete_document_snapshot(doc_id, snapshot_id):
    return snapshot_delete_document_snapshot(
        doc_id,
        snapshot_id,
        load_document_meta_fn=load_document_meta,
        document_snapshot_dir_fn=document_snapshot_dir,
        save_document_meta_fn=save_document_meta,
    )


def rename_document_snapshot(doc_id, snapshot_id, label=""):
    return snapshot_rename_document_snapshot(
        doc_id,
        snapshot_id,
        label,
        load_document_meta_fn=load_document_meta,
        document_snapshot_dir_fn=document_snapshot_dir,
        read_json_fn=read_json,
        write_json_fn=write_json,
        save_document_meta_fn=save_document_meta,
    )


def backfill_snapshot_change_counts(doc_id, meta):
    return snapshot_backfill_snapshot_change_counts(
        doc_id,
        meta,
        document_snapshot_dir_fn=document_snapshot_dir,
        read_json_fn=read_json,
        save_document_meta_fn=save_document_meta,
    )


def snapshot_side_file(side, kind):
    return snapshot_snapshot_side_file(side, kind)


def update_run_meta(meta, run_id, **patch):
    for run in meta.get("runs", []):
        if run.get("run_id") == run_id:
            run.update(patch)
            return run
    return None


AI_ASSESSMENT_MODEL = resolve_ai_model("AI_ASSESSMENT_MODEL")
CHANGE_AI_ASSESSMENT_MODEL = resolve_ai_model("CHANGE_AI_ASSESSMENT_MODEL", fallback=AI_ASSESSMENT_MODEL)
AI_ASSESSMENT_SYSTEM_PROMPT_PATH = BASE_DIR / "prompts" / "ai_assessment_system.md"
CHANGE_AI_ASSESSMENT_SYSTEM_PROMPT_PATH = BASE_DIR / "prompts" / "change_ai_assessment_system.md"
CHARS_MAX_WORDS = max(1, int(os.environ.get("CHARS_MAX_WORDS") or "50000"))
CHARS_MAX_ESTIMATED_COUNT = max(1, int(os.environ.get("CHARS_MAX_ESTIMATED_COUNT") or "250000"))
AI_ASSESSMENT_PROMPT_FALLBACK = (
    "You are an expert financial report review assistant. "
    "Judge whether each prior review requirement is fully resolved (cleared), "
    "partly resolved (partial), unresolved (not_cleared), or impossible to judge "
    "because the review itself is too vague (unclear). "
    "Use markdown tools when needed, then call submit_verdict."
)
CHANGE_AI_ASSESSMENT_PROMPT_FALLBACK = (
    "You are an expert financial report risk reviewer focused on change bundles. "
    "Use the available markdown tools before calling submit_change_review or submit_change_reviews."
)


def ai_assessment_path(doc_id, run_id):
    return run_layout.review_assessment_path(document_run_dir(doc_id, run_id))


def change_ai_assessment_path(doc_id, run_id):
    return run_layout.change_assessment_path(document_run_dir(doc_id, run_id))


def load_ai_assessment(doc_id, run_id):
    default_value = {"workspace_id": doc_id, "run_id": run_id, "items": []}
    return read_json(ai_assessment_path(doc_id, run_id), default_value) or default_value


def save_ai_assessment(doc_id, run_id, assessment):
    assessment["workspace_id"] = doc_id
    assessment["run_id"] = run_id
    assessment["updated_at"] = utc_now()
    write_json(ai_assessment_path(doc_id, run_id), assessment)


def load_change_ai_assessment(doc_id, run_id):
    default_value = {"workspace_id": doc_id, "run_id": run_id, "items": []}
    return read_json(
        change_ai_assessment_path(doc_id, run_id),
        default_value,
    ) or default_value


def save_change_ai_assessment(doc_id, run_id, assessment):
    assessment["workspace_id"] = doc_id
    assessment["run_id"] = run_id
    assessment["updated_at"] = utc_now()
    write_json(change_ai_assessment_path(doc_id, run_id), assessment)


def assessment_reviews_for_run(doc_id, run_id, review_id=None):
    return assess_assessment_reviews_for_run(
        doc_id,
        run_id,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        load_document_reviews_fn=load_reviews_for_run,
        assessment_anchor_for_previous_side_fn=assessment_anchor_for_previous_side,
        review_id=review_id,
        resolve_file_ids_for_run_session_fn=lambda meta, rid: store_resolve_file_ids_for_run_session(
            meta, rid, find_file_by_id_fn=find_file_by_id
        ),
        load_file_reviews_fn=lambda doc, fid: store_load_file_reviews(
            DOCUMENTS_DIR, doc, fid, read_json_fn=read_json
        ),
    )


def map_anchor_to_current_md_anchor(prev_anchor, semantic_map, new_words):
    return projection_map_anchor_to_current_md_anchor(prev_anchor, semantic_map, new_words)


def related_diff_changes_for_review(viewer_data, prev_anchor, semantic_map, limit=8):
    return assess_related_diff_changes_for_review(
        viewer_data,
        prev_anchor,
        semantic_map,
        trim_for_prompt_fn=trim_for_prompt,
        limit=limit,
    )


def build_assessment_context_pack(doc_id, run_id, run_dir, review, prev_anchor, prev_section_map, current_section_map, viewer_data, semantic_map):
    return assess_build_assessment_context_pack(
        doc_id,
        run_id,
        run_dir,
        review,
        prev_anchor,
        prev_section_map,
        current_section_map,
        viewer_data,
        semantic_map,
        read_json_fn=read_json,
        map_anchor_to_current_md_anchor_fn=map_anchor_to_current_md_anchor,
        infer_section_id_for_anchor_fn=infer_section_id_for_anchor,
        first_word_page_fn=first_word_page,
        words_bbox_fn=words_bbox,
        text_for_word_ids_fn=text_for_word_ids,
        review_thread_for_prompt_fn=review_thread_for_prompt,
        reviews_for_sections_fn=reviews_for_sections,
        related_diff_changes_for_review_fn=related_diff_changes_for_review,
    )


def group_assessment_entries_by_report_section(entries):
    return assess_group_assessment_entries_by_report_section(entries)


def change_by_id(viewer_data, change_id):
    return assess_change_by_id(viewer_data, change_id)


def section_id_for_change_anchor(run_dir, section_map, change, side):
    return assess_section_id_for_change_anchor(
        run_dir,
        section_map,
        change,
        side,
        infer_section_id_for_anchor_fn=infer_section_id_for_anchor,
        section_entries_fn=section_entries,
    )


def reviews_for_sections(doc_id, run_id, old_section_id=None, current_section_id=None):
    return assess_reviews_for_sections(
        doc_id,
        run_id,
        document_reviews_for_run_fn=document_reviews_for_run,
        review_threads_for_prompt_from_projection_fn=review_threads_for_prompt_from_projection,
        old_section_id=old_section_id,
        current_section_id=current_section_id,
    )


def build_change_assessment_context_pack(doc_id, run_id, run_dir, change, prev_section_map, current_section_map):
    return assess_build_change_assessment_context_pack(
        doc_id,
        run_id,
        run_dir,
        change,
        prev_section_map,
        current_section_map,
        section_id_for_change_anchor_fn=section_id_for_change_anchor,
        reviews_for_sections_fn=reviews_for_sections,
        trim_for_prompt_fn=trim_for_prompt,
    )


def call_ai_assessment(prompt, tool_context):
    return callers_call_ai_assessment(
        prompt,
        tool_context,
        model=AI_ASSESSMENT_MODEL,
        prompt_path=AI_ASSESSMENT_SYSTEM_PROMPT_PATH,
        prompt_fallback=AI_ASSESSMENT_PROMPT_FALLBACK,
        load_system_prompt_fn=load_system_prompt,
    )


def call_ai_assessment_batch(context_packs, available_sections, tool_context):
    return callers_call_ai_assessment_batch(
        context_packs,
        available_sections,
        tool_context,
        model=AI_ASSESSMENT_MODEL,
        prompt_path=AI_ASSESSMENT_SYSTEM_PROMPT_PATH,
        prompt_fallback=AI_ASSESSMENT_PROMPT_FALLBACK,
        load_system_prompt_fn=load_system_prompt,
    )


def call_ai_change_assessment(prompt, tool_context):
    return callers_call_ai_change_assessment(
        prompt,
        tool_context,
        model=CHANGE_AI_ASSESSMENT_MODEL,
        prompt_path=CHANGE_AI_ASSESSMENT_SYSTEM_PROMPT_PATH,
        prompt_fallback=CHANGE_AI_ASSESSMENT_PROMPT_FALLBACK,
        load_system_prompt_fn=load_system_prompt,
    )


def call_ai_change_assessment_batch(context_packs, available_sections, tool_context):
    return callers_call_ai_change_assessment_batch(
        context_packs,
        available_sections,
        tool_context,
        model=CHANGE_AI_ASSESSMENT_MODEL,
        prompt_path=CHANGE_AI_ASSESSMENT_SYSTEM_PROMPT_PATH,
        prompt_fallback=CHANGE_AI_ASSESSMENT_PROMPT_FALLBACK,
        load_system_prompt_fn=load_system_prompt,
    )


def run_ai_assessment(doc_id, run_id, review_id=None, review_ids=None, *, skip_existing=True):
    return run_ai_assessment_service(
        doc_id,
        run_id,
        review_id=review_id,
        review_ids=review_ids,
        skip_existing=skip_existing,
        model_name=AI_ASSESSMENT_MODEL,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        document_run_dir_fn=document_run_dir,
        read_json_fn=read_json,
        available_assessment_sections_fn=available_assessment_sections,
        assessment_reviews_for_run_fn=assessment_reviews_for_run,
        required_ai_api_key_name_fn=required_ai_api_key_name,
        required_ai_api_key_fn=required_ai_api_key,
        load_ai_assessment_fn=load_ai_assessment,
        utc_now_fn=utc_now,
        build_assessment_context_pack_fn=build_assessment_context_pack,
        anchor_section_id_fn=anchor_section_id,
        call_ai_assessment_fn=call_ai_assessment,
        build_assessment_prompt_fn=build_assessment_prompt,
        group_assessment_entries_by_report_section_fn=group_assessment_entries_by_report_section,
        call_ai_assessment_batch_fn=call_ai_assessment_batch,
        save_ai_assessment_fn=save_ai_assessment,
        update_run_meta_fn=update_run_meta,
        save_document_meta_fn=save_document_meta,
    )


def nearest_words_for_anchor(new_words, page, bbox, limit=8):
    return assess_nearest_words_for_anchor(new_words, page, bbox, limit=limit)


def new_word_ids_for_change(run_dir, change):
    return assess_new_word_ids_for_change(
        run_dir,
        change,
        read_json_fn=read_json,
        word_ids_in_page_bbox_fn=word_ids_in_page_bbox,
        nearest_words_for_anchor_fn=nearest_words_for_anchor,
    )


def build_change_groups_for_run(doc_id, run_id, *, change_ids=None):
    return assess_build_change_groups_for_run(
        doc_id,
        run_id,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        document_run_dir_fn=document_run_dir,
        read_json_fn=read_json,
        build_change_assessment_context_pack_fn=build_change_assessment_context_pack,
        change_ids=change_ids,
    )


def run_change_ai_assessment(doc_id, run_id, change_id=None, change_ids=None):
    return run_change_ai_assessment_service(
        doc_id,
        run_id,
        change_id=change_id,
        change_ids=change_ids,
        model_name=CHANGE_AI_ASSESSMENT_MODEL,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        document_run_dir_fn=document_run_dir,
        read_json_fn=read_json,
        required_ai_api_key_name_fn=required_ai_api_key_name,
        required_ai_api_key_fn=required_ai_api_key,
        load_change_ai_assessment_fn=load_change_ai_assessment,
        utc_now_fn=utc_now,
        available_assessment_sections_fn=available_assessment_sections,
        build_change_assessment_context_pack_fn=build_change_assessment_context_pack,
        call_ai_change_assessment_fn=call_ai_change_assessment,
        build_change_assessment_prompt_fn=build_change_assessment_prompt,
        build_change_groups_for_run_fn=build_change_groups_for_run,
        call_ai_change_assessment_batch_fn=call_ai_change_assessment_batch,
        harmonize_change_items_fn=harmonize_change_items,
        save_change_ai_assessment_fn=save_change_ai_assessment,
        update_run_meta_fn=update_run_meta,
        save_document_meta_fn=save_document_meta,
    )


def forward_change_assessment_to_review(doc_id, run_id, change_id):
    return forward_change_assessment_to_review_service(
        doc_id,
        run_id,
        change_id,
        load_document_meta_fn=load_document_meta,
        run_meta_for_fn=run_meta_for,
        document_run_dir_fn=document_run_dir,
        read_json_fn=read_json,
        change_by_id_fn=change_by_id,
        load_change_ai_assessment_fn=load_change_ai_assessment,
        document_reviews_for_run_fn=document_reviews_for_run,
        new_word_ids_for_change_fn=new_word_ids_for_change,
        anchor_for_selection_fn=anchor_for_selection,
        utc_now_fn=utc_now,
        load_document_reviews_fn=load_reviews_for_run,
        save_document_reviews_fn=save_document_reviews,
        semantic_save_reviews_fn=semantic.save_reviews,
        review_projection_for_anchor_fn=review_projection_for_anchor,
        save_change_ai_assessment_fn=save_change_ai_assessment,
    )


# =========================================================
# Routes
# =========================================================


@app.route("/")
def index():
    index_path = BASE_DIR / "static" / "index.html"
    if index_path.exists():
        return send_file(index_path)
    return Response("static/index.html not found", status=404, mimetype="text/plain")


@app.route("/dashboard")
def dashboard():
    dashboard_path = BASE_DIR / "static" / "dashboard.html"
    if dashboard_path.exists():
        return send_file(dashboard_path)
    return Response("static/dashboard.html not found", status=404, mimetype="text/plain")


def document_blueprint_deps():
    return {
        "get_uploaded_pdf_fn": get_uploaded_pdf,
        "document_run_dir_fn": document_run_dir,
        "utc_now_fn": utc_now,
        "save_document_meta_fn": save_document_meta,
        "process_single_document_run_fn": process_single_document_run,
        "update_run_meta_fn": update_run_meta,
        "load_document_meta_fn": load_document_meta,
        "load_document_reviews_fn": load_document_reviews,
        "load_reviews_for_run_fn": load_reviews_for_run,
        "save_document_reviews_fn": save_document_reviews,
        "mutate_document_review_fn": mutate_document_review,
        "delete_document_review_fn": delete_document_review,
        "update_review_in_run_fn": update_review_in_run,
        "delete_review_in_run_fn": delete_review_in_run,
        "copy_if_exists_fn": copy_if_exists,
        "read_json_fn": read_json,
        "build_chars_from_words_fn": build_chars_from_words,
        "filter_words_for_chars_fn": filter_words_for_chars,
        "estimated_char_count_fn": estimated_char_count,
        "chars_max_words": CHARS_MAX_WORDS,
        "chars_max_estimated_count": CHARS_MAX_ESTIMATED_COUNT,
        "anchor_for_selection_fn": anchor_for_selection,
        "canonical_review_from_anchor_fn": canonical_review_from_anchor,
        "document_reviews_for_run_fn": document_reviews_for_run,
        "process_document_diff_run_fn": process_document_diff_run,
        "render_pdf_page_fn": render_pdf_page,
        "create_document_level_review_fn": create_document_level_review,
        "export_annotated_pdf_fn": export_annotated_pdf,
        "run_meta_for_fn": run_meta_for,
        "overwrite_saved_document_fn": overwrite_saved_document,
        "normalize_document_title_fn": normalize_document_title,
        "document_title_exists_fn": workspace_title_exists,
        "save_run_side_file_fn": save_run_side_file,
        "delete_workspace_file_fn": delete_workspace_file,
        "rename_workspace_file_fn": rename_workspace_file,
        "list_workspace_files_fn": list_workspace_files,
        "resolve_workspace_file_pdf_fn": resolve_workspace_file_pdf,
        "find_file_by_id_fn": find_file_by_id,
        "open_workspace_saved_file_fn": open_workspace_saved_file,
        "document_dir_fn": document_dir,
        "write_json_fn": write_json,
    }


def assessment_blueprint_deps():
    return {
        "run_ai_assessment_fn": run_ai_assessment,
        "run_change_ai_assessment_fn": run_change_ai_assessment,
        "load_document_meta_fn": load_document_meta,
        "run_meta_for_fn": run_meta_for,
        "load_change_ai_assessment_fn": load_change_ai_assessment,
        "build_change_groups_for_run_fn": build_change_groups_for_run,
        "forward_change_assessment_to_review_fn": forward_change_assessment_to_review,
        "load_ai_assessment_fn": load_ai_assessment,
        "utc_now_fn": utc_now,
        "save_ai_assessment_fn": save_ai_assessment,
        "migrate_previous_review_to_current_fn": migrate_previous_review_to_current,
    }


def file_manager_blueprint_deps():
    return {
        "list_documents_for_manager_fn": list_documents_for_manager,
        "ensure_default_workspace_fn": ensure_default_workspace,
        "load_document_meta_fn": load_document_meta,
        "normalize_document_title_fn": normalize_document_title,
        "document_title_exists_fn": workspace_title_exists,
        "save_document_meta_fn": save_document_meta,
        "document_dir_fn": document_dir,
        "utc_now_fn": utc_now,
    }


def snapshot_blueprint_deps():
    return {
        "load_document_meta_fn": load_document_meta,
        "snapshot_limit": SNAPSHOT_LIMIT,
        "create_run_snapshot_fn": create_run_snapshot,
        "delete_document_snapshot_fn": delete_document_snapshot,
        "rename_document_snapshot_fn": rename_document_snapshot,
        "backfill_snapshot_change_counts_fn": backfill_snapshot_change_counts,
        "document_snapshot_dir_fn": document_snapshot_dir,
        "read_json_fn": read_json,
        "build_chars_from_words_fn": build_chars_from_words,
        "filter_words_for_chars_fn": filter_words_for_chars,
        "estimated_char_count_fn": estimated_char_count,
        "chars_max_words": CHARS_MAX_WORDS,
        "chars_max_estimated_count": CHARS_MAX_ESTIMATED_COUNT,
        "snapshot_side_file_fn": snapshot_side_file,
        "render_pdf_page_fn": render_pdf_page,
        "export_annotated_pdf_fn": export_annotated_pdf,
    }


def register_blueprints():
    if "documents" not in app.blueprints:
        app.register_blueprint(create_document_blueprint(deps=document_blueprint_deps()))
    if "assessment" not in app.blueprints:
        app.register_blueprint(create_assessment_blueprint(deps=assessment_blueprint_deps()))
    if "file_manager" not in app.blueprints:
        app.register_blueprint(create_file_manager_blueprint(deps=file_manager_blueprint_deps()))
    if "snapshots" not in app.blueprints:
        app.register_blueprint(create_snapshot_blueprint(deps=snapshot_blueprint_deps()))


register_blueprints()


def suppress_flask_console_noise():
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.ERROR)
    werkzeug_logger.disabled = True


if __name__ == "__main__":
    suppress_flask_console_noise()
    print("PDF Diff Viewer running: http://127.0.0.1:8000", flush=True)
    print(f"Review AI model: {AI_ASSESSMENT_MODEL}", flush=True)
    print(f"Change AI model: {CHANGE_AI_ASSESSMENT_MODEL}", flush=True)
    app.run(host="127.0.0.1", port=8000, debug=True, use_reloader=False)
