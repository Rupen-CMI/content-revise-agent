"""A market named with a short word ('Healthcare IT', 'AI in Healthcare', '3D IC') is searched and matched as that market,
never as its longer words alone. Run: python devtools/test_market_name_match.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import external_reports  # noqa: E402
import facts  # noqa: E402
import search  # noqa: E402


def test_names_market():
    m = "Healthcare IT Market"
    assert facts.names_market("Healthcare IT Market Size, Share & Global Trends 2035", m)
    assert facts.names_market("AI drives healthcare IT market toward $3.2t by 2034", m)
    assert facts.names_market("the market for healthcare-IT software", m)
    assert not facts.names_market("Healthcare Market Size, Share 2030", m)
    assert not facts.names_market("Healthcare - Forbes", m)
    assert not facts.names_market("what it means for healthcare providers", m)
    assert facts.names_market("Artificial intelligence (AI) in healthcare market", "AI in Healthcare Market")
    assert not facts.names_market("Healthcare market to grow", "AI in Healthcare Market")
    assert facts.names_market("3D IC market worth $20 billion", "3D IC Market")
    # names without a short word read as before: every long word, in any order
    assert facts.names_market("Wealth management platforms market", "Wealth Management Platform Market")
    assert not facts.names_market("Wealth management market", "Wealth Management Platform Market")


def test_queries_quote_the_name():
    news_q, web_q = external_reports.search_queries("Healthcare IT Market")
    assert all('"healthcare it market"' in q for q in news_q + web_q[:3]), (news_q, web_q)
    assert web_q[-1] == "healthcare it market"        # one plain query for pages that word it differently
    assert facts.figure_gap_queries("Healthcare IT Market")[0].startswith('"healthcare it market"')


def test_news_gate_keeps_the_short_word():
    head, required, _, _ = search._gate_for_phrase("Healthcare IT Market", search._catalog_token_freq())
    assert head == "healthcare_it" and required == ["healthcare"], (head, required)
    assert "healthcare_it" in search._content_tokens("Black Book names top healthcare IT vendors")
    assert "healthcare_it" not in search._content_tokens("What it means for healthcare workers")
    assert search._ordered_market_tokens("AI in Healthcare Market") == ["ai_healthcare", "healthcare"]
    assert "ai_healthcare" in search._content_tokens("Hospitals expand AI in healthcare pilots")
    assert search._ordered_market_tokens("Wealth Management Platform Market") == ["wealth", "management", "platform"]


def test_figure_filter_needs_the_whole_name():
    hit = lambda t, host: search.SearchHit(title=t, snippet="", url=f"https://{host}/p", source=host, date=None)
    import logging
    log = logging.getLogger("t")
    hits = [hit("Health care - Wikipedia", "en.wikipedia.org"), hit("Healthcare - NPR", "npr.org"),
            hit("Healthcare IT Market Size, Share 2035", "marketresearchfuture.com"),
            hit("Healthcare IT Market Report 2026-2031", "marketsandmarkets.com"),
            hit("Healthcare IT Market Size, Share | Industry Growth", "alliedmarketresearch.com")]
    kept = facts.filter_figure_hits(hits, "Healthcare IT Market", [], log)
    assert {h.host for h in kept} == {"marketresearchfuture.com", "marketsandmarkets.com", "alliedmarketresearch.com"}, kept


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all market-name match tests passed")
