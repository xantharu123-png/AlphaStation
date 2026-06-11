"""Restpunkte-Audit 2026-06-11 — api.py: Scan-Ownership, Grade-Leiter, toter z-Score-Block.

Deckt die drei Fixes ab:
1) API_SCAN_SKIP_BG_OWNED: api-Scheduler skippt die 4 bg-owned Scanner
   (bi_long/bi_short/biotech/new_listing) per Default; manuelle Routen nie.
2) Zentrale Grade-Leiter S>=88/A>=80/B>=65/C>=50/D — konsistent mit dem
   _ALERT-Mail-Gate (Score>=80); crypto_explosion nutzt die zentrale Funktion.
3) Toter Legacy-z-Score-Block in _btc_divergenz_wrapper ist entfernt,
   Live-Pfad _build_crypto_btc_divergence_results bleibt.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402  (Import ist zugleich der Smoke-Test fuer Fix 3)

_BG_OWNED = ("bi_long", "bi_short", "biotech", "new_listing")


# ── Fix 1: Scan-Ownership ───────────────────────────────────────────────────

def test_scheduler_skips_bg_owned_by_default(monkeypatch):
    """Default (ENV unset oder "1"): alle 4 bg-owned Scanner werden geskippt."""
    monkeypatch.delenv("API_SCAN_SKIP_BG_OWNED", raising=False)
    for name in _BG_OWNED:
        assert api._api_scheduler_should_skip(name) is True, name
        assert api._api_scheduler_should_skip(name, env_value="1") is True, name
    assert api._BG_OWNED_SCANNERS == frozenset(_BG_OWNED)


def test_scheduler_env_zero_restores_old_behaviour():
    """ENV "0" (oder false/no): altes Verhalten, api scannt wieder mit."""
    for name in _BG_OWNED:
        assert api._api_scheduler_should_skip(name, env_value="0") is False, name
        assert api._api_scheduler_should_skip(name, env_value="false") is False, name


def test_scheduler_never_skips_other_scanners():
    """Nicht-bg-owned Scanner werden NIE geskippt — egal was die ENV sagt."""
    for name in ("crypto_explosion", "early_movers", "crash_monitor",
                 "market_context", "btc_divergenz", "volume_spikes", "orb",
                 "bear", "strategy_scan", "turtle", "crypto_trade_signals"):
        assert api._api_scheduler_should_skip(name) is False, name
        assert api._api_scheduler_should_skip(name, env_value="1") is False, name


def test_only_scheduler_skips_manual_scan_routes_untouched():
    """Source-Pins: Skip-Logik lebt im Scheduler; manuelle Routen bleiben voll.

    POST /api/scan (run_scan) dispatcht bi_long weiterhin direkt via
    _run_scan_safe und kennt den Skip-Helper bewusst NICHT.
    """
    scheduler_src = inspect.getsource(api._scheduler_loop)
    assert "_api_scheduler_should_skip" in scheduler_src
    manual_src = inspect.getsource(api.run_scan)
    assert "_api_scheduler_should_skip" not in manual_src
    assert '"bi_long"' in manual_src and "_run_scan_safe" in manual_src
    # Ownership-Vertrag mit bg_service (NUR Quelltext-Check, kein Import):
    bg_src = (ROOT / "bg_service.py").read_text(encoding="utf-8")
    assert "BG_API_OWNED_OVERLAP" in bg_src
    for name in _BG_OWNED:
        assert f'"{name}"' in bg_src, f"bg_service kennt {name} nicht mehr"


# ── Fix 2: Zentrale Grade-Leiter ────────────────────────────────────────────

def test_grade_ladder_thresholds():
    """Neue Leiter S>=88/A>=80/B>=65/C>=50/D — A ist damit mail-wuerdig."""
    expectations = [
        (100, "S"), (88, "S"),
        (87, "A"), (80, "A"),
        (79, "B"), (65, "B"),
        (64, "C"), (50, "C"),
        (49, "D"), (0, "D"),
    ]
    for score, expected in expectations:
        grade, _label = api._score_grade_for_value(score)
        assert grade == expected, f"Score {score}: {grade} != {expected}"
    # Konsistenz-Kern des Audits: jedes S/A erreicht das Mail-Score-Gate.
    for score in (80, 85, 88, 99):
        grade, _ = api._score_grade_for_value(score)
        assert grade in api._ALERT_TOP_GRADES
        assert score >= api._ALERT_MIN_SCORE


def test_crypto_explosion_uses_central_ladder():
    """Das Inline-Duplikat (S>=88 else A>=80 else B) ist durch die zentrale
    Funktion ersetzt; identisch, weil explosion_score<70 vorher None liefert."""
    src = inspect.getsource(api._score_crypto_explosion_candidate)
    assert "_score_grade_for_value" in src
    assert '>= 88 else "A"' not in src
    assert "if explosion_score < 70" in src  # Gate, das die Aequivalenz sichert


# ── Fix 3: toter z-Score-Block ──────────────────────────────────────────────

def test_dead_zscore_block_removed_live_path_intact():
    """Der unreachable Legacy-Block (z-Score/Beta hinter fruehem return) ist
    komplett raus; nur der Live-Pfad fuellt den BTC-Divergenz-Cache."""
    api_src = Path(api.__file__).read_text(encoding="utf-8")
    # Diese Bezeichner existierten AUSSCHLIESSLICH im toten Block:
    for marker in ("_aligned_returns", "_change_for_dates", "residual_std"):
        assert marker not in api_src, f"toter Marker noch da: {marker}"
    wrapper_src = inspect.getsource(api._btc_divergenz_wrapper)
    assert "_build_crypto_btc_divergence_results" in wrapper_src
    assert "z_score" not in wrapper_src
    assert "assets = []" not in wrapper_src
    # import api oben = Smoke; Funktion existiert und ist aufrufbar:
    assert callable(api._btc_divergenz_wrapper)
