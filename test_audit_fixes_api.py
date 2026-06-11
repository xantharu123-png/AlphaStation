"""Regressionstests fuer die api.py-Audit-Fixes vom 2026-06-10 (zweite Welle).

Abgedeckt:
- S-1   Mail-Gate: Breakout-Rows brauchen RVOL >= 1.5 (_alert_min_rvol_for_row)
- H-4   Turtle-Rows bekommen native TP1/TP2 + trade_setup (Long-Geometrie)
- H-5   Backtest-Regel "Momentum Breakout Long" ist live-synchron (kein Alt-Alias)
- H-6   Guard-Set-Mitgliedschaft + Health-Override-Wirkung fuer Crypto-Scanner
- H-10  save_cache_file schreibt atomar (kein Partial-File bei Fehler)
- H-11  Paywall: Tab-Gates fuer die 6 Crypto-/Kontext-Endpoints + Default-Deny
- H-12  Login-Throttle (6. Fehlversuch -> 429 + Retry-After) + test-email admin-only
- M-BTC-Z           z-Score nutzt residual_std * sqrt(5)
- M-LeveragedTokens BTCUP/XYZ3L erkannt, BTC/SYRUP nicht
- M-Compression / M-Cup&Handle RVOL-Floors auf 1.5 gepinnt
"""
import asyncio
import inspect
import json
import re
import types

import pytest
from fastapi import HTTPException

import api


# ══════════════════════════════════════════════════════════════
# S-1: Mail-Gate RVOL-Floor fuer Breakout-Pfade
# ══════════════════════════════════════════════════════════════

def test_s1_breakout_strategy_row_requires_rvol_1_5():
    row = {"Strategy": "Momentum Breakout Long", "ticker": "MOMO"}
    floor = api._alert_min_rvol_for_row("stock_strategy", row)
    assert floor == pytest.approx(1.5)
    # Eine Breakout-Row mit RVOL 1.4 liegt unter dem Gate, 1.5 erfuellt es.
    assert 1.4 < floor
    assert 1.5 >= floor


def test_s1_turtle_floor_and_prebreakout_default():
    assert api._alert_min_rvol_for_row("turtle", {}) == pytest.approx(api._ALERT_BREAKOUT_MIN_RVOL)
    assert api._ALERT_BREAKOUT_MIN_RVOL == pytest.approx(1.5)
    # Pre-Breakout-Pfade (bi_long Akkumulation) behalten bewusst den 0.7-Floor.
    assert api._alert_min_rvol_for_row("bi_long", {}) == pytest.approx(api._ALERT_MIN_RVOL)


# ══════════════════════════════════════════════════════════════
# H-4: Turtle-Rows bekommen native TP1/TP2 + trade_setup
# ══════════════════════════════════════════════════════════════

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_h4_turtle_row_has_native_targets_and_trade_setup(monkeypatch):
    # 24 ruhige Basis-Bars + frischer Breakout-Bar ueber dem 20T-Hoch (10.0)
    flat = [{"h": 10.0, "l": 9.4, "c": 9.7, "o": 9.6, "v": 1_000_000} for _ in range(24)]
    breakout = {"h": 10.6, "l": 10.0, "c": 10.5, "o": 10.05, "v": 3_000_000}
    bars = flat + [breakout]
    snapshot = {
        "tickers": [{
            "ticker": "TUTL",
            "day": {"c": 10.5, "h": 10.6, "l": 10.0, "o": 10.05, "v": 3_000_000},
            "prevDay": {"c": 10.3},
        }]
    }

    def fake_get(url, params=None, timeout=None, **kwargs):
        if "/aggs/" in url:
            return _FakeResp({"results": bars})
        if "/snapshot/" in url:
            return _FakeResp(snapshot)
        return _FakeResp({})

    saved = {}
    monkeypatch.setattr(api, "rate_limited_get", fake_get)
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-test-key")
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: None)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"TUTL"}, "unit"))
    monkeypatch.setattr(api, "save_cache_file", lambda path, data, metadata=None: saved.setdefault("rows", data))

    api._turtle_scan_wrapper()

    rows = saved.get("rows") or []
    assert rows, "Turtle-Scan hat keine Row erzeugt"
    row = rows[0]

    entry, stop = row["Entry"], row["Stop"]
    # Long-Geometrie: Stop < Entry < TP1 < TP2
    assert stop < entry < row["TP1"] < row["TP2"]
    risk = entry - stop
    assert row["TP1"] == pytest.approx(entry + 1.5 * risk, abs=0.02)
    assert row["TP2"] == pytest.approx(entry + 2.5 * risk, abs=0.02)
    # Exit_Level (10T-Tief) bleibt als Trailing-Info erhalten
    assert row["Exit_Level"] == pytest.approx(9.4, abs=0.01)
    assert row["StopLoss"] == pytest.approx(stop)
    assert row["direction"] == "LONG"

    setup = row["trade_setup"]
    assert setup["direction"] == "LONG"
    assert setup["rr_tp1"] == pytest.approx(1.5)
    assert setup["rr_tp2"] == pytest.approx(2.5)
    assert setup["stop"] < setup["entry"] < setup["tp1"] < setup["tp2"]
    assert row["Trade_Setup_Source"] == "turtle_r_multiple"

    # Kernziel des Fixes: Trade-Level-Normalisierung sieht native Level,
    # kein estimated/NO_TRADE-Plan mehr.
    levels = api._alert_trade_levels(row)
    assert levels.get("valid") is True
    assert not levels.get("estimated")


