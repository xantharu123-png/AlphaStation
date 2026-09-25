from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_frontend_normalizes_legacy_breakout_labels_and_caps_display_score():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "Math.min(96, rawScore)" in source
    assert "'Durchzug OK': 'Stark'" in source
    assert "'Durchzug moeglich': 'Gemischt'" in source
    assert "'Durchzug schwach': 'Dochtrisiko'" in source
    assert "'Bestaetigung stark': 'Stark'" in source
    assert '>Tagesqualität</th>' in source
    assert 'getBreakoutQuality(item).score}/100' not in source
    assert 'getBreakoutQuality(item).score}/96' in source
    assert 'Keine Einstiegsfreigabe und kein Rücktestnachweis.' in source


def test_frontend_breakout_quality_suppression_label_names_actual_gates():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert (
        "swing_momentum_breakout_quality_wait_retest: "
        "'Ausbruchsqualität noch unzureichend – Kerzenbild und Entry-Abstand prüfen'"
    ) in source
    assert "Breakout-Qualität noch nicht bestätigt – Retest abwarten" not in source
