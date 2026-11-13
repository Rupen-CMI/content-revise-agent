"""Embed reports in backend/catalog.sqlite that have embedding IS NULL.

Companion to sync_new_reports.py: it inserts new rows with embedding=NULL, this
fills those in. Encoding must match the original export exactly — each report's
`keyword` text, plain model.encode(list_of_keywords) (no prefix, no
normalization flag), SentenceTransformer("BAAI/bge-large-en-v1.5") fp32, stored
as a little-endian float32 BLOB (1024 dims → 4096 bytes), which the app reads
with numpy.frombuffer(blob, dtype='<f4').

Only rows whose embedding is NULL are updated; existing embeddings are never
recomputed. Commits after each batch, so a partially finished run simply
resumes where it left off (idempotent).

Run with the venv python (needs torch / sentence-transformers)::

    backend\\venv\\Scripts\\python.exe backend/scripts/catalog_sync/embed_missing_reports.py
"""

import sqlite3
import sys
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer

DB_PATH = Path(r"C:\Users\rupen\Documents\content-revise-agent\backend\catalog.sqlite")
MODEL_NAME = "BAAI/bge-large-en-v1.5"
BATCH_SIZE = 64
EMBED_DIM = 1024
EMBED_BYTES = EMBED_DIM * 4  # float32


def log(msg: str) -> None:
    print(msg, flush=True)


def load_model() -> SentenceTransformer:
    # Model is already in the local HF cache; avoid hitting the network if possible.
    try:
        model = SentenceTransformer(MODEL_NAME, local_files_only=True)
        log(f"loaded {MODEL_NAME} from local cache")
    except Exception as e:  # noqa: BLE001 - fall back to a normal (online) load
        log(f"local cache load failed ({e}); trying normal load ...")
        model = SentenceTransformer(MODEL_NAME)
        log(f"loaded {MODEL_NAME} (network)")
    return model


def main() -> int:
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute(
            "SELECT news_id, keyword FROM reports WHERE embedding IS NULL ORDER BY news_id"
        ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        log(f"{len(rows)}/{total} report(s) with embedding IS NULL")

        if not rows:
            log("nothing to embed — all rows already have an embedding")
            return 0

        model = load_model()

        done = 0
        t0 = time.time()
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            texts = [kw for _, kw in batch]
            vecs = model.encode(texts, show_progress_bar=False)
            blobs = [v.astype("<f4").tobytes() for v in vecs]
            with db:
                db.executemany(
                    "UPDATE reports SET embedding = ? WHERE news_id = ? AND embedding IS NULL",
                    [(blob, nid) for blob, (nid, _) in zip(blobs, batch)],
                )
            done += len(batch)
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0.0
            remaining = (len(rows) - done) / rate if rate > 0 else 0.0
            log(f"  {done}/{len(rows)}  ({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining)")

        n_with = db.execute("SELECT COUNT(*) FROM reports WHERE embedding IS NOT NULL").fetchone()[0]
        n_without = db.execute("SELECT COUNT(*) FROM reports WHERE embedding IS NULL").fetchone()[0]
        bad = db.execute(
            "SELECT news_id, LENGTH(embedding) FROM reports "
            "WHERE embedding IS NOT NULL AND LENGTH(embedding) != ?",
            (EMBED_BYTES,),
        ).fetchall()
        log(f"done in {time.time() - t0:.0f}s")
        log(f"rows with embedding   : {n_with}")
        log(f"rows without embedding: {n_without}")
        if bad:
            log(f"ERROR: {len(bad)} embedding(s) with wrong size, e.g. {bad[:5]}")
            return 2
        log(f"all embedding blobs are exactly {EMBED_BYTES} bytes")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