# ══════════════════════════════════════════════════════════════
# H-5: Backtest-Regel live-synchron
# ══════════════════════════════════════════════════════════════

def test_h5_momentum_breakout_backtest_rule_matches_live():
    # Genau der Pfad aus _run_backtest: erst Alias aufloesen, dann Regel laden.
    effective_key = api.BACKTEST_STRATEGY_ALIASES.get("Momentum Breakout Long", "Momentum Breakout Long")
    rule = api.BACKTEST_RULES[effective_key]
    sig = rule["signal"]
    # Live-Filter seit S-1: Change >= 2%, RVOL >= 1.5, ClosePos >= 0.5 —
    # vorher testete der Backtest Change >= 3% OHNE RVOL-Bedingung.
    assert sig["change_pct_min"] == pytest.approx(2.0)
    assert sig["close_pos_min"] == pytest.approx(0.50)
    assert sig["rvol_min"] == pytest.approx(1.5)
    assert rule["direction"] == "long"
    # Auch der native Live-Name traegt die korrigierte Regel.
    assert api.BACKTEST_RULES["Momentum Breakout Long"]["signal"]["rvol_min"] == pytest.approx(1.5)


# ══════════════════════════════════════════════════════════════
# H-6: Guard-Sets + Override-Wirkung
# ══════════════════════════════════════════════════════════════

def test_h6_guard_set_membership():
    for scanner in ("crypto_explosion", "crypto_trade_signals", "btc_divergenz"):
        assert scanner in api._ALERT_TRADE_HEALTH_GUARD_SCANNERS, scanner
    # Rows mit nativen Plan-Leveln gehoeren auch in den Plan-Guard ...
    assert "crypto_explosion" in api._ALERT_TRADE_PLAN_GUARD_SCANNERS
    assert "crypto_trade_signals" in api._ALERT_TRADE_PLAN_GUARD_SCANNERS
    # ... btc_divergenz ist watch-only ohne Plan-Level -> nur Health-Guard.
    assert "btc_divergenz" not in api._ALERT_TRADE_PLAN_GUARD_SCANNERS


def test_h6_health_override_now_applies_to_crypto_explosion():
    item = {
        "trade_decision": "WAIT_FOR_TRIGGER",
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "LONG_NOW",
        "alertable_crypto": True,
        "risk_flags": [],
        "risk_reasons": [],
    }
    api._apply_trade_health_final_signal(item, "crypto_explosion")
    # Vor H-6 kehrte die Funktion sofort zurueck (Scanner nicht im Guard-Set)
    # und liess das veraltete JETZT_TRADEN stehen.
    assert item["trade_signal"] == "WARTEN"
    assert item["trade_action"] == "WAIT_FOR_TRIGGER"
    assert item["alertable_crypto"] is False
    assert "trade_health_wait_for_trigger" in item["risk_flags"]


# ══════════════════════════════════════════════════════════════
# H-10: Atomarer Cache-Write
# ══════════════════════════════════════════════════════════════

