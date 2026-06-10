"""Audit-Fix-Tests fuer die modules/-Sanierung (2026-06-10).

Abgedeckte Befunde:
- S-2  Stop-Breach-Erkennung in modules/trade_health.py (4 Faelle)
- S-5  Exhaustion Phantom-Decoupling (mit/ohne 14d-Daten)
- S-6  Commerce fail-closed (Legacy-Key default aus, Boot-Gate wirft)
- H-8  Crypto.com-Candles/Ticker vv-Fallback
- H-13 Stub-Beseitigung (close_position + estimate_crypto_atr identisch)
- M-VA Value Area kontiguierlich (POC-Expansion statt Greedy)
- M-Doji Volumen-Erhaltung bei High==Low-Bars
- M-VWAP Sub-Cent-Praezision + volumengewichtete Baender
- N-toFloat Komma-Dezimaltrenner / "0x10" -> None
"""

from datetime import datetime, timedelta, timezone

import pytest

import modules.auth as auth
import modules.indicators as indicators
import modules.new_listing_scanner as nls
import modules.scorers as scorers
from modules.trade_health import _to_float, calculate_trade_health
from modules.volume_analysis import calculate_volume_profile, find_volume_voids_for_chart


# ═══════════════════════════════════════════════════════════════════════════════
# S-2: Stop-Breach-Erkennung (KRITISCH)
# ═══════════════════════════════════════════════════════════════════════════════

def _breach_row(**overrides):
    row = {
        "ticker": "BRCH",
        "direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 110.0,
        "TP2": 120.0,
        "current_price": 92.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 9_000_000,
    }
    row.update(overrides)
    return row


def test_s2_long_stop_breach_is_no_trade_with_critical_health():
    # Audit-Repro: LONG Entry 100 / Stop 95 / Preis 92 war TRADEABLE mit health=100
    health = calculate_trade_health(_breach_row(), "bi_long")

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]
    assert health["health_score"] <= 15
    assert health["risk_level"] == "CRITICAL"
    assert health["stop_breached"] is True
    assert health["metrics"]["live_rr"] == 0.0


def test_s2_short_stop_breach_is_no_trade():
    health = calculate_trade_health(
        _breach_row(
            direction="SHORT",
            Entry=100.0,
            StopLoss=105.0,
            TP1=90.0,
            TP2=80.0,
            current_price=107.0,
            close_pos=0.15,
        ),
        "bi_short",
    )

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]
    assert health["health_score"] <= 15
    assert health["risk_level"] == "CRITICAL"
    assert health["metrics"]["live_rr"] == 0.0


def test_s2_price_exactly_at_stop_counts_as_breach():
    health = calculate_trade_health(_breach_row(current_price=95.0), "bi_long")

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]
    assert health["stop_breached"] is True


def test_s2_pullback_between_stop_and_entry_is_not_a_breach():
    # Preis 97 liegt zwischen Stop 95 und Entry 100 -> normaler Pullback
    health = calculate_trade_health(_breach_row(current_price=97.0), "bi_long")

    assert health["stop_breached"] is False
    assert "setup_invalidated_stop_breached" not in health["exclusion_reasons"]
    assert health["decision"] != "NO_TRADE"
    assert health["health_score"] >= 65
    # Negative Distanz Richtung Stop ist Warnung, kein Positivum
    assert not any("Entry liegt nahe am Trigger" in p for p in health["positives"])
    assert any("Richtung Stop" in w for w in health["warnings"])


# ═══════════════════════════════════════════════════════════════════════════════
# S-5: Exhaustion Phantom-Decoupling
# ═══════════════════════════════════════════════════════════════════════════════

def _exhaustion_kwargs(**overrides):
    kwargs = {
        "change_24h": 6.0,
        "change_7d": 30.0,
        "btc_change_7d": -6.0,
        "rvol": None,
        "close_pos": None,
        "upper_wick_pct": None,
        "lower_wick_pct": None,
        "market_cap": 500_000_000,
    }
    kwargs.update(overrides)
    return kwargs


def test_s5_no_14d_data_means_no_phantom_decoupling_bonus_or_text():
    score, details = scorers.calculate_exhaustion_score(**_exhaustion_kwargs())

    joined = " | ".join(details)
    assert "Decoupling" not in joined
    assert "Correlation" not in joined


