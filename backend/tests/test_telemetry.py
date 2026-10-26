"""Unit tests for backend/telemetry.py (no network: a local fake HTTP server + monkeypatching).

Covers the contract: silent no-op when unconfigured, correct payload shape and headers,
outbox on delivery failure then flush on recovery, and send()/the event builders never raise.
"""

import http.server
import json
import threading
import time

import pytest

import telemetry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the data dir at a temp folder and reset the module's cached state per test."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    telemetry._reset_for_tests()
    yield
    telemetry._reset_for_tests()


def _clear_monitor_env(monkeypatch):
    for key in ("MONITOR_URL", "MONITOR_INGEST_KEY", "MONITOR_PROXY_TOKEN", "MONITOR_USER_LABEL", "MONITOR_APP_VERSION"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_server():
    """A localhost HTTP server that records POSTs to any path and always answers 202."""
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append({"path": self.path, "headers": dict(self.headers), "body": body})
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):  # silence the default stderr access log
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"received": received, "url": f"http://127.0.0.1:{server.server_address[1]}"}
    server.shutdown()
    server.server_close()


def _wait_for(predicate, timeout_s=5.0):
    """Poll until predicate() is true (telemetry delivers on its own daemon thread)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _outbox_text():
    path = telemetry._outbox_path()
    if path is None or not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_noop_when_unconfigured(isolated, fake_server, monkeypatch):
    _clear_monitor_env(monkeypatch)
    assert not telemetry.configured()
    telemetry.emit_app_started()
    telemetry.emit_run_completed("run", "t", 5, "ok", 1.0, 0.01, 10, 20, 3)
    assert not fake_server["received"]
    assert _outbox_text() == []  # unconfigured => nothing queued, nothing outboxed


def test_run_completed_payload_shape(isolated, fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    monkeypatch.setenv("MONITOR_INGEST_KEY", "test-key-123")
    monkeypatch.setenv("MONITOR_USER_LABEL", "Acme Realty")
    telemetry.emit_run_completed(
        run_id="abcd1234", title="Top 10 Widget Markets", word_count=1540, status="ok",
        duration_s=42.3, cost_usd=0.01234, prompt_tokens=100, completion_tokens=50,
        cached_tokens=7,
    )
    assert _wait_for(lambda: len(fake_server["received"]) >= 1)
    req = fake_server["received"][0]
    assert req["path"] == "/api/ingest"
    assert req["headers"].get("X-Ingest-Key") == "test-key-123"
    event = json.loads(req["body"])
    assert event["type"] == "run_completed"
    assert event["install_id"] == telemetry.install_id()
    assert event["user_label"] == "Acme Realty"
    assert event["run_id"] == "abcd1234"
    assert event["title"] == "Top 10 Widget Markets"
    assert event["word_count"] == 1540
    assert event["status"] == "ok"
    assert event["cost_usd"] == 0.01234
    assert event["prompt_tokens"] == 100 and event["completion_tokens"] == 50 and event["cached_tokens"] == 7
    # event_id is a UUID and ts parses as a timestamp
    import uuid as uuid_mod
    uuid_mod.UUID(event["event_id"])
    time.strptime(event["ts"][:19], "%Y-%m-%dT%H:%M:%S")
    assert _outbox_text() == []  # successful delivery: outbox untouched/empty


def test_install_token_is_sent_as_bearer_and_the_shared_key_is_not(isolated, fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    monkeypatch.setenv("MONITOR_PROXY_TOKEN", "cmi_installtoken123")
    monkeypatch.setenv("MONITOR_INGEST_KEY", "legacy-shared-key")
    telemetry.emit_app_started()
    assert _wait_for(lambda: len(fake_server["received"]) >= 1)
    headers = fake_server["received"][0]["headers"]
    assert headers.get("Authorization") == "Bearer cmi_installtoken123"
    assert "X-Ingest-Key" not in headers  # a token-authenticated install must not leak/need the shared key


def test_app_started_payload(isolated, fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    telemetry.emit_app_started()
    assert _wait_for(lambda: len(fake_server["received"]) >= 1)
    event = json.loads(fake_server["received"][0]["body"])
    assert event["type"] == "app_started"
    assert event["install_id"] == telemetry.install_id()
    assert event["app_version"]


def test_install_id_persisted_across_resets(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    telemetry._reset_for_tests()
    first = telemetry.install_id()
    # a second process would re-read the persisted file
    telemetry._reset_for_tests()
    assert telemetry.install_id() == first
    assert (tmp_path / "cmi-blog-agent" / "install_id").is_file()


def test_outbox_on_failure_then_flush(isolated, monkeypatch):
    # Deliver to a dead port -> event lands in the outbox.
    dead = "http://127.0.0.1:9"  # discard port: nothing listens, connection refused
    monkeypatch.setenv("MONITOR_URL", dead)
    telemetry.emit_run_completed("r1", "Title", 10, "ok", 1.0, 0.01, 5, 5, 0)
    assert _wait_for(lambda: len(_outbox_text()) == 1)
    queued = json.loads(_outbox_text()[0])
    assert queued["run_id"] == "r1"

    # Now delivery works again: start flush, outbox empties, server sees the retry.
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(json.loads(body))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("MONITOR_URL", f"http://127.0.0.1:{server.server_address[1]}")
        telemetry.flush_outbox_on_start()
        assert _wait_for(lambda: len(received) >= 1 and _outbox_text() == [])
        assert received[0]["run_id"] == "r1"  # same event, same id -> server-side idempotency
    finally:
        server.shutdown()
        server.server_close()


def test_outbox_capped(isolated, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", "http://127.0.0.1:9")
    for i in range(telemetry.OUTBOX_MAX_EVENTS + 30):
        telemetry._outbox_append({"n": i})
    lines = _outbox_text()
    assert len(lines) == telemetry.OUTBOX_MAX_EVENTS
    assert json.loads(lines[-1])["n"] == telemetry.OUTBOX_MAX_EVENTS + 29  # newest kept


def test_never_raises(isolated, fake_server, monkeypatch):
    # Bad builders, unserializable payloads, dead servers, broken data dir — nothing may throw.
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    telemetry.send({"type": "run_completed", "event_id": 12345})  # malformed is fine client-side
    telemetry.emit_run_completed(None, None, None, "weird", -1, "not-a-float", None, None, None)
    monkeypatch.setenv("MONITOR_URL", "http://127.0.0.1:9")
    telemetry.emit_run_completed("r", "t", 1, "ok", 1.0, 0.0, 0, 0, 0)
    monkeypatch.delenv("MONITOR_URL")
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(telemetry, "_data_dir", lambda: None)
    telemetry.emit_app_started()
    telemetry.flush_outbox_on_start()
    telemetry._outbox_flush()
    telemetry._outbox_append({"x": 1})
    telemetry.install_id()  # falls back to in-memory id
