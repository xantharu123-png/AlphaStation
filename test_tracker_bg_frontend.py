"""Tests Signal-Tracking/Telegram-Integration bg_service + watch_mail_optin-Frontend.

Integriert gegen den festen Team-A-Kontrakt (modules.signal_tracker /
modules.notify_telegram) — alle Kontrakt-Funktionen werden gemockt, damit die
Suite auch laeuft, solange die Module noch nicht gemergt sind.

Muster (Cache schreiben, _send_email_alert mocken) aus test_mail_gates_bg.py.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Session-unabhaengig: bg_service neben dieser Datei importierbar machen.
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import bg_service  # noqa: E402


def _write_bi_cache(path, rows):
    with path.open("w", encoding="utf-8") as f:
        json.dump({"results": rows, "cached_at": time.time()}, f)


def _base_row(ticker="GOOD", **overrides):
    row = {
        "ticker": ticker,
        "BI_Grade": "S",
        "BI_Score": 120,
        "Preis": 10.05,
        "current_price": 10.05,
        "RVOL": 2.5,
        "direction": "long",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 11.0,
        "TP2": 11.8,
        "Name": "Test Corp",
        "latest_bar_change_pct": 0.4,
        "latest_bar_close_pos": 0.8,
    }
    row.update(overrides)
    return row


def _setup_bi(monkeypatch, tmp_path, rows):
    """Setup wie test_mail_gates_bg: Caches/Dedupe isolieren, Versand aufzeichnen."""
    cache_file = tmp_path / "bi_cache_long.json"
    _write_bi_cache(cache_file, rows)
    monkeypatch.setattr(bg_service, "_alert_cache_path", lambda _name: str(cache_file))
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(
        bg_service,
        "_has_open_equivalent_trade_safe",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    monkeypatch.setattr(
        bg_service, "_fetch_long_latest_intraday_state", lambda *a, **k: {}, raising=False
    )
    monkeypatch.setattr(
        bg_service,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
        raising=False,
    )
    sent_mails = []

    def _mail_recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent_mails.append({"subject": subject, "body": body_html,
                           "mail_class": mail_class, "kwargs": kwargs})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _mail_recorder)
    return sent_mails


def _mock_tracker_record(monkeypatch):
    """record_alert_signals (Team-A-Kontrakt) durch Recorder ersetzen."""
    calls = []

    def _rec(scanner_name, rows, mail_class="trade", channel="email"):
        calls.append({"scanner": scanner_name, "rows": rows,
                      "mail_class": mail_class, "channel": channel})
        return len(rows)

    monkeypatch.setattr(bg_service, "record_alert_signals", _rec, raising=False)
    return calls


# ── 1) Logging nach Versand: Original-Rows, nur bei Erfolg ─────────────────

def test_bg_entry_path_neither_sends_nor_tracks(monkeypatch, tmp_path):
    """BG-Caches duerfen weder Entry-SMTP noch Schein-Tracking erzeugen."""
    sent = _setup_bi(monkeypatch, tmp_path, [_base_row("GOOD")])
    calls = _mock_tracker_record(monkeypatch)
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []
    assert calls == []


def test_record_not_called_when_send_fails(monkeypatch, tmp_path):
    """sent=False (SMTP-Fehler) => KEIN Tracking-Eintrag."""
    _setup_bi(monkeypatch, tmp_path, [_base_row("GOOD")])
    calls = _mock_tracker_record(monkeypatch)
    monkeypatch.setattr(bg_service, "_send_email_alert", lambda *a, **k: False)
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert calls == []


def test_bg_tracker_failure_cannot_reactivate_entry_mail(monkeypatch, tmp_path):
    """Ein Trackerfehler darf den fail-closed BG-Entry-Pfad nicht reaktivieren."""
    sent = _setup_bi(monkeypatch, tmp_path, [_base_row("GOOD")])

    def _boom(*a, **k):
        raise RuntimeError("tracker kaputt")

    monkeypatch.setattr(bg_service, "record_alert_signals", _boom, raising=False)
    bg_service._check_and_alert_scan_results("bi_long", {"POLYGON_KEY": ""})
    assert sent == []


def test_nls_bg_entry_is_api_authoritative_and_never_crosses_smtp_boundary(
    monkeypatch, tmp_path
):
    """Der optionale BG-NLS-Pfad darf kein Entry-DATA/Tracker-Split erzeugen.

    Selbst ein hypothetischer SMTP-Erfolg mit anschließendem Tracker-Ausfall ist
    hier unmöglich: der BG-Worker stoppt vor beiden Side Effects.  Nur die
    getrennten Invalidierungs-Info-Updates bleiben aktiv.
    """
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_EMAIL_COOLDOWN", {})
    invalidation_calls = []
    monkeypatch.setattr(
        bg_service,
        "_alert_nls_invalidations",
        lambda rows, secrets: invalidation_calls.append((rows, secrets)),
    )

    def _smtp_data_must_not_be_reached(*args, **kwargs):
        raise AssertionError("BG NLS entry crossed the SMTP DATA boundary")

    def _tracker_must_not_be_reached(*args, **kwargs):
        raise AssertionError("BG NLS entry attempted tracker activation")

    monkeypatch.setattr(bg_service, "_send_email_alert", _smtp_data_must_not_be_reached)
    monkeypatch.setattr(
        bg_service, "record_alert_signals", _tracker_must_not_be_reached, raising=False
    )
    results = {
        "signals": [{
            "symbol": "TSTUSDT",
            "exchange": "mexc",
            "signal": {
                "grade": "S", "exh_score": 95, "timing": "SHORT NOW",
                "timing_quality": 5, "safety_ok": True, "confirmation_ok": True,
                "continuation_risk": False, "micro_required": False,
                "risk_pct": 10, "signal_quality": "tradeable",
                "rr_effective": 3.0, "entry": 1.0, "stop_loss": 1.1,
                "tp1": 0.7, "tp2": 0.6,
            },
        }]
    }
    secrets = {"POLYGON_KEY": "not-used"}
    assert bg_service._alert_nls_signals(results, secrets) is False
    assert invalidation_calls == [(results, secrets)]
    assert not Path(bg_service._EMAIL_DEDUPE_FILE).exists()


# ── 2) Telegram-Hook in _send_email_alert ──────────────────────────────────

class _FakeSMTP:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a, **k):
        pass

    def sendmail(self, *a, **k):
        pass


def _setup_telegram(monkeypatch, configured=True):
    monkeypatch.setattr(bg_service, "_BG_STARTED_AT", time.time() - 3600)
    monkeypatch.setattr(bg_service.smtplib, "SMTP_SSL", _FakeSMTP)
    tg_calls = []
    monkeypatch.setattr(bg_service, "is_telegram_configured",
                        lambda: configured, raising=False)
    monkeypatch.setattr(
        bg_service, "send_telegram_alert",
        lambda subject, text="": tg_calls.append({"subject": subject, "text": text}) or True,
        raising=False,
    )
    secrets = {"GMAIL_USER": "a@b.c", "GMAIL_APP_PASSWORD": "x",
               "ALERT_SEND_TO_SUBSCRIBERS": "0"}
    return tg_calls, secrets


def test_telegram_mirrors_trade_mail_with_final_subject(monkeypatch):
    """trade-Mail + Telegram konfiguriert => genau 1 Telegram-Call mit dem
    finalen (geprefixten) Betreff + telegram_text."""
    tg_calls, secrets = _setup_telegram(monkeypatch)
    ok = bg_service._send_email_alert("3 Top-Setups", "<b>x</b>", secrets,
                                      mail_class="trade", telegram_text="TG BODY")
    assert ok is True
    assert len(tg_calls) == 1
    assert tg_calls[0]["subject"].startswith("🚨 JETZT: ")
    assert tg_calls[0]["text"] == "TG BODY"


def test_telegram_skipped_for_info_mail(monkeypatch):
    """info-Mail (z.B. NLS-Invalidierung) => 0 Telegram-Calls, Mail trotzdem ok."""
    tg_calls, secrets = _setup_telegram(monkeypatch)
    ok = bg_service._send_email_alert("Signal invalidiert", "<b>x</b>", secrets,
                                      mail_class="info")
    assert ok is True
    assert tg_calls == []


def test_telegram_not_configured_or_failing_keeps_mail_success(monkeypatch):
    """Nicht konfiguriert => kein Call; werfendes Telegram => Mail bleibt True."""
    tg_calls, secrets = _setup_telegram(monkeypatch, configured=False)
    assert bg_service._send_email_alert("T", "<b>x</b>", secrets, mail_class="trade") is True
    assert tg_calls == []

    def _boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(bg_service, "is_telegram_configured", lambda: True, raising=False)
    monkeypatch.setattr(bg_service, "send_telegram_alert", _boom, raising=False)
    assert bg_service._send_email_alert("T2", "<b>x</b>", secrets, mail_class="trade") is True


# ── 3) _tracker_stock_fetcher (Polygon Daily) ──────────────────────────────

def test_tracker_stock_fetcher_maps_polygon_daily_bars(monkeypatch):
    monkeypatch.setattr(bg_service, "_load_secrets", lambda: {"POLYGON_KEY": "k"})
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            # 1700000000000 ms => 2023-11-14 UTC
            return {"results": [
                {"t": 1700000000000, "o": 9.5, "h": 11.0, "l": 9.0, "c": 10.5},
                {"t": 1700086400000, "o": 10.75, "h": 12.0, "l": 10.0, "c": 11.5},
                {"bad": "row"},  # tolerant ueberspringen
            ]}

    import requests

    def _fake_get(url, params=None, timeout=None, **k):
        seen["url"] = url
        seen["timeout"] = timeout
        seen["params"] = params or {}
        return _Resp()

    monkeypatch.setattr(requests, "get", _fake_get)
    bars = bg_service._tracker_stock_fetcher("aapl", "2026-06-01")
    assert bars == [
        {"date": "2023-11-14", "open": 9.5, "high": 11.0, "low": 9.0, "close": 10.5},
        {"date": "2023-11-15", "open": 10.75, "high": 12.0, "low": 10.0, "close": 11.5},
    ]
    assert "/AAPL/range/1/day/2026-06-01/" in seen["url"]
    assert seen["timeout"] == 15
    assert seen["params"].get("apiKey") == "k"
    assert seen["params"].get("adjusted") == "true"


def test_tracker_stock_fetcher_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(bg_service, "_load_secrets", lambda: {"POLYGON_KEY": "k"})

    class _Resp:
        status_code = 500

        def json(self):
            return {}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    assert bg_service._tracker_stock_fetcher("AAPL", "2026-06-01") is None

    # Netzwerk-Exception => None statt Crash
    def _net_boom(*a, **k):
        raise OSError("timeout")

    monkeypatch.setattr(requests, "get", _net_boom)
    assert bg_service._tracker_stock_fetcher("AAPL", "2026-06-01") is None
    # Ohne POLYGON_KEY => None ohne HTTP-Call
    monkeypatch.setattr(bg_service, "_load_secrets", lambda: {})
    assert bg_service._tracker_stock_fetcher("AAPL", "2026-06-01") is None


# ── 3b) _tracker_crypto_fetcher (CoinGecko-Markets-Cache) ──────────────────

def test_tracker_stock_intraday_fetcher_marks_unaligned_boundary_candle(monkeypatch):
    monkeypatch.setattr(bg_service, "_load_secrets", lambda: {"POLYGON_KEY": "k"})
    since = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    boundary_start = since.replace(second=0, microsecond=0)

    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [
                {
                    "t": int(boundary_start.timestamp() * 1000),
                    "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0,
                },
                {
                    "t": int((boundary_start + timedelta(minutes=5)).timestamp() * 1000),
                    "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0,
                },
            ]}

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Resp())
    observation = bg_service._tracker_stock_intraday_fetcher(
        "AAPL",
        since=since.isoformat(),
        until=(boundary_start + timedelta(minutes=10)).isoformat(),
    )

    assert observation["interval_complete"] is True
    assert len(observation["intervals"]) == 2
    assert observation["intervals"][0]["boundary_overlap"] is True
    assert observation["intervals"][0]["started_at"] == boundary_start.isoformat()
    assert observation["intervals"][0]["observed_at"] == (
        boundary_start + timedelta(minutes=5)
    ).isoformat()
    assert observation["intervals"][1]["boundary_overlap"] is False


def test_tracker_crypto_fetcher_matches_cache_and_strips_suffix(monkeypatch, tmp_path):
    cache = tmp_path / "cg.json"
    cache.write_text(json.dumps({"coins": [
        {"symbol": "btc", "current_price": 60000.0},
        {"symbol": "tst", "current_price": 1.23},
    ], "ts": time.time(), "pages": 4}))
    monkeypatch.setattr(bg_service, "_CG_MARKETS_CACHE_FILE", str(cache))
    # Perp-Suffix gestrippt + case-insensitive Match
    tst = bg_service._tracker_crypto_fetcher("TSTUSDT")
    assert tst["current"] == pytest.approx(1.23)
    assert tst["interval_high"] == pytest.approx(1.23)
    assert tst["interval_low"] == pytest.approx(1.23)
    assert tst["interval_complete"] is False
    assert "interval_open" not in tst
    assert tst["source"] == "coingecko_point_fallback"
    btc = bg_service._tracker_crypto_fetcher("btc")
    assert btc["current"] == pytest.approx(60000.0)
    assert btc["interval_complete"] is False
    # Kein Treffer => None
    assert bg_service._tracker_crypto_fetcher("NOPEUSDT") is None
    # Cache fehlt => None statt Crash
    monkeypatch.setattr(bg_service, "_CG_MARKETS_CACHE_FILE", str(tmp_path / "missing.json"))
    assert bg_service._tracker_crypto_fetcher("BTC") is None


# ── 4) Stuendlicher Eval-Job ───────────────────────────────────────────────

def test_tracker_crypto_fetcher_marks_unaligned_boundary_candle(monkeypatch):
    import modules.new_listing_scanner as new_listing_scanner

    since = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    boundary_start = since.replace(second=0, microsecond=0)
    monkeypatch.setattr(
        new_listing_scanner,
        "fetch_candles_for",
        lambda *args, **kwargs: [
            {
                "timestamp": boundary_start.timestamp(),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            },
            {
                "timestamp": (boundary_start + timedelta(minutes=5)).timestamp(),
                "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0,
            },
        ],
    )
    observation = bg_service._tracker_crypto_fetcher(
        "BTC",
        instrument_id="bitcoin",
        venue="binance",
        contract_symbol="BTCUSDT",
        since=since.isoformat(),
        until=(boundary_start + timedelta(minutes=10)).isoformat(),
    )

    assert observation["interval_complete"] is True
    assert len(observation["intervals"]) == 2
    assert observation["intervals"][0]["boundary_overlap"] is True
    assert observation["intervals"][0]["started_at"] == boundary_start.isoformat()
    assert observation["intervals"][1]["boundary_overlap"] is False


def test_eval_job_calls_evaluate_with_both_fetchers(monkeypatch):
    seen = {}

    def _fake_eval(
        stock_daily_fetcher=None,
        stock_intraday_fetcher=None,
        crypto_price_fetcher=None,
        now=None,
    ):
        seen["stock"] = stock_daily_fetcher
        seen["stock_intraday"] = stock_intraday_fetcher
        seen["crypto"] = crypto_price_fetcher
        return {"evaluated": 3, "closed": 1, "errors": 0}

    monkeypatch.setattr(bg_service, "evaluate_open_signals", _fake_eval, raising=False)
    stats = bg_service._run_signal_eval_job()
    assert stats == {"evaluated": 3, "closed": 1, "errors": 0}
    assert seen["stock"] is bg_service._tracker_stock_fetcher
    assert seen["stock_intraday"] is bg_service._tracker_stock_intraday_fetcher
    assert seen["crypto"] is bg_service._tracker_crypto_fetcher


def test_eval_job_missing_module_or_failure_no_crash(monkeypatch):
    """Fehlendes Team-A-Modul => sauberes Skip (None); werfendes Eval => None."""
    monkeypatch.setattr(bg_service, "evaluate_open_signals", None, raising=False)
    monkeypatch.setattr(bg_service, "_signal_eval_warned_missing", False, raising=False)
    assert bg_service._run_signal_eval_job() is None
    assert bg_service._run_signal_eval_job() is None  # zweiter Lauf ebenso still

    def _boom(**k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(bg_service, "evaluate_open_signals", _boom, raising=False)
    assert bg_service._run_signal_eval_job() is None


def test_signal_eval_registered_after_scan_set_filter_and_wired():
    """signal_eval haengt im Scheduler NACH dem H-9-Ownership-Filter
    (=> laeuft IMMER, unabhaengig von BG_SCAN_SET) und ist verdrahtet."""
    import inspect

    src = inspect.getsource(bg_service.run_service)
    i_filter = src.index("_resolve_bg_scan_set()")
    i_reg = src.index('SCHEDULE_INTERVAL["signal_eval"]')
    assert i_filter < i_reg, "signal_eval muss NACH dem BG_SCAN_SET-Filter registriert werden"
    assert 'scanner_name == "signal_eval"' in src
    assert "_run_signal_eval_job()" in src
    assert bg_service._SIGNAL_EVAL_INTERVAL_SEC == 900


# ── 5) Frontend: watch_mail_optin-Schalter ─────────────────────────────────

def test_frontend_watch_mail_optin_wired():
    html_src = (BASE_DIR / "frontend" / "index.html").read_text(encoding="utf-8")
    # mind. 2 Treffer: Laden (alertSettings-State) + Speichern (PUT-Payload)
    assert html_src.count("watch_mail_optin") >= 2
    # Speichern: Feld im PUT-Payload-Bau von /api/auth/alert-settings
    assert "watch_mail_optin: !!next.watch_mail_optin" in html_src
    # Laden: Schalter liest das Feld aus der GET-Settings-Response
    assert "alertSettings?.watch_mail_optin" in html_src
    # Label-Text des Schalters
    assert "Watchlist-Mails erhalten" in html_src
    assert "kein Einstiegssignal" in html_src
