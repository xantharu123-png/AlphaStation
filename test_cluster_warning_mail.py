"""Klumpenrisiko-Warnung in Aktien-Signal-Mails (ADR-Cluster + Mehrfach-Mover).

Anlass 11.06.: Zwei Alert-Mails enthielten zusammen 5 argentinische ADRs
(BMA, GGAL, CEPU, TGS, IRS) als "separate" Setups — derselbe Makro-Treiber,
aber kein Hinweis. Abonnenten haetten 3x Size auf EINEN Trade legen koennen.

Abgedeckt:
- _cluster_warning_html: Regel A (>=2 ADRs), Regel B (>=3 Mover |Change|>5%),
  beide Regeln gemeinsam, Schwellen-Unterkanten, leere Liste/Muell.
- _adr_ticker_set: neues + altes Cache-Format (abwaertskompatibel, kein
  Re-Fetch-Zwang), Fehlerpfad wirft nie.
- End-to-End: gemockter Versand fuer _send_strategy_scan_alerts (Sweep) und
  _check_and_alert (bi/biotech/bear-Pfad) — Muster aus test_mail_class_api.py
  bzw. test_email_alert_audit.py.

Kein Netz: Universe-Loader, ADR-Set, Session-Gates und Versand sind gemockt.
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api  # noqa: E402


ADR_SET = {"BMA", "GGAL", "CEPU", "TGS", "IRS"}


@pytest.fixture(autouse=True)
def _isolate_mail_state(monkeypatch, tmp_path):
    """Isolations-Muster wie test_mail_class_api.py — kein State-Leak zwischen Tests."""
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)


def _patch_adr_set(monkeypatch, tickers):
    monkeypatch.setattr(api, "_adr_ticker_set", lambda: set(tickers))


# ── _cluster_warning_html: Regel A (ADR-Cluster) ──

def test_regel_a_zwei_adr_rows_warnen_mit_beiden_tickern(monkeypatch):
    _patch_adr_set(monkeypatch, ADR_SET)
    html = api._cluster_warning_html([
        {"ticker": "BMA", "change_pct": 2.0},
        {"ticker": "GGAL", "change_pct": 1.5},
    ])
    assert "Klumpenrisiko: 2 Setups" in html
    assert "BMA, GGAL" in html
    assert "EINEN Trade behandeln" in html
    assert "Tages-Move" not in html  # Regel B darf hier nicht feuern


def test_regel_a_ein_adr_row_keine_warnung(monkeypatch):
    _patch_adr_set(monkeypatch, ADR_SET)
    assert api._cluster_warning_html([
        {"ticker": "BMA", "change_pct": 2.0},
        {"ticker": "AAPL", "change_pct": 1.0},
    ]) == ""


# ── _cluster_warning_html: Regel B (Mehrfach-Mover) ──

def test_regel_b_drei_starke_mover_feldtolerant(monkeypatch):
    _patch_adr_set(monkeypatch, set())  # keine ADRs => isoliert Regel B
    html = api._cluster_warning_html([
        {"ticker": "AAA", "change_pct": 6.0},
        {"ticker": "BBB", "Change %": 8.0},  # UI-Spaltenname
        {"ticker": "CCC", "change": 9.0},    # Kleinschreibung
    ])
    assert "3 Setups mit starkem Tages-Move" in html
    assert "gemeinsamen" in html and "Treiber" in html
    assert "Klumpenrisiko" not in html


def test_regel_b_zwei_mover_keine_warnung(monkeypatch):
    _patch_adr_set(monkeypatch, set())
    assert api._cluster_warning_html([
        {"ticker": "AAA", "change_pct": 7.0},
        {"ticker": "BBB", "change_pct": 9.0},
        {"ticker": "CCC", "change_pct": 4.9},
    ]) == ""


def test_beide_regeln_gleichzeitig_zwei_zeilen(monkeypatch):
    _patch_adr_set(monkeypatch, ADR_SET)
    html = api._cluster_warning_html([
        {"ticker": "BMA", "change_pct": 7.0},
        {"ticker": "GGAL", "change_pct": -6.5},  # |Change| zaehlt auch short
        {"ticker": "AAPL", "change_pct": 9.0},
    ])
    assert "Klumpenrisiko: 2 Setups" in html and "BMA, GGAL" in html
    assert "3 Setups mit starkem Tages-Move" in html


def test_leere_liste_und_muell_geben_leeren_string(monkeypatch):
    _patch_adr_set(monkeypatch, ADR_SET)
    assert api._cluster_warning_html([]) == ""
    assert api._cluster_warning_html(None) == ""
    assert api._cluster_warning_html(["kein-dict", 42]) == ""


def test_batch_hint_adds_only_hypothetical_plan_r_without_suppressing_rows(monkeypatch):
    _patch_adr_set(monkeypatch, set())
    rows = [
        _sweep_row("AAA", idx=0),
        _sweep_row("BBB", idx=1),
        _sweep_row("CCC", idx=2),
    ]
    rows[0].update(group_key="TECH", group_verified=True)
    rows[1].update(group_key="TECH", group_verified=False)

    html = api._cluster_warning_html(rows)

    assert "Hypothetische Batch-Belastung" in html
    assert "3 gültige Pläne = 3R" in html
    assert "LONG 3R" in html
    assert "Verifizierte Gruppen: TECH 1R" in html
    assert "TECH 2R" not in html
    assert "$" not in html and "USD" not in html
    assert "unterdr" not in html.lower()


# ── _adr_ticker_set: Cache-Formate + Fehlerpfad ──

def test_adr_set_neues_cache_format_und_altes_format_kompatibel(monkeypatch, tmp_path):
    cache = tmp_path / "universe.json"
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache))
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "adr_tickers", None)
    cache.write_text(json.dumps({
        "cached_at": time.time(),
        "tickers": ["AAPL", "BMA"],
        "adr_tickers": ["BMA", "GGAL"],
    }))
    assert api._adr_ticker_set() == {"BMA", "GGAL"}

    # Altes Cache-Format ohne Feld => leeres Set, und der Universe-Loader
    # liefert weiterhin Ticker aus dem File-Cache (KEIN Re-Fetch-Zwang).
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "adr_tickers", None)
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "tickers", None)
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "loaded_at", 0)
    cache.write_text(json.dumps({"cached_at": time.time(), "tickers": ["AAPL", "BMA"]}))
    tickers, source = api._load_common_stock_universe()
    assert tickers == {"AAPL", "BMA"}
    assert source == "file_cache"
    assert api._adr_ticker_set() == set()


def test_adr_set_fehlerpfad_kaputtes_cache_file_wirft_nie(monkeypatch, tmp_path):
    cache = tmp_path / "kaputt.json"
    cache.write_text("{NOT JSON")
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache))
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "adr_tickers", None)
    assert api._adr_ticker_set() == set()


def test_mail_bau_crasht_nie_wenn_adr_quelle_wirft(monkeypatch):
    def _boom():
        raise RuntimeError("ADR-Quelle kaputt")

    monkeypatch.setattr(api, "_adr_ticker_set", _boom)
    # Regel B funktioniert trotzdem — kein Wurf, kein Mail-Abbruch:
    html = api._cluster_warning_html([
        {"ticker": "AAA", "change_pct": 6.0},
        {"ticker": "BBB", "change_pct": 8.0},
        {"ticker": "CCC", "change_pct": 9.0},
    ])
    assert "3 Setups mit starkem Tages-Move" in html
    assert "Klumpenrisiko" not in html


# ── End-to-End: Strategie-Sweep-Mail (_send_strategy_scan_alerts) ──

def _sweep_row(ticker, change_pct=3.5, idx=0):
    """Alertbare Row — gates-gruenes Muster aus test_email_alert_audit.py."""
    return {
        "Ticker": ticker,
        "grade": "A",
        "score": 90,
        "RVOL": 2.0,
        "Preis": 10.0 + idx,
        "current_price": 10.0 + idx,
        "change_pct": change_pct,
        "close_pos": 0.8,
        "Signal_Direction": "LONG",
        "trade_setup": {
            "direction": "LONG",
            "entry": 10.0 + idx,
            "stop": 9.5 + idx,
            "tp1": 10.75 + idx,
            "tp2": 11.0 + idx,
        },
    }


def _mock_sweep_env(monkeypatch, universe):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: (set(universe), "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: None)
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
    )
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {
        "allowed": True, "session": "US_REGULAR", "reason": "unit-test market open",
    })
    return sent


def test_e2e_sweep_mail_mit_drei_adrs_enthaelt_warnzeile(monkeypatch):
    sent = _mock_sweep_env(monkeypatch, {"BMA", "GGAL", "CEPU"})
    _patch_adr_set(monkeypatch, ADR_SET)
    rows = [_sweep_row("BMA", idx=0), _sweep_row("GGAL", idx=1), _sweep_row("CEPU", idx=2)]

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert len(sent) == 3
    for _subject, body in sent:
        assert "Klumpenrisiko: 3 Setups" in body
        assert "BMA, GGAL, CEPU" in body
        assert "EINEN Trade behandeln" in body
    # Produktentscheidung: KEINE Unterdrueckung — alle Setups bleiben in der Mail.
    for ticker in ("BMA", "GGAL", "CEPU"):
        assert sum(f"<b>{ticker}</b>" in body for _, body in sent) == 1


def test_e2e_sweep_mail_mit_normaler_row_ohne_warnzeile(monkeypatch):
    sent = _mock_sweep_env(monkeypatch, {"AAPL"})
    _patch_adr_set(monkeypatch, ADR_SET)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_sweep_row("AAPL")], "stocks")

    assert len(sent) == 1
    assert "Klumpenrisiko" not in sent[0][1]
    assert "Tages-Move" not in sent[0][1]


def test_e2e_sweep_single_wires_keep_three_mover_cluster_context(monkeypatch):
    sent = _mock_sweep_env(monkeypatch, {"AAA", "BBB", "CCC"})
    _patch_adr_set(monkeypatch, set())
    rows = [
        _sweep_row("AAA", change_pct=6.0),
        _sweep_row("BBB", change_pct=-7.0, idx=1),
        _sweep_row("CCC", change_pct=8.0, idx=2),
    ]

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert len(sent) == 3
    assert all("3 Setups mit starkem Tages-Move" in body for _, body in sent)


# ── End-to-End: generische Top-Setup-Mail (_check_and_alert, bi/biotech/bear-Pfad) ──

def test_e2e_check_and_alert_adr_cluster_warnzeile(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append((subject, body)) or True)
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *a, **k: (True, "unit"))
    # bi_long wuerde sonst frischen 5m-Intraday-State fetchen => offline halten:
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda scanner, row, *a, **k: row)
    monkeypatch.setattr(
        api, "_classify_alert_candidate",
        lambda scanner, row, now=None: {
            "ticker": api._extract_alert_ticker(row),
            "grade": "S",
            "score": 95,
            "rvol": 3.0,
            "price": row.get("price"),
            "alertable_now": True,
            "suppression_reasons": [],
        },
    )
    _patch_adr_set(monkeypatch, ADR_SET)
    cache = tmp_path / "bi_cache.json"
    cache.write_text(json.dumps({"results": [
        {"ticker": "TGS", "grade": "S", "score": 95, "price": 30.0, "rvol": 3.0, "change_pct": 4.0},
        {"ticker": "IRS", "grade": "S", "score": 95, "price": 12.0, "rvol": 3.0, "change_pct": 3.0},
    ]}))

    api._check_and_alert("bi_long", str(cache))

    assert len(sent) == 1
    body = sent[0][1]
    # Actionable stock alerts are intentionally one setup per wire message so
    # final quote/path evidence remains adjacent to SMTP acceptance. Cluster
    # analysis is still unit-tested for non-actionable summaries, but must not
    # force two independently executable trades into a stale batch.
    assert "Klumpenrisiko: 2 Setups" not in body
    assert "TGS" in body
    assert "IRS" not in body
