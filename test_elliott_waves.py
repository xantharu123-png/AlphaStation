"""Offline price fixtures: counts are geometry evidence, never order plans."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from modules.elliott_waves import analyze_elliott, validate_elliott_report
from modules.level_zones import CompletedBar


UTC = timezone.utc
START = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
FAMILIES = {
    "impulse": [100, 110, 105, 125, 116, 132],
    "zigzag": [100, 115, 107, 124],
    "regular_flat": [100, 120, 101, 124],
    "expanded_flat": [100, 115, 97, 125],
    "contracting_triangle": [100, 120, 105, 116, 108, 111],
}
SUBDIVISIONS = {
    "impulse": [5, 3, 5, 3, 5], "zigzag": [5, 3, 5],
    "regular_flat": [3, 3, 5], "expanded_flat": [3, 3, 5],
    "contracting_triangle": [3, 3, 3, 3, 3],
}


def _bars_from_prices(prices):
    return [{"timestamp": (START + timedelta(days=i)).isoformat(),
             "close_time": (START + timedelta(days=i, hours=6, minutes=30)).isoformat(),
             "open": price, "high": price + .01, "low": price - .01,
             "close": price, "volume": 1000.0} for i, price in enumerate(prices)]


def _path(vertices, spacing=10):
    prices = []
    for a, b in zip(vertices, vertices[1:]):
        prices.extend(a + (b - a) * j / spacing for j in range(spacing))
    return prices + [vertices[-1]]


def pattern_bars(family="impulse", *, mirror=False, subdivide=False):
    vertices = FAMILIES[family]
    transform = lambda value: 300 - value if mirror else value
    if subdivide:
        path = []
        for a, b, n in zip(vertices, vertices[1:], SUBDIVISIONS[family]):
            fractions = [0, .35, .20, .75, .60, 1] if n == 5 else [0, .65, .35, 1]
            path.extend(_path([a + (b - a) * x for x in fractions], spacing=2)[:-1])
        prices = [vertices[0] + 4 - i * .5 for i in range(8)] + path + [vertices[-1]]
        prices += [vertices[-1] - .4 * i for i in range(1, 10)]
    else:
        prices = _path([vertices[0] + 8, *vertices, vertices[-1] - 8])
    return _bars_from_prices([transform(p) for p in prices])


def report_for(bars, **kwargs):
    as_of = datetime.fromisoformat(bars[-1]["close_time"]) + timedelta(seconds=1)
    return analyze_elliott(bars, as_of=as_of, **kwargs)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mirror", [False, True])
def test_each_known_family_is_detected_as_geometry_not_fabricated_subwaves(family, mirror):
    report = report_for(pattern_bars(family, mirror=mirror))
    matches = [p for p in report["patterns"] if p["family"] == family]
    assert matches, report
    pattern = matches[0]
    assert pattern["direction"] == ("SHORT" if mirror else "LONG")
    assert pattern["pattern_status"] == "geometry_only"
    assert pattern["subdivision_status"] == "unverified"
    assert pattern["trade_ready"] is False and pattern["mail_eligible"] is False
    assert pattern["signal_kind"] == "pattern_context"
    assert not any(k in pattern for k in ("entry", "stop", "tp", "tp1", "tp2", "win_probability"))
    assert all(w["observed_subwaves"] == 1 for w in pattern["waves"])
    assert validate_elliott_report(report)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mirror", [False, True])
def test_confirmed_pattern_requires_real_nested_price_turns(family, mirror):
    report = report_for(pattern_bars(family, mirror=mirror, subdivide=True))
    matches = [p for p in report["patterns"] if p["family"] == family and p["pattern_status"] == "confirmed"]
    assert matches, [(p["family"], p["pattern_status"], p["pivot_radius"], [w["observed_subwaves"] for w in p["waves"]]) for p in report["patterns"]]
    match = matches[0]
    assert [w["observed_subwaves"] for w in match["waves"]] == SUBDIVISIONS[family]
    assert all(w["subdivision_status"] == "verified" for w in match["waves"])
    for wave in match["waves"]:
        for point in wave["subwaves"]:
            source = pattern_bars(family, mirror=mirror, subdivide=True)[point["index"]]
            assert point["price"] == source[point["price_field"]]
            assert point["confirmed_at"] > point["observed_at"]
    assert validate_elliott_report(report)


@pytest.mark.parametrize("vertices", [
    [100, 110, 99, 125, 116, 132],  # wave2 violates origin
    [100, 120, 116, 131, 123, 140],  # wave3 15 shortest of20,15,17
    [100, 110, 105, 125, 109, 132],  # wave4 overlaps wave1
    [100, 110, 105, 125, 116, 124],  # truncated fifth unsupported
])
@pytest.mark.parametrize("mirror", [False, True])
def test_impulse_hard_rules_cannot_be_overridden_by_attractive_endpoint_ratios(vertices, mirror):
    prices = _path([108, *vertices, vertices[-1] - 8])
    report = report_for(_bars_from_prices([300 - p if mirror else p for p in prices]))
    assert not [p for p in report["patterns"] if p["family"] == "impulse"]


@pytest.mark.parametrize("family,vertices", [
    ("zigzag", [100, 115, 98, 124]),
    ("zigzag", [100, 115, 107, 113]),
    ("regular_flat", [100, 120, 110, 124]),
    ("regular_flat", [100, 120, 97, 124]),
    ("expanded_flat", [100, 115, 101, 125]),
    ("expanded_flat", [100, 115, 97, 112]),
    ("contracting_triangle", [100, 120, 98, 116, 108, 111]),
    ("contracting_triangle", [100, 120, 105, 123, 108, 115]),
    ("contracting_triangle", [100, 120, 105, 116, 108, 115]),
])
def test_invalid_correction_geometry_is_not_labeled_as_requested_family(family, vertices):
    report = report_for(_bars_from_prices(_path([108, *vertices, vertices[-1] - 8])))
    assert not [p for p in report["patterns"] if p["family"] == family]


def test_unclosed_right_support_cannot_confirm_final_endpoint():
    bars = pattern_bars()
    end_index = 60  # Last peak in literal 10-bar legs.
    cutoff = datetime.fromisoformat(bars[end_index + 2]["close_time"])
    early = analyze_elliott(bars, as_of=cutoff)
    assert not any(p["family"] == "impulse" for p in early["patterns"])
    later = analyze_elliott(bars, as_of=datetime.fromisoformat(bars[end_index + 3]["close_time"]))
    assert any(p["family"] == "impulse" for p in later["patterns"])


def test_appending_future_bars_never_rewrites_known_historical_count_or_its_clock():
    bars = pattern_bars()
    prior = report_for(bars)
    extra = _bars_from_prices([120 - i * .1 for i in range(10)])
    for i, row in enumerate(extra, len(bars)):
        row["timestamp"] = (START + timedelta(days=i)).isoformat()
        row["close_time"] = (START + timedelta(days=i, hours=6, minutes=30)).isoformat()
    extended = report_for(bars + extra)
    prior_patterns = {p["id"]: p for p in prior["patterns"]}
    later_patterns = {p["id"]: p for p in extended["patterns"]}
    for pattern_id in prior_patterns.keys() & later_patterns.keys():
        assert later_patterns[pattern_id]["points"] == prior_patterns[pattern_id]["points"]
        assert later_patterns[pattern_id]["waves"] == prior_patterns[pattern_id]["waves"]
        assert later_patterns[pattern_id]["confirmed_at"] == prior_patterns[pattern_id]["confirmed_at"]
    assert any(p["family"] == "impulse" for p in extended["patterns"])


@pytest.mark.parametrize("field,value", [("high", True), ("low", float("nan")), ("close", float("inf")), ("open", None), ("volume", -1), ("high", 1), ("timestamp", None)])
def test_invalid_completed_evidence_is_rejected_not_silently_dropped(field, value):
    bars = pattern_bars()
    bars[25][field] = value
    if field == "timestamp":
        bars[25]["close_time"] = None
    result = report_for(bars)
    assert result["status"] == "invalid_data" and result["patterns"] == []


def test_unfinished_bad_candle_is_excluded_without_erasing_completed_history():
    bars = pattern_bars()
    expected = report_for(bars)
    tail = {**bars[-1], "high": float("nan"), "is_closed": False}
    actual = analyze_elliott(bars + [tail], as_of=expected["as_of"])
    assert actual == expected


def test_conflicting_duplicate_close_invalidates_report_identical_duplicate_does_not():
    bars = pattern_bars()
    expected = report_for(bars)
    assert analyze_elliott(bars + [dict(bars[20])], as_of=expected["as_of"]) == expected
    duplicate = {**bars[20], "volume": 1001}
    assert analyze_elliott(bars + [duplicate], as_of=expected["as_of"])["reason"] == "conflicting_completed_bars"


@pytest.mark.parametrize("bars,reason", [(None, "invalid_bars_payload"), ({}, "invalid_bars_payload"), ([], "minimum_completed_bars_missing"), ([{}] * 801, "analysis_window_exceeds_bounded_model")])
def test_invalid_or_unbounded_input_is_explicit(bars, reason):
    result = analyze_elliott(bars, as_of=START)
    assert result["reason"] == reason and result["patterns"] == []


@pytest.mark.parametrize("kwargs", [{"as_of": datetime(2025, 1, 1)}, {"as_of": START, "timeframe": "DAY"}, {"as_of": START, "direction": "BUY"}, {"as_of": START, "timestamp_mode": "guess"}])
def test_invalid_analysis_clock_or_configuration_raises(kwargs):
    with pytest.raises(ValueError):
        analyze_elliott([], **kwargs)


def test_result_is_json_serializable_and_direction_filters_observed_move():
    bars = pattern_bars()
    report = report_for(bars, direction="LONG")
    assert report["patterns"] and all(p["direction"] == "LONG" for p in report["patterns"])
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert all(p["direction"] == "SHORT" for p in report_for(bars, direction="SHORT")["patterns"])


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(trade_ready=True),
    lambda d: d.update(mail_eligible=0),
    lambda d: d.update(model="legacy_elliott"),
    lambda d: d.update(bars_used=True),
    lambda d: d["patterns"][0].update(pattern_status="confirmed"),
    lambda d: d["patterns"][0].update(direction="SHORT"),
    lambda d: d["patterns"][0].update(invalidation_level=999),
    lambda d: d["patterns"][0]["points"][0].update(price=True),
    lambda d: d["patterns"][0]["points"][0].update(confirmed_at="2099-01-01T00:00:00Z"),
    lambda d: d["patterns"][0]["points"][0].update(time=True),
    lambda d: d["patterns"][0]["waves"][0].update(subdivision_status="verified"),
    lambda d: d["patterns"][0].update(rule_checks=[]),
    lambda d: d["patterns"][0]["ratios"].update(wave2_retrace=float("nan")),
    lambda d: d["patterns"][0]["points"][0].update(session="not-a-day"),
    lambda d: d["patterns"][0].update(bars_since_completed=0),
    lambda d: d["patterns"][0].update(degree="guaranteed_macro_trend"),
    lambda d: d["patterns"][0].update(direction_meaning="next_trade_direction"),
])
def test_stored_report_validator_rejects_forged_or_inconsistent_claims(mutate):
    report = report_for(pattern_bars())
    assert validate_elliott_report(report)
    mutate(report)
    assert not validate_elliott_report(report)


def test_stored_report_cannot_borrow_later_analysis_clock():
    report = report_for(pattern_bars())
    assert not validate_elliott_report(report, as_of=START)
    assert not validate_elliott_report(report, timeframe="4H")


def test_completed_bar_objects_match_canonical_mapping_evidence():
    bars = pattern_bars()
    objects = [CompletedBar(datetime.fromisoformat(b["timestamp"]), datetime.fromisoformat(b["close_time"]), b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars]
    report = report_for(bars)
    assert analyze_elliott(objects, as_of=report["as_of"]) == report


def test_stored_nested_evidence_cannot_postdate_whole_pattern_confirmation():
    report = report_for(pattern_bars("impulse", subdivide=True))
    pattern = next(p for p in report["patterns"] if p["family"] == "impulse" and p["pattern_status"] == "confirmed")
    report["patterns"] = [pattern]
    assert validate_elliott_report(report)
    # An inner turn observed early but not confirmed until the final snapshot
    # was not available at the claimed historical pattern confirmation time.
    pattern["waves"][0]["subwaves"][1]["confirmed_at"] = report["latest_completed_at"]
    assert not validate_elliott_report(report)


def test_minor_pivot_ties_and_ambiguous_intrabar_order_never_create_full_count():
    bars = pattern_bars("impulse", subdivide=True)
    original = report_for(bars)
    impulse = next(p for p in original["patterns"] if p["family"] == "impulse" and p["pattern_status"] == "confirmed")
    point = impulse["waves"][0]["subwaves"][1]
    index = point["index"]
    bars[index + 1]["high"] = bars[index]["high"]
    changed = report_for(bars)
    assert not any(p["id"] == impulse["id"] and p["pattern_status"] == "confirmed" for p in changed["patterns"])


def test_internal_extreme_violation_cannot_hide_between_selected_main_points():
    bars = pattern_bars("impulse")
    original = report_for(bars)
    impulse = next(p for p in original["patterns"] if p["family"] == "impulse")
    # An outside bar between waves 3 and 4 violates wave1 territory even
    # though endpoint4 still looks correct. Exact old count must disappear.
    idx = 46
    bars[idx]["low"] = 105
    bars[idx]["high"] = 126
    report = report_for(bars)
    assert not any(p["id"] == impulse["id"] for p in report["patterns"])


def test_large_input_is_consumed_only_to_bounded_overflow_marker():
    consumed = []
    def source():
        for i in range(100_000):
            consumed.append(i)
            yield {}
    report = analyze_elliott(source(), as_of=START)
    assert report["reason"] == "analysis_window_exceeds_bounded_model"
    assert len(consumed) == 801


def test_report_freezes_versioned_parameter_snapshot():
    report = report_for(pattern_bars())
    report["parameters"]["pivot_radii"].append(100)
    fresh = report_for(pattern_bars())
    assert fresh["parameters"]["pivot_radii"] == [3, 5, 8]
    assert not validate_elliott_report(report)


def test_ambiguous_minor_outside_bar_invalidates_only_its_subwave_proof():
    source = pattern_bars("impulse", subdivide=True)
    prices = []
    for a, b in zip(source, source[1:]):
        prices.extend([a["close"], (a["close"] + b["close"]) / 2])
    prices.append(source[-1]["close"])
    bars = _bars_from_prices(prices)
    before = report_for(bars)
    impulse = next(p for p in before["patterns"] if p["family"] == "impulse" and p["pattern_status"] == "confirmed")
    assert impulse["points"][0]["index"] < 18 < impulse["points"][1]["index"]
    bars[18]["high"] = max(bars[17]["high"], bars[19]["high"]) + .001
    bars[18]["low"] = min(bars[17]["low"], bars[19]["low"]) - .001
    after = report_for(bars)
    changed = next(p for p in after["patterns"] if p["id"] == impulse["id"])
    assert changed["pattern_status"] == "geometry_only"
    assert [w["subdivision_status"] for w in changed["waves"]] == ["unverified", "verified", "verified", "verified", "verified"]
    assert validate_elliott_report(after)


def test_ambiguous_minor_outside_bar_before_pattern_does_not_poison_valid_proof():
    bars = pattern_bars("impulse", subdivide=True)
    before = report_for(bars)
    impulse = next(p for p in before["patterns"] if p["family"] == "impulse" and p["pattern_status"] == "confirmed")
    bars[2]["high"] = max(bars[1]["high"], bars[3]["high"]) + .001
    bars[2]["low"] = min(bars[1]["low"], bars[3]["low"]) - .001
    after = report_for(bars)
    assert any(p["id"] == impulse["id"] and p["pattern_status"] == "confirmed" for p in after["patterns"])


@pytest.mark.parametrize("label", ["A", "1", "C", "X", None, 1])
def test_verified_subwave_label_must_match_its_actual_topology(label):
    report = report_for(pattern_bars("impulse", subdivide=True))
    impulse = next(p for p in report["patterns"] if p["family"] == "impulse" and p["pattern_status"] == "confirmed")
    report["patterns"] = [impulse]
    impulse["waves"][0]["subwaves"][2]["label"] = label
    assert not validate_elliott_report(report)


def test_stored_report_cannot_reintroduce_counts_outside_analysis_horizon():
    report = report_for(pattern_bars())
    report["bars_used"] = 300
    later = START + timedelta(days=300)
    report["as_of"] = later.isoformat()
    report["latest_completed_at"] = later.isoformat()
    for pattern in report["patterns"]:
        pattern["bars_since_completed"] = 299 - pattern["points"][-1]["index"]
    assert not validate_elliott_report(report)
