"""Offline test for llm.chat_json's retries (no network): a stalled request is asked again, and a malformed answer is sent back with the
exact error. Run from backend/: python devtools/test_llm_retry.py"""
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from openai import APITimeoutError  # noqa: E402

import llm  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_llm_retry")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def reply(text: str):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None), model="deepseek-flash")


class FakeCompletions:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw["messages"])
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return reply(step)


def run_with(script):
    fake = FakeCompletions(script)
    real = llm._client
    llm._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake))
    try:
        return llm.chat_json("sys", "user", call_name="t", logger=LOG), fake
    finally:
        llm._client = real


def timeout():
    return APITimeoutError(request=httpx.Request("POST", "http://x"))


out, fake = run_with([timeout(), '{"a": 1}'])
check("a stalled request is asked again and the answer is used", out == {"a": 1} and len(fake.calls) == 2)
check("the repeat of a stalled request is the same request, nothing added", fake.calls[0] == fake.calls[1])

try:
    run_with([timeout(), timeout(), timeout()])
    check("three stalls in a row end in a clear error", False)
except llm.LLMError as e:
    check("three stalls in a row end in a clear error", "timed out" in str(e) and "3 times" in str(e), str(e))

bad = '{"intro": {"paragraphs": ["x"]}, "sections": [{"sop": "a", "paragraphs": ["y"]]}'
out, fake = run_with([bad, '{"ok": true}'])
sent_back = fake.calls[1][-1]["content"]
check("a malformed answer is retried and the good answer is used", out == {"ok": True})
check("the retry message names the exact error and shows where it happened", "near:" in sent_back and "Expecting" in sent_back, sent_back)
check("the retry message asks for the complete answer, with every object closed", "COMPLETE answer" in sent_back and "closed" in sent_back)

# a dropped connection (not a timeout) is asked again on a fresh connection, after a short pause
import time as _time  # noqa: E402
from openai import APIConnectionError  # noqa: E402
_sleeps = []
_real_sleep, llm.time.sleep = llm.time.sleep, lambda x: _sleeps.append(x)
try:
    out, fake = run_with([APIConnectionError(request=httpx.Request("POST", "http://x")), '{"a": 2}'])
    check("a dropped connection is asked again and the answer is used", out == {"a": 2} and len(fake.calls) == 2 and _sleeps == [5], str(_sleeps))
    try:
        run_with([APIConnectionError(request=httpx.Request("POST", "http://x"))] * 3)
        check("three dropped connections in a row end in a clear error", False)
    except llm.LLMError as e:
        check("three dropped connections in a row end in a clear error", "could not reach" in str(e) and "3 times" in str(e), str(e))
finally:
    llm.time.sleep = _real_sleep

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all llm retry tests passed")
