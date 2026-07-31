#!/usr/bin/env python3
"""Tests fuer modules/smart_money_radar.py + Endpunkt (31.07.).

Deckt: Farside-HTML-Parsing (Fixture, negative Werte in Klammern),
RVOL-Wellen-Rechnung (pure, synthetische Bars), Whale-Klassifizierung,
Disabled-/Fehler-Pfade (kein Key, HTTP-Fehler), Cache (fresh/stale),
Nie-werfen-Garantie, FastAPI-Endpunkt und — als wichtigster Guard —
dass das Modul in KEINEM Scoring-/Trigger-Pfad importiert wird.

Komplett offline: HTTP wird am Modul-Rand gemockt (_http_get_*).
"""
import json
import os
import sys
from pathlib import Path

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from modules import smart_money_radar as smr


FARSIDE_FIXTURE = """
<html><body><table>
<tr><th>Date</th><th>IBIT</th><th>FBTC</th><th>Total</th></tr>
<tr><td>30 Jul 2026</td><td>210.5</td><td>45.0</td><td>402.3</td></tr>
<tr><td>29 Jul 2026</td><td>(85.2)</td><td>10.0</td><td>(120.7)</td></tr>
<tr><td>28 Jul 2026</td><td>-</td><td>-</td><td>55.1</td></tr>
<tr><td>Total</td><td>9999</td><td>9999</td><td>9999</td></tr>
</table></body></html>
"""


def _bars(vols, close=100.0):
    return [{"v": v, "c": close} for v in vols]


# ── Farside-Parsing ──────────────────────────────────────────────────────────

def test_parse_farside_flows_basic():
    rows = smr.parse_farside_btc_flows(FARSIDE_FIXTURE)
    assert len(rows) == 3
    assert rows[0] == {"date": "30 Jul 2026", "total_musd": 402.3, "ibit_musd": 210.5}
    # Klammern = negativ
    assert rows[1]["total_musd"] == -120.7
    assert rows[1]["ibit_musd"] == -85.2
    # '-' wird zu None bei IBIT, Total-Zeile am Ende wird uebersprungen
    assert rows[2]["ibit_musd"] is None
    assert all(r["date"].lower() != "total" for r in rows)


def test_parse_farside_garbage_raises():
    with pytest.raises(ValueError):
        smr.parse_farside_btc_flows("<html>keine tabelle</html>")


def test_fetch_etf_flows_ok_and_error(monkeypatch):
    monkeypatch.setattr(smr, "_http_get_text", lambda url: FARSIDE_FIXTURE)
    sec = smr.fetch_etf_flows()
    assert sec["status"] == "ok"
    assert sec["as_of"] == "30 Jul 2026"
    assert len(sec["rows"]) == 3

    def _boom(url):
        raise RuntimeError("dns kaputt")

    monkeypatch.setattr(smr, "_http_get_text", _boom)
    sec2 = smr.fetch_etf_flows()
    assert sec2["status"] == "error"
    assert "dns kaputt" in sec2["error"]
    assert sec2["rows"] == []


# ── RVOL-Wellen (pure) ───────────────────────────────────────────────────────

def test_compute_waves_flags_rvol_spike():
    bars = {
        "GLD": _bars([1000] * 20 + [3000]),   # 3x -> Welle
        "SPY": _bars([1000] * 20 + [900]),    # 0.9x -> keine
        "USO": _bars([1000] * 10),            # zu wenige Bars -> skip
    }
    waves = smr.compute_waves(bars)
    by_symbol = {w["symbol"]: w for w in waves}
    assert set(by_symbol) == {"GLD", "SPY"}
    assert by_symbol["GLD"]["wave"] is True
    assert by_symbol["GLD"]["rvol"] == 3.0
    assert by_symbol["SPY"]["wave"] is False
    assert waves[0]["symbol"] == "GLD"  # Sortierung: hoechste RVOL zuerst


def test_compute_waves_zero_volume_safe():
    waves = smr.compute_waves({"X": _bars([0] * 21)})
    assert waves == []


def test_fetch_volume_waves_disabled_without_key():
    sec = smr.fetch_volume_waves(None)
    assert sec["status"] == "disabled"
    assert sec["waves"] == []


