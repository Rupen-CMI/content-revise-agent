"""Hypothesis test: do human-style queries (how an analyst or journalist types to find a figure or a recent development) find more
usable evidence on Google News than the current anchored style? Two markets, 6 queries each per style, the same relevance gate
(search.filter_hits with the log's market name, key players and topic terms), counted on topic / with a figure / with a named actor
and an action verb in the headline. Writes output/query_style_experiment.md. Run from backend/: python devtools/query_style_experiment.py
(network: Google News RSS and the LLM)."""
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import feeds  # noqa: E402
import llm  # noqa: E402
import search  # noqa: E402

LOG = logging.getLogger("qse")

HUMAN_RULES = """Write each query the way an analyst or a journalist would type it into a news search to find ONE of two things: \
(a) a figure, the number a trade report or a company would state (what sold, how many were installed, what share of buyers did it), or \
(b) a recent development, a named company or body doing something (launching, acquiring, opening, signing, investing, expanding).
- Name the product the way people in the trade say it, then the thing you want: "<product> sales rose", "<product> shipments \
record", "<company type> opens new <product> plant", "<product> installations 2026 data".
- A figure query contains the product and a figure word (sales, shipments, installations, share of, percent, rose, fell, record).
- A development query contains the product and an action word (launches, acquires, opens, signs, invests, expands, wins contract).
- 3 to 8 words, no quotes, no OR, no research-firm or report words (market size, forecast, CAGR, report), no advice words (how to, best, guide)."""

MARKETS = {
    "Skin Care Products Market": {
        "terms": ["skin care", "face cream", "moisturizer", "sunscreen", "cleanser", "anti-aging", "K-beauty", "personalized skin care",
                  "clean beauty", "skin care products", "beauty products", "cosmetics", "skin care brand", "skin care launch",
                  "skin care acquisition", "skin care partnership"],
        "players": ["Unilever", "Kao", "The Body Shop International", "Shiseido", "The Estee Lauder Companies", "Colgate-Palmolive",
                    "Johnson & Johnson Services", "Procter & Gamble", "Avon Products", "Beiersdorf", "Revlon", "Amorepacific", "Mary Kay",
                    "Clarins", "Coty"],
        "current": ["skin care products consumer awareness survey", "skin care brands launch new products", "Korean beauty products sales data",
                    "personalized skin care products launch", "women skin care products spending survey",
                    "anti-aging skin care products sales rise"],
    },
    "Electric Vehicle Charger Market": {
        "terms": ["ev charger", "ev charging", "charging station", "fast charger", "dc fast charging", "level 2 charger", "home charger",
                  "on-board charger", "charging network", "charging infrastructure", "ev charging equipment", "charger installation",
                  "charging point", "ev supply equipment"],
        "players": ["ABB", "Robert Bosch", "Siemens", "Delphi Automotive", "Chroma ATE", "Aerovironment", "Silicon Laboratories",
                    "Chargemaster", "Schaffner", "POD Point"],
        "current": ["fast charging network expansion data", "on-board charger production figures", "new fast charging network launch",
                    "on-board charger launch", "home EV charger installation data", "residential EV charger adoption rate"],
    },
}


def human_queries(market: str) -> list[str]:
    out = llm.chat_json(
        "You write news-search queries. Return JSON only.",
        f"MARKET: {market}\n\n{HUMAN_RULES}\n\nWrite exactly 6 queries for this market, a mix of figure queries and development queries.\n"
        'Return JSON: {"queries": ["<query>", ...]}', temperature=0.3, call_name="human-queries")
    return [" ".join(str(q).split()) for q in out["queries"]][:6]


def measure(market: str, queries: list[str], client: httpx.Client) -> dict:
    gnews = feeds._GoogleNews(client, LOG)
    hits = []
    for q in queries:
        hits += gnews.search(q)
    raw = len(hits)
    kept = search.filter_hits(hits, [market], limit=max(raw, 1), key_players=MARKETS[market]["players"], topic_terms=MARKETS[market]["terms"])
    fig = sum(1 for h in kept if feeds._figure_signal(h.title) == 2)
    act = sum(1 for h in kept if feeds._instance_signal(h.title) == 2)
    return {"raw": raw, "kept": len(kept), "figure": fig, "actor_verb": act, "either": sum(
        1 for h in kept if feeds._figure_signal(h.title) == 2 or feeds._instance_signal(h.title) == 2)}


def main() -> None:
    rows, listing = [], []
    with httpx.Client(headers=feeds._HEADERS, timeout=30, follow_redirects=True) as client:
        for market in MARKETS:
            hq = human_queries(market)
            listing.append(f"**{market}**\n- current: {json.dumps(MARKETS[market]['current'])}\n- human: {json.dumps(hq)}")
            for style, qs in (("current", MARKETS[market]["current"]), ("human", hq)):
                rows.append((market, style, measure(market, qs, client)))
    lines = ["| market | style | raw hits | on-topic kept | kept with a figure | kept with actor+verb | either |", "|---|---|---|---|---|---|---|"]
    tot = {"current": [0] * 5, "human": [0] * 5}
    for market, style, m in rows:
        v = [m["raw"], m["kept"], m["figure"], m["actor_verb"], m["either"]]
        tot[style] = [a + b for a, b in zip(tot[style], v)]
        lines.append(f"| {market} | {style} | " + " | ".join(map(str, v)) + " |")
    for style, v in tot.items():
        lines.append(f"| both | {style} | " + " | ".join(map(str, v)) + " |")
    win = tot["human"][4] > tot["current"][4] and tot["human"][1] >= 0.95 * tot["current"][1]
    text = ("# Query style experiment\n\n" + "\n\n".join(listing) + "\n\n" + "\n".join(lines)
            + f"\n\nDecision rule: human-style wins when it keeps more evidence-signalling hits (either) while keeping at least 95% of the on-topic hits. Winner: {'human' if win else 'current'}.\n")
    out = Path(__file__).resolve().parent.parent / "output" / "query_style_experiment.md"
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