def test_h10_save_cache_file_keeps_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "cache.json"
    target.write_text(json.dumps({"results": ["OLD"]}), encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("simulierter Crash mitten im Serialisieren")

    monkeypatch.setattr(api.json, "dump", boom)
    api.save_cache_file(str(target), [{"x": 1}])

    # Alte Datei ist byte-identisch lesbar geblieben (kein Partial-Write) ...
    assert json.loads(target.read_text(encoding="utf-8"))["results"] == ["OLD"]
    # ... und es liegt kein .tmp-Muell im Zielverzeichnis.
    assert list(tmp_path.glob("*.tmp")) == []


def test_h10_save_cache_file_happy_path_via_os_replace(tmp_path):
    target = tmp_path / "cache.json"
    api.save_cache_file(str(target), [{"x": 1}])
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["results"] == [{"x": 1}]
    assert "cached_at" in payload
    assert list(tmp_path.glob("*.tmp")) == []


# ══════════════════════════════════════════════════════════════
# H-11: Paywall-Gates + Default-Deny
# ══════════════════════════════════════════════════════════════

def test_h11_tab_gates_cover_crypto_and_context_endpoints():
    gates = dict(api._TAB_GATES)
    assert gates.get("/api/crypto-explosion") == "crypto-explosion"
    assert gates.get("/api/crypto-trade-signals") == "crypto-signals"
    assert gates.get("/api/narrative-pulse") == "money-flow"
    assert gates.get("/api/market-context") == "crash-monitor"
    assert gates.get("/api/crypto-chart") == "crypto-signals"
    assert gates.get("/api/exchange-chart") == "crypto-signals"


def test_h11_default_deny_public_allowlist_explicit():
    # Legitime Public-Routen bleiben explizit erlaubt ...
    for path in (
        "/api/health",
        "/api/system-health",
        "/api/commercial-readiness",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/plans",
        "/api/stripe/webhook",
    ):
        assert path in api._PUBLIC_API_PATHS, path
    # ... und die alte Blanket-Ausnahme fuer /api/auth/* (Default-Allow fuer
    # unbekannte Auth-Routen) ist aus dem Commerce-Gate entfernt.
    gate_src = inspect.getsource(api.commerce_auth_gate)
    assert 'path.startswith("/api/auth/")' not in gate_src


# ══════════════════════════════════════════════════════════════
# H-12: Login-Throttle + test-email admin-only
# ══════════════════════════════════════════════════════════════

def _fake_request(ip: str):
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip))


def test_h12_sixth_login_attempt_returns_429(monkeypatch):
    email, ip = "throttle-unit@example.com", "203.0.113.7"
    api._login_throttle_reset(email, ip)
    for _ in range(5):
        api._login_throttle_record_failure(email, ip)

    retry_after = api._login_throttle_retry_after(email, ip)
    assert retry_after > 0

    monkeypatch.setattr(api, "HAS_AUTH", True)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(api.api_login(api.LoginRequest(email=email, password="falsch"), _fake_request(ip)))
    assert excinfo.value.status_code == 429
    assert "Retry-After" in (excinfo.value.headers or {})
    assert int(excinfo.value.headers["Retry-After"]) > 0

    api._login_throttle_reset(email, ip)
    assert api._login_throttle_retry_after(email, ip) == 0


def test_h12_failures_below_limit_do_not_throttle_and_success_resets(monkeypatch):
    email, ip = "reset-unit@example.com", "203.0.113.8"
    api._login_throttle_reset(email, ip)
    for _ in range(4):
        api._login_throttle_record_failure(email, ip)
    assert api._login_throttle_retry_after(email, ip) == 0  # erst der 5. sperrt

    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "login_user", lambda e, p: {"success": True, "token": "unit"})
    result = asyncio.run(api.api_login(api.LoginRequest(email=email, password="richtig"), _fake_request(ip)))
    assert result["success"] is True
    # Erfolg setzt den Zaehler zurueck:
    api._login_throttle_record_failure(email, ip)
    assert api._login_throttle_retry_after(email, ip) == 0


def test_h12_throttle_is_scoped_per_email_and_ip():
    email, ip = "scoped-unit@example.com", "203.0.113.9"
    api._login_throttle_reset(email, ip)
    for _ in range(5):
        api._login_throttle_record_failure(email, ip)
    assert api._login_throttle_retry_after(email, ip) > 0
    # Andere IP / andere Email sind nicht gesperrt.
    assert api._login_throttle_retry_after(email, "198.51.100.1") == 0
    assert api._login_throttle_retry_after("andere@example.com", ip) == 0
    api._login_throttle_reset(email, ip)


