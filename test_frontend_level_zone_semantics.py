from pathlib import Path


SOURCE = Path("frontend/index.html").read_text(encoding="utf-8")


def test_both_charts_render_adaptive_zone_boundaries_with_strength_and_overlap():
    assert "function renderAdaptiveLevelBoundaries" in SOURCE
    assert "level.zone_low ?? level.lower" in SOURCE
    assert "level.zone_high ?? level.upper" in SOURCE
    assert "sr.overlapping_zones" in SOURCE
    assert "const width = strength >= 75 ? 3 : strength >= 40 ? 2 : 1" in SOURCE
    assert "OVERLAP" in SOURCE
    assert "ZONE ${quality} LOW" in SOURCE
    assert "ZONE ${quality} HIGH" in SOURCE
    # Definition plus one invocation in each chart component.
    assert SOURCE.count("renderAdaptiveLevelBoundaries({") == 3
    assert "addPriceLine(lvl.price" not in SOURCE


def test_vwap_labels_fail_closed_to_hlc3_without_explicit_metadata():
    assert "meta?.true_intraday_vwap === true ? 'VWAP' : proxyLabel" in SOURCE
    assert SOURCE.count("vwapDisplayLabel(chartData?.vwap_meta, 'HLC3-Proxy')") == 2
    assert SOURCE.count("vwapDisplayLabel(d.vwap_meta, 'Daily HLC3')") == 2
    assert "{ key: 'vwap', label: 'VWAP'" not in SOURCE
    assert "title: 'VWAP'" not in SOURCE


def test_target_quality_distinguishes_first_barrier_from_projection_only():
    assert "targetQualityKey === 'STRUCTURAL_FIRST_BARRIER'" in SOURCE
    assert "Erste Gegenbarriere strukturell bestaetigt" in SOURCE
    assert "q === 'STRUCTURAL_FIRST_BARRIER'" in SOURCE
    assert "Erste Gegenbarriere (strukturell)" in SOURCE
    assert "targetQualityKey.startsWith('PROJECTION_ONLY')" in SOURCE
    assert "q.startsWith('PROJECTION_ONLY')" in SOURCE
    assert "Nur Projektion - kein Strukturziel" in SOURCE