def test_fetch_volume_waves_partial_failure(monkeypatch):
    def _fake(symbol, key, days=45):
        if symbol == "USO":
            raise RuntimeError("timeout")
        return _bars([1000] * 20 + [2500])
    monkeypatch.setattr(smr, "_polygon_daily_bars", _fake)
    sec = smr.fetch_volume_waves("KEY")
    assert sec["status"] == "ok"
    assert sec["partial_errors"]
    assert all(w["wave"] for w in sec["waves"])
    labels = {w["symbol"]: w["label"] for w in sec["waves"]}
    assert labels["GLD"] == "Gold"


# ── Whale-Alerts ─────────────────────────────────────────────────────────────

def test_classify_whale_tx():
    assert smr.classify_whale_tx({"from": {"owner_type": "wallet"},
                                  "to": {"owner_type": "exchange"}}) == "exchange_inflow"
    assert smr.classify_whale_tx({"from": {"owner_type": "exchange"},
                                  "to": {"owner_type": "wallet"}}) == "exchange_outflow"
    assert smr.classify_whale_tx({"from": {"owner_type": "wallet"},
                                  "to": {"owner_type": "wallet"}}) == "wallet_to_wallet"


def test_fetch_whale_alerts_disabled_without_key():
    sec = smr.fetch_whale_alerts(None)
    assert sec["status"] == "disabled"
    assert sec["transactions"] == []


def test_fetch_whale_alerts_ok(monkeypatch):
    def _fake(url, params=None, timeout=12):
        assert "api.whale-alert.io" in url
        return {"transactions": [
            {"symbol": "btc", "blockchain": "bitcoin", "amount_usd": 12_000_000,
             "from": {"owner_type": "wallet"}, "to": {"owner_type": "exchange"},
             "timestamp": 1_800_000_000, "hash": "abc"},
        ]}
    monkeypatch.setattr(smr, "_http_get_json", _fake)
    sec = smr.fetch_whale_alerts("KEY")
    assert sec["status"] == "ok"
    assert sec["summary"] == {"count": 1, "exchange_inflow": 1, "exchange_outflow": 0}
    assert sec["transactions"][0]["kind"] == "exchange_inflow"


# ── build_radar: Cache + Nie-werfen ─────────────────────────────────────────

def _mock_sections(monkeypatch):
    monkeypatch.setattr(smr, "fetch_etf_flows",
                        lambda: {"status": "ok", "rows": [{"date": "d", "total_musd": 1.0}]})
    monkeypatch.setattr(smr, "fetch_volume_waves",
                        lambda key: {"status": "ok", "waves": [{"symbol": "GLD", "wave": True}]})
    monkeypatch.setattr(smr, "fetch_stock_waves",
                        lambda key, history_path=None: {"status": "ok", "waves": []})
    monkeypatch.setattr(smr, "fetch_insider_trades",
                        lambda: {"status": "ok", "trades": []})
    monkeypatch.setattr(smr, "fetch_insider_clusters",
                        lambda history_path=None: {"status": "ok", "clusters": []})
    monkeypatch.setattr(smr, "fetch_whale_alerts",
                        lambda key: {"status": "disabled", "transactions": []})


def test_build_radar_writes_and_reuses_cache(monkeypatch, tmp_path):
    _mock_sections(monkeypatch)
    path = str(tmp_path / "radar.json")
    first = smr.build_radar(polygon_key="K", cache_path=path)
    assert first["cache"] == "new"
    assert set(first["sections"]) == {"etf_flows", "volume_waves", "stock_waves",
                                      "insider_trades", "insider_clusters",
                                      "whale_alerts"}
    assert "kein Trigger" in first["disclaimer"] or "Kein Signal" in first["disclaimer"]
    # Zweiter Aufruf: aus dem Cache (Fetcher wuerden crashen, falls aufgerufen)
    monkeypatch.setattr(smr, "fetch_etf_flows",
                        lambda: pytest.fail("Cache nicht genutzt"))
    second = smr.build_radar(polygon_key="K", cache_path=path)
    assert second["cache"] == "fresh"


def test_build_radar_force_refresh_and_stale_fallback(monkeypatch, tmp_path):
    _mock_sections(monkeypatch)
    path = str(tmp_path / "radar.json")
    smr.build_radar(polygon_key="K", cache_path=path)

    def _boom(*a, **k):
        raise RuntimeError("alles kaputt")
    # Katastrophaler Fall: Cache-Datei korrupt + Fetcher werfen
    (tmp_path / "radar.json").write_text("{kaputt", encoding="utf-8")
    monkeypatch.setattr(smr, "fetch_etf_flows", _boom)
    monkeypatch.setattr(smr, "fetch_volume_waves", _boom)
    monkeypatch.setattr(smr, "fetch_whale_alerts", _boom)
    radar = smr.build_radar(polygon_key="K", cache_path=path, force_refresh=True)
    assert radar["cache"] in ("new", "error")  # kein Exception-Pfad nach aussen
    assert "disclaimer" in radar


