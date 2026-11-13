r"""Append newly-published CMI reports from scraped_reports.json to backend/catalog.sqlite.

Read-only wrt existing rows: INSERTs only, never UPDATE/DELETE. Existing rows in the
DB (exported by backend/scripts/export_catalog_sqlite.py) must stay byte-identical,
so this only adds rows for news_ids that are not yet in the `reports` table.

  reports    news_id, keyword, name (URL slug), fmt (isNewFormat), has_summary,
             subject (newsSubject), segmentation, embedding (float32 BLOB or NULL)

New reports live at https://www.coherentmarketinsights.com/industry-reports/<slug>
(i.e. the new report format), so they are inserted with fmt=2, has_summary=0 and
embedding=NULL — the embeddings are filled in afterwards by
embed_missing_reports.py. A slug that duplicates an existing row's slug is fine
(older reports use a different URL pattern per fmt) but is flagged with a warning.

Stdlib only. Idempotent: a second run finds nothing new and inserts 0 rows.

Run with any Python (C:\Python314\python.exe works)::

    python backend/scripts/catalog_sync/sync_new_reports.py
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(r"C:\Users\rupen\Documents\content-revise-agent\backend\catalog.sqlite")
JSON_PATH = Path(
    r"C:\Users\rupen\Documents\content-revise-agent\backend\scripts\catalog_sync\scraped_reports.json"
)
BASE_URL = "https://www.coherentmarketinsights.com/industry-reports/"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    with open(JSON_PATH, encoding="utf-8") as f:
        records = json.load(f)
    log(f"loaded {len(records)} records from {JSON_PATH.name}")

    db = sqlite3.connect(DB_PATH)
    try:
        existing_ids = {row[0] for row in db.execute("SELECT news_id FROM reports")}
        existing_slugs = {row[0]: row[1] for row in db.execute("SELECT name, news_id FROM reports")}
        log(f"{len(existing_ids)} rows already in reports (max news_id {max(existing_ids)})")

        new_rows = []
        slug_collisions = []
        seen_ids = set()
        skipped_empty = 0
        for r in records:
            nid = int(r["news_id"])
            if nid in existing_ids or nid in seen_ids:
                continue  # already in the DB (or duplicated in the JSON) — never touch it
            seen_ids.add(nid)
            keyword = (r.get("keyword") or "").strip()
            slug = (r.get("slug") or "").strip()
            if not keyword or not slug:
                skipped_empty += 1
                continue
            if slug in existing_slugs:
                slug_collisions.append((nid, slug, existing_slugs[slug]))
            new_rows.append(
                (nid, keyword, slug, 2, 0, r.get("subject") or None, None, None)
            )

        log(f"{len(new_rows)} new row(s) to insert ({skipped_empty} skipped: empty keyword/slug)")

        # One transaction, all-or-nothing: rollback on any error leaves the DB untouched.
        try:
            with db:
                db.executemany(
                    "INSERT INTO reports (news_id, keyword, name, fmt, has_summary, subject, segmentation, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    new_rows,
                )
        except Exception as e:
            db.rollback()
            log(f"ERROR: transaction rolled back, DB unchanged: {e}")
            return 2

        log(f"inserted {len(new_rows)} row(s)")
        if slug_collisions:
            log(f"WARNING: {len(slug_collisions)} new row(s) reuse the slug of an existing row "
                "(expected and fine — different fmt means a different URL):")
            for nid, slug, old_nid in slug_collisions[:10]:
                log(f"    news_id {nid} slug '{slug}' == slug of existing news_id {old_nid}")
        log("--- 3 example new urls ---")
        for nid, kw, slug, *_ in new_rows[:3]:
            log(f"    news_id {nid}: {BASE_URL}{slug}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
