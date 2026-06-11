# Run Artifact Layout

This document is the human-readable contract for files under:

```text
documents/workspaces/<workspace_id>/runs/<run_id>/
```

The code source of truth for these paths is `run_layout.py`.

## Principles

- `compare/` contains diff-facing artifacts (`current/`, `previous/`, `diff/`, and `viewer.json`).
- `analysis/` contains AI/review artifacts (`review_assessment.json`, `change_assessment.json`, `reviews.json`).
- `_cache/` contains disposable intermediate files. It is never snapshotted and can be deleted after troubleshooting.
- Workspace-level metadata/reviews live under `file_manager/` (`meta.json`, `reviews.json`).

## Directory Layout

```text
documents/workspaces/<workspace_id>/
  file_manager/
    meta.json
    reviews.json
  runs/<run_id>/
    compare/
      viewer.json
      current/
        source.pdf
        source.md
        words.json
        sections.json
      previous/
        source.pdf
        source.md
        words.json
        sections.json
      diff/
        segments.json
    analysis/
      review_assessment.json
      change_assessment.json
      reviews.json
    _cache/
      opendataloader/
      diff_md/
        current.md
        previous.md
```

`previous/` and `diff/` exist only for comparison runs.

## Artifact Roles

| Path | Role | Required? | Notes |
|---|---|---:|---|
| `compare/current/source.pdf` | Current-side source PDF | Yes | Rendered by page APIs and used by export. |
| `compare/current/source.md` | Current-side markdown with section markers | Yes | Used for diff, section context, and AI tools. |
| `compare/current/words.json` | Current-side PDF word coordinates | Yes | Source of truth for bbox/page/selection/char generation. |
| `compare/current/sections.json` | Current-side section map | Yes | Used by AI and section navigation. |
| `compare/previous/source.pdf` | Previous-side source PDF | Diff only | Old pane rendering and diff inputs. |
| `compare/previous/source.md` | Previous-side markdown | Diff only | Previous-side diff and AI context. |
| `compare/previous/words.json` | Previous-side word coordinates | Diff only | Old-side bbox/page/selection. |
| `compare/previous/sections.json` | Previous-side section map | Diff only | Previous-side AI and section matching. |
| `compare/diff/segments.json` | Raw text diff segments | Diff only | Former `result.json`; no PDF coordinates. |
| `compare/viewer.json` | UI-facing derived bundle | Yes | Contains highlights, changes, semantic map, section copy, alignment report. |
| `analysis/reviews.json` | Run-level review projection cache | Feature cache | Canonical reviews are stored at workspace level. |
| `analysis/review_assessment.json` | Review AI output | AI feature | Re-runnable if deleted. |
| `analysis/change_assessment.json` | Change AI output | AI feature | Re-runnable if deleted. |
| `_cache/opendataloader/` | OpenDataLoader raw output | No | Disposable after `current/source.md` is copied. |
| `_cache/diff_md/*.md` | Marker-stripped diff inputs | No | Disposable debug inputs for `diff/segments.json`. |

## Removed Legacy Files

- `chars.json`, `prev_chars.json`: no longer persisted. `/chars/<side>` is generated on demand from `words.json`.
- Flat names such as `report.pdf`, `prev_report.pdf`, `words.json`, `prev_words.json`, `section_map.json`, `viewer_data.json`, `result.json`.
- `prev_reviews.json`: old carry-forward cache; the document-level review store is canonical.

## Size Notes

The main storage win is removing old `chars.json` artifacts. In sample data, `chars.json` + `prev_chars.json` consumed about **39 MB per diff run**. By contrast, duplicated section data is usually only a few KB, so it is kept in `viewer.json` for UI and AI convenience.

## Snapshot Rule

Snapshots copy durable artifacts from a run while excluding `_cache/`. Snapshot folders use the same layout as runs, plus `snapshot.json`.
