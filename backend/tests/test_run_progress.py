"""Per-step cost reporting: llm.run_cost_delta, telemetry.emit_run_progress and main._reports_step.

The dashboard SUMS the cost of every event of a run, so each event must carry only what its step added.
"""

import logging
from types import SimpleNamespace

import pytest

import llm
import main
import telemetry


class _RunAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return msg, kwargs


def _logger(run_id: int) -> logging.LoggerAdapter:
    return _RunAdapter(logging.getLogger(f"test.progress.{run_id}"), {"revamp_run_id": run_id})


# ---------------------------------------------------------------- llm.run_cost_delta

def test_delta_reports_only_new_calls_and_sums_back_to_the_total():
    lg = _logger(5101)
    t = llm._run_cost_for(lg)
    t.add("orient", "deepseek-flash", 1000, 200, 100, 0.001)
    d1 = llm.run_cost_delta(lg)
    t.add("fetch", "deepseek-flash", 2000, 300, 0, 0.002)
    t.add("select", "deepseek-flash", 500, 100, 50, 0.0005)
    d2 = llm.run_cost_delta(lg)
    d3 = llm.run_cost_delta(lg)  # nothing happened since

    assert (d1["calls"], d1["cost_usd"], d1["prompt_tokens"]) == (1, 0.001, 1000)
    assert (d2["calls"], d2["prompt_tokens"], d2["completion_tokens"], d2["cached_tokens"]) == (2, 2500, 400, 50)
    assert d2["cost_usd"] == pytest.approx(0.0025)
    assert (d3["calls"], d3["cost_usd"], d3["prompt_tokens"]) == (0, 0, 0)
    # what the dashboard would add up equals the run's true total
    total = llm.run_cost_totals(lg)
    assert d1["cost_usd"] + d2["cost_usd"] + d3["cost_usd"] == pytest.approx(total["cost_usd"])
    assert d1["prompt_tokens"] + d2["prompt_tokens"] == total["prompt_tokens"]
    llm.run_cost_summary(lg)


def test_delta_survives_the_tracker_being_popped_by_finalize():
    """finalize pops the tracker; 'Regenerate draft' then starts a fresh one. The second round must report
    its own cost in full, not (new total - old total), which would be negative or zero."""
    lg = _logger(5102)
    t = llm._run_cost_for(lg)
    t.add("write", "deepseek-flash", 4000, 800, 0, 0.004)
    assert llm.run_cost_delta(lg)["cost_usd"] == 0.004
    llm.run_cost_summary(lg)  # finalize

    t2 = llm._run_cost_for(lg)  # regenerate
    t2.add("write", "deepseek-flash", 3000, 600, 0, 0.003)
    again = llm.run_cost_delta(lg)
    assert again["cost_usd"] == 0.003
    assert again["prompt_tokens"] == 3000
    llm.run_cost_summary(lg)


def test_delta_degrades_to_unknown_never_raises():
    assert llm.run_cost_delta(_logger(5103))["cost_usd"] is None  # no tracker
    assert llm.run_cost_delta(logging.getLogger("bare"))["calls"] == 0  # bare Logger, no run id


def test_delta_with_an_unpriced_call_is_unknown_not_zero():
    lg = _logger(5104)
    llm._run_cost_for(lg).add("orient", "unknown-model", 10, 5, 0, None)
    d = llm.run_cost_delta(lg)
    assert d["cost_usd"] is None and d["prompt_tokens"] == 10
    llm.run_cost_summary(lg)


# ---------------------------------------------------------------- telemetry.emit_run_progress

@pytest.fixture
def sent(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, "send", events.append)
    monkeypatch.setattr(telemetry, "configured", lambda: True)
    monkeypatch.setattr(telemetry, "install_id", lambda: "install-abcdef123456")
    return events


def test_emit_run_progress_shape(sent):
    telemetry.emit_run_progress("run1", "My title", "step2", 12.5, 0.0042, 1000, 200, 300)
    (ev,) = sent
    assert ev["type"] == "run_progress"
    assert ev["install_id"] == "install-abcdef123456"
    assert (ev["run_id"], ev["title"], ev["step"]) == ("run1", "My title", "step2")
    assert (ev["cost_usd"], ev["prompt_tokens"], ev["completion_tokens"], ev["cached_tokens"]) == (0.0042, 1000, 200, 300)
    assert ev["event_id"] and "user_name" in ev  # unique id for dedupe; the display name rides on every event


