import json, sys, time, random, calendar
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs
import requests, feedparser, trafilatura
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/
import backend.sources as S

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
now = time.time()

def norm(h):
    h = h.lower().strip()
    return h[4:] if h.startswith("www.") else h

# hosts -> groups
hosts = {}
for gid, pack in S.PACKS.items():
    for h in list(pack.get("trade", [])) + list(pack.get("regulators", [])):
        hosts.setdefault(norm(h), {"groups": set(), "rss": None})["groups"].add(gid)
# feed hosts from RSS probe
for r in json.load(open("usable.json")):
    if r["cat"] == "search" or r.get("kind") == "json-api":
        continue
    h = norm(urlparse(r["url"]).netloc)
    ent = hosts.setdefault(h, {"groups": set(), "rss": None})
    ent["rss"] = {"url": r["url"], "items": r.get("items"), "newest_days": r.get("newest_days")}
print("hosts to verify:", len(hosts), flush=True)

def bing_site(host):
    for attempt in range(3):
        try:
            r = requests.get("https://www.bing.com/news/search", params={"q": f"site:{host}", "format": "rss", "setmkt": "en-US", "cc": "US"}, headers=UA, timeout=20)
            if r.status_code == 200:
                out = []
                for e in feedparser.parse(r.content).entries:
                    real = parse_qs(urlparse(e.link).query).get("url", [e.link])[0]
                    if norm(urlparse(real).netloc).endswith(host):
                        tt = e.get("published_parsed")
                        out.append({"title": e.title, "url": real, "days": round((now - calendar.timegm(tt)) / 86400) if tt else None})
                return out
            time.sleep(2 + attempt * 2)
        except Exception:
            time.sleep(2)
    return None

def fetch_words(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return -r.status_code
        return len((trafilatura.extract(r.text) or "").split())
    except Exception:
        return -1

def verify(item):
    host, meta = item
    time.sleep(random.uniform(0, 1.5))
    res = {"host": host, "groups": sorted(meta["groups"]), "rss": meta["rss"]}
    hits = bing_site(host)
    res["bing_hits"] = None if hits is None else len(hits)
    if hits:
        recent = [h for h in hits if h["days"] is not None]
        res["bing_newest_days"] = min((h["days"] for h in recent), default=None)
        words = [fetch_words(h["url"]) for h in hits[:3]]
        res["fetch_words"] = words
        good = sum(1 for w in words if w >= 250)
        res["fulltext"] = good >= 2 or (good >= 1 and len(words) == 1)
        res["sample"] = hits[0]["title"][:90]
    return res

with ThreadPoolExecutor(6) as ex:
    out = list(ex.map(verify, hosts.items()))
json.dump(out, open("verified.json", "w"), indent=1)

def mode(r):
    rss = bool(r["rss"])
    bing = bool(r.get("bing_hits"))
    full = r.get("fulltext", False)
    if rss and (full or not bing): return "rss"
    if full: return "bing_full"
    if rss: return "rss"
    if bing: return "bing_snippet"
    return "dead"
from collections import Counter
c = Counter(mode(r) for r in out)
print("DONE", dict(c), "total", len(out), flush=True)
