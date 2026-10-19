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
SEARCH_KEEP_HITS = int(os.getenv("SEARCH_KEEP_HITS", "80"))  # articles handed to SELECT
SEARCH_MAX_SELECTED = int(os.getenv("SEARCH_MAX_SELECTED", "60"))  # most articles SELECT may pick as evidence
SEARCH_MAX_PER_HOST = int(os.getenv("SEARCH_MAX_PER_HOST", "8"))  # per site, so one prolific publisher cannot fill the shortlist

CMI_SITE = "https://www.coherentmarketinsights.com"
CMI_HOST = "coherentmarketinsights.com"
