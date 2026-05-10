from pathlib import Path


def test_frontend_rvol_values_show_factor_suffix():
    source = Path("frontend/index.html").read_text(encoding="utf-8")

    assert "item.rvol?.toFixed(2)}</td>" not in source
    assert "item.rvol?.toFixed(2) ||" not in source
    assert "${item.rvol.toFixed(2)}x" in source
