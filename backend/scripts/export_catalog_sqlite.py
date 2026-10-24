"""Export the CMI report catalog from Postgres to backend/catalog.sqlite for distribution.

Ships with the package so end-user desktops never connect to Postgres. One-time (and rerun
whenever the catalog changes); everything the packaged backend reads is written here:

  reports    news_id, keyword, name (CustomName), fmt (isNewFormat), has_summary,
             subject (newsSubject), segmentation (HTML, used only as context by the writer),
             embedding (float32 BLOB, copied from the Postgres pgvector column)
  categories cat_name (same filter/sort the old code used: IsActive, not 'Category %')

Run with a Python that has psycopg2 + network access to the DB (the dev env)::

    python backend/scripts/export_catalog_sqlite.py

Requires DB_URL in the root .env (the owner creds — SELECT only is enough). Idempotent:
writes to a temp file then atomically replaces the target.
"""

import os
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
from config import DB_URL  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "catalog.sqlite"


def parse_vector(maybe: object) -> bytes | None:
    """pgvector comes back as text like '[0.1,0.2,...]' → packed little-endian float32."""
    if maybe is None:
        return None
    s = maybe if isinstance(maybe, str) else str(maybe)
    s = s.strip().strip("[]")
    if not s:
        return None
    vals = [float(x) for x in s.split(",")]
    return struct.pack(f"<{len(vals)}f", *vals)


def main() -> None:
    if not DB_URL:
        raise SystemExit("DB_URL missing in .env — needed to read the Postgres catalog")
    print(f"exporting catalog from {DB_URL.split('@', 1)[-1]} ...")
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r."newsId", r.keyword, r."CustomName", r."isNewFormat",
                       EXISTS (SELECT 1 FROM cmi_reportsummery s WHERE s.nid = r."newsId"),
                       r."newsSubject", r.segmentation, r.embedding
                FROM cmi_reports r
                WHERE r."IsActive" = 1 AND r.keyword IS NOT NULL AND r."CustomName" IS NOT NULL
            """)
            reports = cur.fetchall()
            cur.execute("""
                SELECT "catName" FROM cmi_category
                WHERE "IsActive" = '1' AND "catName" NOT LIKE 'Category %%'
                ORDER BY sort
            """)
            cats = cur.fetchall()
    finally:
        conn.close()

    n_emb = sum(1 for r in reports if r[7] is not None)
    print(f"{len(reports)} active report(s), {n_emb} with embeddings, {len(cats)} categories")

    tmp = OUT.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    try:
        with db:
            db.execute("""
                CREATE TABLE reports (
                    news_id     INTEGER PRIMARY KEY,
                    keyword     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    fmt         INTEGER NOT NULL,
                    has_summary INTEGER NOT NULL,
                    subject     TEXT,
                    segmentation TEXT,
                    embedding   BLOB
                )
            """)
            db.execute("CREATE TABLE categories (cat_name TEXT NOT NULL)")
            db.executemany(
                "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (nid, kw, name, fmt, 1 if hs else 0, subj, seg, parse_vector(emb))
                    for nid, kw, name, fmt, hs, subj, seg, emb in reports
                ],
            )
            db.executemany("INSERT INTO categories VALUES (?)", cats)
    finally:
        db.close()
    tmp.replace(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
