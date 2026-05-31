from pathlib import Path


def test_frontend_rvol_values_show_factor_suffix():
    source = Path("frontend/index.html").read_text(encoding="utf-8")

    assert "item.rvol?.toFixed(2)}</td>" not in source
    assert "item.rvol?.toFixed(2) ||" not in source
    assert "${item.rvol.toFixed(2)}x" in source


def test_frontend_shows_crypto_target_quality_and_sources():
    source = Path("frontend/index.html").read_text(encoding="utf-8")

    assert "Zielzonen schwach" in source
    assert "VRVP/Volume-Profile bestaetigt" in source
    assert "TP1/TP2 sind rechnerische Range/Fib-Projektionen" in source
    assert "humanizeLevelSource(activeTradeSetup?.tp1_source" in source
