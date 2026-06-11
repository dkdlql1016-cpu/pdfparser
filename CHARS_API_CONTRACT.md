# Chars API Contract

## Decision

`/chars/<side>` endpoints use a **server-side page-scoped API**.

- Server remains the source of truth for per-character bbox generation.
- Clients should request only the page(s) needed for rendering.
- Full-document chars payloads are best-effort and can be rejected by size guards.

## Endpoints

- `GET /api/documents/<workspace_id>/runs/<run_id>/chars/<side>`
- `GET /api/documents/<workspace_id>/snapshots/<snapshot_id>/chars/<side>`

## Query Parameters

- `page`: single page number (1-based)
- `page_start`: range start page (1-based)
- `page_end`: range end page (1-based)

Rules:

- `page` cannot be combined with `page_start` or `page_end`.
- If both `page_start` and `page_end` are set, `page_start <= page_end` must hold.
- Invalid values return `400`.

## Response Behavior

- `200`: list of char items
- `400`: invalid ids, invalid side, or invalid page filter query
- `404`: target words source file missing
- `413`: request rejected by chars size guard

## Size Guard Policy

`/chars` responses are rejected when either threshold is exceeded:

- `CHARS_MAX_WORDS` (default `50000`)
- `CHARS_MAX_ESTIMATED_COUNT` (default `250000`)

On rejection (`413`), the response includes:

- `error`
- `reason` (`word_limit_exceeded` or `char_limit_exceeded`)
- threshold value and measured estimate

## Operational Notes

- Endpoints log words/char counts and generation latency (`elapsed_ms`) for tuning.
- Thresholds are controlled through environment variables:
  - `CHARS_MAX_WORDS`
  - `CHARS_MAX_ESTIMATED_COUNT`