def test_s5_real_14d_data_yields_decoupling_text_and_dimension_stays_capped_at_12():
    coin = [5, -3, 4, -2, 6, -1, 3, -4, 2, 5, -2, 4, -3, 6]
    btc = [-2, 3, -3, 2, -4, 1, -2, 3, -1, -3, 2, -3, 3, -4]

    score_without, _ = scorers.calculate_exhaustion_score(**_exhaustion_kwargs())
    score_with, details_with = scorers.calculate_exhaustion_score(
        **_exhaustion_kwargs(coin_changes_14d=coin, btc_changes_14d=btc)
    )

    assert any("Real Decoupling" in d for d in details_with)
    # M-ExhCap: Divergenz (12) + Decoupling (15) zusammen auf 12 gedeckelt ->
    # der Decoupling-Bonus darf den Score hier nicht mehr erhoehen.
    assert score_with == score_without


def test_s5_short_14d_lists_do_not_trigger_decoupling():
    score, details = scorers.calculate_exhaustion_score(
        **_exhaustion_kwargs(coin_changes_14d=[1, 2], btc_changes_14d=[1, 2])
    )

    joined = " | ".join(details)
    assert "Decoupling" not in joined
    assert "Correlation" not in joined


# ═══════════════════════════════════════════════════════════════════════════════
# S-6: Commerce fail-closed
# ═══════════════════════════════════════════════════════════════════════════════

def test_s6_legacy_admin_master_key_is_disabled_by_default():
    # Testumgebung setzt ALLOW_LEGACY_ADMIN_MASTER_KEY nicht -> Default "0" = aus
    assert auth.ALLOW_LEGACY_ADMIN_MASTER_KEY is False


def test_s6_jwt_expiry_defaults_to_24h():
    assert auth.JWT_EXPIRE_HOURS == 24


def test_s6_enforce_commercial_boot_security_raises_on_default_secret(monkeypatch):
    monkeypatch.setenv("COMMERCE_ENFORCE_AUTH", "1")
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_DEFAULT_SECRET)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_EPHEMERAL", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", False)

    with pytest.raises(RuntimeError):
        auth.enforce_commercial_boot_security()


def test_s6_enforce_commercial_boot_security_raises_on_active_legacy_key(monkeypatch):
    monkeypatch.setenv("COMMERCE_ENFORCE_AUTH", "1")
    monkeypatch.setattr(auth, "JWT_SECRET", "x" * 64)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_EPHEMERAL", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)

    with pytest.raises(RuntimeError):
        auth.enforce_commercial_boot_security()


def test_s6_enforce_commercial_boot_security_passes_with_secure_config(monkeypatch):
    monkeypatch.setenv("COMMERCE_ENFORCE_AUTH", "1")
    monkeypatch.setattr(auth, "JWT_SECRET", "x" * 64)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_EPHEMERAL", False)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", False)

    auth.enforce_commercial_boot_security()  # darf nicht werfen


def test_s6_enforce_is_noop_without_commerce_flag(monkeypatch):
    monkeypatch.setenv("COMMERCE_ENFORCE_AUTH", "0")
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_DEFAULT_SECRET)
    monkeypatch.setattr(auth, "JWT_SECRET_IS_EPHEMERAL", True)
    monkeypatch.setattr(auth, "ALLOW_LEGACY_ADMIN_MASTER_KEY", True)

    auth.enforce_commercial_boot_security()  # Nicht-Commercial -> kein Raise


# ═══════════════════════════════════════════════════════════════════════════════
# H-8: Crypto.com vv-Fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_h8_cryptocom_candles_fall_back_to_v_times_close_when_vv_missing(monkeypatch):
    fake = {"result": {"data": [
        {"t": 1, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "100", "vv": "250"},
        {"t": 2, "o": "1", "h": "2", "l": "0.5", "c": "2.0", "v": "100"},
        {"t": 3, "o": "1", "h": "2", "l": "0.5", "c": "3.0", "v": "10", "vv": 0},
    ]}}
    monkeypatch.setattr(nls, "_api_get", lambda *a, **k: fake)

    candles = nls.fetch_cryptocom_candles("X_USDT")
    by_ts = {c["timestamp"]: c for c in candles}

    assert by_ts[1]["volume_usd"] == 250.0           # vv vorhanden -> vv
    assert by_ts[2]["volume_usd"] == 100 * 2.0       # vv fehlt -> v * close
    assert by_ts[3]["volume_usd"] == 10 * 3.0        # vv == 0 -> v * close


