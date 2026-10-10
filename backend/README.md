# Backend — CMI Blog Revamp Agent

FastAPI service consumed by `../frontend` (also served statically from `/`).

## Run

From this directory:

```powershell
python -m pip install -r requirements.txt   # system python already has most of these
uvicorn main:app --port 8000
```

Then open http://localhost:8000 (or open `../frontend/index.html`
directly — it calls the API at `http://localhost:8000` by default).

Needs the root `.env` (`DB_URL`, plus optional `LLM_*`). The report catalog
must have its `embedding` column populated for semantic matching
(see `scripts/reembed_catalog.py`); text matching works without it.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{ status }` |
| POST | `/upload` | multipart `.docx` | `{ filename, article }` |
| POST | `/step1` | `{ filename, article, sponsor_index }` | `{ token, run_token, orient, candidates, hits }` |
| POST | `/step2` | `{ run_token, report_id }` | `{ selection, report, evidence }` |
| POST | `/step3` | `{ run_token }` | `{ title, sections }` |
| POST | `/finalize` | `{ run_token }` | `{ result }` |
| GET | `/download/{filename}` | — | revised `.docx` |

Errors are plain-text bodies (the UI reads `resp.text()`). Run tokens are
in-memory, single-use (consumed by `/finalize`), and expire after 6h.
Per-run logs land in `logs/`.
