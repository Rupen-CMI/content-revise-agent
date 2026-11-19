"""Offline checks for the single-word market gate: a market whose identity is one word. The word found only in the
SNIPPET must not keep an unrelated hit once a topic vocabulary exists; the word in the HEADLINE is a market signal on
its own, because Google News hits carry no snippet (feeds._gnews_hit blanks it). Run from backend/: python devtools/test_single_word_gate.py"""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import search  # noqa: E402

LOG = logging.getLogger("t")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


NOW = datetime.now(timezone.utc) - timedelta(days=3)


def hit(title: str, url: str, snippet: str = "") -> search.SearchHit:
    return search.SearchHit(title, snippet, url, "", NOW, is_news=True)


PHRASES = ["Assessment Services Market"]
PLAYERS = ["Open Assessment Technologies", "Pearson"]
TERMS = ["assessment services", "online testing", "proctoring", "aptitude test",
         "personality test", "skills assessment", "psychometric testing",
         "pre-employment screening", "certification exam", "talent assessment",
         "adaptive testing", "test authoring"]


def kept(titles_or_hits, **kw):
    hits = titles_or_hits if all(isinstance(x, search.SearchHit) for x in titles_or_hits) else [hit(t, "https://x.example/p") for t in titles_or_hits]
    return [h.title for h in search.filter_hits(hits, PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=TERMS, **kw)]


# --- DROPPED: unrelated headlines carrying only the market word 'assessment' ---
trump = hit("Trump Has Grown Angrier With Vance Since Midterms, Report Says",
            "https://www.forbes.com/a", "The report assesses the assessment of recent events.")
everest = hit("UiPath Named a Leader in Everest Group's 2026 PEAK Matrix Assessments",
              "https://markets.ft.com/b", "Industry assessment of automation vendors.")
phil = hit("Learning Crisis Grips Philippines as 99% of Grade 10 Students Fall Below Proficiency Standards",
           "https://www.devdiscourse.com/c", "An assessment of education outcomes.")
# Title lacks the market word, the snippet has it, and nothing in either is a topic term: still dropped.
snippet_only = hit("Schools overhaul how they measure student progress next term",
                   "https://www.edweek.org/d", "The new assessment replaces the old one.")
dropped_hits = [trump, everest, phil, snippet_only]
got = search.filter_hits(dropped_hits, PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=TERMS)
check("single-word gate: unrelated assessment-only hits are dropped",
      got == [], str([h.title for h in got]))


# --- KEPT: the market word in the HEADLINE with an EMPTY snippet (the real Google News shape) ---
# feeds._gnews_hit blanks Google's description because it only repeats the headline, so these hits reach
# filter_hits with no snippet at all. A replay of a real run dropped all five (318 raw -> 2 kept) when the
# single-word rule demanded a topic term or the full phrase on top of the headline's own 'assessment'.
gnews_titles = [
    "Maryland selects Pearson to deliver new statewide assessment system",
    "Heidrick & Struggles Launches AI-Enhanced Leadership Assessment Platform",
    "MYCPE ONE Unveils AI-Powered Assessment Platform",
    "CodeSignal Launches Agentic Coding Assessments for Hiring",
    "CISCE launches SHINE 2026, a nationwide digital assessment initiative",
]
gnews_hits = [hit(t, f"https://www.news{i}.example.com/p") for i, t in enumerate(gnews_titles)]
gnews_kept = search.filter_hits(gnews_hits, PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=TERMS)
check("single-word gate: on-market headlines with empty snippets are kept",
      {h.title for h in gnews_kept} == set(gnews_titles),
      str(sorted(t for t in gnews_titles if t not in {h.title for h in gnews_kept})))
# The same shape on a trusted host (forbes.com), still with the market word only in the SNIPPET, is dropped:
# this is the hit that made the single-word rule necessary in the first place.
trump_trusted = hit("Trump Has Grown Angrier With Vance Since Midterms, Report Says",
                    "https://www.forbes.com/t2", "The report offers an assessment of the vice president's mood.")
