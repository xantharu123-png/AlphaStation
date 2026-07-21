from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_frontend_normalizes_legacy_breakout_labels_and_caps_display_score():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "Math.min(96, rawScore)" in source
    assert "'Durchzug OK': 'Bestaetigung stark'" in source
    assert "'Durchzug moeglich': 'Bestaetigung gemischt'" in source
    assert "'Durchzug schwach': 'Wick-Risiko'" in source
