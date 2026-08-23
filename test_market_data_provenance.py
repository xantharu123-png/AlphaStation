import pytest

import modules.data_fetchers as data_fetchers
from modules.volume_analysis import (
    OHLCV_VOLUME_PROFILE_ASSUMPTION,
    OHLCV_VOLUME_PROFILE_METHOD,
    calculate_volume_profile,
)
from modules.vrvp_levels import build_vrvp_structure


class _Response:
    status_code = 200

    def json(self):
        return {
            "results": {
                "name": "Example Holdings",
                "description": "Example company",
                "cik": "0000123456",
                "market_cap": 750_000_000,
                "share_class_shares_outstanding": 25_000_000,
                "sic_code": "7372",
                "sic_description": "PREPACKAGED SOFTWARE",
            }
        }


def _profile_bars(*, estimated=False):
    bars = []
    for index in range(30):
        low = 100.0 + (index % 3) * 0.1
        bars.append({
            "open": low + 0.2,
            "high": low + 2.0,
            "low": low,
            "close": low + 1.0,
            "volume": 1_000.0 + index,
            "volume_is_estimate": estimated,
            "data_quality": (
                "aggregated_intraday_ohlcv_estimate"
                if estimated
                else "exchange_ohlcv"
            ),
            "source_timeframe": "15m",
        })
    return bars


def test_polygon_ticker_details_preserve_sic_and_industry(monkeypatch):
    monkeypatch.setattr(data_fetchers, "rate_limited_get", lambda *args, **kwargs: _Response())

    details = data_fetchers.get_ticker_details("test-key", "EXM")

    assert details["shares_outstanding"] == 25_000_000
    assert details["market_cap"] == 750_000_000
    assert details["sic_code"] == "7372"
    assert details["sic_description"] == "PREPACKAGED SOFTWARE"
    assert details["industry"] == "PREPACKAGED SOFTWARE"


def test_volume_profile_exposes_bar_approximation_provenance_additively():
    profile = calculate_volume_profile(_profile_bars(), num_bins=20, timeframe="15m")

    assert profile is not None
    assert profile["poc"] > 0
    assert profile["approximation"] is True
    assert profile["tick_data_used"] is False
    assert profile["method"] == OHLCV_VOLUME_PROFILE_METHOD
    assert profile["volume_allocation_assumption"] == OHLCV_VOLUME_PROFILE_ASSUMPTION
    assert profile["timeframe"] == "15m"
    assert profile["bin_count"] == 20
    assert profile["bin_width"] == pytest.approx(
        (profile["range_high"] - profile["range_low"]) / 20
    )
    assert profile["data_quality"] == "ohlcv_bar_approximation"
    assert profile["input_data_quality"] == ["exchange_ohlcv"]
    assert profile["source_timeframes"] == ["15m"]
    assert profile["contributing_bar_count"] == 30
    assert profile["volume_coverage_ratio"] == pytest.approx(1.0)


def test_vrvp_structure_carries_estimated_input_provenance_without_breaking_legacy_shape():
    structure = build_vrvp_structure(
        _profile_bars(estimated=True),
        101.0,
        "LONG",
        timeframe="15m",
        num_bins=20,
        min_bars=20,
    )

    assert structure is not None
    legacy_keys = {
        "timeframe", "direction", "bars", "poc", "vah", "val",
        "range_high", "range_low", "supports", "resistances", "levels",
        "volume_voids", "profile_quality", "source",
    }
    assert legacy_keys.issubset(structure)
    assert structure["source"] == "ohlcv_volume_profile"
    assert structure["approximation"] is True
    assert structure["profile_method"] == OHLCV_VOLUME_PROFILE_METHOD
    assert structure["bin_width"] > 0
    assert structure["data_quality"] == "estimated_ohlcv_bar_approximation"

    provenance = structure["provenance"]
    assert provenance["approximation"] is True
    assert provenance["tick_data_used"] is False
    assert provenance["method"] == OHLCV_VOLUME_PROFILE_METHOD
    assert provenance["timeframe"] == "15m"
    assert provenance["bin_count"] == 20
    assert provenance["bin_width"] == pytest.approx(structure["bin_width"])
    assert provenance["bar_count"] == 30
    assert provenance["data_quality"] == "estimated_ohlcv_bar_approximation"
    assert provenance["profile_quality"] == "ok"
    assert provenance["input_data_quality"] == ["aggregated_intraday_ohlcv_estimate"]
    assert provenance["source_timeframes"] == ["15m"]
    assert provenance["volume_is_estimate"] is True
