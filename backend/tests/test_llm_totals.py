"""Tests for llm.py's run_cost_totals accessor (the telemetry-facing peek)."""

import logging

import llm


class _RunAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return msg, kwargs


def _run_logger(run_id: int) -> logging.LoggerAdapter:
    return _RunAdapter(logging.getLogger(f"test.run.{run_id}"), {"revamp_run_id": run_id})


def test_totals_before_and_after_summary_pop():
    logger = _run_logger(4242)
    tracker = llm._run_cost_for(logger)
    tracker.add("orient", "deepseek-flash", 1000, 200, 100, 0.001)
    tracker.add("write", "deepseek-flash", 2000, 300, 0, 0.002)

    totals = llm.run_cost_totals(logger)
    assert totals == {
        "calls": 2, "cost_usd": 0.003,
        "prompt_tokens": 3000, "completion_tokens": 500, "cached_tokens": 100,
    }

    # run_cost_summary pops the tracker; totals afterwards degrade to zeros, never raise
    assert llm.run_cost_summary(logger)
    assert llm.run_cost_totals(logger) == {
        "calls": 0, "cost_usd": None, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
    }


def test_totals_unknown_run_degrades_to_zeros():
    logger = _run_logger(9999)
    assert llm.run_cost_totals(logger)["cost_usd"] is None


def test_totals_bare_logger_no_extra():
    # a plain Logger without the run-id extra: no crash, zeros
    assert llm.run_cost_totals(logging.getLogger("bare"))["calls"] == 0


def test_totals_with_unpriced_calls():
    logger = _run_logger(777)
    tracker = llm._run_cost_for(logger)
    tracker.add("orient", "unknown-model", 10, 5, 0, None)  # unpriced model
    totals = llm.run_cost_totals(logger)
    assert totals["cost_usd"] is None  # unpriced rather than silently wrong (matches RunCost.total)
    assert totals["prompt_tokens"] == 10
