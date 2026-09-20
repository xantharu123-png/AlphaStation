"""Wyckoff proof is mandatory at cache, display and mail boundaries."""
from datetime import datetime, timezone
import json

import pytest

import api

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
STRATEGIES = ("Wyckoff Accumulation", "Wyckoff Distribution",
              "Wyckoff Accumulation ⬆", "Wyckoff Distribution ⬇")


def _row(strategy):
    direction = "SHORT" if "Distribution" in strategy else "LONG"
    return {"ticker": "OFFLINE", "Ticker": "OFFLINE", "Strategy": strategy,
            "strategy": strategy, "score": 99, "grade": "S", "RVOL": 3,
            "direction": direction, "Signal_Direction": direction, "price": 100,
            "trade_signal": "JETZT_TRADEN", "trade_action": direction + "_NOW",
            "trade_decision": "TRADEABLE", "entry": 100,
            "stop_loss": 105 if direction == "SHORT" else 95,
            "tp1": 90 if direction == "SHORT" else 110,
            "tp2": 85 if direction == "SHORT" else 115}


def _forbidden(*args, **kwargs):
    pytest.fail("Rejected Wyckoff data reached enrichment, provider, tracking or mail")


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("scanner", ["strategy_scan", "stock_strategy"])
def test_high_score_trade_now_legacy_row_is_not_a_scanner_signal(strategy, scanner):
    row = _row(strategy)
    assert not api._stock_momentum_row_contract_valid(row, as_of=NOW)
    assert not api._scanner_row_is_trade_signal(row, scanner)
    assert api._apply_signal_only_policy(scanner, [row]) == []


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_classifier_cannot_promote_legacy_wyckoff_plan(monkeypatch, strategy):
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 99)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"OFFLINE"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: "")
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", _forbidden)
    monkeypatch.setattr(api, "_stock_swing_short_rule_reasons", _forbidden)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", _forbidden)
    result = api._classify_alert_candidate("stock_strategy", _row(strategy), NOW.timestamp())
    assert result["alertable_now"] is False
    assert "wyckoff_contract_invalid" in result["suppression_reasons"]


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("row_identity", ["matching", "absent", "other_strategy"])
def test_sender_rejects_unproven_rows_before_premarket_or_any_enrichment(
        monkeypatch, strategy, row_identity):
    row = _row(strategy)
    if row_identity == "absent":
        row.pop("Strategy")
        row.pop("strategy")
    elif row_identity == "other_strategy":
        row["Strategy"] = row["strategy"] = "Gap Momentum Long"
    recorded = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args: recorded.append(args))
    for name in ("_load_common_stock_universe", "_stock_trade_email_status",
                 "_attach_stock_company_name", "_enrich_stock_alert_5m_state",
                 "_classify_premarket_candidate", "_safe_record_alert_signals",
                 "_email_dedupe_claim"):
        monkeypatch.setattr(api, name, _forbidden)
    api._send_strategy_scan_alerts(strategy, [row], "stocks")
    assert recorded == [("stock_strategy", {"wyckoff_contract_invalid": 1})]


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("row_strategy", ["matching", "other_strategy"])
def test_results_route_binds_wyckoff_readiness_to_request_even_for_shared_fallback(
        monkeypatch, tmp_path, strategy, partial, row_strategy):
    row = _row(strategy if row_strategy == "matching" else "Gap Momentum Long")
    # The strategy-specific path does not exist: this exercises shared fallback,
    # which historically checked identity only for four automatically run scans.
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(tmp_path / "absent.json"))
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: (
        [row], "2026-09-19T12:00:00",
        {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION,
         "diagnostics": {"coverage": "incomplete" if partial else "complete"}}, partial))
    decorated = []
    def decorate(rows, *args):
        decorated.extend(rows)
        return rows
    monkeypatch.setattr(api, "_decorate_scan_results", decorate)
    monkeypatch.setattr(api, "_scan_quality_payload", lambda *a: {
        "warnings": [], "data_source": "fixture", "exclusion_policy": []})
    monkeypatch.setattr(api, "rate_limited_get", _forbidden)
    result = api.get_scan_results(strategy, None, "stocks")
    assert result.count == 0 and result.data == []
    assert decorated == []
    assert result.partial is partial
    assert result.diagnostics["wyckoff_contract_rejected"] == 1


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_pattern_tag_cannot_hide_wyckoff_from_shared_row_guard(strategy):
    row = _row(strategy)
    row.pop("Strategy")
    row.pop("strategy")
    row["pattern_type"] = "wyckoff_distribution" if "Distribution" in strategy else "wyckoff_accumulation"
    assert not api._stock_momentum_row_contract_valid(row, as_of=NOW)


