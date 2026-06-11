import shutil
import time
import uuid
from pathlib import Path

import document_semantic as semantic
import run_layout
from flask import Blueprint, Response, current_app, jsonify, request, send_file

from routes.common import (
    chars_page_filters_from_query,
    has_invalid_hex_id,
    ids_payload,
    is_valid_hex_id,
)


def create_document_blueprint(*, deps):
    get_uploaded_pdf_fn = deps["get_uploaded_pdf_fn"]
    document_run_dir_fn = deps["document_run_dir_fn"]
    utc_now_fn = deps["utc_now_fn"]
    save_document_meta_fn = deps["save_document_meta_fn"]
    process_single_document_run_fn = deps["process_single_document_run_fn"]
    update_run_meta_fn = deps["update_run_meta_fn"]
    load_document_meta_fn = deps["load_document_meta_fn"]
    load_document_reviews_fn = deps["load_document_reviews_fn"]
    load_reviews_for_run_fn = deps["load_reviews_for_run_fn"]
    save_document_reviews_fn = deps["save_document_reviews_fn"]
    mutate_document_review_fn = deps["mutate_document_review_fn"]
    delete_document_review_fn = deps["delete_document_review_fn"]
    update_review_in_run_fn = deps["update_review_in_run_fn"]
    delete_review_in_run_fn = deps["delete_review_in_run_fn"]
    copy_if_exists_fn = deps["copy_if_exists_fn"]
    read_json_fn = deps["read_json_fn"]
    build_chars_from_words_fn = deps["build_chars_from_words_fn"]
    filter_words_for_chars_fn = deps.get("filter_words_for_chars_fn", lambda words, **_kwargs: list(words or []))
    estimated_char_count_fn = deps.get("estimated_char_count_fn", lambda words: sum(len(str(w.get("text", ""))) for w in (words or [])))
    chars_max_words = int(deps.get("chars_max_words", 50000))
    chars_max_estimated_count = int(deps.get("chars_max_estimated_count", 250000))
    anchor_for_selection_fn = deps["anchor_for_selection_fn"]
    canonical_review_from_anchor_fn = deps["canonical_review_from_anchor_fn"]
    document_reviews_for_run_fn = deps["document_reviews_for_run_fn"]
    process_document_diff_run_fn = deps["process_document_diff_run_fn"]
    render_pdf_page_fn = deps["render_pdf_page_fn"]
    create_document_level_review_fn = deps["create_document_level_review_fn"]
    export_annotated_pdf_fn = deps["export_annotated_pdf_fn"]
    run_meta_for_fn = deps["run_meta_for_fn"]
    overwrite_saved_document_fn = deps["overwrite_saved_document_fn"]
    normalize_document_title_fn = deps["normalize_document_title_fn"]
    document_title_exists_fn = deps["document_title_exists_fn"]
    save_run_side_file_fn = deps["save_run_side_file_fn"]
    delete_workspace_file_fn = deps["delete_workspace_file_fn"]
    rename_workspace_file_fn = deps["rename_workspace_file_fn"]
    list_workspace_files_fn = deps["list_workspace_files_fn"]
    resolve_workspace_file_pdf_fn = deps["resolve_workspace_file_pdf_fn"]
    find_file_by_id_fn = deps["find_file_by_id_fn"]
    open_workspace_saved_file_fn = deps["open_workspace_saved_file_fn"]
    document_dir_fn = deps["document_dir_fn"]
    write_json_fn = deps["write_json_fn"]
    bp = Blueprint("documents", __name__)

    @bp.route("/api/documents", methods=["POST"])
    def create_document():
        uploaded = get_uploaded_pdf_fn()
        if uploaded is None:
            return jsonify({"error": "pdf file is required; use form field pdf, report_pdf, or file"}), 400

        doc_id = uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:12]
        run_dir = document_run_dir_fn(doc_id, run_id)
        run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
        report_pdf = run_layout.source_pdf(run_dir, "current")
        uploaded.save(report_pdf)

        safe_filename = Path(uploaded.filename or "report.pdf").name or "report.pdf"
        title = request.form.get("title") or Path(safe_filename).stem
        created_at = utc_now_fn()
        meta = {
            "workspace_id": doc_id,
            "title": title,
            "is_saved": False,
            "created_at": created_at,
            "updated_at": created_at,
            "runs": [{
                "run_id": run_id,
                "kind": "initial",
                "status": "processing",
                "created_at": created_at,
                "filename": safe_filename,
                "has_diff": False,
                "has_ai_assessment": False,
            }],
        }
        save_document_meta_fn(meta)

        try:
            viewer_data = process_single_document_run_fn(
                run_dir,
                report_pdf,
                doc_id=doc_id,
                run_id=run_id,
                filename=safe_filename,
            )
        except Exception as e:
            update_run_meta_fn(meta, run_id, status="error", error=str(e))
            save_document_meta_fn(meta)
            return jsonify({"error": str(e), **ids_payload(doc_id), "run_id": run_id}), 500

        update_run_meta_fn(meta, run_id, status="ready")
        save_document_meta_fn(meta)
        return jsonify({**ids_payload(doc_id), "run_id": run_id, "meta": meta, "result": viewer_data}), 201

    @bp.route("/api/documents/<doc_id>/runs/initial", methods=["POST"])
    def create_initial_document_run(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404

        uploaded = get_uploaded_pdf_fn()
        if uploaded is None:
            return jsonify({"error": "pdf file is required; use form field pdf, report_pdf, or file"}), 400

        safe_filename = Path(uploaded.filename or "report.pdf").name or "report.pdf"
        created_at = utc_now_fn()

        # Single-workspace model: a fresh base upload always starts a new analysis, so the
        # base-upload UI never has to 409 ("already has a comparison"). Clear the prior ephemeral
        # analysis runs (initial/diff) -- saved files and snapshots are the durable store and live
        # outside meta["runs"] -- then create exactly one fresh initial run.
        replaced = bool(meta.get("runs"))
        for old in meta.get("runs", []) or []:
            old_dir = document_run_dir_fn(doc_id, old.get("run_id"))
            if old_dir.exists():
                shutil.rmtree(old_dir, ignore_errors=True)

        run_id = uuid.uuid4().hex[:12]
        run_dir = document_run_dir_fn(doc_id, run_id)
        run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
        meta["runs"] = [{
            "run_id": run_id,
            "kind": "initial",
            "status": "processing",
            "created_at": created_at,
            "filename": safe_filename,
            "has_diff": False,
            "has_ai_assessment": False,
        }]
        meta["updated_at"] = created_at
        save_document_meta_fn(meta)

        report_pdf = run_layout.source_pdf(run_dir, "current")
        uploaded.save(report_pdf)

        try:
            viewer_data = process_single_document_run_fn(
                run_dir,
                report_pdf,
                doc_id=doc_id,
                run_id=run_id,
                filename=safe_filename,
            )
        except Exception as e:
            update_run_meta_fn(meta, run_id, status="error", error=str(e))
            save_document_meta_fn(meta)
            return jsonify({"error": str(e), **ids_payload(doc_id), "run_id": run_id}), 500

        update_run_meta_fn(meta, run_id, status="ready")
        save_document_meta_fn(meta)
        return jsonify({
            **ids_payload(doc_id),
            "run_id": run_id,
            "meta": meta,
            "result": viewer_data,
            "replaced": replaced,
        }), 200 if replaced else 201

    @bp.route("/api/documents/<doc_id>")
    def get_document(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        return jsonify(meta)

    @bp.route("/api/documents/<doc_id>/files")
    def list_document_files(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        if not load_document_meta_fn(doc_id):
            return jsonify({"error": "document not found"}), 404
        return jsonify({"workspace_id": doc_id, "files": list_workspace_files_fn(doc_id)})

    @bp.route("/api/documents/<doc_id>/files/<run_id>", methods=["PATCH"])
    def rename_document_file(doc_id, run_id):
        if has_invalid_hex_id(doc_id) or has_invalid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        payload = request.get_json(force=True) or {}
        title = payload.get("title")
        if not title:
            return jsonify({"error": "title required"}), 400
        body, status = rename_workspace_file_fn(doc_id, run_id, title)
        return jsonify(body), status

    @bp.route("/api/documents/<doc_id>/files/<run_id>", methods=["DELETE"])
    def delete_document_file(doc_id, run_id):
        if has_invalid_hex_id(doc_id) or has_invalid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        body, status = delete_workspace_file_fn(doc_id, run_id)
        return jsonify(body), status

    @bp.route("/api/documents/<doc_id>/reviews", methods=["GET"])
    def list_document_level_reviews(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        if not load_document_meta_fn(doc_id):
            return jsonify({"error": "document not found"}), 404
        return jsonify(load_document_reviews_fn(doc_id))

    @bp.route("/api/documents/<doc_id>/reviews/<review_id>", methods=["PATCH"])
    def patch_document_level_review(doc_id, review_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        patch = request.get_json(force=True) or {}

        def mutator(review):
            for key in ("status", "human_decision", "is_floating"):
                if key in patch:
                    review[key] = patch[key]
            review["updated_at"] = utc_now_fn()

        updated = mutate_document_review_fn(doc_id, review_id, mutator)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @bp.route("/api/documents/<doc_id>/reviews/<review_id>", methods=["DELETE"])
    def delete_document_level_review(doc_id, review_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        if not delete_document_review_fn(doc_id, review_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"deleted": True})

    @bp.route("/api/documents/<doc_id>/reviews/<review_id>/comments", methods=["POST"])
    def add_document_level_comment(doc_id, review_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        data = request.get_json(force=True) or {}

        def mutator(review):
            review.setdefault("comments", []).append({
                "comment_id": "c-" + uuid.uuid4().hex[:8],
                "author": data.get("author", "user"),
                "text": data.get("text") or data.get("comment") or "",
                "created_at": utc_now_fn(),
            })
            review["updated_at"] = utc_now_fn()

        updated = mutate_document_review_fn(doc_id, review_id, mutator)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @bp.route("/api/documents/<doc_id>/runs", methods=["POST"])
    def create_document_run(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        if not meta.get("runs"):
            return jsonify({"error": "document has no previous run"}), 400

        uploaded = get_uploaded_pdf_fn()
        if uploaded is None:
            return jsonify({"error": "pdf file is required; use form field pdf, report_pdf, or file"}), 400

        prev_run_id = meta["runs"][-1]["run_id"]
        prev_dir = document_run_dir_fn(doc_id, prev_run_id)
        if not run_layout.source_pdf(prev_dir, "current").exists():
            return jsonify({"error": f"previous run {prev_run_id} is missing current/source.pdf"}), 400

        run_id = uuid.uuid4().hex[:12]
        run_dir = document_run_dir_fn(doc_id, run_id)
        run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
        run_layout.previous_dir(run_dir).mkdir(parents=True, exist_ok=True)
        copy_if_exists_fn(run_layout.source_pdf(prev_dir, "current"), run_layout.source_pdf(run_dir, "previous"))
        copy_if_exists_fn(run_layout.source_md(prev_dir, "current"), run_layout.source_md(run_dir, "previous"))
        copy_if_exists_fn(run_layout.words_path(prev_dir, "current"), run_layout.words_path(run_dir, "previous"))
        copy_if_exists_fn(run_layout.sections_path(prev_dir, "current"), run_layout.sections_path(run_dir, "previous"))

        report_pdf = run_layout.source_pdf(run_dir, "current")
        uploaded.save(report_pdf)
        safe_filename = Path(uploaded.filename or "report.pdf").name or "report.pdf"
        created_at = utc_now_fn()
        run_meta = {
            "run_id": run_id,
            "kind": "update",
            "status": "processing",
            "created_at": created_at,
            "filename": safe_filename,
            "previous_run_id": prev_run_id,
            "has_diff": False,
            "has_ai_assessment": False,
        }
        meta.setdefault("runs", []).append(run_meta)
        save_document_meta_fn(meta)

        try:
            viewer_data = process_single_document_run_fn(
                run_dir,
                report_pdf,
                doc_id=doc_id,
                run_id=run_id,
                filename=safe_filename,
            )
        except Exception as e:
            update_run_meta_fn(meta, run_id, status="error", error=str(e))
            save_document_meta_fn(meta)
            return jsonify({"error": str(e), **ids_payload(doc_id), "run_id": run_id}), 500

        update_run_meta_fn(meta, run_id, status="ready")
        save_document_meta_fn(meta)
        return jsonify({**ids_payload(doc_id), "run_id": run_id, "previous_run_id": prev_run_id, "result": viewer_data}), 201

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/import-reviews", methods=["POST"])
    def import_document_reviews(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        if not load_document_meta_fn(doc_id):
            return jsonify({"error": "document not found"}), 404
        run_dir = document_run_dir_fn(doc_id, run_id)
        if not run_dir.exists():
            return jsonify({"error": "run not found"}), 404
        data = request.get_json(silent=True) or {}
        source_workspace_id = str(data.get("source_workspace_id") or "").strip()
        if source_workspace_id and has_invalid_hex_id(source_workspace_id):
            return jsonify({"error": "invalid source_workspace_id"}), 400
        source_meta = load_document_meta_fn(source_workspace_id) if source_workspace_id else None
        if not source_meta:
            return jsonify({"error": "source document not found"}), 404
        source_runs = source_meta.get("runs", []) or []
        latest_source_run_id = (source_runs[-1] if source_runs else {}).get("run_id")
        existing = load_reviews_for_run_fn(doc_id, run_id)
        existing_source_ids = {r.get("source_review_id") for r in existing if r.get("source_review_id")}
        imported = []
        for review in load_document_reviews_fn(source_workspace_id):
            anchors = review.get("anchors") or {}
            src_anchor = anchors.get(latest_source_run_id) or next(iter(anchors.values()), None)
            word_ids = list((src_anchor or {}).get("word_ids") or review.get("new_word_ids") or [])
            side_hint = (src_anchor or {}).get("side") or review.get("side") or "new"
            if not word_ids or side_hint == "old":
                continue
            source_review_id = review.get("review_id")
            if source_review_id and source_review_id in existing_source_ids:
                continue
            anchor = anchor_for_selection_fn(run_dir, run_id, "new", word_ids, (src_anchor or {}).get("rect"))
            if not anchor:
                continue
            imported.append(canonical_review_from_anchor_fn(
                doc_id,
                run_id,
                anchor,
                status=review.get("status", "open"),
                comments=review.get("comments"),
                text=review.get("text", ""),
                source_review_id=source_review_id,
                created_at=review.get("created_at"),
            ))
        if imported:
            merged = existing + imported
            save_document_reviews_fn(doc_id, run_id, merged)
            semantic.save_reviews(run_dir, document_reviews_for_run_fn(doc_id, run_id))
        return jsonify({"imported": len(imported)})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/diff", methods=["POST"])
    def diff_document_run(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        run_dir = document_run_dir_fn(doc_id, run_id)
        if not run_dir.exists():
            return jsonify({"error": "run not found"}), 404
        try:
            viewer_data = process_document_diff_run_fn(run_dir, doc_id=doc_id, run_id=run_id)
        except Exception as e:
            update_run_meta_fn(meta, run_id, status="error", error=str(e))
            save_document_meta_fn(meta)
            return jsonify({"error": str(e), **ids_payload(doc_id), "run_id": run_id}), 500
        update_run_meta_fn(meta, run_id, status="ready", has_diff=True)
        save_document_meta_fn(meta)
        return jsonify({**ids_payload(doc_id), "run_id": run_id, "result": viewer_data})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>")
    def get_document_run(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        run_dir = document_run_dir_fn(doc_id, run_id)
        viewer_path = run_layout.viewer_path(run_dir)
        if not viewer_path.exists():
            return jsonify({"error": "run data not found"}), 404
        return jsonify(read_json_fn(viewer_path, {}))

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/page/<side>/<int:page_no>")
    def document_page_image(doc_id, run_id, side, page_no):
        if not is_valid_hex_id(doc_id) or not is_valid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        zoom = float(request.args.get("zoom", "1.6"))
        try:
            pdf_path = run_layout.source_pdf(document_run_dir_fn(doc_id, run_id), side)
        except ValueError:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        if not pdf_path.exists():
            return jsonify({"error": "pdf not found"}), 404
        return Response(render_pdf_page_fn(pdf_path, page_no, zoom), mimetype="image/png")

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/words/<side>")
    def document_words(doc_id, run_id, side):
        if not is_valid_hex_id(doc_id) or not is_valid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        try:
            path = run_layout.words_path(document_run_dir_fn(doc_id, run_id), side)
        except ValueError:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        if not path.exists():
            return jsonify({"error": "not found"}), 404
        return send_file(path, mimetype="application/json")

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/chars/<side>")
    def document_chars(doc_id, run_id, side):
        if not is_valid_hex_id(doc_id) or not is_valid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        try:
            words_path = run_layout.words_path(document_run_dir_fn(doc_id, run_id), side)
        except ValueError:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        if not words_path.exists():
            return jsonify({"error": "not found"}), 404
        page_filters, filter_error = chars_page_filters_from_query()
        if filter_error:
            return jsonify({"error": filter_error}), 400
        words = read_json_fn(words_path, []) or []
        scoped_words = filter_words_for_chars_fn(
            words,
            page=page_filters["page"],
            page_start=page_filters["page_start"],
            page_end=page_filters["page_end"],
        )
        if len(scoped_words) > chars_max_words:
            current_app.logger.warning(
                "chars request rejected (word limit): doc_id=%s run_id=%s side=%s words=%s max_words=%s page=%s page_start=%s page_end=%s",
                doc_id,
                run_id,
                side,
                len(scoped_words),
                chars_max_words,
                page_filters["page"],
                page_filters["page_start"],
                page_filters["page_end"],
            )
            return jsonify(
                {
                    "error": "chars payload too large",
                    "reason": "word_limit_exceeded",
                    "max_words": chars_max_words,
                    "word_count": len(scoped_words),
                }
            ), 413
        estimated_chars = estimated_char_count_fn(scoped_words)
        if estimated_chars > chars_max_estimated_count:
            current_app.logger.warning(
                "chars request rejected (char estimate): doc_id=%s run_id=%s side=%s estimated_chars=%s max_chars=%s page=%s page_start=%s page_end=%s",
                doc_id,
                run_id,
                side,
                estimated_chars,
                chars_max_estimated_count,
                page_filters["page"],
                page_filters["page_start"],
                page_filters["page_end"],
            )
            return jsonify(
                {
                    "error": "chars payload too large",
                    "reason": "char_limit_exceeded",
                    "max_estimated_chars": chars_max_estimated_count,
                    "estimated_chars": estimated_chars,
                }
            ), 413
        started = time.perf_counter()
        chars = build_chars_from_words_fn(scoped_words)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        current_app.logger.info(
            "chars built: doc_id=%s run_id=%s side=%s words=%s chars=%s elapsed_ms=%s page=%s page_start=%s page_end=%s",
            doc_id,
            run_id,
            side,
            len(scoped_words),
            len(chars),
            elapsed_ms,
            page_filters["page"],
            page_filters["page_start"],
            page_filters["page_end"],
        )
        return jsonify(chars)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews", methods=["GET"])
    def list_document_reviews(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        reviews = document_reviews_for_run_fn(doc_id, run_id)
        return jsonify(reviews)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews", methods=["POST"])
    def create_document_review(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        run_dir = document_run_dir_fn(doc_id, run_id)
        if not run_dir.exists():
            return jsonify({"error": "run not found"}), 404
        data = request.get_json(force=True) or {}
        rev = create_document_level_review_fn(doc_id, run_id, run_dir, data)
        semantic.save_reviews(run_dir, document_reviews_for_run_fn(doc_id, run_id))
        return jsonify(rev), (400 if rev.get("error") else 201)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>", methods=["PATCH"])
    def patch_document_review(doc_id, run_id, review_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        patch = request.get_json(force=True) or {}

        def mutator(review):
            for key in ("status", "human_decision", "is_floating"):
                if key in patch:
                    review[key] = patch[key]
            review["updated_at"] = utc_now_fn()

        updated = update_review_in_run_fn(doc_id, run_id, review_id, mutator)
        if not updated:
            return jsonify({"error": "not found"}), 404
        semantic.save_reviews(document_run_dir_fn(doc_id, run_id), document_reviews_for_run_fn(doc_id, run_id))
        return jsonify(updated)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>", methods=["DELETE"])
    def remove_document_review(doc_id, run_id, review_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        if not delete_review_in_run_fn(doc_id, run_id, review_id):
            return jsonify({"error": "not found"}), 404
        semantic.save_reviews(document_run_dir_fn(doc_id, run_id), document_reviews_for_run_fn(doc_id, run_id))
        return jsonify({"deleted": True})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>/comments", methods=["POST"])
    def add_document_review_comment(doc_id, run_id, review_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        data = request.get_json(force=True) or {}

        def mutator(review):
            review.setdefault("comments", []).append({
                "comment_id": "c-" + uuid.uuid4().hex[:8],
                "author": data.get("author", "user"),
                "text": data.get("text") or data.get("comment") or "",
                "created_at": utc_now_fn(),
            })
            review["updated_at"] = utc_now_fn()

        updated = update_review_in_run_fn(doc_id, run_id, review_id, mutator)
        if not updated:
            return jsonify({"error": "not found"}), 404
        semantic.save_reviews(document_run_dir_fn(doc_id, run_id), document_reviews_for_run_fn(doc_id, run_id))
        return jsonify(updated)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>/comments/<comment_id>", methods=["PATCH"])
    def patch_document_review_comment(doc_id, run_id, review_id, comment_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        data = request.get_json(force=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        now = utc_now_fn()

        def mutator(review):
            comments = review.get("comments", []) or []
            for comment in comments:
                if comment.get("comment_id") == comment_id:
                    comment["text"] = text
                    comment["edited_at"] = now
                    comment["edited"] = True
                    review["updated_at"] = now
                    return
            raise ValueError("comment not found")

        try:
            updated_review = update_review_in_run_fn(doc_id, run_id, review_id, mutator)
        except ValueError:
            return jsonify({"error": "comment not found"}), 404
        if not updated_review:
            return jsonify({"error": "not found"}), 404
        semantic.save_reviews(document_run_dir_fn(doc_id, run_id), document_reviews_for_run_fn(doc_id, run_id))
        return jsonify(updated_review)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/export/<side>")
    def export_document_pdf(doc_id, run_id, side):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        if side in ("new", "report", "current"):
            compat_side = "new"
        elif side in ("old", "prev", "previous"):
            compat_side = "old"
        else:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        run_dir = document_run_dir_fn(doc_id, run_id)
        reviews = document_reviews_for_run_fn(doc_id, run_id)
        semantic.save_reviews(run_dir, reviews)
        if not reviews:
            return jsonify({"error": "No saved reviews"}), 400
        try:
            pdf_bytes = export_annotated_pdf_fn(run_dir, compat_side)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        filename = f"reviewed_{side}_{run_id[:6]}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @bp.route("/api/documents/<doc_id>/workspace")
    def document_workspace(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        runs = []
        for run in reversed(meta.get("runs", [])[-5:]):
            run_dir = document_run_dir_fn(doc_id, run["run_id"])
            reviews = document_reviews_for_run_fn(doc_id, run["run_id"])
            runs.append({
                **run,
                "review_count": len(reviews),
                "open_reviews": sum(1 for r in reviews if r.get("status", "open") == "open"),
                "closed_reviews": sum(1 for r in reviews if r.get("status") in ("closed", "cleared", "resolved")),
                "has_diff": bool(run_layout.diff_segments_path(run_dir).exists()),
                "has_ai_assessment": bool(run_layout.review_assessment_path(run_dir).exists()),
            })
        return jsonify({**ids_payload(doc_id), "title": meta.get("title"), "runs": runs})

    @bp.route("/api/documents/<doc_id>/files/<file_id>/open", methods=["POST"])
    def open_document_file(doc_id, file_id):
        if has_invalid_hex_id(doc_id, file_id):
            return jsonify({"error": "invalid id"}), 400
        payload, status = open_workspace_saved_file_fn(doc_id, file_id)
        return jsonify(payload), status

    @bp.route("/api/documents/<doc_id>/files/<file_id>/report")
    def document_file_report(doc_id, file_id):
        if has_invalid_hex_id(doc_id, file_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        entry = find_file_by_id_fn(meta, file_id)
        if not entry:
            return jsonify({"error": "file not found"}), 404
        path = resolve_workspace_file_pdf_fn(doc_id, file_id, entry=entry)
        if not path.exists():
            return jsonify({"error": "file not found"}), 404
        download_name = str(path.name or entry.get("filename") or f"{file_id}.pdf")
        return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=download_name)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/file/report")
    def document_report_file(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if meta and find_file_by_id_fn(meta, run_id):
            entry = find_file_by_id_fn(meta, run_id)
            path = resolve_workspace_file_pdf_fn(doc_id, run_id, entry=entry)
            if path.exists():
                download_name = str((entry or {}).get("filename") or f"{run_id}.pdf")
                return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=download_name)
        path = run_layout.source_pdf(document_run_dir_fn(doc_id, run_id), "current")
        if not path.exists():
            return jsonify({"error": "current/source.pdf not found"}), 404
        return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=f"{run_id}.pdf")

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/save", methods=["POST"])
    def save_document_run(doc_id, run_id):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        run_meta = run_meta_for_fn(meta, run_id)
        if not run_meta:
            return jsonify({"error": "run not found"}), 404
        data = request.get_json(silent=True) or {}
        target_workspace_id = str(data.get("target_workspace_id") or "").strip()
        if target_workspace_id and has_invalid_hex_id(target_workspace_id):
            return jsonify({"error": "invalid target_workspace_id"}), 400
        if target_workspace_id and target_workspace_id != doc_id:
            payload, status = overwrite_saved_document_fn(doc_id, run_id, target_workspace_id)
            return jsonify(payload), status
        current_title = normalize_document_title_fn(meta.get("title") or "Workspace")
        if not bool(meta.get("is_saved")) and document_title_exists_fn(current_title, exclude_workspace_id=doc_id):
            return jsonify({"error": "duplicate title; use Save As with a different name"}), 409
        meta["is_saved"] = True
        meta["title"] = current_title
        update_run_meta_fn(meta, run_id, saved_at=utc_now_fn(), status=run_meta.get("status") or "ready")
        save_document_meta_fn(meta)
        return jsonify({"saved": True, **ids_payload(doc_id), "run_id": run_id})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/save-file/<side>", methods=["POST"])
    def save_document_run_file(doc_id, run_id, side):
        if has_invalid_hex_id(doc_id, run_id):
            return jsonify({"error": "invalid id"}), 400
        if not load_document_meta_fn(doc_id):
            return jsonify({"error": "document not found"}), 404
        data = request.get_json(silent=True) or {}
        payload, status = save_run_side_file_fn(
            doc_id,
            run_id,
            side,
            title=data.get("title"),
            overwrite_existing=bool(data.get("overwrite_existing")),
        )
        return jsonify(payload), status

    @bp.route("/api/documents/<doc_id>/save-as", methods=["POST"])
    def save_document_as(doc_id):
        if has_invalid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        source_meta = load_document_meta_fn(doc_id)
        if not source_meta:
            return jsonify({"error": "document not found"}), 404
        data = request.get_json(silent=True) or {}
        new_title = normalize_document_title_fn(str(data.get("title") or "").strip())
        if not new_title:
            return jsonify({"error": "title is required"}), 400
        if document_title_exists_fn(new_title):
            return jsonify({"error": "duplicate title"}), 409
        new_doc_id = uuid.uuid4().hex[:12]
        src_dir = document_dir_fn(doc_id)
        dst_dir = document_dir_fn(new_doc_id)
        shutil.copytree(src_dir, dst_dir)
        meta_path = dst_dir / "file_manager" / "meta.json"
        if not meta_path.exists():
            meta_path = dst_dir / "meta.json"
        meta = read_json_fn(meta_path, {}) or {}
        meta["workspace_id"] = new_doc_id
        meta["is_saved"] = True
        meta["title"] = new_title[:120]
        meta["created_at"] = utc_now_fn()
        save_document_meta_fn(meta)
        reviews_path = dst_dir / "file_manager" / "reviews.json"
        if not reviews_path.exists():
            reviews_path = dst_dir / "reviews.json"
        reviews = read_json_fn(reviews_path, []) or []
        for review in reviews:
            review["workspace_id"] = new_doc_id
        write_json_fn(dst_dir / "file_manager" / "reviews.json", reviews)
        latest_run_id = (meta.get("runs") or [{}])[-1].get("run_id")
        return jsonify({"saved_as": True, **ids_payload(new_doc_id), "run_id": latest_run_id, "title": meta.get("title")}), 201

    return bp
