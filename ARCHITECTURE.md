# Architecture Handoff Guide

English is the source of truth. `ARCHITECTURE_KOR.md` is its translation.

## Term Conventions

Use these terms consistently throughout this document:
- **run**: one processing execution unit for a document version.
- **single-run / diff-run**: single report processing vs previous/current comparison processing.
- **semantic map**: mapping derived from `equal` diff segments between old/new word IDs.
- **section index**: structured section catalog (`section_id`, title, range metadata).
- **viewer payload**: the UI-facing aggregated data, primarily stored in `viewer_data.json`.
- **AI call loop**: iterative model/tool execution loop in `ai_callers.py`.

## 1) Runtime Sequence (File-Mapped)

This is the actual execution path from upload to AI assessment.

1. **Document upload API**
   - `routes/document_routes.py`: `/api/documents`, `/api/documents/<doc_id>/runs`
   - Composition entrypoint: `app.py` (`register_blueprints`, deps wiring)

2. **PDF -> Markdown conversion (OpenDataLoader library)**
   - Execution: `document_io_service.py` -> `run_opendataloader_to_markdown()`
   - Orchestration: `document_pipeline_service.py`

3. **Markdown diff generation (text-level diff)**
   - Execution: `document_io_service.py` -> `run_diff_extract()`
   - Diff engine script: `document_diff_extract.py`
   - Output: `result.json` segments (`equal`, `delete`, `add`) from markdown text comparison.
   - Note: this step is **not** PDF-coordinate mapping yet. It is textual diff first.

4. **Map text diff segments to PDF-word JSON coordinates**
   - `document_diff_analysis_service.py`
   - Alignment, move suppression, highlight/change generation
   - Input mapping target is `words.json` / `prev_words.json` (PDF words already extracted as JSON).
   - In short: map markdown diff segments from step 3 onto PDF word-index/bbox/page data.

5. **Generate semantic map + section index + viewer payload**
   - Semantic: `document_semantic.py`
   - Indexing: `document_index_service.py`
   - Outputs saved under `documents/<doc_id>/runs/<run_id>/...`
   - `semantic_map` means mapping between old/new words that belong to `equal` diff segments (`old_word_id` <-> `new_word_id`) with line/segment metadata.
   - `section index` means structured section catalog (title, section_id, start/end range metadata) used for section-aware review/AI context.

6. **Review create/update/read**
   - HTTP layer: `routes/document_routes.py` (`/reviews` endpoints)
   - Anchor projection/mapping: `document_review_projection_service.py`

7. **AI assessment**
   - HTTP layer: `routes/assessment_routes.py` (`/assess`, `/change-assess`)
   - Orchestration: `assessment_service.py`
   - Model call loop: `ai_callers.py`
   - Tool schemas: `ai_runtime_utils.py`
   - Tool implementations: `section_context_utils.py`

8. **Snapshot/save/export**
   - Snapshot: `document_snapshot_service.py`, `routes/snapshot_routes.py`
   - PDF export: `document_export_service.py`

---

## 2) Domain Groups

### 2-1. `document_` Group

#### Common role
Owns the full document-centric workflow: conversion, diff, anchors, reviews, snapshots.

#### Files
- `document_io_service.py`
  - PDF word extraction/page rendering, OpenDataLoader conversion, diff script execution
- `document_pipeline_service.py`
  - Single-run / diff-run orchestration
- `document_diff_extract.py`
  - Markdown diff segment generator (execution script)
- `document_diff_analysis_service.py`
  - Segment-PDF alignment and highlight/change generation
- `document_semantic.py`
  - Semantic map built from `equal` diff segments + run review storage helpers
- `document_index_service.py`
  - Section index extraction and marker injection
- `document_review_projection_service.py`
  - Review anchor creation/projection and run-to-run migration
- `document_review_migration_service.py`
  - Previous review carry-forward and anchor remap helpers
- `document_snapshot_service.py`
  - Snapshot creation/counting/retention pruning
- `document_export_service.py`
  - Review-based annotated PDF export
- `document_bootstrap_service.py`
  - Seed document bootstrap from `input/`
- `document_assessment_context_service.py`
  - Document-side context building/grouping for AI assessment

### 2-2. `assessment_` + `ai_` Group

#### Common role
Runs AI assessment for reviews/changes and stores structured outputs.

#### Files
- `assessment_service.py`
  - Main orchestration (target selection, execution, persistence)
- `assessment_prompt_builders.py`
  - Prompt construction (single/batch)
- `prompts/ai_assessment_system.md`
  - System prompt for review-level assessment behavior/policy.
- `prompts/change_ai_assessment_system.md`
  - System prompt for change-level risk assessment behavior/policy.
