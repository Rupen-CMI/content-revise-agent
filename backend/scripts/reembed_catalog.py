"""One-time job: embed every cmi_reports.keyword with bge-large-en-v1.5 (1024 dims),
replacing the old MiniLM (384-dim) embeddings. Run once after the column has been
altered to vector(1024) (see conversation/db migration notes)."""
import sys
import time

import psycopg2
import psycopg2.extras
from sentence_transformers import SentenceTransformer

sys.path.insert(0, r"C:\Users\rupen\Documents\content-revise-agent")
from config import DB_URL  # noqa: E402

MODEL_NAME = "BAAI/bge-large-en-v1.5"
BATCH_SIZE = 200


def main() -> None:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute('SELECT "newsId", keyword FROM cmi_reports WHERE keyword IS NOT NULL')
    rows = cur.fetchall()
    print(f"{len(rows)} reports to embed")

    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    print(f"model loaded in {time.time() - t0:.1f}s")

    done = 0
    t0 = time.time()
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        vecs = model.encode(texts, show_progress_bar=False)

        psycopg2.extras.execute_batch(
            cur,
            "UPDATE cmi_reports SET embedding = %s WHERE \"newsId\" = %s",
            [(vec.tolist(), news_id) for vec, news_id in zip(vecs, ids)],
        )
        conn.commit()
        done += len(batch)
        elapsed = time.time() - t0
        rate = done / elapsed
        remaining = (len(rows) - done) / rate if rate > 0 else 0
        print(f"  {done}/{len(rows)}  ({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining)")

    cur.execute("SELECT COUNT(*) FROM cmi_reports WHERE embedding IS NOT NULL")
    print(f"done in {time.time() - t0:.0f}s. non-null embeddings: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
