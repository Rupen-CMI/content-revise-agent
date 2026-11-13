"""Offline test for the client's name in the key players (no network, no real LLM calls).

Run from backend/: python devtools/test_client_players.py

The client is NEVER named in the post — its backlink is its only mention — yet a live run's CMI report listed the
client (webfx.com) among the market's key players, the key-players section named it, and the "Client not named"
check failed. Covers the scrub at the point the key-player lists are built: clients.scrub_players / naming_hosts
(matching the client's name exactly as the "Client not named" check matches it) and planning.build_inputs, which
scrubs the fact sheet's player list and leaves out key-players evidence whose fact names a client, so neither the
planner, the writer nor the FAQ ever sees the client's name.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clients  # noqa: E402
import facts  # noqa: E402
import faqs  # noqa: E402
import planning  # noqa: E402
from docx_io import Paragraph  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_client_players")
LOG.setLevel(logging.INFO)    # the scrub logs its drops at INFO: capture them
MARKET = "Search Engine Optimization (SEO) Services Market"
CAPTURED: list[str] = []      # the scrub log's lines, captured below


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        CAPTURED.append(record.getMessage())


_capture = _Capture()


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def make_client(host: str = "webfx.com", brief_name: str = "WebFX") -> clients.Client:
    return clients.Client(id="C1", host=host,
                          paragraph=Paragraph(index=0, text="Search visibility services for growing brands."),
                          links=[{"id": "L1", "anchor": "search visibility services", "url": f"https://{host}/"}],
                          brief={"name": brief_name})


# ------------------------------------------------------------------ matching the client's name
c = make_client()
check("match: the player that IS the client matches (WebFX, of webfx.com)",
      clients.naming_hosts("WebFX", [c]) == ["webfx.com"])
check("match: the brief's name for the client matches too",
      clients.naming_hosts("WebFX Digital Marketing", [c]) == ["webfx.com"])
check("match: the domain matches", clients.naming_hosts("A WebFX.com study found change", [c]) == ["webfx.com"])
check("match: a name that only ends the same way is not the client (NetFX is not webfx)",
      clients.naming_hosts("NetFX", [c]) == [])
check("match: a client with no brief still matches on its domain's first label",
      clients.naming_hosts("WebFX", [make_client(brief_name="")]) == ["webfx.com"])
check("match: no client names nothing", clients.naming_hosts("Semrush", [c]) == [])

# ------------------------------------------------------------------ scrubbing the key-player list
LOG.addHandler(_capture)
scrubbed = clients.scrub_players(["WebFX", "Semrush", "Ahrefs", "WebFX Digital Marketing"], [c], LOG)
LOG.removeHandler(_capture)
check("players: the client, and any player carrying its name, are removed", scrubbed == ["Semrush", "Ahrefs"], str(scrubbed))
check("players: the drop is logged with the player and the client",
      any("key player 'WebFX' dropped" in m and "webfx.com" in m for m in CAPTURED), str(CAPTURED))
check("players: a list with no client keeps every player unchanged",
      clients.scrub_players(["Semrush", "Ahrefs"], [c], LOG) == ["Semrush", "Ahrefs"])

# ------------------------------------------------------------------ the plan's inputs (the writer never sees it)
fs = facts.validate_fact_sheet({}, "page", MARKET, LOG)
fs["players"] = ["WebFX", "Semrush", "Ahrefs"]
EVIDENCE = [
    {"id": "W1", "fact": "WebFX launched an AI visibility suite that measures brand mentions in AI answers.",
     "detail": "", "source": "MSN, 2026-10", "tags": ["players"], "group": "news"},
    {"id": "W2", "fact": "Semrush reached a deal to be acquired at twelve dollars a share.",
     "detail": "", "source": "Yahoo Finance, 2026-08", "tags": ["players"], "group": "news"},
    {"id": "W3", "fact": "Ahrefs expanded its crawler index across North America in September.",
     "detail": "", "source": "VentureBeat, 2026-09", "tags": ["trends"], "group": "news"},
    {"id": "W4", "fact": "WebFX published six reasons businesses should invest in SEO.",
     "detail": "", "source": "WebFX, 2026-10", "tags": ["subsegment:On-Page SEO"], "group": "news"},
]
inp = planning.build_inputs(MARKET, fs, EVIDENCE, {"questions": [], "terms": [], "long_tail": []}, {},
                            45, clients=[c], logger=LOG, sectioned=True)
check("inputs: the fact sheet's player list no longer names the client", fs["players"] == ["Semrush", "Ahrefs"], str(fs["players"]))
check("inputs: the fs.players fact the plan and the FAQ read carries no client",
      "WebFX" not in inp.items["fs.players"]["text"] and "Semrush" in inp.items["fs.players"]["text"],
      inp.items["fs.players"]["text"])
check("inputs: key-players evidence naming the client is left out of the plan", "W1" not in inp.items, str(list(inp.items)))
check("inputs: the other key-players evidence survives (W2)",
      "W2" in inp.items and inp.items["W2"]["sections"] == {"key_players"})
check("inputs: evidence of another section is never touched (W3)", "W3" in inp.items)
check("inputs: evidence of any other section naming the client is left out too (W4, found live)", "W4" not in inp.items, str(list(inp.items)))
check("inputs: the fs.players fact sits in the key-players section",
      inp.items["fs.players"]["sections"] == {"key_players"})

# the FAQ reads its key-player answers from the same items
srcs = faqs.sources_for_question({"id": "Q1", "intent": "players", "question": "Who leads the market?"}, inp)
check("faq: a key-player answer's facts no longer name the client",
      srcs and all("WebFX" not in s["text"] for s in srcs), str([s["text"] for s in srcs]))

# ------------------------------------------------------------------ without clients nothing changes
fs2 = facts.validate_fact_sheet({}, "page", MARKET, LOG)
fs2["players"] = ["WebFX", "Semrush"]
inp2 = planning.build_inputs(MARKET, fs2, EVIDENCE, {"questions": [], "terms": [], "long_tail": []}, {},
                             45, logger=LOG, sectioned=True)
check("inputs: with no client, the player list and every evidence item stay as they are",
      fs2["players"] == ["WebFX", "Semrush"] and "W1" in inp2.items, str(fs2["players"]))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all client-player tests passed")
