"""Local replay contracts. Synthetic recognition is not trading-performance evidence."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from scripts import evaluate_wyckoff as replay
from test_wyckoff_engine import BASE, textbook_bars


def dataset():
    bars = textbook_bars("LONG", spring=False)
    for bar in bars:
        for name in ("open_time", "close_time"):
            bar[name] = bar[name].isoformat()
    return {"timeframe": "1D", "as_of": (BASE + timedelta(days=100)).isoformat(), "bars": bars}


def test_each_confirmed_trigger_mode_counts_once_without_inventing_accuracy():
    report = replay.evaluate(dataset())
    assert report["distinct_signal_count"] == 2
    assert sum(bool(row["active_signals"]) for row in report["observations"]) > 1
    by_mode = {row["trigger_mode"]: row for row in report["signals"]}
    assert by_mode["confirmed_breakout"]["first_seen_at"] == "2026-03-22T00:00:00.000000Z"
    assert by_mode["confirmed_retest"]["first_seen_at"] == "2026-03-29T00:00:00.000000Z"
    assert report["label_comparison"] is None
    assert report["profitability_evaluated"] is False
    assert report["real_world_accuracy_verified"] is False
    assert "Unlabelled observations are not negatives" in report["disclaimer"]
    assert "win_rate" not in report and "profit" not in report


def test_cutoff_never_sees_future_bar_and_prefix_replay_is_stable(monkeypatch):
    doc = dataset()
    doc["as_of"] = doc["bars"][86]["close_time"]
    doc["cutoffs"] = [doc["bars"][69]["close_time"], doc["bars"][86]["close_time"]]
    expected = replay.evaluate(doc)
    doc["bars"][-1].update(open=200., high=201., low=199., close=200., volume=999999.)
    assert replay.evaluate(doc) == expected
    actual = replay.analyze_wyckoff
    seen = []

    def observe(bars, **kwargs):
        assert all(bar["close_time"] <= kwargs["as_of"] for bar in bars)
        seen.append(len(bars))
        return actual(bars, **kwargs)

    monkeypatch.setattr(replay, "analyze_wyckoff", observe)
    replay.evaluate(doc)
    assert seen == [70, 87]


def test_event_identity_ignores_latest_price_score_phase_and_additional_events():
    doc = dataset()
    row = replay.analyze_wyckoff(doc["bars"], as_of=doc["as_of"], timeframe="1D", direction="LONG")["patterns"][0]
    expected = replay._signal_identity(row)
    changed = deepcopy(row)
    changed.update(score=1, phase="E", latest_completed_at="2027-01-01T00:00:00Z")
    changed["trade"]["entry"] += 1
    changed["events"].append({"name": "TrendContinuation"})
    assert replay._signal_identity(changed) == expected
    changed["events"][-2]["observed_at"] = "2026-03-30T00:00:00Z"
    assert replay._signal_identity(changed) != expected


def test_only_explicit_independent_labels_enter_comparison():
    doc = dataset()
    doc["label_source"] = "Synthetic test author; not an independent real-market study"
    doc["labels"] = [
        {"cutoff": doc["bars"][69]["close_time"], "direction": "LONG", "expected_signal": False},
        {"cutoff": doc["bars"][99]["close_time"], "direction": "LONG", "expected_signal": True},
        {"cutoff": doc["bars"][99]["close_time"], "direction": "SHORT", "expected_signal": False},
        {"cutoff": doc["bars"][20]["close_time"], "direction": "LONG", "expected_signal": True},
    ]
    comparison = replay.evaluate(doc)["label_comparison"]
    assert comparison["labels_supplied"] == 4 and comparison["labels_scored"] == 3
    assert comparison["true_positive"] == 1 and comparison["true_negative"] == 2
    assert comparison["false_positive"] == comparison["false_negative"] == 0
    assert comparison["unscored_labels"][0]["reason"] == "minimum_completed_bars_missing"
    assert comparison["independence_verified"] is False


@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("timeframe"),
    lambda d: d.update(as_of="2026-04-11T00:00:00"),
    lambda d: d["bars"][0].pop("close_time"),
    lambda d: d["bars"][0].update(close_time="2026-01-02T00:00:00"),
    lambda d: d["bars"][0].update(close_time=d["bars"][0]["open_time"]),
    lambda d: d["bars"][0].update(volume=None),
    lambda d: d["bars"][0].update(volume=float("nan")),
    lambda d: d["bars"][0].update(volume=-1),
    lambda d: d["bars"][0].update(high=False),
    lambda d: d["bars"][0].update(low=10000),
    lambda d: d["bars"][0].update(is_closed=False),
    lambda d: d["bars"].append(deepcopy(d["bars"][0])),
    lambda d: d.update(cutoffs=["2099-01-01T00:00:00Z"]),
    lambda d: d.update(cutoffs=[d["bars"][0]["close_time"]] * 2),
    lambda d: d.update(labels=[{"cutoff": d["as_of"], "direction": "LONG", "expected_signal": True}]),
    lambda d: d.update(label_source="test", labels=[{"cutoff": d["as_of"], "direction": "LONG", "expected_signal": "true"}]),
])
def test_bad_inputs_are_rejected_not_converted_to_no_signal(mutation):
    doc = dataset()
    mutation(doc)
    with pytest.raises(ValueError):
        replay.evaluate(doc)


def test_order_and_timezone_offsets_do_not_change_replay():
    doc = dataset()
    expected = replay.evaluate(doc)
    doc["bars"].reverse()
    doc["as_of"] = "2026-04-11T02:00:00+02:00"
    assert replay.evaluate(doc) == expected


def test_fractional_timestamps_do_not_sort_future_candle_before_exact_cutoff(monkeypatch):
    first = {"open_time": "2026-01-01T00:00:00Z", "close_time": "2026-01-01T00:01:00Z",
             "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.}
    second = {**first, "open_time": "2026-01-01T00:01:00Z", "close_time": "2026-01-01T00:01:00.000001Z"}
    doc = {"timeframe": "1M", "as_of": second["close_time"], "bars": [second, first]}
    observed = []
    actual = replay.analyze_wyckoff

    def observe(bars, **kwargs):
        observed.append(len(bars))
        return actual(bars, **kwargs)

    monkeypatch.setattr(replay, "analyze_wyckoff", observe)
    replay.evaluate(doc)
    assert observed == [1, 2]


def test_cli_creates_new_report_and_never_overwrites_input_or_report(tmp_path, capsys):
    source = tmp_path / "input.json"
    target = tmp_path / "report.json"
    contents = json.dumps(dataset())
    source.write_text(contents, encoding="utf-8")
    assert replay.main([str(source), "--output", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["label_comparison"] is None
    previous = target.read_bytes()
    assert replay.main([str(source), "--output", str(target)]) == 2
    assert target.read_bytes() == previous
    assert replay.main([str(source), "--output", str(source)]) == 2
    assert source.read_text(encoding="utf-8") == contents
    assert "failed" in capsys.readouterr().err


def test_invalid_json_exits_without_report(tmp_path):
    source = tmp_path / "bad.json"
    output = tmp_path / "report.json"
    source.write_text("not-json", encoding="utf-8")
    assert replay.main([str(source), "--output", str(output)]) == 2
    assert not output.exists()