- `assessment_normalizers.py`
  - Output normalization and policy enforcement
- `ai_callers.py`
  - Anthropic/OpenAI AI call loop with tool execution
- `ai_runtime_utils.py`
  - Tool schemas + runtime helpers
- `ai_config_utils.py`
  - Model/provider/key config helpers
- `prompts/ai_assessment_system_Kor.md`
  - Korean translation of review-level system prompt.
- `prompts/change_ai_assessment_system_Kor.md`
  - Korean translation of change-level system prompt.

#### AI tools used by the model (detailed)
- `read_section(file, section_id)`
  - Purpose: read one specific structured markdown section body.
  - Typical use: when the model already knows which section to verify.
  - Output shape: section text for that `file` (`previous` or `current`) and `section_id`.
- `search_markdown(file, query)`
  - Purpose: context discovery when exact location is unclear.
  - Typical use: find nearby wording and context windows for ambiguous evidence.
  - Output shape: line-based hit windows (`line`, `context`).
- `keyword_search_markdown(file, keyword, case_sensitive?, whole_word?, max_hits?)`
  - Purpose: exhaustive keyword check (count + positions).
  - Typical use: broad replacement checks where full count and locations are required.
  - Output shape: `total_count`, `matches` (line/column/section), `section_counts`, `truncated`.
- `submit_verdict(...)`
  - Purpose: final submission for single review-level assessment.
- `submit_verdicts(items=[...])`
  - Purpose: final submission for batch review-level assessment.
- `submit_change_review(...)`
  - Purpose: final submission for single change-risk assessment.
- `submit_change_reviews(items=[...])`
  - Purpose: final submission for batch change-risk assessment.

### 2-3. `routes` Group (HTTP Layer)

#### Common role
Handles request parsing/validation/response formatting and delegates business logic.

#### Files
- `routes/document_routes.py`: document/run/review/save/export APIs
- `routes/assessment_routes.py`: AI assess/forward/migrate APIs
- `routes/snapshot_routes.py`: snapshot APIs
- `routes/file_manager_routes.py`: file-manager APIs

### 2-4. `*_utils` Group

#### Common role
Cross-domain reusable utility helpers (no domain policy decisions).

#### Files
- `storage_utils.py`
  - Path builders + JSON read/write + UTC helper
- `text_utils.py`
  - Token/space/title normalization + prompt-safe trim
- `section_context_utils.py`
  - Markdown section/context tooling and AI tools:
  - `read_section`, `search_markdown`, `keyword_search_markdown`

---

## 3) Entrypoint and Composition

- `app.py`
  - Flask entrypoint
  - Blueprint registration
  - Dependency composition/injection into route factories

---

## 4) Data Layout

- `documents/<doc_id>/meta.json`
  - Document metadata, title, saved flag, and run list (`runs[]`).
- `documents/<doc_id>/reviews.json`
  - Document-level canonical review threads/anchors.
- `documents/<doc_id>/runs/<run_id>/report.pdf`
  - Current run source PDF file.
- `documents/<doc_id>/runs/<run_id>/report.md`
  - Current run markdown generated from PDF.
- `documents/<doc_id>/runs/<run_id>/words.json`
  - Current run PDF words extracted as JSON (text/page/bbox/index).
- `documents/<doc_id>/runs/<run_id>/chars.json`
  - Character-level JSON derived from words.
- `documents/<doc_id>/runs/<run_id>/result.json`
  - Markdown diff result segments (`equal/delete/add`).
- `documents/<doc_id>/runs/<run_id>/viewer_data.json`
  - Main viewer payload (highlights, changes, semantic/index outputs).
- `documents/<doc_id>/runs/<run_id>/ai_assessment.json`
  - Review-level AI assessment results and metadata.
- `documents/<doc_id>/runs/<run_id>/change_ai_assessment.json`
  - Change-level AI assessment results and metadata.
- `documents/<doc_id>/runs/<run_id>/prev_report.pdf`, `prev_report.md`, `prev_words.json`, `prev_chars.json`, `prev_section_map.json`
  - Previous-run artifacts copied for update/diff context.
- `documents/<doc_id>/runs/<run_id>/section_map.json`
  - Current run section index metadata.
- `documents/<doc_id>/snapshots/<snapshot_id>/snapshot.json`
  - Snapshot metadata (counts, mode, source run).
- `documents/<doc_id>/snapshots/<snapshot_id>/viewer_data.json`
  - Frozen viewer payload for snapshot.
- `documents/<doc_id>/snapshots/<snapshot_id>/reviews.json`
  - Frozen review set for snapshot.

---