def test_h8_cryptocom_ticker_falls_back_to_v_times_price(monkeypatch):
    fake = {"result": {"data": [
        {"a": "2.5", "b": "2.4", "k": "2.6", "h": "3", "l": "2",
         "v": "1000", "c": "0.1", "oi": "0", "t": 1}
    ]}}
    monkeypatch.setattr(nls, "_api_get", lambda *a, **k: fake)

    ticker = nls.fetch_cryptocom_ticker("X_USDT")

    assert ticker["volume_usd_24h"] == 1000 * 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# H-13: Stub-Beseitigung in scorers.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_h13_scorers_close_position_matches_indicators_including_clamp():
    cases = [
        (110, 100, 105, 1.0),
        (110, 100, 115, 1.0),   # Clamp oben
        (110, 100, 95, 1.0),    # Clamp unten
        (100, 100, 100, 1.0),   # Keine Range -> None
        (100.1, 100.0, 100.05, 1.0),  # min_range_pct greift -> None
        (None, 100, 105, 1.0),
    ]
    for high, low, close, mrp in cases:
        assert scorers.calculate_close_position(high, low, close, min_range_pct=mrp) == \
            indicators.calculate_close_position(high, low, close, min_range_pct=mrp)

    # Stub gab frueher ungeclampte Werte und 0.5-Defaults zurueck:
    assert scorers.calculate_close_position(110, 100, 115) == 1.0
    assert scorers.calculate_close_position(100, 100, 100) is None


def test_h13_estimate_crypto_atr_is_single_sourced_with_pump_cap():
    # Normalfall: beide Pfade identisch (echte Range)
    assert scorers.estimate_crypto_atr(5_000_000_000, 110, 100, 105) == \
        indicators.estimate_crypto_atr(5_000_000_000, 110, 100, 105)

    # Pump-Tag: Range (66%) > 2x Baseline (7.0) -> beide kappen auf Baseline
    pumped_scorers = scorers.estimate_crypto_atr(5_000_000_000, 200, 100, 150)
    pumped_indicators = indicators.estimate_crypto_atr(5_000_000_000, 200, 100, 150)
    assert pumped_scorers == pumped_indicators == 7.0

    # Fallback ohne Range-Daten: kanonische Tiers
    assert scorers.estimate_crypto_atr(50_000_000) == indicators.estimate_crypto_atr(50_000_000) == 15.0
    assert indicators.estimate_crypto_atr(20_000_000_000) == 4.5


def test_h13_detect_chart_patterns_delegates_to_real_implementation():
    from modules.patterns import detect_chart_patterns as real_detect

    bars = []
    price = 100.0
    for i in range(60):
        price *= 1.002 if i % 3 else 0.999
        bars.append({
            "open": price * 0.999,
            "high": price * 1.004,
            "low": price * 0.996,
            "close": price,
            "volume": 1_000_000 + i * 1000,
        })

    assert scorers.detect_chart_patterns(bars, lookback=50) == real_detect(bars, lookback=50)
    assert isinstance(scorers.detect_chart_patterns(bars), list)


# ═══════════════════════════════════════════════════════════════════════════════
# M-VA: Value Area kontiguierlich (Audit-Gegenbeispiel)
# ═══════════════════════════════════════════════════════════════════════════════

def _bar(low, high, volume):
    return {"high": high, "low": low, "volume": volume}


