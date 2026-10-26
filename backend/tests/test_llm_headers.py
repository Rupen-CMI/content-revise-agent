import sys
import types

import llm


def test_no_install_header_when_talking_to_deepseek_directly():
    assert llm._extra_headers("https://api.deepseek.com") == {}


def test_install_header_added_behind_the_proxy(monkeypatch):
    fake = types.ModuleType("telemetry")
    fake.install_id = lambda: "inst-abc-123"
    monkeypatch.setitem(sys.modules, "telemetry", fake)
    assert llm._extra_headers("https://mon.example.app/api/v1") == {"X-Install-Id": "inst-abc-123"}


def test_header_helper_never_raises(monkeypatch):
    fake = types.ModuleType("telemetry")

    def boom():
        raise RuntimeError("no disk")

    fake.install_id = boom
    monkeypatch.setitem(sys.modules, "telemetry", fake)
    assert llm._extra_headers("https://mon.example.app/api/v1") == {}