def test_h12_test_email_endpoint_requires_admin():
    with pytest.raises(HTTPException) as excinfo:
        api.test_email_alert(authorization=None)
    assert excinfo.value.status_code == 403


# ══════════════════════════════════════════════════════════════
# M-BTC-Z: Legacy-z-Score-Block ist entfernt (Restpunkte 2026-06-11)
# ══════════════════════════════════════════════════════════════
# HISTORIE: Dieser Test pinnte frueher die sqrt(5)-Formel im Source von
# _btc_divergenz_wrapper. Die Formel stand aber AUSSCHLIESSLICH im toten
# Legacy-Block hinter einem unbedingten fruehen return — der Live-Pfad
# _build_crypto_btc_divergence_results (CoinGecko-Regime-Modell) rechnet
# gar keinen residual-z-Score. Nach der auftragsgemaessen Entfernung des
# unreachable Blocks (Restpunkte-Fix 3) sichert der Test jetzt das
# Gegenteil: Der Legacy-Block bleibt draussen, der Wrapper delegiert nur.

def test_mbtcz_legacy_zscore_block_stays_removed():
    src = inspect.getsource(api._btc_divergenz_wrapper)
    assert "_build_crypto_btc_divergence_results" in src, \
        "Wrapper muss den Live-Builder aufrufen"
    assert not re.search(r"z_score\s*=", src), \
        "Legacy-z-Score-Berechnung darf nicht in den Wrapper zurueckkehren"
    assert "residual_std" not in src and "assets = []" not in src, \
        "toter Legacy-Block (Beta/Residual-Modell) darf nicht zurueckkehren"


# ══════════════════════════════════════════════════════════════
# M-LeveragedTokens
# ══════════════════════════════════════════════════════════════

def test_leveraged_token_symbols_detected():
    assert api._is_leveraged_token_symbol("BTCUP") is True
    assert api._is_leveraged_token_symbol("ETHDOWN") is True
    assert api._is_leveraged_token_symbol("XYZ3L") is True
    assert api._is_leveraged_token_symbol("ADA4S") is True
    assert api._is_leveraged_token_symbol("SOL5L") is True
    assert api._is_leveraged_token_symbol("ETHBULL") is True
    assert api._is_leveraged_token_symbol("EOSBEAR") is True


def test_normal_symbols_not_flagged_as_leveraged():
    assert api._is_leveraged_token_symbol("BTC") is False
    assert api._is_leveraged_token_symbol("ETH") is False
    # Reale Tokens, die wie Leveraged-Suffixe aussehen koennten:
    assert api._is_leveraged_token_symbol("SYRUP") is False  # endet auf UP
    assert api._is_leveraged_token_symbol("JUP") is False    # endet auf UP
    assert api._is_leveraged_token_symbol("BULL") is False   # Memecoin, keine Basis
    assert api._is_leveraged_token_symbol("") is False
    assert api._is_leveraged_token_symbol(None) is False


def test_leveraged_tokens_excluded_from_crypto_universe():
    reason = api._crypto_asset_exclusion_reason("BTCUP", "btcup", "BTCUP")
    assert reason and "leveraged" in reason
    assert api._is_excluded_crypto_asset("XYZ3L", "xyz3l", "XYZ 3x Long") is True
    assert api._crypto_asset_exclusion_reason("BTC", "bitcoin", "Bitcoin") is None


# ══════════════════════════════════════════════════════════════
# M-Compression / M-Cup&Handle: RVOL-Floors gepinnt
# ══════════════════════════════════════════════════════════════

def test_breakout_strategy_rvol_floors_are_1_5():
    assert api.STRATEGIES["Compression Breakout"]["filters"]["RVOL"][0] == pytest.approx(1.5)
    assert api.STRATEGIES["Cup and Handle Breakout"]["filters"]["RVOL"][0] == pytest.approx(1.5)


def test_cup_handle_confirmation_rvol_floor_is_1_5():
    src = inspect.getsource(api._detect_cup_handle_breakout)
    assert re.search(r"if\s+rvol\s*<\s*1\.5\s*:", src), (
        "Cup&Handle-Bestaetigung muss RVOL >= 1.5 verlangen (war 1.1)"
    )
