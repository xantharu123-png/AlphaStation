#!/usr/bin/env python3
"""Regime-Filter (F-14, AUDIT 2026-08-01) — Regression-Tests.

Beweist die zwei Layer und ihre Mail-Wirkung:
- Layer 1 MARKET: Mapping market_context-Regime -> GREEN/YELLOW/RED (fail-open)
- Layer 2 BREAKER: Trip (n>=10, ØR<=-0.3, Win<=25%), Release (ØR>-0.1 oder
  5 Handelstage), Persistenz des Trip-Zustands, Selbstheilung ohne Deadlock
- Dominanz ROT-Markt > ROT-Breaker > GELB > GREEN; Banner-Inhalte
- api.py-Integration: RED degradiert swing_trade -> watch + Shadow-Tracking
  (send-unabhaengig), Breaker-Watch-Kappe 1/Tag, YELLOW filtert + kappt
- _regime_mail_decision: Env-Schalter, crypto ohne Markt-Gate, State-Persistenz

Session-unabhaengig: alle Zeit-/Markt-/Dedupe-Abhaengigkeiten werden gemockt.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import api
from modules import regime_filter as rf


UTC = timezone.utc
MON = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)   # Montag
FRI = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)  # Freitag derselben Woche
NXT_MON = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)  # naechster Montag


def _summary(decided=0, win=0.0, avg_r=0.0, scanner="stock_strategy"):
    return {"per_scanner": {scanner: {
        "decided_signals": decided, "win_rate_pct": win, "avg_r": avg_r}}}


# ── Layer 1: Markt-Mapping ───────────────────────────────────────────────────

def test_market_layer_mapping():
    assert rf.market_layer_state({"regime": "PANIC"})["state"] == rf.RED
    assert rf.market_layer_state({"regime": "RISK_OFF"})["state"] == rf.RED
    assert rf.market_layer_state({"regime": "RISK_OFF_LIGHT"})["state"] == rf.YELLOW
    assert rf.market_layer_state({"regime": "NEUTRAL"})["state"] == rf.GREEN
    assert rf.market_layer_state({"regime": "RISK_ON"})["state"] == rf.GREEN
    # fail-open: unbekannt/fehlend darf nie ROT erfinden
    assert rf.market_layer_state({"regime": ""})["state"] == rf.GREEN
    assert rf.market_layer_state(None)["state"] == rf.GREEN
    assert rf.market_layer_state({"regime": "FOO"})["state"] == rf.GREEN


# ── Layer 2: Breaker ─────────────────────────────────────────────────────────

def test_breaker_metrics_extraction():
    m = rf.breaker_metrics(_summary(decided=12, win=33.3, avg_r=-0.51), "stock_strategy")
    assert m == {"decided": 12, "win_pct": 33.3, "avg_r": -0.51}
    assert rf.breaker_metrics({}, "stock_strategy") == {"decided": 0, "win_pct": 0.0, "avg_r": 0.0}
    assert rf.breaker_metrics(None, "x")["decided"] == 0


def test_breaker_trip_exact_boundaries():
    # exakt auf den Schwellen => Trip (<=)
    ev = rf.evaluate_breaker({"decided": 10, "win_pct": 25.0, "avg_r": -0.3}, None, MON)
    assert ev["state"] == rf.RED and ev["tripped_at"] == MON.isoformat()
    assert "breaker_trip" in ev["reason"]
    # jede Bedingung einzeln verhindert den Trip
    assert rf.evaluate_breaker({"decided": 9, "win_pct": 0.0, "avg_r": -2.0}, None, MON)["state"] == rf.GREEN
    assert rf.evaluate_breaker({"decided": 10, "win_pct": 25.1, "avg_r": -0.5}, None, MON)["state"] == rf.GREEN
    assert rf.evaluate_breaker({"decided": 10, "win_pct": 20.0, "avg_r": -0.29}, None, MON)["state"] == rf.GREEN


def test_breaker_cooldown_persists_and_recovers():
    entry = {"tripped_at": MON.isoformat()}
    # naechster Tag, weiter schlecht => COOLDOWN haelt, Trip-Zeit bleibt
    ev = rf.evaluate_breaker({"decided": 12, "win_pct": 10.0, "avg_r": -0.5}, entry,
                             MON + timedelta(days=1))
    assert ev["state"] == rf.RED and ev["tripped_at"] == MON.isoformat()
    assert "breaker_cooldown" in ev["reason"]
    # Erholung => Release, Trip geloescht
    ev2 = rf.evaluate_breaker({"decided": 12, "win_pct": 40.0, "avg_r": -0.05}, entry,
                              MON + timedelta(days=2))
    assert ev2["state"] == rf.GREEN and ev2["tripped_at"] is None
    assert "breaker_release_recovered" in ev2["reason"]


def test_breaker_release_after_5_trading_days():
    entry = {"tripped_at": MON.isoformat()}
    bad = {"decided": 12, "win_pct": 0.0, "avg_r": -0.6}
    # Freitag derselben Woche: erst 4 Werktage => haelt
    assert rf.evaluate_breaker(bad, entry, FRI)["state"] == rf.RED
    # naechster Montag: 5 Werktage (Di–Fr + Mo) => Zeit-Release trotz schlechter Werte
    ev = rf.evaluate_breaker(bad, entry, NXT_MON)
    assert ev["state"] == rf.GREEN and ev["tripped_at"] is None
    assert "breaker_release_time" in ev["reason"]


def test_trading_days_between_edges():
    assert rf.trading_days_between(MON.date(), MON.date()) == 0
    assert rf.trading_days_between(FRI.date(), (FRI + timedelta(days=2)).date()) == 0  # Sa+So
    assert rf.trading_days_between(FRI.date(), (FRI + timedelta(days=3)).date()) == 1  # +Mo
    assert rf.trading_days_between(MON.date(), NXT_MON.date()) == 5
    assert rf.trading_days_between(None, NXT_MON.date()) == 0


def test_state_roundtrip_and_corrupt(tmp_path):
    path = tmp_path / "regime_state.json"
    assert rf.load_state(path) == {}                      # fehlend => {}
    assert rf.save_state({"breakers": {"x": {"tripped_at": "t"}}}, path) is True
    assert rf.load_state(path)["breakers"]["x"]["tripped_at"] == "t"
    path.write_text("{kaputt", encoding="utf-8")
    assert rf.load_state(path) == {}                      # korrupt => {}


# ── Kombinierte Entscheidung ─────────────────────────────────────────────────

def test_decide_dominance_and_state_entry():
    # Markt ROT dominiert Breaker
    d = rf.decide_mail_regime("stock_strategy",
                              context={"regime": "RISK_OFF", "overall_risk_score": 70},
                              summary=_summary(decided=12, win=0.0, avg_r=-0.6),
                              state={}, now=MON)
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_MARKET
    assert d["reason_tag"] == rf.REASON_MARKET_RED and "🟥" in d["banner"]
    # Breaker ROT, wenn Markt gruen — Trip-Zeit wird zur Persistierung gereicht
    d2 = rf.decide_mail_regime("stock_strategy",
                               context={"regime": "RISK_ON"},
                               summary=_summary(decided=12, win=10.0, avg_r=-0.5),
                               state={}, now=MON)
    assert d2["state"] == rf.RED and d2["layer"] == rf.LAYER_BREAKER
    assert d2["reason_tag"] == rf.REASON_BREAKER_COOLDOWN
    assert d2["new_state_entry"] == {"tripped_at": MON.isoformat()}
    assert d2["watch_cap_seconds"] == 20 * 3600
    # GELB mit Verschaerfungsparametern
    d3 = rf.decide_mail_regime("stock_strategy",
                               context={"regime": "RISK_OFF_LIGHT"},
                               summary=_summary(), state={}, now=MON)
    assert d3["state"] == rf.YELLOW and d3["score_boost"] == 5 and d3["max_rows"] == 2
    assert "🟨" in d3["banner"]
    # GREEN ohne Banner/Tag
    d4 = rf.decide_mail_regime("stock_strategy", context={"regime": "NEUTRAL"},
                               summary=_summary(decided=30, win=50.0, avg_r=0.4),
                               state={}, now=MON)
    assert d4["state"] == rf.GREEN and d4["banner"] == "" and d4["reason_tag"] == ""
    # Schalter aus => GREEN
    d5 = rf.decide_mail_regime("stock_strategy", context={"regime": "PANIC"},
                               summary=_summary(decided=12, win=0.0, avg_r=-0.6),
                               state={}, now=MON,
                               market_gate_enabled=False, breaker_enabled=False)
    assert d5["state"] == rf.GREEN


def test_banners_carry_facts():
    d = rf.decide_mail_regime("stock_strategy", context={"regime": "PANIC", "overall_risk_score": 81},
                              summary=None, state={}, now=MON)
    assert "PANIC" in d["banner"] and "81" in d["banner"] and "NUR BEOBACHTUNG" in d["banner"]
    d2 = rf.decide_mail_regime("stock_strategy", context=None,
                               summary=_summary(decided=12, win=10.0, avg_r=-0.5),
                               state={}, now=MON)
    assert "COOLDOWN" in d2["banner"] and "stock_strategy" in d2["banner"]


# ── api.py-Integration: _regime_mail_decision ────────────────────────────────

def test_api_decision_market_red_and_state_path(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot",
                        lambda: {"regime": "RISK_OFF", "overall_risk_score": 68})
    monkeypatch.setattr(api, "load_performance_summary", lambda days=7: _summary(), raising=False)
    d = api._regime_mail_decision("stock_strategy", "stocks", False, MON)
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_MARKET


def test_api_decision_env_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.setenv("REGIME_FILTER_ENABLED", "0")
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "PANIC"})
    assert api._regime_mail_decision("stock_strategy", "stocks", False, MON) is None


def test_api_decision_breaker_trip_persists_state(monkeypatch, tmp_path):
    state_path = tmp_path / "regime_state.json"
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", state_path)
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "RISK_ON"})
    monkeypatch.setattr(api, "load_performance_summary",
                        lambda days=7: _summary(decided=12, win=10.0, avg_r=-0.5), raising=False)
    d = api._regime_mail_decision("stock_strategy", "stocks", False, MON)
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_BREAKER
    persisted = rf.load_state(state_path)
    assert persisted["breakers"]["stock_strategy"]["tripped_at"] == MON.isoformat()


def test_api_decision_crypto_has_no_market_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "PANIC"})
    monkeypatch.setattr(api, "load_performance_summary", lambda days=7: _summary(scanner="crypto_strategy"), raising=False)
    # PANIC gilt nicht fuer Crypto; Breaker ohne Daten => GREEN
    assert api._regime_mail_decision("crypto_strategy", "crypto", False, MON) is None
    # PM-Radar wird nie vom Regime-Filter angefasst
    assert api._regime_mail_decision("stock_strategy", "stocks", True, MON) is None


# ── api.py-Integration: Mail-Pfad ────────────────────────────────────────────

class _FakeDedupe:
    def __init__(self):
        self.claimed = {}
        self.released = []

    def claim(self, key, ttl, now=None):
        last = self.claimed.get(key)
        if last is not None and ((now or 0) - last) < ttl:
            return False
        self.claimed[key] = now if now is not None else 0
        return True

    def remaining(self, key, ttl, now=None):
        last = self.claimed.get(key)
        if last is None:
            return 0
        return max(0, ttl - ((now or 0) - last))

    def release(self, key, claimed_at=None):
        self.released.append(key)
        self.claimed.pop(key, None)

    def mark(self, key, now=None):
        self.claimed[key] = now if now is not None else 0


def _row(ticker, score=90.0):
    return {
        "Ticker": ticker, "grade": "A", "score": score, "RVOL": 2.0,
        "Preis": 10.0, "current_price": 10.0, "change_pct": 3.5, "close_pos": 0.8,
        "Signal_Direction": "LONG",
        "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.5,
                        "tp1": 10.75, "tp2": 11.0},
    }


def _mail_harness(monkeypatch):
    """Gemeinsames Geruest: Send/Track/Event aufgezeichnet, Dedupe im Speicher,
    US-Session offen, PM-Fenster aus."""
    sent, tracked, events = [], [], []
    dedupe = _FakeDedupe()
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_send_email_alert",
                        lambda subject, body, **kw: sent.append({"subject": subject, "body": body, **kw}) or True)
    monkeypatch.setattr(api, "_safe_record_alert_signals",
                        lambda scanner, rows, mail_class="trade", channel="email":
                        tracked.append({"scanner": scanner, "rows": rows,
                                        "mail_class": mail_class, "channel": channel}))
    monkeypatch.setattr(api, "_record_email_event",
                        lambda subject, status, reason=None: events.append((subject, status, reason)))
    monkeypatch.setattr(api, "_email_dedupe_claim", dedupe.claim)
    monkeypatch.setattr(api, "_email_dedupe_remaining", dedupe.remaining)
    monkeypatch.setattr(api, "_email_dedupe_release", dedupe.release)
    monkeypatch.setattr(api, "_email_dedupe_mark", dedupe.mark)
    monkeypatch.setattr(api, "_stock_trade_email_status",
                        lambda *a, **k: {"allowed": True, "session": "US_REGULAR", "reason": ""})
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: False)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"AAA", "BBB", "CCC"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: None)
    # Keine Marktdaten-Caches im Unit-Test: sonst haengt der Ausgang davon ab,
    # ob zufaellig echte Cache-Dateien (z. B. fuer den echten Ticker "CCC")
    # auf der Platte liegen. Der 4H-Execution-State wird direkt auf CLEAR
    # fixiert (fail-open-Zustand eines ruhigen Marktes).
    monkeypatch.setattr(api, "_fetch_stock_swing_execution_state",
                        lambda ticker: {"Swing_4H_Execution_Checked": True,
                                        "Swing_4H_Execution_Status": "CLEAR",
                                        "Swing_4H_Execution_Reason": "unit_test"})
    return sent, tracked, events, dedupe


def test_mail_path_red_degrades_to_watch_and_shadow(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_OFF", "overall_risk_score": 70},
        summary=None, state={}, now=MON)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA"), _row("BBB")], "stocks")

    assert len(sent) == 1
    assert sent[0]["mail_class"] == "watch"                       # degradiert
    assert "🟥 MARKT-REGIME ROT" in sent[0]["body"]               # Banner sichtbar
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    trades = [t for t in tracked if t["mail_class"] == "trade"]
    assert trades == []                                           # kein Trade-Tracking
    assert len(shadow) == 1
    reasons = {r.get("block_reasons") for r in shadow[0]["rows"]}
    assert reasons == {rf.REASON_MARKET_RED}
    assert len(shadow[0]["rows"]) == 2                            # beide Setups gemessen


def test_mail_path_red_breaker_daily_cap_skips_mail(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context=None,
        summary=_summary(decided=12, win=10.0, avg_r=-0.5), state={}, now=MON)
    assert decision["layer"] == rf.LAYER_BREAKER
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)
    # Watch-Kappe bereits verbraucht (vor < 20h verschickt)
    import time as _time
    dedupe.claim("regime_cooldown_watch_stock_strategy", 20 * 3600, now=_time.time())

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA")], "stocks")

    assert sent == []                                             # keine Spam-Watch
    assert any(e[1] == "skipped" and "regime_cooldown_watch_daily_cap" in str(e[2]) for e in events)
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    assert len(shadow) == 1 and shadow[0]["rows"][0]["block_reasons"] == rf.REASON_BREAKER_COOLDOWN
    assert "stock_strategy_AAA" in dedupe.released                # Claims freigegeben


def test_mail_path_yellow_filters_and_caps(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_OFF_LIGHT"},
        summary=None, state={}, now=MON)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)
    # Die Mail-Pipeline rechnet den Aktien-Score intern als Trade-Health-Score
    # neu (_stock_alert_trade_score); synthetische Rows ohne Detailfelder
    # landen dabei alle auf demselben Default. Fuer den Filter-Test zaehlt nur,
    # dass der YELLOW-Filter gegen DEN Score arbeitet, der auch in der Mail
    # steht — deshalb Passthrough auf den Row-Score.
    monkeypatch.setattr(api, "_stock_alert_trade_score",
                        lambda row, scanner_name: int(row.get("score", 0) or 0))

    rows = [_row("AAA", 90.0), _row("BBB", 84.0), _row("CCC", 82.0)]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert len(sent) == 1
    assert sent[0]["mail_class"] == "swing_trade"                 # GELB bleibt trade-faehig
    assert "🟨 MARKT-REGIME GELB" in sent[0]["body"]
    assert "ab Score 85" in sent[0]["body"]                       # versch. Schwelle gezeigt
    assert "AAA" in sent[0]["body"] and "BBB" not in sent[0]["body"]
    trades = [t for t in tracked if t["mail_class"] == "trade"]
    assert len(trades) == 1 and len(trades[0]["rows"]) == 1       # nur der Ueberlebende
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    assert len(shadow) == 1
    assert {r["block_reasons"] for r in shadow[0]["rows"]} == {rf.REASON_MARKET_YELLOW}
    assert len(shadow[0]["rows"]) == 2                            # BBB + CCC gemessen


def test_mail_path_green_unchanged(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA")], "stocks")

    assert len(sent) == 1 and sent[0]["mail_class"] == "swing_trade"
    assert "🟥" not in sent[0]["body"] and "🟨" not in sent[0]["body"]
    assert [t["mail_class"] for t in tracked] == ["trade"]
