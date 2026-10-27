"""The display name a user types into the agent: validation, persistence, telemetry events, endpoints."""

import http.server
import json
import threading
import time

import pytest

import telemetry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Data dir in a temp folder; no monitor config unless a test sets it; clean module state."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for key in ("MONITOR_URL", "MONITOR_INGEST_KEY", "MONITOR_PROXY_TOKEN", "MONITOR_USER_LABEL"):
        monkeypatch.delenv(key, raising=False)
    telemetry._reset_for_tests()
    yield
    telemetry._reset_for_tests()


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("raw,expected", [
    ("Priya Nair", "Priya Nair"),
    ("  Priya   Nair  ", "Priya Nair"),
    ("Priya\tNair\n", "Priya Nair"),          # pasted tabs/newlines become single spaces
    ("Pri\x00ya Nair", "Priya Nair"),         # other control characters are dropped
    ("Zoë Müller-Åkesson", "Zoë Müller-Åkesson"),
    ("李雷", "李雷"),
    ("Jo", "Jo"),
    ("x" * 60, "x" * 60),
])
def test_valid_names_are_normalised(raw, expected):
    assert telemetry.normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "\n\t", "J", "x" * 61, None, 42, ["a", "b"]])
def test_invalid_names_are_rejected(raw):
    assert telemetry.normalize_name(raw) is None


# ---------------------------------------------------------------- persistence


def test_name_is_unset_until_chosen_then_survives_a_restart():
    assert telemetry.user_name() == ""
    assert telemetry.set_user_name("  Priya  Nair ") == "Priya Nair"
    assert telemetry.user_name() == "Priya Nair"

    telemetry._reset_for_tests()  # simulates closing and re-opening the app
    assert telemetry.user_name() == "Priya Nair"


def test_invalid_name_is_refused_and_keeps_the_old_one():
    telemetry.set_user_name("Priya Nair")
    with pytest.raises(ValueError):
        telemetry.set_user_name("x")
    assert telemetry.user_name() == "Priya Nair"


def test_a_corrupt_name_file_is_treated_as_unset():
    path = telemetry._name_path()
    path.write_text("\x00\x01 \n", encoding="utf-8")
    assert telemetry.user_name() == ""


def test_name_works_without_a_data_dir(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    telemetry._reset_for_tests()
    assert telemetry.set_user_name("Priya Nair") == "Priya Nair"  # in memory for the session
    assert telemetry.user_name() == "Priya Nair"


# ---------------------------------------------------------------- events


@pytest.fixture
def fake_server():
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(json.loads(body))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"received": received, "url": f"http://127.0.0.1:{server.server_address[1]}"}
    server.shutdown()
    server.server_close()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_every_event_carries_the_current_name(fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    telemetry.set_user_name("Priya Nair")
    telemetry.emit_app_started()
    telemetry.emit_run_completed("r1", "Title", 100, "ok", 1.0, 0.01, 1, 1, 0)
    assert _wait(lambda: len(fake_server["received"]) >= 2)
    assert {e["type"] for e in fake_server["received"]} == {"app_started", "run_completed"}
    assert all(e["user_name"] == "Priya Nair" for e in fake_server["received"])


def test_events_before_a_name_is_chosen_carry_an_empty_name(fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    telemetry.emit_app_started()
    assert _wait(lambda: len(fake_server["received"]) >= 1)
    assert fake_server["received"][0]["user_name"] == ""


def test_emit_name_updated_sends_a_name_updated_event(fake_server, monkeypatch):
    monkeypatch.setenv("MONITOR_URL", fake_server["url"])
    telemetry.set_user_name("Priya Nair")
    telemetry.emit_name_updated()
    assert _wait(lambda: len(fake_server["received"]) >= 1)
    ev = fake_server["received"][0]
    assert ev["type"] == "name_updated"
    assert ev["user_name"] == "Priya Nair"
    assert ev["install_id"] == telemetry.install_id()


def test_name_events_are_a_silent_noop_when_monitoring_is_not_configured():
    telemetry.set_user_name("Priya Nair")
    telemetry.emit_name_updated()  # must not raise or queue anything
    assert telemetry._outbox_path() is None or not telemetry._outbox_path().is_file()


# ---------------------------------------------------------------- HTTP endpoints


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def test_get_user_is_null_before_a_name_is_set(client):
    assert client.get("/app/user").json() == {"user_name": None}


def test_set_then_get_round_trip(client):
    r = client.post("/app/user", json={"user_name": "  Priya   Nair "})
    assert r.status_code == 200 and r.json() == {"user_name": "Priya Nair"}
    assert client.get("/app/user").json() == {"user_name": "Priya Nair"}


def test_changing_the_name_replaces_it(client):
    client.post("/app/user", json={"user_name": "Priya Nair"})
    client.post("/app/user", json={"user_name": "Priya N."})
    assert client.get("/app/user").json() == {"user_name": "Priya N."}


@pytest.mark.parametrize("payload", [{"user_name": ""}, {"user_name": "x"}, {"user_name": "y" * 61}, {}, {"user_name": 7}])
def test_bad_names_are_refused_without_changing_anything(client, payload):
    client.post("/app/user", json={"user_name": "Priya Nair"})
    r = client.post("/app/user", json=payload)
    assert r.status_code in (400, 422)
    assert client.get("/app/user").json() == {"user_name": "Priya Nair"}
