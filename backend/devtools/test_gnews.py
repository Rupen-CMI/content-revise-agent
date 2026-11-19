"""Offline test for the Google News client in feeds.py (RSS parsing, the " - Publisher" title strip, link decoding) and the
DDGS fail-fast state in search.py. No network: the decoder's HTTP client is a stub.

Run from backend/: python devtools/test_gnews.py
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feeds  # noqa: E402
import search  # noqa: E402

logging.basicConfig(level=logging.ERROR)
LOG = logging.getLogger("test_gnews")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>q</title>
<item><title>Electric Vehicle Charger Market Size, Share &amp; Forecast 2032 - Fortune Business Insights</title>
<link>https://news.google.com/rss/articles/CBMiAAA111?oc=5</link><guid>1</guid><pubDate>Tue, 03 Nov 2026 08:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;Electric Vehicle Charger Market Size, Share &amp;amp; Forecast 2032&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;Fortune Business Insights&lt;/font&gt;</description>
<source url="https://www.fortunebusinessinsights.com">Fortune Business Insights</source></item>
<item><title>No source element here</title><link>https://news.google.com/rss/articles/CBMiBBB222</link><pubDate>Tue, 03 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Chargers - roll-out - Reuters</title><link>https://news.google.com/rss/articles/CBMiCCC333</link>
<description>Utilities add 5,000 chargers across the U.S. this quarter.</description><source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""

entries = feeds._parse_feed(RSS.encode()).entries
hits = [h for e in entries if (h := feeds._gnews_hit(e))]
check("rss: items without a <source> are skipped", len(hits) == 2, str(len(hits)))
h0, h1 = hits
check("rss: publisher suffix stripped", h0.title == "Electric Vehicle Charger Market Size, Share & Forecast 2032", h0.title)
check("rss: only the matching publisher suffix is stripped", h1.title == "Chargers - roll-out", h1.title)
check("rss: host comes from <source url>", h0.host == "fortunebusinessinsights.com" and h1.host == "reuters.com", f"{h0.host} {h1.host}")
check("rss: provisional url carries the article id", feeds._gnews_id(h0.url) == "CBMiAAA111", h0.url)
check("rss: date parsed from pubDate", h0.date is not None and h0.date.year == 2026 and h0.is_news)
check("rss: a snippet that only repeats the headline is blanked", h0.snippet == "", repr(h0.snippet))
check("rss: a real description is kept", "5,000 chargers" in h1.snippet)
check("rss: source name kept", h0.source == "Fortune Business Insights")

# --- link decoding with a canned batchexecute response
REAL = "https://www.fortunebusinessinsights.com/electric-vehicle-charger-market-110000"
inner = json.dumps(["garturlres", REAL, 1])
BATCH = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]) + "\n"
check("decode: batchexecute answer parsed", feeds._parse_batchexecute(BATCH) == REAL, feeds._parse_batchexecute(BATCH))
check("decode: garbage gives ''", feeds._parse_batchexecute("nope") == "" and feeds._parse_batchexecute("a\n\n[]") == "")


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class StubClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return Resp('<c-wiz data-n-a-sg="SIG123" data-n-a-ts="1700000000">')

    def post(self, url, data=None, headers=None, **kw):
        self.calls.append(("POST", url, data))
        return Resp(BATCH)


feeds._GNEWS_DECODE_GATE = feeds._HostGate(0.0)
c = StubClient()
check("decode: real url returned", feeds._decode_gnews(c, "CBMiAAA111") == REAL)
req = json.loads(c.calls[1][2]["f.req"])
check("decode: request carries id, timestamp and signature", '"CBMiAAA111",1700000000,"SIG123"' in req[0][0][1], req[0][0][1][-60:])
n = len(c.calls)
check("decode: a decoded id is cached (no new requests)", feeds._decode_gnews(c, "CBMiAAA111") == REAL and len(c.calls) == n)

# resolve_hits: provisional urls replaced, undecodable dropped, non-Google hits pass through
orig = feeds._decode_gnews
feeds._decode_gnews = lambda client, gid: REAL if gid == "CBMiAAA111" else ""
plain = search.SearchHit("Plain", "", "https://example.com/a", "", None)
out = feeds.resolve_hits([h0, h1, plain], LOG)
feeds._decode_gnews = orig
check("resolve: decoded kept, undecodable dropped, others pass through", [h.url for h in out] == [REAL, "https://example.com/a"], str([h.url for h in out]))

# --- the one-year window: every Google News query carries Google's `when:1y` operator ---
# RSS has no date limit, but search._MAX_NEWS_AGE is 365 days, so older entries used to be fetched and then thrown
# away by filter_hits. A planned query that sets its own range keeps it.
check("gnews: query gains when:1y",
      feeds._gnews_query("assessment services market") == "assessment services market when:1y",
      feeds._gnews_query("assessment services market"))
check("gnews: a query with its own range is left alone",
      feeds._gnews_query("widget market when:6m") == "widget market when:6m",
      feeds._gnews_query("widget market when:6m"))
check("gnews: empty query untouched", feeds._gnews_query("  ") == "", repr(feeds._gnews_query("  ")))


class RSSResponse:
    status_code = 200
    content = RSS.encode()


class RSSClient:
    def __init__(self):
        self.params = []

    def get(self, url, params=None, **kw):
        self.params.append((url, dict(params or {})))
        return RSSResponse()


feeds._gnews_last_end = 0.0
rss_client = RSSClient()
_orig_lookup, _orig_store = feeds.search.cache_lookup, feeds.search.cache_store
feeds.search.cache_lookup, feeds.search.cache_store = (lambda *a: None), (lambda *a: None)
try:
    sent_hits = feeds._GoogleNews(rss_client, LOG)._get("assessment services market")
finally:
    feeds.search.cache_lookup, feeds.search.cache_store = _orig_lookup, _orig_store
req_url, req_params = rss_client.params[0]
check("gnews: the RSS request itself carries when:1y with the locale params",
      req_params.get("q") == "assessment services market when:1y"
      and req_params.get("hl") == "en-US" and req_params.get("gl") == "US" and req_params.get("ceid") == "US:en",
      str(req_params))
check("gnews: the windowed request still parses its hits",
      sent_hits is not None and len(sent_hits) == 2, str(sent_hits))

# --- DDGS fail-fast
search._ddgs_disabled_until = 0.0
check("ddgs: available at first", search.ddgs_available())
search.ddgs_disable(LOG, "test")
check("ddgs: unavailable after a failure", not search.ddgs_available())
check("ddgs: disabled for ~30 minutes", search._ddgs_disabled_until - time.monotonic() > 29 * 60)
check("ddgs: run_text_searches makes no call while disabled", search.run_text_searches(["x"], LOG) == [])
check("ddgs: run_searches makes no call while disabled", search.run_searches(["x"], LOG) == [])


class FakeDDGSError(Exception):
    pass


search._ddgs_disabled_until = 0.0
orig_exc = search.DDGSException
search.DDGSException = FakeDDGSError


def boom(d, q):
    raise FakeDDGSError("No results found.")


check("ddgs: 'No results found' returns [] and switches DDGS off",
      search._attempt(boom, None, "q", "text", LOG) == [] and not search.ddgs_available())
search.DDGSException = orig_exc
search._ddgs_disabled_until = 0.0
check("ddgs: timeout cut to seconds, no retries", search._QUERY_TIMEOUT_S <= 8 and search._QUERY_RETRIES == 0)

print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
