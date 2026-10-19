import json
import logging
import re
import time
from datetime import datetime, timezone

from openai import APIError, APIStatusError, APITimeoutError, OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_S

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=LLM_TIMEOUT_S)


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- cost tracking (logs only)
#
# $/1M tokens, confirmed live for THIS account on 2026-10-12: config.LLM_MODEL is "deepseek-chat",
# a legacy alias DeepSeek deprecated 2026-07-24 — a real call's resp.model came back
# "deepseek-flash", so that alias is what's actually billed here now, not whatever "deepseek-chat"
# meant historically. If the account/model ever changes, re-verify with a live call before trusting
# this table (resp.model is logged every call specifically so that drift is visible, not silent).
# Source: https://api-docs.deepseek.com/quick_start/pricing (fetched 2026-10-12).
_PRICING_PER_1M_USD = {
    "deepseek-flash": {
        "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
        "peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
    },
}
# "Peak hours are 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, excluding Chinese public
# holidays" (per the pricing page). Holiday exclusions aren't modeled — a rare, small inaccuracy
# versus the complexity of a holiday calendar for a cost ESTIMATE, not an invoice.
_PEAK_UTC_WINDOWS = ((1, 4), (6, 10))


def _is_peak(dt: datetime) -> bool:
    if dt.weekday() >= 5:  # Sat/Sun
        return False
    hour = dt.hour
    return any(start <= hour < end for start, end in _PEAK_UTC_WINDOWS)


def _call_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int) -> float | None:
    """USD cost of one call, or None if `model` isn't in the pricing table (unpriced rather than
    silently wrong — an unlisted/renamed model must not be charged at another model's rate)."""
    table = _PRICING_PER_1M_USD.get(model)
    if table is None:
        return None
    rates = table["peak" if _is_peak(datetime.now(timezone.utc)) else "off_peak"]
    cache_miss_tokens = max(0, prompt_tokens - cached_tokens)
    cost = (
        cached_tokens * rates["cache_hit"]
        + cache_miss_tokens * rates["cache_miss"]
        + completion_tokens * rates["output"]
    ) / 1_000_000
    return cost


class RunCost:
    """Accumulates $ across every LLM call sharing one run logger (see new_run_logger /
    _run_cost_for). Logs-only — never shown in the UI, never affects pipeline behavior."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add(self, call_name: str, model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int, cost: float | None) -> None:
        self.calls.append({
            "call_name": call_name, "model": model, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens, "cached_tokens": cached_tokens, "cost": cost,
        })

    def total(self) -> float | None:
        costs = [c["cost"] for c in self.calls]
        return None if any(c is None for c in costs) else sum(costs)

    def summary_lines(self) -> list[str]:
        lines = [
            f"  {c['call_name']:<8} {c['model']:<16} prompt={c['prompt_tokens']:>6} "
            f"(cached={c['cached_tokens']:>6}) completion={c['completion_tokens']:>6}  "
            + (f"${c['cost']:.6f}" if c["cost"] is not None else "(unpriced model)")
            for c in self.calls
        ]
        total = self.total()
        lines.append(f"  TOTAL: " + (f"${total:.6f}" if total is not None else "(unpriced — see per-call lines)"))
        return lines


_run_costs: dict[int, RunCost] = {}


def _run_id_of(logger: logging.Logger) -> int | None:
    """new_run_logger() hands out a logging.LoggerAdapter carrying a distinct run id per run (see
    its docstring — the plain Logger it wraps is a process-wide singleton and can't be used for
    this). A bare Logger (e.g. in a unit test that didn't go through new_run_logger) has no run id
    at all — cost tracking degrades to a fresh, un-shared tracker per call rather than raising."""
    extra = getattr(logger, "extra", None)
    return extra.get("revamp_run_id") if extra else None


def _run_cost_for(logger: logging.Logger) -> RunCost:
    run_id = _run_id_of(logger)
    if run_id is None:
        return RunCost()
    tracker = _run_costs.get(run_id)
    if tracker is None:
        tracker = _run_costs[run_id] = RunCost()
    return tracker


def run_cost_summary(logger: logging.Logger) -> list[str]:
    """Called once at the end of a run (main.py, right before "RUN COMPLETE") to log the total."""
    run_id = _run_id_of(logger)
    tracker = _run_costs.pop(run_id, None) if run_id is not None else None  # pop: free it, this run is done
    return tracker.summary_lines() if tracker else ["  (no LLM calls recorded for this run)"]


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    return json.loads(text)


def chat_json(
    system: str, user: str, *, temperature: float = 0.3, retries: int = 2,
    call_name: str = "call", logger: logging.Logger = _null_logger, max_tokens: int | None = None,
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
                **({"max_tokens": max_tokens} if max_tokens else {}),
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
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached_tokens = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None) or 0
        actual_model = resp.model or LLM_MODEL  # log what was ACTUALLY billed, not the alias requested —
        # deepseek-chat silently routes to a different real model; pricing must key off that, not the alias.
        cost = _call_cost(actual_model, prompt_tokens, completion_tokens, cached_tokens)
        _run_cost_for(logger).add(call_name, actual_model, prompt_tokens, completion_tokens, cached_tokens, cost)
        logger.info(
            f"[{call_name}] attempt {attempt + 1}: {elapsed:.1f}s, model={actual_model}, "
            f"tokens prompt={prompt_tokens} (cached={cached_tokens}) completion={completion_tokens}, "
            + (f"cost=${cost:.6f}" if cost is not None else "cost=unpriced (model not in _PRICING_PER_1M_USD)")
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
