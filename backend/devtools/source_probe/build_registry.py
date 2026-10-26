"""Builds backend/source_registry.json from the probe outputs.

Pipeline (run from a scratch directory, since the scripts read/write JSON in the cwd):
    python probe.py          # candidates.py -> results.json   (which feed URLs answer, how fresh)
    python retry.py          # results.json  -> results2.json  (retries 403s; informational only)
    python build_registry.py # needs usable.json = results2 entries that are ok and fresh (see below)
    python verify.py         # PACKS + feed hosts -> verified.json (Bing site: + article-page fetch test)
    python build_registry.py <scratch dir> # writes ../../source_registry.json

usable.json: the entries of results2.json with ok == true and (kind == "json-api" or newest_days <= 45).
verified2.json: verify.py's verified.json with a "mode" key added (see mode() in verify.py).
Feeds that only answered after retry.py swapped in another client's User-Agent ("ua" key) are left out —
the runtime identifies itself with one browser-style User-Agent and never impersonates a feed reader.
"""
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
out_path = Path(__file__).resolve().parents[2] / "source_registry.json"

usable = json.loads((work / "usable.json").read_text())
verified = {v["host"]: v for v in json.loads((work / "verified2.json").read_text())}

# Probe category -> industry groups (sources.PACKS ids) for feeds whose host is not already in a pack.
CAT_GROUPS = {
    "tech": ["enterprise_software_cloud", "ai_data_robotics", "cybersecurity_identity"],
    "fintech": ["fintech_wealth", "banking_payments", "insurance"],
    "health": ["healthcare_digital_health", "pharma_biotech", "medtech_diagnostics"],
    "energy": ["energy_power_renewables", "oil_gas", "metals_mining"],
    "industrial": ["industrial_manufacturing", "electronics_semiconductors", "aerospace_defense_space"],
    "mobility": ["automotive_mobility", "transport_logistics"],
    "consumer": ["retail_ecommerce_consumer", "consumer_beauty_fashion", "food_beverage_agri", "marketing_media_adtech",
                 "travel_hospitality_gaming"],
    "realestate": ["construction_real_estate"],
    "education": ["education_workforce"],
    "public": ["public_sector_govtech"],
    "environment": ["environment_water_waste"],
    "telecom": ["telecom_infrastructure"],
}
HOST_GROUPS = {
    "ciodive.com": ["enterprise_software_cloud"], "cybersecuritydive.com": ["cybersecurity_identity"],
    "esgdive.com": ["environment_water_waste"], "smartcitiesdive.com": ["public_sector_govtech"],
    "trellis.net": ["environment_water_waste"],
}
# Feeds that answer fine but are consumer reviews / aggregators / one-item feeds — not market evidence.
SKIP_NAMES = {"Car and Driver", "Simple Flying", "Hacker News Frontpage", "Quartz", "Dezeen", "Autoblog",
              "Reuters Business (via Google)", "arXiv q-fin", "arXiv cs.AI", "Eurostat News"}
GENERAL_CATS = {"business", "prwire", "research"}
MAX_ITEMS_PER_FEED = 60


def norm(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


feeds, seen_urls, feed_hosts = [], set(), set()
for r in usable:
    if r["cat"] == "search" or r.get("kind") == "json-api" or r.get("ua") or r["name"] in SKIP_NAMES:
        continue
    if r["url"] in seen_urls or "news.google.com" in r["url"]:
        continue
    seen_urls.add(r["url"])
    host = norm(urlparse(r["url"]).netloc)
    feed_hosts.add(host)
    groups = (verified.get(host) or {}).get("groups") or HOST_GROUPS.get(host) or CAT_GROUPS.get(r["cat"], [])
    feeds.append({
        "name": r["name"], "url": r["url"], "host": host,
        "groups": sorted(groups), "general": r["cat"] in GENERAL_CATS,
    })

bing_hosts, blocked = {}, []
for host, v in verified.items():
    words = v.get("fetch_words") or []
    if words and all(w < 0 for w in words):
        blocked.append(host)  # every test fetch was an HTTP error: don't spend article fetches on it
    if host in feed_hosts or not v.get("bing_hits"):
        continue
    bing_hosts[host] = {"groups": sorted(v["groups"]), "fulltext": bool(v.get("fulltext"))}

registry = {
    "generated": date.today().isoformat(),
    "max_items_per_feed": MAX_ITEMS_PER_FEED,
    "feeds": sorted(feeds, key=lambda f: (f["host"], f["url"])),
    "bing_hosts": dict(sorted(bing_hosts.items())),
    "fulltext_blocked": sorted(blocked),
}
out_path.write_text(json.dumps(registry, indent=1), encoding="utf-8")
print(f"{out_path}: {len(feeds)} feeds, {len(bing_hosts)} bing-only hosts, {len(blocked)} fulltext-blocked hosts")
