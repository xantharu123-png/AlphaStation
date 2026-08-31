"""Core regressions for the strict stock-BI 17-of-20 contract."""

import pytest

import modules.patterns as patterns
from test_bi_deep_fixes_patterns import gen_macd_turn, gen_textbook_accumulation


def _bars():
    return gen_textbook_accumulation(seed=11)[-50:]


def _force_checks(monkeypatch, green, unavailable=()):
    original = patterns._bi_indicator_check
    unavailable = set(unavailable)

    def forced(indicator_id, *, available, passed, points, reason):
        is_available = indicator_id not in unavailable
        return original(
            indicator_id,
            available=is_available,
            passed=is_available and indicator_id <= green,
            points=points,
            reason=reason,
        )

    monkeypatch.setattr(patterns, "_bi_indicator_check", forced)


def test_result_preserves_legacy_eight_tuple_and_exposes_exact_contract():
    result = patterns.analyze_breakout_imminent(_bars(), direction="long")

    assert isinstance(result, tuple)
    assert len(result) == 8
    assert len(result.indicator_checks) == patterns.BI_STOCK_INDICATOR_COUNT == 20
    assert [item["id"] for item in result.indicator_checks] == list(range(1, 21))
    assert len({item["key"] for item in result.indicator_checks}) == 20
    assert all(type(item["available"]) is bool for item in result.indicator_checks)
    assert all(type(item["passed"]) is bool for item in result.indicator_checks)
    assert result.required_green == patterns.BI_STOCK_REQUIRED_GREEN == 17
    assert result.green_count == sum(item["passed"] for item in result.indicator_checks)
    assert result.available_count == sum(item["available"] for item in result.indicator_checks)


@pytest.mark.parametrize("green, expected", [(16, False), (17, True), (20, True)])
def test_weighted_score_cannot_bypass_or_replace_17_of_20(monkeypatch, green, expected):
    _force_checks(monkeypatch, green)
    result = patterns.analyze_breakout_imminent(_bars(), direction="long")

    assert result.indicator_contract_ok is True
    assert result.green_count == green
    assert result[0] is expected
    assert result[4] == green * 5


def test_any_unavailable_indicator_fails_closed(monkeypatch):
    _force_checks(monkeypatch, 20, unavailable={13})
    result = patterns.analyze_breakout_imminent(_bars(), direction="long")

    assert result.available_count == 19
    assert result.indicator_contract_ok is False
    assert result[0] is False
    assert result[4] == 0


def test_hard_gate_still_overrides_twenty_green_checks(monkeypatch):
    _force_checks(monkeypatch, 20)
    bars = _bars()
    previous_close = bars[-2]["close"]
    pumped_close = previous_close * 1.40
    bars[-1] = {
        **bars[-1],
        "open": previous_close,
        "low": previous_close * 0.99,
        "high": pumped_close * 1.01,
        "close": pumped_close,
    }

    result = patterns.analyze_breakout_imminent(bars, direction="long")

    assert result.green_count == 20
    assert result.indicator_contract_ok is True
    assert result[0] is False
    assert "last_bar_pump" in result.hard_gate_failures


def test_crypto_does_not_claim_stock_twenty_indicator_contract(monkeypatch):
    _force_checks(monkeypatch, 20)
    result = patterns.analyze_breakout_imminent(_bars(), direction="long", crypto_mode=True)

    assert len(result.indicator_checks) == 20
    assert result.indicator_contract_ok is False
    assert result[0] is False


def test_macd_and_adx_availability_require_sufficient_history():
    bars = gen_macd_turn(n=50)
    result_35 = patterns.analyze_breakout_imminent(bars[-35:], direction="long")
    result_36 = patterns.analyze_breakout_imminent(bars[-36:], direction="long")

    by_id_35 = {item["id"]: item for item in result_35.indicator_checks}
    by_id_36 = {item["id"]: item for item in result_36.indicator_checks}
    assert by_id_35[13]["available"] is False
    assert by_id_36[13]["available"] is True
    assert by_id_36[7]["available"] is True


def test_range_duration_counts_current_bar():
    bars = []
    for _ in range(30):
        bars.append({"open": 90.0, "high": 90.4, "low": 89.6, "close": 90.0, "volume": 1_000_000})
    for _ in range(6):
        bars.append({"open": 100.0, "high": 100.4, "low": 99.6, "close": 100.0, "volume": 1_000_000})

    result = patterns.analyze_breakout_imminent(bars, direction="long")
    duration = result.indicator_checks[4]

    assert duration["available"] is True
    assert duration["passed"] is True
    assert any("Konsolidierung: 6 Tage" in detail for detail in result[3])


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError, match="direction"):
        patterns.analyze_breakout_imminent(_bars(), direction="sideways")
