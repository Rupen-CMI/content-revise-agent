"""Offline tests for the DEV-ONLY search results disk cache (search.cache_lookup/cache_store
via config.SEARCH_CACHE), using a throwaway cache directory. Run: python devtools\\test_searchcache.py
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import search

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if not ok else ""))
    if not ok:
        FAILURES.append(name)


# Point the cache at a throwaway directory and enable it without touching the environment
# (config reads the env at import; mutating the module here matches what the mechanism reads).
tmp = Path(tempfile.mkdtemp(prefix="cmi_cache_test_"))
real_enabled, real_dir, real_ttl = config.SEARCH_CACHE, config.SEARCH_CACHE_DIR, config.SEARCH_CACHE_TTL_S
config.SEARCH_CACHE, config.SEARCH_CACHE_DIR, config.SEARCH_CACHE_TTL_S = True, tmp, 3600.0
try:
    hits = [
        search.SearchHit("Title A", "snippet a", "https://example.org/a", "Example", datetime(2026, 3, 1, tzinfo=timezone.utc), is_news=True),
        search.SearchHit("Title B", "snippet b", "https://sub.example.org/b", "Example", None, is_news=False),
    ]
    search.cache_store("bing_news", "some market query", hits)
    restored = search.cache_lookup("bing_news", "some market query")
    check("round-trip: count and titles survive disk", restored is not None and [h.title for h in restored] == ["Title A", "Title B"], str(restored))
    check("round-trip: the timezone-aware date round-trips",
          restored[0].date == hits[0].date and restored[0].date.tzinfo is not None,
          str(restored[0].date if restored else None))
    check("round-trip: a dateless hit stays dateless", restored[1].date is None)
    check("round-trip: is_news survives", bool(restored[0].is_news) and not restored[1].is_news)
    check("round-trip: caller-set fields never come from the cache (cache hit arrives blank)",
          restored[0].scope == "market" and restored[0].claim_id == "" and restored[0].excerpt == "",
          str((restored[0].scope, restored[0].claim_id)))

    # A different query must not see this entry (kind is part of the key)
    check("different kind does not match the same query", search.cache_lookup("ddgs_text", "some market query") is None)

    # Cache OFF behaves like a miss even with a stored entry
    config.SEARCH_CACHE = False
    check("disabled cache always looks like a miss", search.cache_lookup("bing_news", "some market query") is None)
    search.cache_store("other", "q", hits)  # with cache off, store writes nothing, raises nothing
    config.SEARCH_CACHE = True
    check("store with cache off is a no-op", search.cache_lookup("other", "q") is None)

    # Expiry: expiry anneals to a miss (stale entries are refetched, not served)
    config.SEARCH_CACHE_TTL_S = -1.0
    check("expired entry is a miss", search.cache_lookup("bing_news", "some market query") is None)

    # Corrupt entry behaves like a miss, not an error
    p = search._cache_path("bing_news", "some market query")
    p.write_text("not json", encoding="utf-8")
    config.SEARCH_CACHE_TTL_S = 3600.0
    check("corrupt entry is a miss (never an error)", search.cache_lookup("bing_news", "some market query") is None)
finally:
    config.SEARCH_CACHE, config.SEARCH_CACHE_DIR, config.SEARCH_CACHE_TTL_S = real_enabled, real_dir, real_ttl

if FAILURES:
    print(f"\n{len(FAILURES)} search-cache test(s) FAILED")
    sys.exit(1)
print("\nall search-cache tests passed")