def test_emit_run_progress_caps_free_text(sent):
    telemetry.emit_run_progress("r" * 200, "t" * 999, "s" * 99, None, None, 0, 0, 0)
    (ev,) = sent
    assert len(ev["run_id"]) == 64 and len(ev["title"]) == 200 and len(ev["step"]) == 24
    assert ev["cost_usd"] is None


# ---------------------------------------------------------------- main._reports_step

def _fake_run(run_id: int):
    return SimpleNamespace(logger=_logger(run_id), title="", article=SimpleNamespace(title="Article T"),
                           filename="f.docx", created_at=0.0)


@pytest.fixture
def progress(sent, monkeypatch):
    monkeypatch.setattr(main, "_RUNS", {})
    return sent


def test_step_success_reports_the_cost_it_incurred(progress):
    run = _fake_run(5201)
    main._RUNS["tok"] = run

    @main._reports_step("step2")
    def step(req):
        llm._run_cost_for(run.logger).add("search", "deepseek-flash", 800, 100, 0, 0.0012)
        return {"ok": True}

    assert step(SimpleNamespace(run_token="tok")) == {"ok": True}
    (ev,) = progress
    assert (ev["type"], ev["step"], ev["run_id"], ev["title"]) == ("run_progress", "step2", "tok", "Article T")
    assert ev["cost_usd"] == 0.0012 and ev["prompt_tokens"] == 800
    llm.run_cost_summary(run.logger)


def test_step_failure_after_spending_still_reports_and_reraises(progress):
    run = _fake_run(5202)
    main._RUNS["tok"] = run

    @main._reports_step("step3")
    def step(req):
        llm._run_cost_for(run.logger).add("select", "deepseek-flash", 500, 50, 0, 0.0007)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        step(SimpleNamespace(run_token="tok"))
    (ev,) = progress
    assert ev["step"] == "step3" and ev["cost_usd"] == 0.0007  # the money was spent even though the step failed
    llm.run_cost_summary(run.logger)


def test_step_rejected_before_any_call_reports_nothing(progress):
    run = _fake_run(5203)
    main._RUNS["tok"] = run

    @main._reports_step("step4")
    def step(req):
        raise ValueError("400: finish step 3 first")  # validation failure, no LLM call made

    with pytest.raises(ValueError):
        step(SimpleNamespace(run_token="tok"))
    assert progress == []


def test_each_step_reports_only_its_own_slice(progress):
    run = _fake_run(5204)
    main._RUNS["tok"] = run
    t = llm._run_cost_for(run.logger)

    @main._reports_step("step2")
    def s2(req):
        t.add("a", "deepseek-flash", 100, 10, 0, 0.001)

    @main._reports_step("step3")
    def s3(req):
        t.add("b", "deepseek-flash", 200, 20, 0, 0.002)

    s2(SimpleNamespace(run_token="tok"))
    s3(SimpleNamespace(run_token="tok"))
    assert [e["step"] for e in progress] == ["step2", "step3"]
    assert [e["cost_usd"] for e in progress] == [0.001, 0.002]  # summing them gives the run's cost exactly once
    llm.run_cost_summary(run.logger)


def test_unknown_run_token_and_telemetry_off_are_silent(progress, monkeypatch):
    @main._reports_step("step2")
    def step(req):
        return 1

    assert step(SimpleNamespace(run_token="missing")) == 1  # run evicted / unknown: no crash, no event
    assert progress == []

    run = _fake_run(5205)
    main._RUNS["tok"] = run
    monkeypatch.setattr(telemetry, "configured", lambda: False)
    assert step(SimpleNamespace(run_token="tok")) == 1
    assert progress == []


def test_decorated_endpoints_keep_their_fastapi_signature():
    """FastAPI builds the request model from the function signature; the decorator must not hide it."""
    import inspect
    for fn, model in ((main.step2, main.Step2Request), (main.step3, main.Step3Request), (main.step4, main.Step4Request)):
        params = list(inspect.signature(fn).parameters.values())
        assert [p.name for p in params] == ["req"] and params[0].annotation in (model, model.__name__)
