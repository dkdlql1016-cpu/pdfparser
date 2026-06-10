# Architecture Handoff Guide

## Goal

This project is organized for maintainability and handoff.  
`app.py` is the entrypoint/composition root, and domain logic is split into services and route blueprints.

## High-Level Flow

1. Client calls HTTP API.
2. `routes/*` handles request/response and delegates to app-level functions.
3. App-level functions delegate to domain services (`document_*`, `assessment_*`, etc).
4. Data is read/written under `documents/<doc_id>/...`.

## Main Domains

- `document_*`: Core document processing and review workflow.
  - PDF extraction/rendering, markdown diff pipeline, semantic mapping, review projection, snapshots, bootstrap/seed helpers.
- `assessment_*` + `ai_*`: AI assessment orchestration.
  - Builds context packs, groups targets, calls model tool-runtime, normalizes outputs.
- `routes/*`: HTTP layer by feature domain.
  - `document_routes`, `assessment_routes`, `snapshot_routes`, `file_manager_routes`.
- `*_utils`: Cross-domain reusable helpers.
  - Storage/path/json, text normalization, markdown section context.

## Key Files and Responsibilities

- `app.py`
  - Flask app bootstrap, blueprint registration, dependency composition.
  - Keeps thin bridge/wrapper functions and shared app-level orchestration.

- `routes/document_routes.py`
  - Document CRUD/run/review/export/save API endpoints.

- `routes/assessment_routes.py`
  - AI assessment APIs (run, batch/single, forward, migrate).

- `routes/snapshot_routes.py`
  - Snapshot list/create/read/export APIs.

- `routes/file_manager_routes.py`
  - File manager list/rename/delete APIs.

- `document_pipeline_service.py`
  - Single run/diff run processing pipeline.

- `document_io_service.py`
  - PDF word extraction/rendering and markdown diff execution.

- `document_diff_analysis_service.py`
  - Segment alignment, move suppression, highlights/changes generation.

- `document_index_service.py`
  - Section index extraction and marker injection.

- `document_review_projection_service.py`
  - Review anchor projection and migration to current run context.

- `document_assessment_context_service.py`
  - Assessment context building, grouping, change-context helpers.

- `document_snapshot_service.py`
  - Snapshot metadata/files creation and retention pruning.

- `document_bootstrap_service.py`
  - Default seed document bootstrap from `input/`.

- `assessment_service.py`
  - Main assessment orchestration for review-level and change-level analysis.

- `ai_callers.py`
  - Model API callers (Anthropic/OpenAI) and tool call loop execution.

- `ai_runtime_utils.py`
  - Tool schema definitions (`read_section`, `search_markdown`, submit tools) and runtime helpers.

- `section_context_utils.py`
  - Markdown section extraction and contextual search.

- `storage_utils.py`
  - Path helpers and JSON read/write utilities.

- `text_utils.py`
  - Text/token normalization and prompt-safe trimming helpers.

## AI Tooling Notes

The AI runtime exposes internal tools for context retrieval:

- `read_section(file, section_id)`
- `search_markdown(file, query)`

These are defined in `ai_runtime_utils.py` and executed in `ai_callers.py`.

## Data Layout

- `documents/<doc_id>/meta.json`: document metadata + run list.
- `documents/<doc_id>/reviews.json`: document-level review store.
- `documents/<doc_id>/runs/<run_id>/...`: run artifacts (`viewer_data.json`, words/chars, PDFs, assessments).
- `documents/<doc_id>/snapshots/<snapshot_id>/...`: snapshot artifacts and metadata.

## Where to Start (for new maintainers)

1. Read `app.py` (composition and blueprint deps).
2. Read `routes/document_routes.py` for primary API behavior.
3. Follow into `document_*_service.py` files for business logic.
4. For AI behavior, read `assessment_service.py` -> `ai_callers.py` -> `ai_runtime_utils.py`.
