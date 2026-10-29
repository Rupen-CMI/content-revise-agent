"""Dry checks of packaging/build_package.py's proxy-mode secret handling, against a throwaway repo +
stage dir (no Nuitka build): the DeepSeek key must never reach a proxy build, the staged config must
point the LLM client at the proxy, and --direct must keep working as the explicit legacy opt-in."""
import importlib.util
import sys
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parents[2]
SECRET = "sk-test-0123456789abcdef0123456789abcdef"
TOKEN = "cmi_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v"


@pytest.fixture()
def bp(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_package_under_test", REAL / "packaging" / "build_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    repo = tmp_path / "repo"
    stage = tmp_path / "stage"
    (repo).mkdir()
    (stage / "backend").mkdir(parents=True)
    (repo / ".env").write_text(
        f"DEEPSEEK_API_KEY={SECRET}\nDB_URL=postgres://u:p@h/db\n"
        "MONITOR_URL=https://mon.example.app/\nMONITOR_INGEST_KEY=ingest-key-123\n",
        encoding="utf-8",
    )
    (stage / "backend" / "config.py").write_text((REAL / "backend" / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", repo)
    monkeypatch.setattr(mod, "STAGE", stage)
    return mod


def _load_staged_config(stage: Path):
    """Import the PATCHED config.py with the generated bpk next to it, then clean up sys.modules."""
    backend = str(stage / "backend")
    sys.path.insert(0, backend)
    for m in ("bpk", "config_staged"):
        sys.modules.pop(m, None)
    try:
        import bpk  # noqa: F401  (must be importable before config does `import bpk`)
        spec = importlib.util.spec_from_file_location("config_staged", stage / "backend" / "config.py")
        cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfg)
        return cfg, sys.modules["bpk"]
    finally:
        sys.path.remove(backend)


def test_proxy_build_points_client_at_proxy_and_never_bakes_the_deepseek_key(bp):
    bp.generate_bpk("Priya", TOKEN, direct=False)
    bp.patch_config_fallbacks(direct=False)

    cfg, bpk = _load_staged_config(bp.STAGE)
    assert cfg.LLM_BASE_URL == "https://mon.example.app/api/v1"  # trailing slash normalised
    assert cfg.LLM_API_KEY == TOKEN
    assert cfg.LLM_TIMEOUT_S == 130.0
    assert "deepseek.com" not in cfg.LLM_BASE_URL
    with pytest.raises(KeyError):
        bpk.get("DEEPSEEK_API_KEY")
    assert bpk.get("MONITOR_USER_LABEL") == "Priya"
    # the shared ingest key must NOT ship in a proxy build: telemetry authenticates with the install token
    with pytest.raises(KeyError):
        bpk.get("MONITOR_INGEST_KEY")
    assert bpk.get("MONITOR_PROXY_TOKEN") == TOKEN

    baked = (bp.STAGE / "backend" / "bpk.py").read_text(encoding="utf-8")
    assert SECRET not in baked and TOKEN not in baked  # XOR-masked, nothing greppable
    bp.assert_no_deepseek_key(direct=False)  # no raise


def test_env_var_cannot_redirect_a_proxy_build(bp, monkeypatch):
    bp.generate_bpk("Priya", TOKEN, direct=False)
    bp.patch_config_fallbacks(direct=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-attacker")
    monkeypatch.setenv("LLM_BASE_URL", "https://evil.example")
    cfg, _ = _load_staged_config(bp.STAGE)
    assert cfg.LLM_API_KEY == TOKEN
    assert cfg.LLM_BASE_URL == "https://mon.example.app/api/v1"


DEV_TOKEN = "cmi_" + "D" * 40


def test_the_dev_token_in_env_is_never_baked_into_a_customer_build(bp):
    """The repo .env holds the DEVELOPER's token for dev-run telemetry. A build that forgets --proxy-token
    must fail, not quietly ship the developer's token inside a customer's package."""
    env_path = bp.REPO / ".env"
    env_path.write_text(env_path.read_text(encoding="utf-8") + f"MONITOR_PROXY_TOKEN={DEV_TOKEN}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        bp.generate_bpk("Priya", "", direct=False)  # no explicit token: no fallback to .env
    assert "token" in str(e.value)

    with pytest.raises(SystemExit) as e:
        bp.generate_bpk("Priya", DEV_TOKEN, direct=False)  # explicitly passing the dev token is refused too
    assert "dev token" in str(e.value)

    bp.generate_bpk("Priya", TOKEN, direct=False)  # the customer's own token is what gets baked
    baked_token, bpk = _load_staged_config_after(bp)
    assert baked_token == TOKEN
    assert baked_token != DEV_TOKEN


def test_the_shipped_vc_runtime_covers_what_torch_loads(bp):
    """Torch fails to import ('WinError 1114 c10.dll' / 'WinError 126 shm.dll') on PCs with an old Visual C++
    runtime. torch_cpu.dll imports exactly these (found by scanning the installed wheel's import tables), so a
    package that omits any of them is broken on such PCs even though it works on a developer machine."""
    needed_by_torch = {"msvcp140.dll", "msvcp140_atomic_wait.dll", "vcruntime140.dll",
                       "vcruntime140_1.dll", "vcruntime140_threads.dll"}
    assert needed_by_torch <= set(bp.VC_RUNTIME_REQUIRED)


def _load_staged_config_after(bp):
    backend = str(bp.STAGE / "backend")
    sys.path.insert(0, backend)
    sys.modules.pop("bpk", None)
    try:
        import bpk
        return bpk.get("MONITOR_PROXY_TOKEN"), bpk
    finally:
        sys.path.remove(backend)


@pytest.mark.parametrize("bad", ["", "not-a-token", "cmi_short", "sk-" + "x" * 40])
def test_proxy_build_refuses_without_a_valid_token(bp, bad):
    with pytest.raises(SystemExit) as e:
        bp.generate_bpk("Priya", bad, direct=False)
    assert "token" in str(e.value)


def test_proxy_build_refuses_without_monitor_url(bp):
    (bp.REPO / ".env").write_text(f"DEEPSEEK_API_KEY={SECRET}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        bp.generate_bpk("Priya", TOKEN, direct=False)
    assert "MONITOR_URL" in str(e.value)


def test_audit_catches_a_leaked_key_in_any_staged_file(bp):
    bp.generate_bpk("Priya", TOKEN, direct=False)
    (bp.STAGE / "backend" / "oops.bin").write_bytes(b"xx" + SECRET.encode() + b"yy")
    with pytest.raises(SystemExit) as e:
        bp.assert_no_deepseek_key(direct=False)
    assert "oops.bin" in str(e.value)


def test_direct_build_is_the_explicit_legacy_opt_in(bp):
    bp.generate_bpk("Priya", "", direct=True)
    bp.patch_config_fallbacks(direct=True)
    sys.path.insert(0, str(bp.STAGE / "backend"))
    try:
        sys.modules.pop("bpk", None)
        import bpk
        assert bpk.get("DEEPSEEK_API_KEY") == SECRET
        assert bpk.get("MONITOR_INGEST_KEY") == "ingest-key-123"  # --direct still uses the shared key
        with pytest.raises(KeyError):
            bpk.get("MONITOR_PROXY_TOKEN")
    finally:
        sys.path.remove(str(bp.STAGE / "backend"))
    cfg_text = (bp.STAGE / "backend" / "config.py").read_text(encoding="utf-8")
    assert 'LLM_BASE_URL = "https://api.deepseek.com"' in cfg_text
    bp.assert_no_deepseek_key(direct=True)  # no-op in direct mode
