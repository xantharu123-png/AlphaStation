"""Render-source contracts supplementary to actual local browser acceptance."""
from pathlib import Path


SOURCE = (Path(__file__).parent / "frontend/index.html").read_text(encoding="utf-8")


def test_exact_fibonacci_labels_in_both_chart_implementations():
    assert SOURCE.count("const extendedKeys = ['127.2%', '161.8%', '200%'];") == 2
    for label in ("23.6%", "38.2%", "61.8%", "78.6%"):
        assert SOURCE.count(f"'{label}':") == 2
    assert "'161%':" not in SOURCE and "'61%':" not in SOURCE


def test_unsupported_markets_are_disabled_and_guide_is_explicit():
    for market in ("futures", "forex"):
        assert f'<option value="{market}" disabled>' in SOURCE
    assert "const guide = strat.scan_supported === false" in SOURCE
    assert "noch kein Scanner implementiert" in SOURCE


def test_crypto_venue_alternatives_keep_each_complete_plan_separate():
    block = SOURCE.split("function CryptoTradeSignalsTab(", 1)[1].split("function ", 1)[0]
    for field in ("entry", "stop", "tp1", "tp2", "risk_reward", "risk_rank", "trade_score",
                  "entry_score", "scanner_source", "spread_pct", "funding_rate_pct"):
        assert f"plan.{field}" in block
    assert "Eigenständige Pläne; keine Mischung von Börsenpreisen." in block
    assert "Quelle nicht belegt" in block
    assert "item.entry_score || 0" not in block
    assert "item.risk_level || 'LOW'" not in block


def test_wyckoff_guide_does_not_claim_optional_retest_is_required():
    assert "gehaltenen Ruecktest" not in SOURCE
    assert "gehaltenem Ruecktest" not in SOURCE
    assert "spaeterer Ruecktest ist eine Warnung" in SOURCE
