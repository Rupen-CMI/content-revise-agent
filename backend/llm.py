import json
import logging
import re
import time

from openai import APIError, APIStatusError, APITimeoutError, OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_S

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=LLM_TIMEOUT_S)


class LLMError(RuntimeError):
    pass


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    return json.loads(text)


def chat_json(
    system: str, user: str, *, temperature: float = 0.3, retries: int = 2,
    call_name: str = "call", logger: logging.Logger = _null_logger,
) -> dict:
    logger.info(f"--- LLM call: {call_name} (model={LLM_MODEL}, temperature={temperature}) ---")
    logger.info(f"[{call_name}] SYSTEM PROMPT:\n{system}")
    logger.info(f"[{call_name}] USER PROMPT:\n{user}")

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            resp = _client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except APITimeoutError as e:
            logger.error(f"[{call_name}] request to {LLM_BASE_URL} timed out after {LLM_TIMEOUT_S}s: {e}", exc_info=True)
            raise LLMError(f"'{call_name}' call to the model timed out after {LLM_TIMEOUT_S}s") from e
        except APIStatusError as e:
            logger.error(f"[{call_name}] model server returned HTTP {e.status_code}: {e}", exc_info=True)
            raise LLMError(f"'{call_name}' call failed: model server returned HTTP {e.status_code} ({e})") from e
        except APIError as e:
            logger.error(f"[{call_name}] could not reach the model server at {LLM_BASE_URL}: {e}", exc_info=True)
            raise LLMError(
                f"'{call_name}' call could not reach the model server at {LLM_BASE_URL}. "
                "Check the internet connection and that the API key/balance is valid."
            ) from e
        elapsed = time.time() - t0
        content = resp.choices[0].message.content
        usage = resp.usage
        logger.info(
            f"[{call_name}] attempt {attempt + 1}: {elapsed:.1f}s, "
            f"tokens prompt={getattr(usage, 'prompt_tokens', '?')} completion={getattr(usage, 'completion_tokens', '?')}"
        )
        logger.info(f"[{call_name}] RAW RESPONSE:\n{content}")
        try:
            parsed = _parse_json(content)
        except json.JSONDecodeError as e:
            last_error = e
            logger.info(f"[{call_name}] JSON parse failed: {e}; retrying")
            messages = messages[:2] + [
                {"role": "assistant", "content": content or ""},
                {"role": "user", "content": "That was not valid JSON. Return only the corrected JSON object."},
            ]
            continue
        logger.info(f"[{call_name}] PARSED JSON:\n{json.dumps(parsed, indent=2, ensure_ascii=False)}")
        return parsed
    raise LLMError(f"model did not return valid JSON after {retries + 1} attempts: {last_error}")