def test_mva_value_area_is_contiguous_and_excludes_far_cluster():
    # 55 % Volumen bei 100-102, 20 % Fern-Cluster bei 118-120, 25 % verteilt.
    # Alte Greedy-VA: [100, 120] (Fern-Cluster eingesammelt).
    # Korrekte POC-Expansion: kontiguierlich ~[100, 112].
    bars = []
    bars += [_bar(100, 101, 1500), _bar(100, 101, 1500)]   # Bin 0: 3000 (POC)
    bars += [_bar(101, 102, 1250), _bar(101, 102, 1250)]   # Bin 1: 2500
    for i in range(16):                                     # Bins 2-17: je 156.25
        bars.append(_bar(102 + i, 103 + i, 156.25))
    bars += [_bar(118, 119, 1000), _bar(119, 120, 1000)]    # Fern-Cluster: 2000

    profile = calculate_volume_profile(bars, num_bins=20)

    assert profile is not None
    assert abs(profile["val"] - 100.0) < 1e-6
    assert profile["vah"] <= 113.0          # ~112 bei Bin-Breite 1.0
    assert profile["vah"] < 118.0           # Fern-Cluster NICHT in der VA
    assert 100.0 <= profile["poc"] <= 101.0


# ═══════════════════════════════════════════════════════════════════════════════
# M-Doji: Volumen-Erhaltung bei High==Low-Bars
# ═══════════════════════════════════════════════════════════════════════════════

def test_mdoji_profile_conserves_total_volume_exactly():
    bars = [_bar(100 + (i % 10), 101 + (i % 10), 100.0) for i in range(20)]
    bars.append({"high": 105.7, "low": 105.7, "volume": 777.0})  # Doji

    profile = calculate_volume_profile(bars, num_bins=20)

    assert profile is not None
    total_in = sum(b["volume"] for b in bars)
    total_binned = sum(b["volume"] for b in profile["bins"])
    assert abs(total_binned - total_in) < 1e-6 * total_in

    # Doji-Volumen liegt im Bin, der 105.7 enthaelt
    doji_bin = next(b for b in profile["bins"] if b["low"] <= 105.7 <= b["high"])
    assert doji_bin["volume"] >= 777.0


def test_mdoji_chart_voids_count_doji_volume():
    bars = []
    for i in range(15):
        bars.append({"high": 110.0, "low": 100.0, "volume": 10.0})
    for _ in range(5):
        bars.append({"high": 105.7, "low": 105.7, "volume": 1000.0})  # Dojis

    voids = find_volume_voids_for_chart(bars, num_bins=20)

    assert len(voids) > 0  # Ohne Doji-Fix gingen 5000 verloren -> keine Voids erkennbar
    assert all(not (v["price_low"] <= 105.7 <= v["price_high"]) for v in voids)


# ═══════════════════════════════════════════════════════════════════════════════
# M-VWAP: Sub-Cent-Praezision + gewichtete Baender
# ═══════════════════════════════════════════════════════════════════════════════

def test_mvwap_sub_cent_price_keeps_precision():
    bars = [
        {"high": 0.00052, "low": 0.00048, "close": 0.0005, "volume": 1_000_000}
        for _ in range(10)
    ]

    res = indicators.calculate_vwap(bars)

    assert res is not None
    assert res["vwap"] > 0                      # alt: round(0.0005, 2) == 0.0
    assert abs(res["vwap"] - 0.0005) < 1e-5
    assert res["upper_1"] >= res["vwap"] >= res["lower_1"]


def test_mvwap_bands_are_volume_weighted():
    # 9 Bars mit riesigem Volumen exakt am VWAP + 1 Mini-Volumen-Ausreisser:
    # ungewichtete StdDev waere ~30, volumengewichtet nahe 0.
    bars = [{"high": 100, "low": 100, "close": 100, "volume": 1_000_000} for _ in range(9)]
    bars.append({"high": 200, "low": 200, "close": 200, "volume": 1})

    res = indicators.calculate_vwap(bars)

    assert res is not None
    assert res["std_dev"] < 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# N-toFloat: trade_health._to_float
# ═══════════════════════════════════════════════════════════════════════════════

def test_ntofloat_comma_decimal_and_hex_like_strings():
    assert _to_float("1,5") == 1.5            # Komma = Dezimaltrenner ohne Punkt
    assert _to_float("0x10") is None          # alt: 10.0 (alle "x" entfernt)
    assert _to_float("2.5x") == 2.5           # RVOL-Suffix bleibt erlaubt
    assert _to_float("$1,234.56") == 1234.56  # Tausendertrenner mit Punkt
    assert _to_float("1,234,567") == 1234567.0
    assert _to_float("12%") == 12.0
    assert _to_float("nan") is None
    assert _to_float("") is None
    assert _to_float(None) is None
    assert _to_float("abc") is None
    assert _to_float(3) == 3.0
