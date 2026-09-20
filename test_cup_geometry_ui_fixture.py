"""The visual QA fixture is explicit synthetic data, never an EXPD replay."""
import importlib.util
from pathlib import Path
import sys

from modules.cup_pattern_evidence import project_cup_geometry_evidence


def fixture_module():
    scripts = Path(__file__).resolve().parent / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("cup_visual_fixture", scripts / "audit_cup_geometry_fixture.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


def test_visual_fixture_anchors_match_the_synthetic_ohlcv_exactly():
    fixture = fixture_module()
    bars, row = fixture.cup_fixture()
    evidence = row["cup_pattern_evidence"]
    assert row["ticker"] == "CUPQA"
    assert "Synthetischer" in row["company_name"]
    assert evidence == project_cup_geometry_evidence(evidence, symbol=row["ticker"])
    assert len(bars) == evidence["cup_length"] + evidence["handle_length"] == 100
    for anchor in evidence["anchors"].values():
        assert anchor["price"] == bars[anchor["index"]][anchor["price_field"]]


def test_visual_fixture_is_reproducible_and_does_not_mutate_shared_bars():
    fixture = fixture_module()
    bars, row = fixture.cup_fixture()
    bars[0]["close"] = 0
    row["cup_pattern_evidence"]["anchors"].clear()
    bars_again, row_again = fixture.cup_fixture()
    assert bars_again[0]["close"] == 100
    assert len(row_again["cup_pattern_evidence"]["anchors"]) == 7
