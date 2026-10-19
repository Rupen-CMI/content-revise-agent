import json, sys, time, calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse
import feedparser, requests
sys.path.insert(0, ".")
from candidates import C

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*"}
now = time.time()

def probe(item):
    cat, name, url = item
    r = {"cat": cat, "name": name, "url": url, "ok": False}
    t0 = time.time()
    try:
        resp = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
        r["status"] = resp.status_code
        r["secs"] = round(time.time() - t0, 1)
        if resp.status_code != 200:
            return r
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype or resp.text.lstrip().startswith(("{", "[")):
            r["kind"] = "json-api"
            r["ok"] = True
            r["items"] = 1
            return r
        d = feedparser.parse(resp.content)
        ents = d.entries
        r["kind"] = "feed"
        r["items"] = len(ents)
        if not ents:
            r["why"] = "no entries (html/blocked?)"
            return r
        ts = []
        for e in ents:
            tt = e.get("published_parsed") or e.get("updated_parsed")
            if tt:
                ts.append(calendar.timegm(tt))
        if ts:
            r["newest_days"] = round((now - max(ts)) / 86400, 1)
            r["oldest_days"] = round((now - min(ts)) / 86400, 1)
        else:
            r["why"] = "no dates"
        r["sample"] = (ents[0].get("title") or "")[:90]
        r["ok"] = True
    except Exception as e:
        r["err"] = type(e).__name__
    return r

# dedupe by URL
seen, items = set(), []
for it in C:
    if it[2] not in seen:
        seen.add(it[2]); items.append(it)
with ThreadPoolExecutor(24) as ex:
    res = list(ex.map(probe, items))
json.dump(res, open("results.json", "w"), indent=1)
ok = [r for r in res if r["ok"]]
print("tested", len(res), "ok", len(ok))
