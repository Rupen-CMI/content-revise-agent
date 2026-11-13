"""Scrape the full list of reports from coherentmarketinsights.com/latest-insights.

Strategy:
  1. Open the latest-insights page in a Playwright chromium context (desktop Chrome UA).
  2. Listen to network responses to capture/log the api/reports fetch the page itself makes.
  3. Then page through ALL pages of https://api.coherentmarketinsights.com/api/reports
     at pageSize=100 using context.request.get (shares the browser session), until
     meta.hasMore is false.
  4. Write scraped_reports.json with one normalized object per report.

This script does NOT modify any database. It only writes scraped_reports.json.
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.coherentmarketinsights.com"
INSIGHTS_URL = f"{BASE}/latest-insights"
API_URL = "https://api.coherentmarketinsights.com/api/reports"
PAGE_SIZE = 100
MAX_PAGES_RETRIES = 3
BACKOFF_BASE = 2.0  # seconds; retry backoff multiplier base

OUT_PATH = Path(
    r"C:\Users\rupen\Documents\content-revise-agent\backend\scripts\catalog_sync\scraped_reports.json"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize(item: dict) -> dict:
    cat = item.get("category") or {}
    cat_name = cat.get("catName") if isinstance(cat, dict) else None
    slug = item.get("customName") or ""
    return {
        "news_id": int(item["newsId"]),
        "keyword": item.get("keyword") or "",
        "slug": slug,
        "subject": item.get("newsSubject") or "",
        "news_date": item.get("newsDate") or "",
        "is_active": int(item.get("isActive") or 0),
        "report_status": int(item.get("reportstatus") or 0),
        "category": cat_name,
        "url": f"{BASE}/industry-reports/{slug}",
    }


def fetch_page(context, page_no: int) -> dict:
    """Fetch one page of the API with retries; returns parsed JSON."""
    url = f"{API_URL}?page={page_no}&pageSize={PAGE_SIZE}"
    last_err = None
    for attempt in range(1, MAX_PAGES_RETRIES + 1):
        try:
            resp = context.request.get(url, timeout=60000)
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            data = resp.json()
            if "data" not in data or "meta" not in data:
                raise RuntimeError(f"unexpected response shape for {url}: keys={list(data.keys())}")
            return data
        except Exception as e:  # noqa: BLE001 - fail loudly after retries
            last_err = e
            wait = BACKOFF_BASE ** attempt
            log(f"    attempt {attempt}/{MAX_PAGES_RETRIES} failed for page {page_no}: {e}; sleeping {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"could not fetch page {page_no} after {MAX_PAGES_RETRIES} attempts: {last_err}")


def main() -> int:
    all_rows = []
    total = None
    captured_request_seen = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        # Log the API request the page itself makes (network inspection).
        def on_response(response):
            nonlocal captured_request_seen
            if "api.coherentmarketinsights.com/api/reports" in response.url:
                captured_request_seen = True
                try:
                    js = response.json()
                    m = js.get("meta", {})
                    log(
                        "[network] page fetched: "
                        f"{response.url} -> status={response.status} "
                        f"rows={len(js.get('data', []))} meta={{page: {m.get('page')}, pageSize: {m.get('pageSize')}, total: {m.get('total')}, totalPages: {m.get('totalPages')}, hasMore: {m.get('hasMore')}}}"
                    )
                except Exception as e:  # noqa: BLE001
                    log(f"[network] {response.url} -> status={response.status} (body not JSON: {e})")

        page.on("response", on_response)

        log(f"Opening {INSIGHTS_URL} ...")
        page.goto(INSIGHTS_URL, wait_until="domcontentloaded", timeout=120000)
        # Give the page time to fire its own API fetch.
        page.wait_for_timeout(5000)
        if not captured_request_seen:
            log("WARNING: page's own api/reports fetch was not observed; proceeding with direct paging anyway.")

        # Discover total from page 1.
        first = fetch_page(context, 1)
        meta = first["meta"]
        total = meta["total"]
        total_pages = meta["totalPages"]
        log(f"meta: page={meta['page']} pageSize={meta['pageSize']} total={total} totalPages={total_pages} hasMore={meta['hasMore']}")

        rows = first["data"]
        all_rows.extend(rows)
        log(f"collected {len(all_rows)}/{total} rows")

        page_no = 2
        while page_no <= total_pages:
            data = fetch_page(context, page_no)
            m = data["meta"]
            all_rows.extend(data["data"])
            log(f"collected {len(all_rows)}/{total} rows (page {page_no}/{total_pages}, hasMore={m['hasMore']})")
            if not m["hasMore"] and page_no < total_pages:
                log(f"meta.hasMore is False at page {page_no} but totalPages={total_pages}; stopping early.")
                break
            page_no += 1
            time.sleep(0.5)  # be polite

        browser.close()

    # ---- Validation (fail loudly) ----
    ids = [r["newsId"] for r in all_rows]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        log(f"ERROR: duplicate newsIds found ({len(dupes)} distinct): {sorted(dupes)[:10]} ...")
        return 2
    if len(all_rows) != total:
        log(f"ERROR: collected {len(all_rows)} rows but meta.total is {total}")
        return 3

    records = [normalize(r) for r in all_rows]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    log(f"wrote {len(records)} records -> {OUT_PATH}")

    # ---- Summary report ----
    news_ids = [r["news_id"] for r in records]
    active = sum(1 for r in records if r["is_active"] == 1)
    status1 = sum(1 for r in records if r["report_status"] == 1)
    empty_kw = sum(1 for r in records if not r["keyword"].strip())
    empty_slug = sum(1 for r in records if not r["slug"].strip())
    log("--- SUMMARY ---")
    log(f"row count        : {len(records)}")
    log(f"meta.total       : {total}")
    log(f"min news_id      : {min(news_ids)}")
    log(f"max news_id      : {max(news_ids)}")
    log(f"is_active == 1   : {active}")
    log(f"report_status==1 : {status1}")
    log(f"empty keyword    : {empty_kw}")
    log(f"empty slug       : {empty_slug}")
    log("--- 3 sample rows ---")
    for r in records[:3]:
        log(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
