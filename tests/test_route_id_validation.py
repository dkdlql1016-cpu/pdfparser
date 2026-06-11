import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.document_routes import create_document_blueprint
from routes.file_manager_routes import create_file_manager_blueprint
from routes.snapshot_routes import create_snapshot_blueprint


def _noop(*_args, **_kwargs):
    return None


def _missing(*_args, **_kwargs):
    return {}


class RouteIdValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.app = Flask(__name__)
        self.app.register_blueprint(create_document_blueprint(deps=self._document_deps()))
        self.app.register_blueprint(create_snapshot_blueprint(deps=self._snapshot_deps()))
        self.app.register_blueprint(create_file_manager_blueprint(deps=self._file_manager_deps()))
        self.client = self.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    def _document_deps(self):
        run_dir_fn = lambda doc_id, run_id: self.tmp_dir / doc_id / "runs" / run_id
        return {
            "get_uploaded_pdf_fn": _noop,
            "document_run_dir_fn": run_dir_fn,
            "utc_now_fn": lambda: "2026-01-01T00:00:00+00:00",
            "save_document_meta_fn": _noop,
            "process_single_document_run_fn": _noop,
            "update_run_meta_fn": _noop,
            "load_document_meta_fn": lambda _doc_id: None,
            "load_document_reviews_fn": lambda _doc_id: [],
            "save_document_reviews_fn": _noop,
            "copy_if_exists_fn": _noop,
            "read_json_fn": lambda _path, default=None: default,
            "build_chars_from_words_fn": lambda words: words,
            "anchor_for_selection_fn": _noop,
            "canonical_review_from_anchor_fn": _missing,
            "document_reviews_for_run_fn": lambda _doc_id, _run_id: [],
            "process_document_diff_run_fn": _noop,
            "render_pdf_page_fn": _noop,
            "create_document_level_review_fn": _missing,
            "export_annotated_pdf_fn": _noop,
            "run_meta_for_fn": _noop,
            "overwrite_saved_document_fn": lambda *_args, **_kwargs: ({}, 200),
            "normalize_document_title_fn": lambda title: title,
            "document_title_exists_fn": lambda *_args, **_kwargs: False,
            "save_run_side_file_fn": lambda *_args, **_kwargs: ({}, 200),
            "document_dir_fn": lambda doc_id: self.tmp_dir / doc_id,
            "write_json_fn": _noop,
        }

    def _snapshot_deps(self):
        return {
            "load_document_meta_fn": lambda _doc_id: None,
            "snapshot_limit": 10,
            "create_run_snapshot_fn": lambda *_args, **_kwargs: ({}, 200),
            "document_snapshot_dir_fn": lambda doc_id, snap_id: self.tmp_dir / doc_id / "snapshots" / snap_id,
            "read_json_fn": lambda _path, default=None: default,
            "build_chars_from_words_fn": lambda words: words,
            "snapshot_side_file_fn": lambda side, kind: f"{side}.{kind}",
            "render_pdf_page_fn": _noop,
            "export_annotated_pdf_fn": _noop,
        }

    def _file_manager_deps(self):
        return {
            "ensure_default_file_manager_documents_fn": _noop,
            "list_documents_for_manager_fn": lambda: [],
            "load_document_meta_fn": lambda _doc_id: None,
            "normalize_document_title_fn": lambda title: title,
            "document_title_exists_fn": lambda *_args, **_kwargs: False,
            "save_document_meta_fn": _noop,
            "document_dir_fn": lambda doc_id: self.tmp_dir / doc_id,
        }

    def test_document_invalid_id_returns_400(self):
        response = self.client.get("/api/documents/nothex")
        self.assertEqual(response.status_code, 400)

    def test_document_missing_with_valid_id_returns_404(self):
        response = self.client.get("/api/documents/abcdef123456")
        self.assertEqual(response.status_code, 404)

    def test_document_chars_invalid_run_id_returns_400(self):
        response = self.client.get("/api/documents/abcdef123456/runs/nothex/chars/new")
        self.assertEqual(response.status_code, 400)

    def test_document_chars_missing_run_file_returns_404(self):
        response = self.client.get("/api/documents/abcdef123456/runs/123456abcdef/chars/new")
        self.assertEqual(response.status_code, 404)

    def test_snapshot_invalid_snapshot_id_returns_400(self):
        response = self.client.get("/api/documents/abcdef123456/snapshots/123456abcdef/words/new")
        self.assertEqual(response.status_code, 400)

    def test_snapshot_missing_with_valid_ids_returns_404(self):
        response = self.client.get("/api/documents/abcdef123456/snapshots/snap-abcdef1234/words/new")
        self.assertEqual(response.status_code, 404)

    def test_snapshot_create_invalid_run_id_returns_400(self):
        response = self.client.post("/api/documents/abcdef123456/runs/nothex/snapshots", json={})
        self.assertEqual(response.status_code, 400)

    def test_file_manager_patch_invalid_id_returns_400(self):
        response = self.client.patch("/api/file-manager/nothex", json={"title": "x"})
        self.assertEqual(response.status_code, 400)

    def test_file_manager_delete_invalid_id_returns_400(self):
        response = self.client.delete("/api/file-manager/nothex")
        self.assertEqual(response.status_code, 400)

    def test_file_manager_patch_missing_document_returns_404(self):
        response = self.client.patch("/api/file-manager/abcdef123456", json={"title": "x"})
        self.assertEqual(response.status_code, 404)

    def test_file_manager_delete_permission_error_returns_423(self):
        with patch("routes.file_manager_routes.shutil.rmtree", side_effect=PermissionError()):
            response = self.client.delete("/api/file-manager/abcdef123456")
        self.assertEqual(response.status_code, 423)

    def test_file_manager_delete_oserror_returns_500(self):
        with patch("routes.file_manager_routes.shutil.rmtree", side_effect=OSError("io error")):
            response = self.client.delete("/api/file-manager/abcdef123456")
        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
