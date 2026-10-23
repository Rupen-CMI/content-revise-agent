import os
from pathlib import Path

from dotenv import load_dotenv

# Explicit path (not a bare load_dotenv() search) so this resolves correctly regardless of
# the working directory the server process was launched from.
load_dotenv(Path(__file__).parent.parent / ".env")

DB_URL = os.environ["DB_URL"]

# DeepSeek is the only supported model: no provider/model overrides and no fallback. If it is
# unreachable or errors, the pipeline surfaces an LLMError instead of trying anything else.
LLM_BASE_URL = "https://api.deepseek.com"
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
LLM_MODEL = "deepseek-chat"
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "600"))

# Evidence search. Bing News RSS + curated publisher RSS (feeds.py) are the primary sources. DDGS is the
# fallback: "fallback" runs it only when the primary stage keeps fewer than SEARCH_PRIMARY_MIN_HITS articles
# (or Bing was abandoned), "always" runs it every time, "off" never does.
SEARCH_DDGS_MODE = os.getenv("SEARCH_DDGS_MODE", "fallback").strip().lower()
SEARCH_PRIMARY_MIN_HITS = int(os.getenv("SEARCH_PRIMARY_MIN_HITS", "12"))
# 10 -> 4 (2026-10): DDGS is the slowest path in this pipeline (~9s + a 4s politeness gap per query,
# serialized across all streams by its gate), and its historical usefulness is poor — most runs'
# usable evidence comes from Bing + the publisher feeds. Four queries still gives a thin market a
# real fallback (one engine's worth of coverage); raising this costs minutes when it fires.
SEARCH_DDGS_MAX_QUERIES = int(os.getenv("SEARCH_DDGS_MAX_QUERIES", "4"))
# Trimmed from 80/60 (2026-10): the evidence emphasis shifts toward the CMI report's own claims
# (trends/subsegment/country_us via the report-claim backing stream), so the general market stream
# feeds fewer articles into SELECT instead of flooding the same evidence groups.
SEARCH_KEEP_HITS = int(os.getenv("SEARCH_KEEP_HITS", "50"))  # articles handed to SELECT
SEARCH_MAX_SELECTED = int(os.getenv("SEARCH_MAX_SELECTED", "40"))  # most articles SELECT may pick as evidence
SEARCH_MAX_PER_HOST = int(os.getenv("SEARCH_MAX_PER_HOST", "8"))  # per site, so one prolific publisher cannot fill the shortlist
# Claim backing: Step 1 extracts the original article's unbacked claims, DDGS text search looks for backing, SELECT keeps
# at most two sources per claim. DISABLED (default "false"): the publisher block's evidence was scaled back down to
# just the guest article and the retained paragraph, so this whole path (ORIENT's claims_to_back, the claim-backing
# search, SELECT's claim_backing, the publisher prompt's ARTICLE EVIDENCE/CLAIMS handling) sits unused but intact —
# set true to turn it back on, nothing else needs to change (orient_claims() returns [] everywhere while this is off).
SEARCH_CLAIMS_ENABLED = os.getenv("SEARCH_CLAIMS_ENABLED", "false").strip().lower() not in ("0", "false", "no", "off")

# Market-first chain switch (docs/restructure_plan.md §4 phase 8): when true (default) the backend runs the
# new chain — Step 2 builds the fact sheet (+ figure gap backing), search demand and publisher points, Step 3
# selects evidence and marks the MSQs answerable, and Step 4 plans, writes and answers the FAQ, with
# finalize running run_checks_v2. When false the original chain stays wired and running exactly as it was
# (old Step 2 prep, old write + revise, old FAQ, old checks) — this only flips which Step 2/3/4 branches run.
CMI_CHAIN_V2 = os.getenv("CMI_CHAIN_V2", "true").strip().lower() not in ("0", "false", "no", "off")

CMI_SITE = "https://www.coherentmarketinsights.com"
CMI_HOST = "coherentmarketinsights.com"

# DEV-ONLY search cache (CMI_SEARCH_CACHE=1): reuse a previous run's engine results (Bing news/web
# queries, DDGS news/text) for an identical query instead of re-hitting the rate-limited engines.
# Exists purely so repeated dev runs of the same market iterate in seconds, not minutes; production
# runs must leave it off (default), since cached coverage grows stale and evidence must reflect the
# live web. Cache files live under backend/.search_cache (delete the directory to invalidate).
SEARCH_CACHE = os.getenv("CMI_SEARCH_CACHE", "").strip().lower() not in ("", "0", "false", "no", "off")
SEARCH_CACHE_TTL_S = float(os.getenv("CMI_SEARCH_CACHE_TTL_S", str(12 * 3600)))
SEARCH_CACHE_DIR = Path(os.getenv("CMI_SEARCH_CACHE_DIR") or (Path(__file__).parent / ".search_cache"))
