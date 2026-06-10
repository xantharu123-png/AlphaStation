# -*- coding: utf-8 -*-
"""Audit-Fix-Tests für scanner.py (Streamlit-Legacy-UI) und bg_service.py.

Abgedeckte Audit-Punkte:
  H-7  : BTC-Schwäche-Gate + JETZT-Schwelle 65 (_btc_div_signal_status, beide Pfade)
  H-9  : Scan-Ownership bg vs. api (_resolve_bg_scan_set, ENV BG_SCAN_SET)
  H-14 : CoinGecko-Partial-Cache (Writer schreibt Teilabrufe nicht als frisch,
         Konsument behandelt partial als stale)
  H-1  : MEXC-Einheiten (amount24 = USD-Volumen, holdVol nur mit contractSize exakt)
  M-5  : Symbol-Kollisionen (1000{SYM}-Mapping + Faktor-3-Preis-Plausibilität)
  M-7  : Stablecoin-/Leveraged-Token-Filter

Pfad-Konvention: session-unabhängig via __file__ (keine hardcodeten Session-Pfade).
"""
import ast
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_service  # noqa: E402


def _load_scanner_helpers():
    """Extrahiert die getesteten PURE-Helper aus scanner.py per AST, OHNE das
    Modul auszufuehren.

    Grund: scanner.py ist eine Streamlit-App — `import scanner` fuehrt auf
    Modulebene UI-/Scan-Code aus (z.B. fetch_orb_scanner -> echte Polygon-Calls),
    der waehrend der US-Session im Test-Sandbox-Netz HAENGT (Audit 10.06.,
    nichtdeterministischer Collection-Timeout). Die getesteten Helper sind pure
    Funktionen/Konstanten — Quelltext-Extraktion ist hier die robuste Loesung.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanner.py")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    wanted_funcs = {
        "_btc_div_signal_status",
        "_cg_file_cache_usable",
        "_lookup_perp_info",
        "_parse_mexc_perp_tickers",
        "_is_leveraged_token_symbol",
        "_safe_float",
    }
    wanted_consts = {"EXCLUDED_CRYPTO_SYMBOLS_LOCAL"}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_funcs:
            nodes.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted_consts:
                nodes.append(node)
    module = types.ModuleType("scanner_helpers_extracted")
    module.__dict__["time"] = __import__("time")
    module.__dict__["re"] = __import__("re")
    module.__dict__["math"] = __import__("math")
    code = compile(ast.Module(body=nodes, type_ignores=[]), path, "exec")
    exec(code, module.__dict__)
    return module


scanner = _load_scanner_helpers()  # noqa: E402 — Drop-in fuer die Tests unten


# ══════════════════════════════════════════════════════════════════
# H-7: BTC-Schwäche-Gate + einheitliche JETZT-Schwelle 65
# ══════════════════════════════════════════════════════════════════

BOTH_IMPLS = [
    pytest.param(scanner._btc_div_signal_status, id="scanner"),
    pytest.param(bg_service._btc_div_signal_status, id="bg_service"),
]


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_kein_jetzt_ohne_btc_schwaeche(impl):
    """BTC stark (btc_weak=False) → NIE ein JETZT-Signal, egal wie heiß der Coin ist."""
    for exh in (50, 65, 80, 95, 100):
        for cp in (0.75, 0.85, 1.0):
            for ch1h in (-5.0, -2.0, -0.6):
                timing, quality, gate = impl(exh, cp, ch1h, change_24h=5.0, btc_weak=False)
                assert "JETZT" not in timing, f"JETZT trotz BTC-Stärke: {timing}"
                assert quality <= 2
                assert gate is False


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_beobachten_statt_short_bei_btc_staerke(impl):
    """Heißer Kandidat + BTC stark → explizites Watch-Signal mit BTC-Hinweis."""
    timing, quality, gate = impl(80, 0.9, -3.0, change_24h=4.0, btc_weak=False)
    assert "BEOBACHTEN" in timing
    assert "BTC stark" in timing
    assert quality == 2
    assert gate is False


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_jetzt_shorten_ab_65_nur_mit_btc_schwaeche(impl):
    """ExhScore 65 + nahe High + 1h kippt + BTC schwach → JETZT SHORTEN (q=5)."""
    timing, quality, gate = impl(65, 0.85, -2.0, change_24h=3.0, btc_weak=True)
    assert "JETZT SHORTEN" in timing
    assert quality == 5
    assert gate is True


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_keine_jetzt_signale_unter_schwelle_65(impl):
    """Alte bg-Schwellen 55/50/45 dürfen kein JETZT mehr auslösen."""
    for exh in (45, 50, 55, 64):
        timing, quality, _ = impl(exh, 0.85, -2.5, change_24h=3.0, btc_weak=True)
        assert "JETZT" not in timing, f"JETZT bei ExhScore {exh}: {timing}"
        assert quality <= 3


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_jetzt_traegt_kein_stop_hinweis(impl):
    """Ohne Entry/Stop/TP ist auch JETZT nur ein Beobachtungssignal — Hinweis Pflicht."""
    t5, _, _ = impl(70, 0.9, -2.0, change_24h=2.0, btc_weak=True)
    t4, _, _ = impl(70, 0.9, -0.8, change_24h=2.0, btc_weak=True)
    for t in (t5, t4):
        assert "JETZT" in t
        assert "kein definierter Stop" in t
        assert "Beobachtungssignal" in t


@pytest.mark.parametrize("impl", BOTH_IMPLS)
def test_h7_zu_spaet_bleibt_zu_spaet(impl):
    """Preis nahe Low + 24h stark gefallen → ZU SPÄT (unabhängig vom Gate)."""
    for weak in (True, False):
        timing, quality, _ = impl(80, 0.2, -1.0, change_24h=-8.0, btc_weak=weak)
        assert "ZU SPÄT" in timing
        assert quality == -1


def test_h7_scanner_und_bg_implementierung_identisch():
    """SYNC-Wächter: beide Kopien der Helper-Logik müssen identisch entscheiden."""
    grid_scores = (0, 40, 49, 50, 55, 64, 65, 70, 80, 100)
    grid_cp = (None, 0.0, 0.2, 0.39, 0.4, 0.5, 0.69, 0.7, 0.85, 1.0)
    grid_1h = (-3.0, -2.0, -1.6, -1.0, -0.6, 0.0, 1.0)
    grid_24h = (-8.0, -3.1, 0.0, 5.0)
    for exh in grid_scores:
        for cp in grid_cp:
            for ch1h in grid_1h:
                for ch24h in grid_24h:
                    for weak in (True, False):
                        a = scanner._btc_div_signal_status(exh, cp, ch1h, ch24h, weak)
                        b = bg_service._btc_div_signal_status(exh, cp, ch1h, ch24h, weak)
                        assert a == b, f"Divergenz bei exh={exh} cp={cp} 1h={ch1h} 24h={ch24h} weak={weak}: {a} != {b}"


# ══════════════════════════════════════════════════════════════════
# H-9: Scan-Ownership bg_service vs. api-Scheduler
# ══════════════════════════════════════════════════════════════════

def test_h9_default_scan_set_ohne_api_overlap():
    """Default: bg übernimmt NUR die Nicht-Überlappenden (inkl. new_listing 15-Min)."""
    active, skipped = bg_service._resolve_bg_scan_set(env_value="")
    assert active == {"bi_long", "bi_short", "biotech", "new_listing"}
    assert skipped == {"crash_monitor", "btc_divergence", "bear_scan", "strategies", "orb"}
    assert "new_listing" in active  # NLS-15-Min-Zyklus MUSS weiterlaufen


def test_h9_env_override_bestimmt_scans():
    active, skipped = bg_service._resolve_bg_scan_set(env_value="btc_divergence, orb")
    assert active == {"btc_divergence", "orb"}
    assert "bi_long" in skipped and "new_listing" in skipped


def test_h9_env_override_ignoriert_unbekannte_scans():
    active, _ = bg_service._resolve_bg_scan_set(env_value="bi_long,quatsch_scan")
    assert active == {"bi_long"}


def test_h9_env_nur_unbekannte_faellt_auf_default_zurueck():
    active, _ = bg_service._resolve_bg_scan_set(env_value="gibts_nicht")
    assert active == bg_service.BG_DEFAULT_SCAN_SET


# ══════════════════════════════════════════════════════════════════
# H-14: CoinGecko-Partial-Cache
# ══════════════════════════════════════════════════════════════════

def _coins(n):
    return [{"id": f"coin{i}", "symbol": f"C{i}"} for i in range(n)]


def test_h14_bg_writer_schreibt_teilabruf_nicht():
    """429 nach Seite 2 → kein frischer Voll-Cache (Payload None)."""
    assert bg_service._cg_markets_cache_payload(_coins(500), pages_ok=2, pages_wanted=4) is None
    assert bg_service._cg_markets_cache_payload([], pages_ok=0, pages_wanted=4) is None
    # 4 Seiten gemeldet, aber zu wenige Coins (Rumpf-Seiten) → ebenfalls nicht frisch
    assert bg_service._cg_markets_cache_payload(_coins(700), pages_ok=4, pages_wanted=4) is None


def test_h14_bg_writer_schreibt_vollabruf():
    payload = bg_service._cg_markets_cache_payload(_coins(1000), pages_ok=4, pages_wanted=4)
    assert payload is not None
    assert len(payload["coins"]) == 1000
    assert "ts" in payload
    assert not payload.get("partial")


def test_h14_konsument_behandelt_partial_als_stale():
    """Frischer (30s alter) partial-Cache darf NICHT blind verwendet werden."""
    partial_cache = {"coins": _coins(500), "ts": 0, "partial": True, "pages_fetched": 2}
    assert scanner._cg_file_cache_usable(partial_cache, age_seconds=30) is False


def test_h14_konsument_akzeptiert_frischen_vollcache():
    full_cache = {"coins": _coins(1000), "ts": 0}
    assert scanner._cg_file_cache_usable(full_cache, age_seconds=30) is True
    # zu alt → stale
    assert scanner._cg_file_cache_usable(full_cache, age_seconds=121) is False
    # leer/kaputt → stale
    assert scanner._cg_file_cache_usable({"coins": []}, age_seconds=10) is False
    assert scanner._cg_file_cache_usable(None, age_seconds=10) is False


# ══════════════════════════════════════════════════════════════════
# H-1: MEXC-Einheiten (amount24 statt volume24*lastPrice)
# ══════════════════════════════════════════════════════════════════

def test_h1_mexc_volumen_aus_amount24():
    """Mock-Response: amount24 (USD) muss verwendet werden, nicht volume24*price."""
    items = [{
        "symbol": "DOGE_USDT",
        "lastPrice": 0.25,
        "holdVol": 1_000_000,      # Kontrakte
        "volume24": 80_000_000,    # Kontrakte (NICHT Coins!)
        "amount24": 5_000_000,     # 24h-Turnover in USDT — korrekt
        "fundingRate": 0.0001,
    }]
    parsed = scanner._parse_mexc_perp_tickers(items)
    assert "DOGE" in parsed
    row = parsed["DOGE"]
    assert row["volume24"] == 5_000_000  # amount24, NICHT 80M*0.25=20M
    assert row["volume24"] != 80_000_000 * 0.25
    assert row["vol_usd_estimate"] is False


def test_h1_mexc_oi_mit_contractsize_exakt():
    items = [{
        "symbol": "BTC_USDT",
        "lastPrice": 100_000.0,
        "holdVol": 500_000,        # Kontrakte
        "contractSize": 0.0001,    # 1 Kontrakt = 0.0001 BTC
        "volume24": 10_000_000,
        "amount24": 2_000_000_000,
        "fundingRate": 0.0001,
    }]
    row = scanner._parse_mexc_perp_tickers(items)["BTC"]
    # OI = 500k Kontrakte × 0.0001 BTC/Kontrakt × 100k USD = 5,0 Mio USD
    assert row["oi_usdt"] == pytest.approx(500_000 * 0.0001 * 100_000.0)
    assert row["oi_usd_estimate"] is False


def test_h1_mexc_oi_ohne_contractsize_ist_schaetzung():
    items = [{
        "symbol": "PEPE_USDT",
        "lastPrice": 0.00001,
        "holdVol": 2_000_000,
        "volume24": 50_000_000,
        "amount24": 1_000_000,
        "fundingRate": -0.0002,
    }]
    row = scanner._parse_mexc_perp_tickers(items)["PEPE"]
    assert row["oi_usd_estimate"] is True  # holdVol=Kontrakte ≠ Coins → nur Schätzung
    assert row["volume24"] == 1_000_000


def test_h1_mexc_ignoriert_nicht_usdt_kontrakte():
    parsed = scanner._parse_mexc_perp_tickers([{"symbol": "BTC_USD", "amount24": 1}])
    assert parsed == {}


# ══════════════════════════════════════════════════════════════════
# M-5: Symbol-Kollisionen (1000-Mapping + Faktor-3-Plausi)
# ══════════════════════════════════════════════════════════════════

def test_m5_faktor_3_plausi_verwirft_kollision():
    """CoinGecko-Preis 0.50, Perp-Preis 5.00 (Faktor 10) → Kollision, kein Match."""
    perp_data = {"NEIRO": {"funding_rate": 0.0001, "oi_ratio": 1.2, "last_price": 5.00}}
    assert scanner._lookup_perp_info(perp_data, "NEIRO", cg_price=0.50) == {}


def test_m5_plausibler_preis_matcht():
    perp_data = {"SOL": {"funding_rate": 0.0001, "oi_ratio": 1.5, "last_price": 150.0}}
    info = scanner._lookup_perp_info(perp_data, "SOL", cg_price=149.0)
    assert info.get("oi_ratio") == 1.5


def test_m5_1000_mapping_mit_preis_plausi():
    """PEPE: CG-Preis 0.00001, Perp '1000PEPE' bei 0.01 (=1000x) → Match über Mapping."""
    perp_data = {"1000PEPE": {"funding_rate": -0.0001, "oi_ratio": 2.0, "last_price": 0.01}}
    info = scanner._lookup_perp_info(perp_data, "PEPE", cg_price=0.00001)
    assert info.get("oi_ratio") == 2.0


def test_m5_1000_mapping_verwirft_unplausiblen_preis():
    """'1000XYZ'-Kontrakt dessen Preis NICHT ~1000x CG-Preis ist → Kollision."""
    perp_data = {"1000XYZ": {"funding_rate": 0.0, "oi_ratio": 1.0, "last_price": 0.5}}
    # erwartet wäre ~1000*0.00001=0.01 — 0.5 ist Faktor 50 daneben
    assert scanner._lookup_perp_info(perp_data, "XYZ", cg_price=0.00001) == {}


def test_m5_ohne_perp_preis_kein_plausi_block():
    """Fehlt last_price beim Perp, kann nicht geprüft werden → Match bleibt (Status quo)."""
    perp_data = {"ABC": {"funding_rate": 0.0001, "oi_ratio": 0.9}}
    info = scanner._lookup_perp_info(perp_data, "ABC", cg_price=1.23)
    assert info.get("oi_ratio") == 0.9


# ══════════════════════════════════════════════════════════════════
# M-7: Stablecoin-/Leveraged-Token-Filter
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mod", [scanner, bg_service], ids=["scanner", "bg_service"])
def test_m7_leveraged_tokens_erkannt(mod):
    for sym in ("BTCUP", "BTCDOWN", "XYZ3L", "ETH3S", "ADA4L", "DOT5S", "ETHBULL", "LINKBEAR"):
        assert mod._is_leveraged_token_symbol(sym) is True, f"{sym} nicht erkannt"


@pytest.mark.parametrize("mod", [scanner, bg_service], ids=["scanner", "bg_service"])
def test_m7_echte_coins_nicht_gefiltert(mod):
    for sym in ("BTC", "ETH", "SOL", "JUP", "PEPE", "DOWN", "BULL"):
        # JUP endet auf UP, 'BULL'/'DOWN' allein sind Memecoins — dürfen NICHT fliegen
        assert mod._is_leveraged_token_symbol(sym) is False, f"{sym} fälschlich gefiltert"


@pytest.mark.parametrize("mod", [scanner, bg_service], ids=["scanner", "bg_service"])
def test_m7_excluded_liste_vollstaendig(mod):
    s = mod.EXCLUDED_CRYPTO_SYMBOLS_LOCAL
    # Audit-geforderte Ergänzungen
    for sym in ("USDE", "USDS", "PYUSD", "FRAX", "PAXG", "CBBTC", "WSTETH"):
        assert sym in s, f"{sym} fehlt in EXCLUDED_CRYPTO_SYMBOLS_LOCAL"
    # Kern-Stables aus der api-Liste
    for sym in ("USDT", "USDC", "DAI", "WBTC", "WETH", "STETH"):
        assert sym in s
    # Echte Coins gehören NICHT in die Liste
    for sym in ("BTC", "ETH", "SOL"):
        assert sym not in s


def test_m7_scanner_und_bg_listen_identisch():
    """SYNC-Wächter für die gespiegelte Konstante + Helper."""
    assert scanner.EXCLUDED_CRYPTO_SYMBOLS_LOCAL == bg_service.EXCLUDED_CRYPTO_SYMBOLS_LOCAL