def test_wyckoff_gate_does_not_reclassify_unrelated_gap_signal():
    assert api._stock_wyckoff_row_contract_valid(_row("Gap Momentum Long"), as_of=NOW)


@pytest.mark.parametrize("strategy", ["Wyckoff Accumulation", "Gap Momentum Long"])
def test_sender_discards_non_object_payloads_without_fabricating_wyckoff_rejections(monkeypatch, strategy):
    calls = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args: calls.append(args))
    monkeypatch.setattr(api, "_load_common_stock_universe", _forbidden)
    monkeypatch.setattr(api, "_email_dedupe_claim", _forbidden)
    monkeypatch.setattr(api, "_safe_record_alert_signals", _forbidden)
    api._send_strategy_scan_alerts(strategy, [None, "bad", [], 123], "stocks")
    assert calls == []


@pytest.mark.parametrize("strategy", STRATEGIES[:2])
def test_cache_mail_path_cannot_claim_or_track_unproven_wyckoff(monkeypatch, tmp_path, strategy):
    path = tmp_path / "stock-cache.json"
    path.write_text(json.dumps({"results": [_row(strategy)]}), encoding="utf8")
    suppressed = []
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *a: (True, "fixture"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"OFFLINE"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: "")
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda scanner, row, *a: row)
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **k: row)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 99)
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args: suppressed.append(args))
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_email_dedupe_claim", _forbidden)
    monkeypatch.setattr(api, "_safe_record_alert_signals", _forbidden)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", _forbidden)
    api._check_and_alert("stock_strategy", str(path))
    assert suppressed == [("stock_strategy", {"wyckoff_contract_invalid": 1})]


def _ready_row(strategy):
    from test_wyckoff_engine import analyze, selected, textbook_bars
    direction = "SHORT" if "Distribution" in strategy else "LONG"
    bars = textbook_bars(direction)
    result = analyze(bars, direction)
    evidence = selected(result, direction)
    row = _row(strategy)
    row.update(wyckoff=evidence, wyckoff_model=result["model"],
               wyckoff_as_of=result["as_of"], wyckoff_timeframe=result["timeframe"],
               price=bars[-1]["close"], entry=evidence["trade"]["entry"],
               stop_loss=evidence["trade"]["stop"], tp1=evidence["trade"]["tp1"],
               tp2=evidence["trade"]["tp2"])
    return row


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_real_canonical_event_chain_survives_readiness_guard_and_aliases(strategy):
    row = _ready_row(strategy)
    assert api._stock_wyckoff_row_contract_valid(row, as_of=NOW, expected_strategy=strategy)
    assert api._stock_momentum_row_contract_valid(row, as_of=NOW)
    wrong_strategy = "Wyckoff Accumulation" if "Distribution" in strategy else "Wyckoff Distribution"
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=NOW, expected_strategy=wrong_strategy)
    # This proves pattern readiness only; price freshness and native-plan/mail
    # gates are separate and are not bypassed or asserted by this fixture.


@pytest.mark.parametrize("strategy", STRATEGIES[:2])
@pytest.mark.parametrize("mutation", [
    "model", "timeframe", "not_ready", "truthy_ready", "phase", "context",
    "missing_proof", "missing_confirmation_event", "future_cutoff", "future_evidence",
    "wrong_direction", "price_inside_range", "conflicting_identity", "naive_time",
])
def test_invalid_proof_cannot_be_promoted_by_score_or_trade_now(strategy, mutation):
    row = _ready_row(strategy)
    if mutation == "model":
        row["wyckoff_model"] = "legacy_score_v1"
    elif mutation == "timeframe":
        row["wyckoff_timeframe"] = "4H"
    elif mutation in {"not_ready", "truthy_ready"}:
        row["wyckoff"]["trade_ready"] = False if mutation == "not_ready" else 1
    elif mutation == "phase":
        row["wyckoff"]["phase"] = "B"
    elif mutation == "context":
        row["wyckoff"]["signal_state"] = "context"
    elif mutation == "missing_proof":
        row.pop("wyckoff")
    elif mutation == "missing_confirmation_event":
        row["wyckoff"]["events"] = [e for e in row["wyckoff"]["events"] if e["name"] not in {"LPS", "LPSY"}]
    elif mutation == "future_cutoff":
        row["wyckoff_as_of"] = "2027-01-01T00:00:00Z"
    elif mutation == "future_evidence":
        row["wyckoff"]["latest_completed_at"] = "2027-01-01T00:00:00Z"
    elif mutation == "wrong_direction":
        row["Signal_Direction"] = "SHORT" if row["direction"] == "LONG" else "LONG"
    elif mutation == "price_inside_range":
        row["price"] = (row["wyckoff"]["range_low"] + row["wyckoff"]["range_high"]) / 2
    elif mutation == "conflicting_identity":
        row["strategy"] = "Gap Momentum Long"
    elif mutation == "naive_time":
        row["wyckoff_as_of"] = "2026-04-11T00:00:00"
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=NOW)
    assert not api._stock_momentum_row_contract_valid(row, as_of=NOW)