def test_build_radar_never_raises_without_anything(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(smr, "fetch_etf_flows", _boom)
    monkeypatch.setattr(smr, "fetch_volume_waves", _boom)
    monkeypatch.setattr(smr, "fetch_whale_alerts", _boom)
    radar = smr.build_radar(polygon_key="K", cache_path=str(tmp_path / "x.json"))
    assert "disclaimer" in radar  # lebt noch


# ── Endpunkt ─────────────────────────────────────────────────────────────────

def test_api_endpoint_smoke(monkeypatch, tmp_path):
    import api
    monkeypatch.setattr(smr, "RADAR_CACHE_PATH", str(tmp_path / "radar.json"))
    _mock_sections(monkeypatch)
    if api._smart_money_build_radar is None:
        pytest.skip("Modul-Import in api.py fehlgeschlagen")
    resp = api.get_smart_money_radar(refresh=1)
    assert resp.status_code == 200
    data = json.loads(resp.body)
    assert set(data["sections"]) == {"etf_flows", "volume_waves", "stock_waves",
                                     "insider_trades", "insider_clusters",
                                     "whale_alerts"}
    assert data["disclaimer"]


def test_smart_money_page_served():
    import asyncio
    import api
    resp = asyncio.run(api.serve_smart_money_page())
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "Smart-Money-Radar" in html
    assert "kein Signal" in html


# ── GUARD: Radar ist NIE Teil eines Trigger-/Scoring-Pfads ──────────────────

def test_radar_not_imported_in_trigger_paths():
    """modules.smart_money_radar darf nur in api.py (Endpunkt) und eigenen
    Tests importiert werden — niemals in bg_service, Scannern, Tracker,
    Scoring- oder Mail-Modulen."""
    root = Path(_DIR)
    offenders = []
    allowed = {"api.py", "test_smart_money_radar.py", "smart_money_radar.py"}
    for py in list(root.glob("*.py")) + list((root / "modules").glob("*.py")):
        if py.name in allowed or py.name.startswith("test_"):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "smart_money_radar" in src:
            offenders.append(py.name)
    assert offenders == [], f"Radar-Import in Trigger-Pfad gefunden: {offenders}"


def test_radar_disclaimer_present_in_artifact(monkeypatch, tmp_path):
    _mock_sections(monkeypatch)
    radar = smr.build_radar(polygon_key="K", cache_path=str(tmp_path / "r.json"))
    assert "Kein Signal" in radar["disclaimer"]


# ── Monster-Volumen Aktien (Snapshot + eigene Baseline, 31.07.) ─────────────

def _row(ticker, volume, close=100.0, change_pct=1.0):
    return {"ticker": ticker, "volume": float(volume),
            "close": float(close), "change_pct": change_pct}


def test_update_volume_history_rolling_and_idempotent():
    hist = None
    # 35 Tage einschreiben => auf 30 gekuerzt, aelteste raus
    for i in range(35):
        date = f"2026-06-{i + 1:02d}"
        hist = smr.update_volume_history(hist, date, [_row("AAA", 1000 + i)])
    assert len(hist["dates"]) == 30
    assert "2026-06-01" not in hist["dates"]
    assert hist["dates"][-1] == "2026-06-35"
    # Idempotent: gleicher Tag nochmal => keine Dublette, Wert aktualisiert
    n = len(hist["dates"])
    hist = smr.update_volume_history(hist, "2026-06-35", [_row("AAA", 9999)])
    assert len(hist["dates"]) == n
    assert hist["volumes"]["AAA"]["2026-06-35"] == 9999.0


def test_history_to_lists_excludes_today_and_orders():
    hist = {"dates": ["2026-07-28", "2026-07-29", "2026-07-30"],
            "volumes": {"AAA": {"2026-07-28": 1.0, "2026-07-30": 3.0},
                        "BBB": {"2026-07-30": 5.0}}}
    lists = smr.history_to_lists(hist, "2026-07-30")
    assert lists == {"AAA": [1.0]}
    lists_today = smr.history_to_lists(hist, "2026-07-31")
    assert lists_today["AAA"] == [1.0, 3.0]
    assert lists_today["BBB"] == [5.0]


def test_compute_stock_waves_rvol_filter_direction():
    today = [
        _row("BIG", 3_000_000, close=100.0, change_pct=4.2),   # 300M$, RVOL 3x -> Welle up
        _row("DUMP", 2_000_000, close=100.0, change_pct=-6.1), # 200M$, RVOL 2x -> Welle down
        _row("SMALL", 100_000, close=100.0, change_pct=9.9),   # 10M$ -> Filter
        _row("NEW", 5_000_000, close=100.0, change_pct=1.0),   # keine Baseline -> rvol None
    ]
    hist = {"BIG": [1_000_000.0] * 20, "DUMP": [1_000_000.0] * 20, "SMALL": [1.0] * 20}
    waves, baseline = smr.compute_stock_waves(today, hist)
    assert baseline == 20
    tickers = [w["ticker"] for w in waves]
    assert "SMALL" not in tickers  # $-Filter
    assert tickers[0] == "BIG"     # hoechste RVOL zuerst
    big = waves[0]
    assert big["rvol"] == 3.0 and big["wave"] is True and big["direction"] == "up"
    dump = waves[1]
    assert dump["direction"] == "down" and dump["wave"] is True
    new = [w for w in waves if w["ticker"] == "NEW"][0]
    assert new["rvol"] is None and new["wave"] is False


def test_fetch_stock_waves_disabled_without_key():
    sec = smr.fetch_stock_waves(None)
    assert sec["status"] == "disabled"
    assert sec["waves"] == []


def test_fetch_stock_waves_building_then_maturing(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _fake_snapshot(key):
        calls["n"] += 1
        return "2026-07-31", [_row("AAA", 3_000_000, close=100.0, change_pct=2.5)]
    monkeypatch.setattr(smr, "_polygon_market_snapshot", _fake_snapshot)
    hist_path = str(tmp_path / "volumes.json")

    sec = smr.fetch_stock_waves("KEY", history_path=hist_path)
    assert sec["status"] == "ok"
    assert sec["building"] is True            # 0 Baseline-Tage
    assert sec["baseline_days"] == 0
    assert "Baseline im Aufbau" in sec["note"]
    assert sec["waves"][0]["rvol"] is None    # noch keine Referenz
    # History wurde geschrieben
    stored = json.loads(open(hist_path, encoding="utf-8").read())
    assert stored["volumes"]["AAA"]["2026-07-31"] == 3_000_000.0

    # Zweiter (spaeterer) Tag: Baseline 1 -> weiter building, aber Werte da
    def _fake_snapshot2(key):
        return "2026-08-01", [_row("AAA", 9_000_000, close=100.0, change_pct=5.0)]
    monkeypatch.setattr(smr, "_polygon_market_snapshot", _fake_snapshot2)
    sec2 = smr.fetch_stock_waves("KEY", history_path=hist_path)
    assert sec2["baseline_days"] == 1
    assert sec2["waves"][0]["rvol"] is None   # < 3 Baseline-Tage -> noch None


def test_fetch_stock_waves_full_baseline_flags_wave(monkeypatch, tmp_path):
    hist = {"dates": [f"2026-07-{d:02d}" for d in range(1, 21)],
            "volumes": {"AAA": {f"2026-07-{d:02d}": 1_000_000.0 for d in range(1, 21)}}}
    path = tmp_path / "volumes.json"
    path.write_text(json.dumps(hist), encoding="utf-8")
    monkeypatch.setattr(smr, "_polygon_market_snapshot",
                        lambda key: ("2026-07-31", [_row("AAA", 5_000_000, close=100.0)]))
    sec = smr.fetch_stock_waves("KEY", history_path=str(path))
    assert sec["building"] is False
    assert sec["waves"][0]["rvol"] == 5.0
    assert sec["waves"][0]["wave"] is True


def test_fetch_stock_waves_error_never_raises(monkeypatch, tmp_path):
    def _boom(key):
        raise RuntimeError("polygon down")
    monkeypatch.setattr(smr, "_polygon_market_snapshot", _boom)
    sec = smr.fetch_stock_waves("KEY", history_path=str(tmp_path / "v.json"))
    assert sec["status"] == "error"
    assert "polygon down" in sec["error"]
    assert sec["waves"] == []


# ── Insider-Trades (SEC EDGAR Form 4, 31.07.) ────────────────────────────────

ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <title>ACME Corp (ACME)</title>
  <link href="https://www.sec.gov/Archives/edgar/data/123/0001-26-000001-index.htm"/>
  <updated>2026-07-31T08:00:00-04:00</updated>
</entry>
<entry>
  <title>BETA Inc (BETA)</title>
  <link href="https://www.sec.gov/Archives/edgar/data/456/0002-26-000002-index.htm"/>
  <updated>2026-07-31T07:55:00-04:00</updated>
</entry>
</feed>"""

FORM4_FIXTURE = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerName><value>ACME Corp</value></issuerName>
    <issuerTradingSymbol><value>ACME</value></issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName><value>Doe Jane</value></rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-30</value></transactionDate>
      <transactionCoding><transactionCode><value>P</value></transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-30</value></transactionDate>
      <transactionCoding><transactionCode><value>A</value></transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-29</value></transactionDate>
      <transactionCoding><transactionCode><value>S</value></transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>20</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_atom_feed_basic():
    entries = smr.parse_atom_feed(ATOM_FIXTURE)
    assert len(entries) == 2
    assert entries[0]["title"] == "ACME Corp (ACME)"
    assert entries[0]["link"].endswith("-index.htm")
    with pytest.raises(ValueError):
        smr.parse_atom_feed("<feed xmlns='http://www.w3.org/2005/Atom'/>")


def test_parse_form4_xml_filters_and_fields():
    rows = smr.parse_form4_xml(FORM4_FIXTURE, link="L")
    assert len(rows) == 1  # A (Grant) und S unter $100k fallen raus
    r = rows[0]
    assert r["kind"] == "buy"
    assert r["insider"] == "Doe Jane"
    assert r["title"] == "Director"
    assert r["ticker"] == "ACME"
    assert r["value_usd"] == 255000.0
    assert r["date"] == "2026-07-30"
    assert r["link"] == "L"


def test_parse_form4_xml_sell_direction():
    xml = FORM4_FIXTURE.replace('<transactionCode><value>P</value></transactionCode>',
                                '<transactionCode><value>S</value></transactionCode>')
    rows = smr.parse_form4_xml(xml)
    assert rows[0]["kind"] == "sell"


def test_fetch_insider_trades_e2e_mocked(monkeypatch):
    class _Resp:
        def __init__(self, text):
            self.text = text
        def raise_for_status(self):
            return None

    def _fake_get(url, timeout=12, headers=None):
        assert "AlphaStation" in (headers or {}).get("User-Agent", "")
        if "browse-edgar" in url:
            return _Resp(ATOM_FIXTURE)
        return _Resp(FORM4_FIXTURE)

    monkeypatch.setattr(smr.requests, "get", _fake_get)
    monkeypatch.setattr(smr, "_filing_xml_url", lambda link: "https://x/y.xml")
    sec = smr.fetch_insider_trades()
    assert sec["status"] == "ok"
    assert sec["trades"][0]["ticker"] == "ACME"
    assert sec["trades"][0]["kind"] == "buy"
    assert "SEC EDGAR" in sec["note"]


def test_fetch_insider_trades_empty_when_no_big_deals(monkeypatch):
    xml_small = FORM4_FIXTURE.replace("<value>10000</value>", "<value>10</value>")
    monkeypatch.setattr(smr, "_filing_xml_url", lambda link: "https://x/y.xml")

    class _Resp:
        def __init__(self, text):
            self.text = text
        def raise_for_status(self):
            return None

    def _fake_get(url, timeout=12, headers=None):
        return _Resp(ATOM_FIXTURE if "browse-edgar" in url else xml_small)
    monkeypatch.setattr(smr.requests, "get", _fake_get)
    sec = smr.fetch_insider_trades()
    assert sec["status"] == "empty"
    assert sec["trades"] == []


def test_fetch_insider_trades_error_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("sec.gov down")
    monkeypatch.setattr(smr.requests, "get", _boom)
    sec = smr.fetch_insider_trades()
    assert sec["status"] == "error"
    assert "sec.gov down" in sec["error"]
    assert sec["trades"] == []


# ── Insider-Cluster (31.07.) ─────────────────────────────────────────────────

def _ins_trade(ticker, insider, kind, date, value=200_000.0, link="L1"):
    return {"issuer": f"{ticker} Corp", "ticker": ticker, "insider": insider,
            "title": "Director", "kind": kind, "shares": 1000.0,
            "price": value / 1000.0, "value_usd": value, "date": date, "link": link}


def test_update_insider_history_dedupe_and_prune():
    trades = [_ins_trade("AAA", "X", "buy", "2026-07-30"),
              _ins_trade("AAA", "X", "buy", "2026-07-30")]  # exaktes Duplikat
    hist = smr.update_insider_history(None, trades, "2026-07-31")
    assert len(hist["trades"]) == 1  # Dedupe
    # Alt-Deal (> 45 Tage) fliegt raus
    old = [_ins_trade("OLD", "Y", "buy", "2026-06-01", link="L2")]
    hist = smr.update_insider_history(hist, old, "2026-07-31")
    keys = list(hist["trades"])
    assert not any(k.startswith("L2") for k in keys)
    assert any(k.startswith("L1") for k in keys)


def test_detect_clusters_buy_three_insiders():
    trades = [
        _ins_trade("AAA", "Doe", "buy", "2026-07-28", 300_000),
        _ins_trade("AAA", "Smith", "buy", "2026-07-29", 150_000, link="L2"),
        _ins_trade("AAA", "Chen", "buy", "2026-07-30", 500_000, link="L3"),
        _ins_trade("AAA", "Chen", "buy", "2026-07-30", 900_000, link="L4"),  # doppelt zählt als 1 Insider
        _ins_trade("BBB", "Solo", "buy", "2026-07-30", 800_000, link="L5"),  # nur 1 Insider
        _ins_trade("AAA", "Old", "buy", "2026-07-01", 400_000, link="L6"),   # ausserhalb Fenster
    ]
    res = smr.detect_insider_clusters(trades, today="2026-07-31")
    assert len(res["clusters"]) == 1
    c = res["clusters"][0]
    assert c["symbol"] == "AAA" and c["side"] == "buy"
    assert c["insiders"] == 3           # Chen nur einmal gezaehlt
    assert c["trades"] == 4
    assert c["total_value_usd"] == 1_850_000.0
    assert set(c["names"]) == {"Doe", "Smith", "Chen"}


def test_detect_clusters_sell_side_and_sorting():
    trades = [
        _ins_trade("SELL", f"V{i}", "sell", "2026-07-30", 100_000, link=f"S{i}")
        for i in range(3)
    ] + [
        _ins_trade("BUY", f"K{i}", "buy", "2026-07-30", 100_000, link=f"B{i}")
        for i in range(3)
    ]
    res = smr.detect_insider_clusters(trades, today="2026-07-31")
    assert len(res["clusters"]) == 2
    assert res["clusters"][0]["side"] == "buy"   # Kauf-Cluster zuerst
    assert res["clusters"][1]["side"] == "sell"


def test_detect_clusters_below_threshold_empty():
    trades = [_ins_trade("AAA", "A", "buy", "2026-07-30"),
              _ins_trade("AAA", "B", "buy", "2026-07-30", link="L2")]
    res = smr.detect_insider_clusters(trades, today="2026-07-31")
    assert res["clusters"] == []


def test_fetch_insider_clusters_building_then_cluster(monkeypatch, tmp_path):
    path = str(tmp_path / "insider_hist.json")
    monkeypatch.setattr(smr, "_fetch_latest_form4_trades",
                        lambda: ([_ins_trade("AAA", "X", "buy", "2026-07-30")], 1, 0))
    sec = smr.fetch_insider_clusters(history_path=path)
    assert sec["status"] == "building"   # zu wenig Verlauf-Tage
    assert sec["building"] is True
    assert "Verlauf im Aufbau" in sec["note"]
    # Verlauf-Datei wurde geschrieben
    assert json.loads(open(path, encoding="utf-8").read())["trades"]

    # Vorbefuellter Verlauf mit echtem Cluster + EDGAR down -> Verlauf allein reicht
    trades = {
        f"k{i}": _ins_trade("AAA", f"I{i}", "buy", "2026-07-29", link=f"L{i}")
        for i in range(3)
    }
    path2 = tmp_path / "hist2.json"
    path2.write_text(json.dumps({"trades": trades, "updated": "2026-07-30"}),
                     encoding="utf-8")
    monkeypatch.setattr(smr, "_fetch_latest_form4_trades",
                        lambda: (_ for _ in ()).throw(RuntimeError("edgar down")))
    sec2 = smr.fetch_insider_clusters(history_path=str(path2))
    assert sec2["clusters"][0]["symbol"] == "AAA"  # EDGAR-Ausfall toleriert
