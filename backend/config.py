import os
from pathlib import Path

from dotenv import load_dotenv

# Explicit path (not a bare load_dotenv() search) so this resolves correctly regardless of
# the working directory the server process was launched from.
load_dotenv(Path(__file__).parent.parent / ".env")

DB_URL = os.environ["DB_URL"]

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss:120b-cloud")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "600"))

CMI_SITE = "https://www.coherentmarketinsights.com"
CMI_HOST = "coherentmarketinsights.com"
