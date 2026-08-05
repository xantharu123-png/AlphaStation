# -*- coding: utf-8 -*-
"""Regressionstests fuer die Nachaudit-Fixes vom 21.07.2026.

Deckt die im Nachaudit (AUDIT_NACHAUDIT_CODEX_2026-07-21.md) markierten
roten Blocker und gelben Kleinfixes ab:
N1 (Bear-ATR-Chronologie), N2 (NLS-TypeError), N3 (LVN-Overhead-Filter),
H7-Rest (Quick-Scan rvol_direction), M9 (LSD-/Stable-Ausschluesse +
Symbol-Kollision), M16-Rest (1x-Short/VIX-Decay), M18-Rest (Tracker-Cache-TTL),
N6/N7 (Downgrade-Konsistenz), N8 (Projektions-Guard erste 15 Min),
N10 (Mikro-Preis-Toleranz), N11 (int(None)-Falle), H3-Rest (failed_fetch_days),
L8 (Monitoring-Purge), H8 (Dreieck mit exakt 3 Swings), H9 (Downside-Z-Score),
Biotech-News-Reihenfolge, pos_90d-or-Falle.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent


# ─────────────────────────────────────────────────────────────────────────────
# N1: Wilder-ATR muss chronologisch rechnen — auch bei sort=desc-Input
# ─────────────────────────────────────────────────────────────────────────────

def _make_bars_with_crash(n_calm=55, n_crash=5):
    bars = []
    price = 100.0
    for i in range(n_calm):
        o = price
        c = price * (1.0 + (0.002 if i % 2 == 0 else -0.002))
        h = max(o, c) * 1.004
        low = min(o, c) * 0.996
        bars.append({"t": 1_700_000_000_000 + i * 86_400_000,
                     "o": o, "h": h, "l": low, "c": c, "v": 1_000_000})
        price = c
    for j in range(n_crash):
        o = price
        c = price * 0.90
        h = o * 1.01
        low = c * 0.97
        bars.append({"t": 1_700_000_000_000 + (n_calm + j) * 86_400_000,
                     "o": o, "h": h, "l": low, "c": c, "v": 5_000_000})
        price = c
    return bars


def test_wilder_atr_identisch_fuer_asc_und_desc_bars():
    from modules.vrvp_levels import calculate_wilder_atr

    asc = _make_bars_with_crash()
    desc = list(reversed(asc))
    atr_asc = calculate_wilder_atr(asc, period=14, lookback=60)
    atr_desc = calculate_wilder_atr(desc, period=14, lookback=60)
    assert atr_asc > 0
    # Defensiv-Sortierung: desc-Input darf das Ergebnis nicht mehr veraendern.
    assert atr_desc == pytest.approx(atr_asc, rel=1e-12)
    # Und der Crash am Ende muss die ATR dominieren (Recency), nicht die
    # ruhige Vergangenheit: ATR des Gesamtfensters >> ATR des ruhigen Teils.
    atr_calm = calculate_wilder_atr(asc[:55], period=14, lookback=55)
    assert atr_asc > atr_calm * 1.5


def test_bear_history_wird_chronologisiert_source():
    src = (REPO / "api.py").read_text(encoding="utf-8", errors="replace")
    assert "history_bars = list(reversed(bars)) if isinstance(bars, list) else []" in src, (
        "Bear-Pfad muss die sort=desc-Polygon-Bars vor der ATR-Berechnung umdrehen (N1)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# N2: NLS-Exhaustion darf bei None-Volumen-Baseline nicht crashen
# ─────────────────────────────────────────────────────────────────────────────

def _nls_candle(o, h, low, c, v, ts):
    return {"open": o, "high": h, "low": low, "close": c, "volume": v, "timestamp": ts}


def test_nls_exhaustion_ueberlebt_duenne_volumenhistorie():
    from modules.new_listing_scanner import calculate_listing_exhaustion

    now_ms = int(time.time() * 1000)
    # 5 Kerzen, eine davon 0-Volumen: die Haelften-Baselines koennen None
    # werden — vorher TypeError (`None > 0`), jetzt sauber "nicht messbar".
    candles = [
        _nls_candle(1.00, 1.30, 0.99, 1.25, 1000, now_ms - 5 * 3600_000),
        _nls_candle(1.25, 1.60, 1.20, 1.55, 0, now_ms - 4 * 3600_000),
        _nls_candle(1.55, 1.90, 1.50, 1.80, 900, now_ms - 3 * 3600_000),
        _nls_candle(1.80, 2.10, 1.70, 1.85, 800, now_ms - 2 * 3600_000),
        _nls_candle(1.85, 2.00, 1.60, 1.65, 700, now_ms - 1 * 3600_000),
    ]
    score, details, pump_data = calculate_listing_exhaustion(
        candles, "TESTCOIN", book=None, listing_age_hours=6.0, is_new_listing=True
    )
    assert isinstance(score, int)
    assert isinstance(pump_data, dict)
    # Nicht messbare Dimension darf nicht als "verfuegbar" in die Coverage zaehlen.
    if "missing_dimensions" in pump_data:
        assert "volume_decline" in pump_data.get("missing_dimensions", [])


# ─────────────────────────────────────────────────────────────────────────────
# N3: LVN-Kanten duerfen keine Overhead-Resistance/Barriere sein
# ─────────────────────────────────────────────────────────────────────────────

def test_overhead_resistance_ignoriert_lvn_kanten():
    import api

    row = {"entry": 100.0, "stop_loss": 97.0, "trade_setup": {}}
    lvn = {"price": 100.8, "source": "vrvp_lvn_upper_edge"}
    api._annotate_early_mover_overhead_resistance(row, lvn, 100.0, 3.0)
    assert "overhead_resistance" not in row, "LVN-Kante darf keine Barriere annotieren (N3)"

    hvn = {"price": 101.5, "source": "vrvp_hvn_upper"}
    api._annotate_early_mover_overhead_resistance(row, hvn, 100.0, 3.0)
    assert row.get("overhead_resistance", {}).get("source") == "vrvp_hvn_upper"


def test_vrvp_targets_waehlen_strukturelle_barriere():
    import api

    row = {"entry": 100.0, "stop_loss": 97.0, "trade_setup": {}}
    vrvp = {"levels": [
        {"price": 100.8, "source": "vrvp_lvn_upper_edge"},
        {"price": 101.5, "source": "vrvp_hvn_upper"},
        {"price": 110.0, "source": "vrvp_resistance"},
        {"price": 118.0, "source": "vrvp_resistance"},
    ]}
    api._apply_early_mover_vrvp_targets(row, vrvp)
    res = row.get("overhead_resistance")
    assert res is not None
    assert not str(res.get("source", "")).startswith("vrvp_lvn")


# ─────────────────────────────────────────────────────────────────────────────
# M9: LSD/Stable-Ausschluesse + Symbol-Kollisionsschutz
# ─────────────────────────────────────────────────────────────────────────────

def test_sol_lsds_und_neue_stables_ausgeschlossen():
    import api

    excluded = [
        ("JITOSOL", "jito-staked-sol", "Jito Staked SOL"),
        ("MSOL", "msol", "Marinade Staked SOL"),
        ("BNSOL", "binance-staked-sol", "Binance Staked SOL"),
        ("JUPSOL", "jupiter-staked-sol", "Jupiter Staked SOL"),
        ("RLUSD", "ripple-usd", "Ripple USD"),
        ("SOLVBTC", "solv-btc", "Solv Protocol SolvBTC"),
    ]
    for sym, cid, name in excluded:
        assert api._is_excluded_crypto_asset(sym, cid, name), f"{sym} muss ausgeschlossen sein (M9)"

    allowed = [
        ("SOL", "solana", "Solana"),
        ("JUP", "jupiter-exchange-solana", "Jupiter"),
        ("BTC", "bitcoin", "Bitcoin"),
        ("PENGU", "pudgy-penguins", "Pudgy Penguins"),
    ]
    for sym, cid, name in allowed:
        assert not api._is_excluded_crypto_asset(sym, cid, name), f"{sym} darf NICHT ausgeschlossen sein"


def test_symbol_kollision_erzeugt_keine_cross_coin_konfluenz_source():
    src = (REPO / "api.py").read_text(encoding="utf-8", errors="replace")
    assert "seen_symbol_ids" in src
    assert "_this_coin_id != _seen_coin_id" in src, (
        "Zwei CoinGecko-Coins mit gleichem Ticker duerfen nicht zu einer Row mergen (M9)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# M16-Rest: 1x-Short- und VIX-Produkte brauchen die Decay-Warnung
# ─────────────────────────────────────────────────────────────────────────────

def test_decay_warning_deckt_short_und_vix_ab_source():
    src = (REPO / "api.py").read_text(encoding="utf-8", errors="replace")
    assert '"short" in desc_lower or "vix" in desc_lower' in src, (
        "SH/PSQ/DOG/RWM (1x Short) und VIX-Produkte muessen decay_warning bekommen (M16)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# M18-Rest: Tracker-Preis-Fetcher darf keinen stale Markets-Cache nutzen
# ─────────────────────────────────────────────────────────────────────────────

def test_tracker_crypto_fetcher_blockt_stale_cache(monkeypatch, tmp_path):
    bg_service = pytest.importorskip("bg_service")

    cache_file = tmp_path / "cg_markets.json"
    coins = [{"symbol": "BTC", "current_price": 50_000.0}]

    def _assert_fresh_fallback():
        observation = bg_service._tracker_crypto_fetcher("BTCUSDT")
        assert observation["current"] == pytest.approx(50_000.0)
        assert observation["interval_complete"] is False

    # Stale (4h alt, > 3h-TTL) -> None
    cache_file.write_text(json.dumps({"ts": time.time() - 4 * 3600, "coins": coins}))
    monkeypatch.setattr(bg_service, "_CG_MARKETS_CACHE_FILE", str(cache_file))
    assert bg_service._tracker_crypto_fetcher("BTCUSDT") is None

    # Frisch (60s alt) -> Preis
    cache_file.write_text(json.dumps({"ts": time.time() - 60, "coins": coins}))
    _assert_fresh_fallback()

    # 2h alt (< 3h-TTL, deckt das 2h-Divergenz-Scan-Intervall ab) -> Preis
    cache_file.write_text(json.dumps({"ts": time.time() - 2 * 3600, "coins": coins}))
    _assert_fresh_fallback()

    # Re-audit: der api.py-Writer nutzt "cached_at" (ISO-String) statt "ts".
    # Frischer cached_at -> Preis (nicht faelschlich None).
    fresh_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    cache_file.write_text(json.dumps({"cached_at": fresh_iso, "coins": coins}))
    _assert_fresh_fallback()

    # Alter cached_at (4h) -> None
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    cache_file.write_text(json.dumps({"cached_at": old_iso, "coins": coins}))
    assert bg_service._tracker_crypto_fetcher("BTCUSDT") is None

    # Kein Zeitfeld -> Fallback auf Datei-mtime (frisch geschrieben) -> Preis
    cache_file.write_text(json.dumps({"coins": coins}))
    _assert_fresh_fallback()


# ─────────────────────────────────────────────────────────────────────────────
# N6/N7: Downgrade-Konsistenz (entry_status + Statistik)
# ─────────────────────────────────────────────────────────────────────────────

def test_downgrade_setzt_auch_entry_status_zurueck():
    import api

    row = {
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "LONG_NOW",
        "entry_status": "JETZT_TRADEN",
        "execution_trigger_ok": True,
    }
    api._downgrade_expired_crypto_triggers([row], cache_age=99_999)
    assert row["trade_signal"] == "WARTEN"
    assert row["entry_status"] == "WAIT_FOR_TRIGGER", "entry_status muss den Downgrade mitmachen (N6)"


def test_get_early_movers_zaehlt_stats_nach_downgrade_source():
    src = (REPO / "api.py").read_text(encoding="utf-8", errors="replace")
    assert "post-downgrade stats sync" in src or "fresh_trade_now" in src, (
        "trade_now_count muss NACH dem Trigger-Downgrade gezaehlt werden (N7)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# N8: Keine RVOL-Projektion in den ersten 15 Handelsminuten
# ─────────────────────────────────────────────────────────────────────────────

def test_projektion_in_ersten_15_minuten_unterdrueckt():
    import api

    # Dienstag 2026-07-21, 13:40 UTC = 09:40 ET (EDT) -> Minute 10
    early = datetime(2026, 7, 21, 13, 40, tzinfo=timezone.utc)
    assert api._project_us_equity_rvol(0.66, now_utc=early) == pytest.approx(0.66), (
        "In Minute 1-14 darf nicht projiziert werden (Premarket-Volumen im Zaehler, N8)"
    )

    # 14:10 UTC = 10:10 ET -> Minute 40: Projektion aktiv (Wert steigt)
    later = datetime(2026, 7, 21, 14, 10, tzinfo=timezone.utc)
    assert api._project_us_equity_rvol(0.66, now_utc=later) > 0.66

    # Wochenende: keine Projektion (kompletter Tagesbar)
    weekend = datetime(2026, 7, 19, 14, 10, tzinfo=timezone.utc)  # Sonntag
    assert api._project_us_equity_rvol(1.23, now_utc=weekend) == pytest.approx(1.23)


# ─────────────────────────────────────────────────────────────────────────────
# N10: Level-Dedupe darf Mikro-Preis-Profile nicht kollabieren
# ─────────────────────────────────────────────────────────────────────────────

def test_micro_preis_levels_ueberleben_dedupe():
    from modules.vrvp_levels import _dedupe_levels

    entry = 2.2e-9
    levels = [
        {"price": 2.0e-9, "source": "vrvp_val", "weight": 60},
        {"price": 2.2e-9, "source": "vrvp_poc", "weight": 70},
        {"price": 2.6e-9, "source": "vrvp_vah", "weight": 60},
        {"price": 3.0e-9, "source": "vrvp_hvn_upper", "weight": 56},
    ]
    merged = _dedupe_levels(levels, entry)
    assert len(merged) == 4, (
        f"Sub-Nano-Level duerfen nicht zu einem Level verschmelzen (N10) — got {len(merged)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# H3-Rest: hart gescheiterte Fetch-Tage werden gezaehlt
# ─────────────────────────────────────────────────────────────────────────────

def test_backtests_zaehlen_failed_fetch_days_source():
    src = (REPO / "modules" / "backtests.py").read_text(encoding="utf-8", errors="replace")
    assert src.count("if day_data is None:") >= 3, "Alle 3 Grouped-Konsumenten muessen None erkennen (H3)"
    assert src.count("failed_fetch_days += 1") >= 3
    assert '"failed_fetch_days": failed_fetch_days,' in src, "failed_fetch_days muss im Summary stehen"


# ─────────────────────────────────────────────────────────────────────────────
# L8: Monitoring-Purge entfernt alte expired-Eintraege endgueltig
# ─────────────────────────────────────────────────────────────────────────────

def test_cleanup_purgt_alte_expired_eintraege():
    from modules.new_listing_scanner import cleanup_monitoring

    now = datetime.now(timezone.utc)
    monitoring = {
        "OLDCOIN_bitget": {
            "status": "expired",
            "source": "new_listing",
            "expired_at": (now - timedelta(days=8)).isoformat(),
            "detected_at": (now - timedelta(days=9)).isoformat(),
        },
        "FRESHCOIN_bitget": {
            "status": "expired",
            "source": "new_listing",
            "expired_at": now.isoformat(),
            "detected_at": (now - timedelta(hours=80)).isoformat(),
        },
        "ACTIVECOIN_bitget": {
            "status": "monitoring",
            "source": "new_listing",
            "listing_time": (now - timedelta(hours=5)).isoformat(),
            "detected_at": (now - timedelta(hours=5)).isoformat(),
        },
    }
    result = cleanup_monitoring(monitoring)
    assert "OLDCOIN_bitget" not in result, "8 Tage alter expired-Eintrag muss entfernt werden (L8)"
    assert "FRESHCOIN_bitget" in result
    assert "ACTIVECOIN_bitget" in result
    assert result["ACTIVECOIN_bitget"]["status"] == "monitoring"


def test_cleanup_ueberlebt_naiven_zeitstempel():
    """Re-audit: ein naiver (tz-loser) persistierter Zeitstempel darf
    cleanup_monitoring nicht mit TypeError (naive vs aware) abbrechen."""
    from modules.new_listing_scanner import cleanup_monitoring

    now = datetime.now(timezone.utc)
    naive_old = (now - timedelta(days=9)).replace(tzinfo=None).isoformat()  # kein Offset
    monitoring = {
        "NAIVECOIN_bitget": {
            "status": "expired",
            "source": "new_listing",
            "expired_at": naive_old,
        },
        "ACTIVE2_bitget": {
            "status": "monitoring",
            "source": "new_listing",
            "listing_time": (now - timedelta(hours=5)).isoformat(),
            "detected_at": (now - timedelta(hours=5)).isoformat(),
        },
    }
    # Darf nicht werfen und muss den anderen Eintrag korrekt behalten.
    result = cleanup_monitoring(monitoring)
    assert "ACTIVE2_bitget" in result
    assert "NAIVECOIN_bitget" not in result  # 9 Tage alt -> gepurged


# ─────────────────────────────────────────────────────────────────────────────
# H8: Dreieck mit exakt 3 Swing-Highs/Lows darf nicht crashen
# ─────────────────────────────────────────────────────────────────────────────

def _ascending_triangle_ohlcv(n=50):
    """3 flache Peaks bei ~100, steigende Lows — erzeugt genau 3 Swings."""
    data = []
    lows = [90.0, 92.5, 95.0]
    peak_idx = [10, 24, 38]
    low_idx = [3, 17, 31]
    price = 91.0
    for i in range(n):
        if i in peak_idx:
            h, low, c, o = 100.0, 97.0, 99.2, 97.5
        elif i in low_idx:
            base = lows[low_idx.index(i)]
            h, low, c, o = base + 2.0, base, base + 0.8, base + 1.5
        else:
            drift = 90.0 + (i / n) * 6.0
            h, low, c, o = drift + 2.5, drift, drift + 1.2, drift + 0.5
        data.append({
            "time": 1_700_000_000 + i * 86_400,
            "open": o, "high": h, "low": low, "close": c,
            "volume": 1_000_000 + (300_000 if i in peak_idx else 0),
        })
        price = c
    return data


def test_dreieck_mit_exakt_drei_swings_crasht_nicht():
    from modules import patterns

    assert hasattr(patterns, "log"), "patterns.log muss definiert sein (H8)"
    data = _ascending_triangle_ohlcv()
    result = patterns.detect_chart_patterns(data, lookback=50)
    assert isinstance(result, list), "detect_chart_patterns muss eine Liste liefern, keine Exception (H8)"
    # Indizes aller gelieferten Patterns muessen in den Datenbereich zeigen.
    for pattern in result:
        for key in ("draw_points", "points"):
            for point in (pattern.get(key) or []):
                idx = point.get("index")
                if idx is not None:
                    assert 0 <= idx < len(data)


# ─────────────────────────────────────────────────────────────────────────────
# H9: Downside-Z-Score-Zweige muessen erreichbar sein
# ─────────────────────────────────────────────────────────────────────────────

def _exh_score(change_1h):
    from modules.scorers import calculate_exhaustion_score

    result = calculate_exhaustion_score(
        change_24h=6.0, change_7d=25.0, btc_change_7d=2.0, rvol=1.5,
        close_pos=0.5, upper_wick_pct=10.0, lower_wick_pct=5.0,
        market_cap=500_000_000, change_1h=change_1h,
    )
    return result[0] if isinstance(result, tuple) else result


def test_downside_zscore_zweig_feuert():
    base = _exh_score(None)
    reversal = _exh_score(-4.0)
    assert reversal >= base + 7, (
        f"1h-Reversal nach 7d-Pump muss Punkte geben (H9): base={base}, reversal={reversal}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Biotech: News-Reihenfolge + pos_90d + Quick-Scan-Richtung
# ─────────────────────────────────────────────────────────────────────────────

def test_news_momentum_negativ_mehrheit_gewinnt():
    from modules.scanners import _biotech_news_momentum

    mixed_negative = [{"sentiment": "positive"}] * 2 + [{"sentiment": "negative"}] * 3
    res = _biotech_news_momentum(mixed_negative)
    # 2 pos / 3 neg: Sentiment-Anteil -4, Frequenz +4 => 0 (vorher faelschlich 8)
    assert res["momentum_score"] <= 4
    assert "Negativ" in res["sentiment_summary"]

    mostly_positive = [{"sentiment": "positive"}] * 3 + [{"sentiment": "negative"}] * 2
    res_pos = _biotech_news_momentum(mostly_positive)
    assert res_pos["momentum_score"] > res["momentum_score"]


def test_quick_scan_uebergibt_rvol_richtung_source():
    src = (REPO / "modules" / "scanners.py").read_text(encoding="utf-8", errors="replace")
    assert 'rvol_direction=(old.get("Tech_Details") or {}).get("rvol_up_day", True)' in src, (
        "Quick-Scan muss die RVOL-Richtung des Full Scans weiterreichen (H7)"
    )


def test_pos_90d_null_bleibt_null_source():
    src = (REPO / "modules" / "scanners.py").read_text(encoding="utf-8", errors="replace")
    assert "pos_90d = 50 if pos_90d is None else pos_90d" in src
    assert 'tech_details.get("pos_90d", 50) or 50' not in src, "or-Falle bei pos_90d=0.0 (Handbuch Par. 21)"


def test_avg_vol_none_faellt_nicht_um_source():
    src = (REPO / "modules" / "scanners.py").read_text(encoding="utf-8", errors="replace")
    assert 'details["avg_vol"] = int(avg_vol_20 or 0)' in src, (
        "int(None) wuerde den Technik-Score still auf 0 werfen (N11)"
    )
