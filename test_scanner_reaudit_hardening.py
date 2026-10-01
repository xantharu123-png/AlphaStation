"""Boundary, causality, outage and actual sender controls for R1-R9 (offline)."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest
import api
from modules import scanners, patterns, mail_outbox, signal_tracker as tracker
from modules.biotech_news_contract import BIOTECH_NEWS_CONTRACT_VERSION
from modules.penny_stock_scanner import evaluate_penny_signal_outcome
from test_scanner_reaudit_regressions import (
    NOW, Response, candle, mirror, gap_history, ob_history, freeze,
    setup_orb, configure_final_orb,
)
from test_cross_scanner_reaudit_regressions import grouped_reply, turtle_run


def news_at(monkeypatch, title, published, description=""):
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({"results": [
        {"title": title, "description": description, "published_utc": published}
    ]}))
    return scanners._scan_biotech_news("fixture", "AUDT", as_of=NOW)


@pytest.mark.parametrize("headline", [
    "FDA approval has not been granted", "FDA approval wasn't granted",
    "FDA approval was never granted", "FDA approval was denied",
    "Primary endpoint has not been met", "Primary endpoints were not achieved",
    "Primary endpoint wasn't met", "Primary endpoint has never been met",
])
def test_passive_negative_news_has_negative_flag_and_no_positive_score(monkeypatch, headline):
    row = news_at(monkeypatch, headline, NOW.isoformat())
    assert row["catalyst_score"] == 0 and row["best_catalyst"] is None
    assert row["negative_flags"]


@pytest.mark.parametrize("headline,description,score", [
    ("FDA approval was not denied", "", 0),  # no proof that it was granted
    ("No safety concerns; Acme receives FDA approval", "", 30),
    ("No safety concerns", "Acme receives FDA approval", 30),
    ("Acme receives not only FDA approval but also a patent", "", 30),
    ("Acme receives FDA approval", "No safety concerns in trial", 30),
    ("Acme meets primary endpoint", "", 22),
])
def test_biotech_sentence_boundaries_and_positive_controls(monkeypatch, headline, description, score):
    row = news_at(monkeypatch, headline, NOW.isoformat(), description)
    assert row["catalyst_score"] == score
    assert not row["negative_flags"]


@pytest.mark.parametrize("published,excluded", [
    (NOW.isoformat(), None),
    (NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(), None),
    ((NOW + timedelta(microseconds=1)).isoformat(), "future_publication"),
    ((NOW + timedelta(hours=1)).isoformat(), "future_publication"),
    (NOW.replace(tzinfo=None).isoformat(), "invalid_publication_time"),
    (None, "invalid_publication_time"), ("bad date", "invalid_publication_time"),
    ("2026-09-30", "invalid_publication_time"),
])
def test_biotech_publication_cutoff_is_timezone_aware_to_the_instant(monkeypatch, published, excluded):
    row = news_at(monkeypatch, "Acme receives FDA approval", published)
    assert row["catalyst_score"] == (0 if excluded else 30)
    assert row["publication_exclusions"] == ({excluded: 1} if excluded else {})
    if excluded:
        assert row["news"] == [] and row["negative_flags"] == []


def test_future_negative_news_does_not_reduce_known_positive_catalyst(monkeypatch):
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({"results": [
        {"title": "FDA approval received", "published_utc": NOW.isoformat()},
        {"title": "FDA approval denied", "published_utc": (NOW + timedelta(days=1)).isoformat()},
    ]}))
    row = scanners._scan_biotech_news("fixture", "AUDT", as_of=NOW)
    assert row["catalyst_score"] == 30 and not row["negative_flags"]


def test_biotech_legacy_scores_cannot_be_renewed_by_cache_write_time(monkeypatch, tmp_path):
    path = tmp_path / "biotech.json"
    monkeypatch.setattr(scanners, "_biotech_cache_file", lambda: str(path))
    scanners._biotech_cache_save([{"Ticker": "AUDT", "Score": 95}])
    assert scanners._biotech_cache_load() is None
    scanners._biotech_cache_save([{"Ticker": "AUDT", "Score": 95, "News_Contract_Version": BIOTECH_NEWS_CONTRACT_VERSION}])
    assert scanners._biotech_cache_load()[0]["Ticker"] == "AUDT"
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    state = api._classify_alert_candidate("biotech", {"Ticker": "AUDT", "Grade": "A", "Score": 95, "RVOL": 3})
    assert not state["alertable_now"]
    assert "biotech_news_contract_invalid" in state["suppression_reasons"]


def test_biotech_legacy_cache_is_not_released_by_display_or_background_sender(monkeypatch, tmp_path):
    from test_bi_biotech_infra_fixes import _biotech_row, _bg_setup, BIOTECH_CACHE
    import bg_service
    freeze(monkeypatch)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"BIOX"}, "fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    old = _biotech_row()
    state = api._scanner_result_trade_state("biotech", old)
    assert state["decision"] == "NO_TRADE"
    assert not state["alertable_now"]
    api._apply_scanner_result_trade_state(old, "biotech")
    api._apply_trade_health_final_signal(old, "biotech")
    assert not api._scanner_row_is_trade_signal(old, "biotech")
    assert api._apply_scanner_visibility_policy("biotech", [old])[0]["visibility_status"] == "candidate_warning"
    current = _biotech_row(News_Contract_Version=BIOTECH_NEWS_CONTRACT_VERSION)
    assert "biotech_news_contract_invalid" not in api._classify_alert_candidate("biotech", current)["suppression_reasons"]
    # The background entry sender must remain closed for both schema versions;
    # only the API final revalidation path owns actionable stock mail.
    for row in (old, current):
        sent = _bg_setup(monkeypatch, tmp_path, BIOTECH_CACHE,
                         {"results": [row], "timestamp": NOW.timestamp()})
        assert bg_service._check_and_alert_scan_results("biotech", {}) is False
        assert not sent


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_penny_invalid_values_do_not_become_synthetic_candles(field, value):
    good = dict(open=1., high=1.1, low=.95, close=1.03, volume=1000)
    bad = dict(good, **{field: value})
    assert not evaluate_penny_signal_outcome(1., .9, 1.15, 1.25, [bad])["valid"]
    # An unknown candle before a later target must not be silently dropped.
    target = dict(open=1.30, high=1.35, low=1.29, close=1.32, volume=1000)
    result = evaluate_penny_signal_outcome(1., .9, 1.15, 1.25, [good, bad, target])
    assert not result["valid"] and result["net_r"] is None


def test_penny_real_short_aliases_and_zero_volume_remain_valid():
    row = dict(o=1.3, h=1.35, l=1.29, c=1.32, v=0)
    assert evaluate_penny_signal_outcome(1., .9, 1.15, 1.25, [row])["valid"]


def fvg_zone(bars):
    return next(z for z in patterns.detect_volume_imbalances(bars, max_zones=1000)["zones"]
                if z["type"] == "FVG" and z["bar_idx"] == 26)


@pytest.mark.parametrize("bear", [False, True])
@pytest.mark.parametrize("case", ["touch", "half", "full", "partial_gap", "jump_reclaim"])
def test_fvg_touch_fill_and_invalidation_are_different_states(bear, case):
    bars = gap_history()
    additions = {
        "touch": [candle(101.6, 101.7, 101.4, 101.6)],
        "half": [candle(101.6, 101.7, 100.8, 101.6)],
        "full": [candle(101.6, 101.7, 100.3, 101.6)],
        "partial_gap": [candle(100.5, 100.7, 100.3, 100.35)],
        "jump_reclaim": [candle(99, 99.2, 98.5, 98.8), candle(101.5, 101.9, 101.5, 101.8)],
    }[case]
    data = bars + additions
    if bear:
        data = mirror(data)
    zone = fvg_zone(data)
    assert zone["filled"] is (case == "full")
    assert zone["ce_filled"] is (case in {"half", "full"})
    assert zone["invalidated"] is (case in {"partial_gap", "jump_reclaim"})
    assert zone["touched"] is (case != "jump_reclaim")
    report = patterns.detect_volume_imbalances(data, max_zones=1000)
    if zone["invalidated"]:
        assert zone not in report["unfilled_bull"] + report["unfilled_bear"]
    stats = report["stats"]
    assert stats["total"] == stats["filled"] + stats["unfilled"] + stats["invalidated"]


@pytest.mark.parametrize("bear", [False, True])
def test_disjoint_fvg_trading_ranges_do_not_fill_the_unobserved_middle(bear):
    data = gap_history() + [candle(101.4, 101.6, 101.2, 101.5), candle(100.5, 100.7, 100.4, 100.6)]
    if bear:
        data = mirror(data)
    zone = fvg_zone(data)
    assert zone["touched"] and not zone["filled"] and not zone["ce_filled"]


@pytest.mark.parametrize("bear", [False, True])
def test_prefix_zone_identity_and_strength_survive_noncontact_suffix(bear):
    bars = ob_history()
    suffix = [candle(103, 180 + i, 102.5, 103.1) for i in range(6)]
    if bear:
        bars, suffix = mirror(bars), mirror(suffix)
    key = "bearish_obs" if bear else "bullish_obs"
    base = next(b for b in patterns.detect_order_blocks(bars)[key] if b["idx"] == 27)
    for n in range(1, len(suffix) + 1):
        row = next(b for b in patterns.detect_order_blocks(bars + suffix[:n])[key] if b["idx"] == 27)
        for field in ("ob_high", "ob_low", "impulse_size", "strength", "range_baseline", "confirmed_idx"):
            assert row[field] == base[field]
    prefix = bars[:28]
    oracle = sum(b["high"] - b["low"] for b in prefix) / len(prefix)
    assert base["range_baseline"] == pytest.approx(oracle)


@pytest.mark.parametrize("bear", [False, True])
def test_orderblock_origin_breach_before_confirmation_invalidates_it(bear):
    data = ob_history()
    data[-2] = candle(99.5, 100.5, 99.3, 100.4, 3000)
    if bear:
        data = mirror(data)
    key = "bearish_obs" if bear else "bullish_obs"
    assert not any(row["idx"] == 27 for row in patterns.detect_order_blocks(data)[key])


@pytest.mark.parametrize("short", [False, True])
def test_orb_partial_provider_outage_preserves_valid_rows_and_counts_exclusions(monkeypatch, short):
    saved, sends, _ = setup_orb(monkeypatch, short=short)
    original_get = api.rate_limited_get
    def get(url, **kwargs):
        if "/ticker/MISS/" in url:
            return Response(status=503)
        reply = original_get(url, **kwargs)
        if "/aggs/" not in url:
            payload = deepcopy(reply.json())
            payload["tickers"].append(dict(payload["tickers"][0], ticker="MISS"))
            return Response(payload)
        return reply
    original_grouped = api.fetch_grouped_daily
    def grouped(*args):
        rows = original_grouped(*args)
        return dict(rows, MISS=deepcopy(rows["AUDT"])) if rows else rows
    monkeypatch.setattr(api, "rate_limited_get", get)
    monkeypatch.setattr(api, "fetch_grouped_daily", grouped)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT", "MISS"}, "fixture"))
    api._orb_scanner_wrapper()
    assert saved[0]["data_coverage"] == dict(selected=2, excluded=1, checked=1, status="completed_with_data_exclusions")
    assert len(saved[0]["actionable_breakouts"]) == 1 and not sends


@pytest.mark.parametrize("fault", [None, "quote_back_inside_or", "quote_at_or_boundary"])
@pytest.mark.parametrize("short", [False, True])
def test_orb_real_sender_and_delivery_intent_after_final_quote(monkeypatch, tmp_path, short, fault):
    from test_alert_delivery_intent_api import _AcceptedSMTP, _setup
    real_sender = api._send_email_alert
    saved, _, validations = configure_final_orb(monkeypatch, short, fault)
    class SMTP(_AcceptedSMTP):
        calls, messages, recipient_batches = 0, [], []
    db_path = _setup(monkeypatch, tmp_path, SMTP)
    monkeypatch.setattr(api.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(api, "_send_email_alert", real_sender)
    monkeypatch.setattr(api, "_EMAIL_STARTUP_TIME", NOW.timestamp() - 3600)
    monkeypatch.setattr(api, "_mail_outbox", mail_outbox)
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", str(tmp_path / "outbox.sqlite"))
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH", str(tmp_path / "journal.sqlite"))
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    events = []
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **kw: events.append((a, kw)))
    api._orb_scanner_wrapper()
    assert validations
    if fault is None:
        assert tracker._deferred_delivery_candidates(
            "orb", [validations[-1]["candidate"]], "fixture", "stock"
        ), json.dumps(tracker.extract_signal_fields(validations[-1]["candidate"]), sort_keys=True)
    assert SMTP.calls == (1 if fault is None else 0), (
        validations, events,
        tracker.extract_signal_fields(validations[-1].get("candidate")),
    )
    if fault is None:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT direction, delivery_state, entry, stop, tp1, tp2 FROM signals").fetchone()
        source = validations[-1]["candidate"]
        assert row == ("SHORT" if short else "LONG", "ACTIVE", source["entry"],
                       source["stop"], source["target1"], source["target2"])
    else:
        assert not (tmp_path / "signal_tracker.sqlite").exists()


def test_turtle_forming_snapshot_extremes_do_not_change_completed_plan(monkeypatch):
    baseline, _ = turtle_run(monkeypatch)
    def forming(row):
        row["day"] = dict(o=20, h=500, l=1, c=2, v=1)
        row["lastTrade"] = {"p": 2}
    changed, _ = turtle_run(monkeypatch, snapshot_mutator=forming)
    assert len(changed) == len(baseline) == 1
    assert {k: v for k, v in changed[0].items() if not k.startswith("snapshot_")} == {
        k: v for k, v in baseline[0].items() if not k.startswith("snapshot_")
    }
    assert changed[0]["snapshot_price"] == 2


@pytest.mark.parametrize("fault", ["close", "high", "volume", "date", "adjustment", "empty"])
def test_turtle_mismatched_grouped_evidence_cannot_publish_plan(monkeypatch, fault):
    writes = []
    def corrupt(payload):
        if fault == "adjustment":
            payload["adjusted"] = False
        elif fault == "empty":
            payload["results"] = []
        else:
            bar = payload["results"][0]
            if fault == "close":
                bar["c"] += .01  # still valid OHLC, but contradicts individual history
            elif fault == "high":
                bar["h"] += 1
            elif fault == "volume":
                bar["v"] *= 2
            else:
                bar["t"] += 86400000
    with pytest.raises(api.ScannerDataError):
        turtle_run(monkeypatch, grouped_mutator=corrupt, writes=writes)
    assert not writes


@pytest.mark.parametrize("timeframe,minutes", [("5m", 5), ("15m", 15), ("1H", 60), ("4H", 240)])
@pytest.mark.parametrize("starter", [False, True], ids=["live", "starter"])
def test_open_chart_candle_cannot_invalidate_confirmed_orderblock(monkeypatch, timeframe, minutes, starter):
    # NOW is the observed market clock used to construct the history. In
    # Starter mode the corresponding request is 900 seconds later; the open
    # candle must be evaluated against that same available market cutoff.
    delay = timedelta(seconds=api.stock_swing.DELAY_SECONDS if starter else 0)
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: starter)
    freeze(monkeypatch, NOW + delay)
    bars = ob_history() + [candle(98, 99, 97, 98)]
    for i, bar in enumerate(bars):
        bar["time"] = int((NOW - timedelta(minutes=minutes * (len(bars)-1-i) + 1)).timestamp())
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: deepcopy(bars))
    observed = []
    real = api.detect_chart_patterns
    def capture(data, *a, **kw):
        observed.append(deepcopy(data))
        return real(data, *a, **kw)
    monkeypatch.setattr(api, "detect_chart_patterns", capture)
    result = api.get_chart_data(ticker="AUDT", timeframe=timeframe, overlays="patterns", direction="LONG")
    assert result["chart_as_of"] == NOW.timestamp()
    assert result["chart_requested_at"] == (NOW + delay).timestamp()
    assert len(observed[-1]) == len(bars) - 1
    assert any(b["idx"] == 27 for b in patterns.detect_order_blocks(observed[-1])["bullish_obs"])
    completed_market_at = NOW + timedelta(minutes=minutes)
    freeze(monkeypatch, completed_market_at + delay)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    result = api.get_chart_data(ticker="AUDT", timeframe=timeframe, overlays="patterns", direction="LONG")
    assert result["chart_as_of"] == completed_market_at.timestamp()
    assert result["chart_requested_at"] == (completed_market_at + delay).timestamp()
    assert len(observed[-1]) == len(bars)
    assert not patterns.detect_order_blocks(observed[-1])["bullish_obs"]


@pytest.mark.parametrize("key1,key2", [("TP1", "TP2"), ("tp1", "tp2"), ("target1", "target2"), ("Target1", "Target2")])
@pytest.mark.parametrize("short", [False, True])
def test_tracker_preserves_native_target_aliases_without_fabricating_geometry(key1, key2, short):
    sign = -1 if short else 1
    row = dict(ticker="AUDT", direction="SHORT" if short else "LONG", entry=100, stop=100-sign,
               **{key1: 100+2*sign, key2: 100+3*sign})
    result = tracker.extract_signal_fields(row)
    assert result["tp1"] == row[key1] and result["tp2"] == row[key2]
    assert tracker._deferred_delivery_candidates("orb", [row], "fixture", "stock")
    del row[key2]
    assert tracker._deferred_delivery_candidates("orb", [row], "fixture", "stock") is None