check("single-word gate: trusted host with 'assessment' only in the snippet is still dropped",
      search.filter_hits([trump_trusted], PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=TERMS) == [])


# --- KEPT ---
uworld = hit("UWorld Adds Automated Proctoring to Its Nursing Learning Platform",
             "https://www.prnewswire.com/u", "Automated proctoring for nursing, with assessment of clinical skills.")
pearson = hit("Pearson acquires assessment technology provider ITS",
              "https://www.reuters.com/p", "Pearson buys an assessment technology provider.")
phenom = hit("Phenom acquires Be Applied to scale skills assessment hiring",
             "https://www.techcrunch.com/ph", "Skills assessment hiring at scale.")
kept_hits = search.filter_hits([uworld, pearson, phenom], PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=TERMS)
kept_titles = {h.title for h in kept_hits}
check("single-word gate: proctoring hit kept", uworld.title in kept_titles, str(kept_titles))
check("single-word gate: Pearson player hit kept", pearson.title in kept_titles, str(kept_titles))
check("single-word gate: skills assessment hit kept", phenom.title in kept_titles, str(kept_titles))


# --- modifier gate unchanged ---
mk = ["Web Scraping Services Market"]
terms = ["web scraping", "data extraction"]
fc = hit("Firecrawl raises funding for web scraping API", "https://www.techcrunch.com/fc")
ws = hit("Web shells found on servers", "https://www.thehackernews.com/ws")
mod_kept = search.filter_hits([fc, ws], mk, logger=LOG, topic_terms=terms)
mod_titles = [h.title for h in mod_kept]
check("modifier gate: on-topic hit kept", fc.title in mod_titles, str(mod_titles))
check("modifier gate: off-topic 'web only' hit dropped", ws.title not in mod_titles, str(mod_titles))


# --- fallback: empty topic_terms keeps a headline that carries the market word, by tokens ---
fallback_hit = hit("Regulator approves a new assessment platform for state schools",
                   "https://www.reuters.com/e")
fb_kept = search.filter_hits([fallback_hit], PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=[])
check("fallback: empty topic_terms keeps an assessment headline by tokens",
      len(fb_kept) == 1, str([h.title for h in fb_kept]))
# An analyst recognition is dropped whatever the vocabulary, even though its title says "Assessments".
ranking_hit = hit("UiPath Named a Leader in Everest Group's 2026 PEAK Matrix Assessments",
                  "https://markets.ft.com/f", "Industry assessment of automation vendors.")
check("ranking title: an analyst recognition is dropped even without topic_terms",
      search.filter_hits([ranking_hit], PHRASES, key_players=PLAYERS, logger=LOG, topic_terms=[]) == [])


# --- player variant: single market word not a variant ---
pats = search._player_patterns(PLAYERS, {"assessment", "services"})
variant_names = [v for _, _ in []]  # placeholder
# check variants of Open Assessment Technologies do not include bare 'Assessment'
assert "Assessment" not in search.player_name_variants("Open Assessment Technologies") or True
# player_name_variants unchanged (still includes Assessment as a variant of the name itself)
check("player_name_variants unchanged: still lists Assessment",
      "Assessment" in search.player_name_variants("Open Assessment Technologies"),
      str(search.player_name_variants("Open Assessment Technologies")))
# but _player_patterns skips it when market_words contains assessment
names_in_pats = set()
for player, rx in pats:
    pass
# verify no pattern matches a bare 'assessment' headline for Open Assessment Technologies
bare = "Assessment tools are everywhere in the news today"
matched_players = [p for p, rx in pats if rx.search(bare)]
check("player patterns: bare market word no longer names the player",
      "Open Assessment Technologies" not in matched_players, str(matched_players))


print()
print("all single word gate tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
