# Catalog sync: add new website reports to catalog.sqlite

Keeps `backend/catalog.sqlite` (the report catalog the app matches markets against) in step with
https://www.coherentmarketinsights.com/latest-insights. Insert-only: reports already in the DB are never
changed or deleted.

## How it works

The latest-insights page loads its list from a JSON API (found by watching the page's network requests in Playwright):

    https://api.coherentmarketinsights.com/api/reports?page=N&pageSize=100     (pageSize max 100)

Response: `data[]` (`newsId`, `customName` = URL slug, `keyword`, `newsSubject`, ...) and `meta` (`total`, `hasMore`).

| Step | Script | Python | What it does |
|---|---|---|---|
| 1 | `scrape_latest_insights.py` | `C:\Python314\python.exe` (Playwright; no `-I`) | Opens the page, logs the page's own API call, pages through all results at 100 per page (0.5 s apart, 3 retries), checks that no ids repeat and the count equals `meta.total`, and writes `scraped_reports.json` (gitignored). |
| 2 | `sync_new_reports.py` | any Python (stdlib only) | Inserts rows whose `news_id` is not already in `reports`: `fmt=2`, `has_summary=0`, `subject` from the API, `segmentation` and `embedding` NULL. One transaction; safe to re-run. |
| 3 | `embed_missing_reports.py` | `backend\venv\Scripts\python.exe` (sentence-transformers) | For rows with `embedding IS NULL`, encodes `keyword` with `BAAI/bge-large-en-v1.5` (plain `encode`, no prefix, fp32, batches of 64) and stores 1024 x little-endian float32 (4096 bytes), the same format `reports.py` reads with `np.frombuffer(blob, "<f4")`. Safe to re-run. |

New reports use the link form `https://www.coherentmarketinsights.com/industry-reports/<slug>` (`fmt=2`, as `report_url()` builds it).

## Run again later

Back up first (`catalog.sqlite` is gitignored): `cp backend/catalog.sqlite backend/catalog.sqlite.bak-<date>`, then run steps 1, 2 and 3 above in order.

## Run of 2026-11-13

- The site listed 9,580 reports and the DB held 9,564, of which 706 are no longer on the site's list (left in place). 722 reports were new (`news_id` 9660 to 10390).
- After the run the DB has 10,286 reports, all with embeddings. The 9,564 original rows were verified unchanged against a backup (`catalog.sqlite.bak-20261113`, deletable).
- Embedding check: re-encoding 5 new and 3 old keywords gave cosine 1.000000 against the stored vectors, so the model and settings match the original ones. The embedding step took about 2 minutes on CPU.
- New report 9691 (`overactive-bladder-treatment-market`) has the same slug as existing report 2007. 2007 is `fmt=0`, so its URL differs; both were kept.
- Steps 1 to 3 were written and run by GLM-5.3-Flash subagents; the results were checked independently.

## Not covered

- `segmentation` and `summary` are empty for new reports (the API doesn't return them).
- `packaging/_build/stage/backend/catalog.sqlite` is not updated; package builds copy from `backend/catalog.sqlite`, so it refreshes on the next build.
- The `categories` table is untouched.
