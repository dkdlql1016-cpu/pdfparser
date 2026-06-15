# PDF Review Workspace

A local web app for reviewing financial-report PDFs: open a report, compare a
revised version against the previous one (visual diff), anchor review comments to
the text, and optionally generate AI assessments of the changes. Runs entirely on
your machine.

## 1. Prerequisites

Install these first and confirm they are on your `PATH`:

| Requirement | Verify with | Why it is needed |
|---|---|---|
| **Python 3.11+** | `python --version` | Runs the app server |
| **Java 17+** (OpenJDK / Temurin) | `java -version` | **Required.** PDF text extraction shells out to a bundled Java tool; PDF upload and diff fail without it |

> Java is not optional — uploading or comparing any PDF invokes it.

## 2. Get the code running

Open a terminal **in the project folder** (the one containing `app.py` and
`requirements.txt`), then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

On macOS/Linux, activate with `source .venv/bin/activate` instead of line 2.

When you see `PDF Diff Viewer running: http://127.0.0.1:8000`, open
**http://127.0.0.1:8000** in your browser.

## 3. AI features (optional)

PDF upload, viewer, diff, comments, and export all work **without** any API key.
Only the AI assessment step needs one.

To enable it, copy `.env.example` to `.env` (note the leading dot) and fill in your key:

```powershell
copy .env.example .env
```

```ini
# .env
OPENAI_API_KEY=sk-...        # default model is gpt-5.4 (OpenAI)
```

> The default model `gpt-5.4` uses OpenAI, so `OPENAI_API_KEY` is what you need.
> If you instead set a Claude model (e.g. `AI_ASSESSMENT_MODEL=claude-...`), provide
> `ANTHROPIC_API_KEY` instead. Restart `python app.py` after editing `.env`.

## 4. Try it (sample reports included)

Three demo PDFs ship in [input/](input/) so you can exercise the app without your
own files:

1. **Upload a report** — upload `input/Demo_FY24_Report_v1_clean.pdf` to open it in the viewer.
2. **Compare revisions** — upload `input/Demo_FY24_Report_v2_clean.pdf` to run a previous/current diff.
3. **Add review comments** — select text in the PDF to anchor and save a comment.
4. **AI assessment** — generate an AI review of the changes (requires the key from step 3).
5. **Export annotations** — export saved reviews as PDF annotations.

## A note on `_KOR` / `_Kor` files

The app runs entirely on the **English** prompts and docs. Files ending in `_KOR`
or `_Kor` (e.g. `docs/ARCHITECTURE_KOR.md`, `prompts/ai_assessment_system_Kor.md`)
are the developer's own Korean review notes — they are **not loaded at runtime** and
can be ignored.

## Troubleshooting

- **`'java' command not found`** — install Java 17+ and reopen the terminal so `PATH` updates.
- **Port 8000 already in use** — stop the other process, or it is likely a previous
  instance of this app still running.
- **AI step says a key is required** — confirm the file is named exactly `.env` (not
  `env`), sits next to `app.py`, and that you restarted the server.
