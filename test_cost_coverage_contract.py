"""Known-cost arithmetic must not claim a complete net result."""
import pytest

from modules.trade_health import calculate_trade_health, _execution_cost_coverage


@pytest.mark.parametrize("costs,basis,missing", [
    ({}, "gross_no_cost_data", ["spread", "fees", "slippage"]),
    ({"spread_pct": .1}, "after_known_costs", ["fees", "slippage"]),
    ({"spread_pct": .1, "fee_pct": .05}, "after_known_costs", ["slippage"]),
    ({"spread_pct": .1, "fee_pct": .05, "slippage_pct": 0}, "net", []),
    ({"execution_cost_pct": .2}, "net", []),
])
def test_rr_basis_reports_only_available_cost_components(costs, basis, missing):
    row = dict(ticker="COST", direction="LONG", current_price=10, entry=10,
               stop=9.5, target1=11, target2=12, rvol=2.5, vol_confirmed=True,
               vwap_aligned=True, close_pos=.86, dollar_volume=8_000_000, **costs)
    metrics = calculate_trade_health(row, "orb")["metrics"]
    assert metrics["rr_cost_basis"] == basis
    assert metrics["execution_cost_coverage"]["missing_components"] == missing
    assert metrics["execution_cost_coverage"]["complete"] is (not missing)


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), True])
def test_invalid_cost_is_not_a_complete_cost_estimate(invalid):
    assert not _execution_cost_coverage({"execution_cost_pct": invalid}, None)["complete"]
