# Run Artifact Layout

This document is the human-readable contract for files under:

```text
documents/<doc_id>/runs/<run_id>/
```

The code source of truth for these paths is `run_layout.py`.

## Principles

- `current/`, `previous/`, and `diff/` contain durable source artifacts.
- `viewer.json` is a derived UI bundle. It is convenient to load, but not the raw source.
- `_cache/` contains disposable intermediate files. It is never snapshotted and can be deleted after troubleshooting.
- `documents/<doc_id>/reviews.json` remains the canonical review store. Run-level `reviews.json` is a projection cache for export and snapshots.

## Directory Layout

```text
documents/<doc_id>/
  meta.json
  reviews.json
  runs/<run_id>/
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
    ai/
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
| `current/source.pdf` | Current-side source PDF | Yes | Rendered by page APIs and used by export. |
| `current/source.md` | Current-side markdown with section markers | Yes | Used for diff, section context, and AI tools. |
| `current/words.json` | Current-side PDF word coordinates | Yes | Source of truth for bbox/page/selection/char generation. |
| `current/sections.json` | Current-side section map | Yes | Used by AI and section navigation. |
| `previous/source.pdf` | Previous-side source PDF | Diff only | Old pane rendering and diff inputs. |
| `previous/source.md` | Previous-side markdown | Diff only | Previous-side diff and AI context. |
| `previous/words.json` | Previous-side word coordinates | Diff only | Old-side bbox/page/selection. |
| `previous/sections.json` | Previous-side section map | Diff only | Previous-side AI and section matching. |
| `diff/segments.json` | Raw text diff segments | Diff only | Former `result.json`; no PDF coordinates. |
| `viewer.json` | UI-facing derived bundle | Yes | Contains highlights, changes, semantic map, section copy, alignment report. |
| `reviews.json` | Run-level review projection cache | Feature cache | Canonical reviews are stored at document level. |
| `ai/review_assessment.json` | Review AI output | AI feature | Re-runnable if deleted. |
| `ai/change_assessment.json` | Change AI output | AI feature | Re-runnable if deleted. |
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
