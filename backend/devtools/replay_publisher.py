"""Runs the real publisher-points call on frozen fixtures and prints what survived validation (real LLM call).

Usage (from backend/): python devtools/replay_publisher.py devtools/fixture_scraping.json [more ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import publisher
from docx_io import Link, Paragraph, SourceArticle
from pipeline_log import new_run_logger


def main() -> None:
    for path in sys.argv[1:]:
        f = json.loads(Path(path).read_text(encoding="utf-8"))
        mk = lambda d: Paragraph(index=d["index"], text=d["text"], links=[Link(**l) for l in d["links"]])  # noqa: E731
        article = SourceArticle(title=f["article"]["title"], paragraphs=[mk(p) for p in f["article"]["paragraphs"]])
        logger, log_path = new_run_logger()
        print(f"\n===== {article.title}  ({f['report']['name']}; log {log_path})")
        res = publisher.run_publisher_points(article, mk(f["sponsor"]), f["report"]["name"], logger)
        print("INTRO ANGLE:", res["intro_angle"])
        for p in res["points"]:
            print(f"  {p['id']}: {p['text']}")


if __name__ == "__main__":
    main()
