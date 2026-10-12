import os
from pathlib import Path

from dotenv import load_dotenv

# Explicit path (not a bare load_dotenv() search) so this resolves correctly regardless of
# the working directory the server process was launched from.
load_dotenv(Path(__file__).parent.parent / ".env")

DB_URL = os.environ["DB_URL"]

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "600"))

CMI_SITE = "https://www.coherentmarketinsights.com"
CMI_HOST = "coherentmarketinsights.com"
