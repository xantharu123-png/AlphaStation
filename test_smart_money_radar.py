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
    monkeypatch.setattr(smr, "fetch_whale_alerts",
                        lambda key: {"status": "disabled", "transactions": []})


def test_build_radar_writes_and_reuses_cache(monkeypatch, tmp_path):
    _mock_sections(monkeypatch)
    path = str(tmp_path / "radar.json")
    first = smr.build_radar(polygon_key="K", cache_path=path)
    assert first["cache"] == "new"
    assert set(first["sections"]) == {"etf_flows", "volume_waves", "whale_alerts"}
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
    assert set(data["sections"]) == {"etf_flows", "volume_waves", "whale_alerts"}
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