@pytest.mark.parametrize("strategy", STRATEGIES[:2])
def test_cached_numeric_string_projection_is_handled_without_raising(strategy):
    row = _ready_row(strategy)
    for key in ("entry", "stop", "tp1", "tp2"):
        row["wyckoff"]["trade"][key] = str(row["wyckoff"]["trade"][key])
    # Either strict rejection or deliberate normalization is safe. An exception
    # would instead break the whole results/mail pipeline for one cache row.
    assert isinstance(api._stock_wyckoff_row_contract_valid(row, as_of=NOW), bool)


@pytest.mark.parametrize("strategy", STRATEGIES[:2])
def test_confirmed_wyckoff_row_cannot_impersonate_another_named_strategy(strategy):
    assert "Trend Reversal" in api.STRATEGIES
    row = _ready_row(strategy)
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=NOW, expected_strategy="Trend Reversal")


@pytest.mark.parametrize("strategy", STRATEGIES[:2])
def test_aggregate_mail_label_does_not_reject_valid_wyckoff_identity(strategy):
    row = _ready_row(strategy)
    assert api._stock_wyckoff_row_contract_valid(row, as_of=NOW, expected_strategy="Aktien Auto-Sweep")


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("near_boundary", [False, True])
def test_real_wrapper_preserves_confirmed_proof_in_partial_and_final_output(
        monkeypatch, strategy, near_boundary):
    from datetime import timedelta
    from copy import deepcopy
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, NOW as scan_now
    from test_wyckoff_engine import BASE, textbook_bars
    direction = "SHORT" if "Distribution" in strategy else "LONG"
    bars = textbook_bars(direction)
    shift = scan_now - (BASE + timedelta(days=100))
    for bar in bars:
        bar["open_time"] += shift
        bar["close_time"] += shift
    if near_boundary:
        # A real close is outside the range, but display rounding lands exactly
        # on the boundary. Display precision must never erase valid evidence.
        if direction == "LONG":
            bars[-1].update(open=106.001, high=106.01, low=105.99, close=106.0001)
        else:
            bars[-1].update(open=93.999, high=94.01, low=93.99, close=93.9999)
    price = bars[-1]["close"]
    actual_post_filter = api._apply_special_strategy_post_filter
    written = _wrapper_fixture(monkeypatch, price=price)
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", actual_post_filter)
    snapshot = {"ticker": "TEST", "day": {"o": price, "h": price + .5,
                "l": price - .5, "c": price, "v": 3_000_000},
                "prevDay": {"c": price, "v": 1_000_000},
                "lastTrade": {"p": price, "t": int(scan_now.timestamp() * 1_000_000_000)},
                "lastQuote": {"p": price - .01, "P": price + .01}}
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: [snapshot])
    requests, previews, plans = [], [], []
    def history(ticker, count, *args):
        requests.append(count)
        return deepcopy(bars)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", history)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda cache, rows, **kw: previews.append(deepcopy(rows)))
    def native_plan(*args, **kwargs):
        plans.append(args[0])
        return None  # Pattern readiness is not native execution qualification.
    monkeypatch.setattr(api, "_build_structured_trade_setup", native_plan)
    rows = api._strategy_scan_wrapper(strategy, send_email=False)
    assert requests == [180]
    assert plans == [direction]
    assert len(rows) == 1
    row = rows[0]
    assert api._stock_wyckoff_row_contract_valid(row, as_of=scan_now, expected_strategy=strategy)
    assert row["wyckoff"]["trade_ready"] is True
    assert row["wyckoff_timeframe"] == "1D"
    assert row["native_plan_status"] == "unavailable" and not row.get("trade_setup")
    assert any(len(preview) == 1 for preview in previews)
    assert written[0][0] == rows
    assert written[0][1]["metadata"]["diagnostics"]["coverage"] == "complete"
