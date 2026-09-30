"""New counterexamples and metamorphic controls; keep inherited fixtures separate.

Run only with tmp/offline_mail_fix_tests_20260925.py (no external I/O).
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import pytest
import api
from modules import scanners
from modules.penny_stock_scanner import evaluate_penny_signal_outcome
from modules.stock_momentum_contract import evaluate_momentum_breakout
from modules.indicators import calculate_adx
from modules.elliott_waves import analyze_elliott, validate_elliott_report

from test_scanner_reaudit_regressions import freeze, Response, NOW


def grouped_reply(url, bars, tickers=("AUDT",)):
    """Dated adjusted bulk fixture from the same individual daily history."""
    if "/aggs/grouped/" not in url:
        return None
    from zoneinfo import ZoneInfo
    session = url.rsplit("/", 1)[-1]
    matches = [bar for bar in bars if datetime.fromtimestamp(bar["t"] / 1000, ZoneInfo("America/New_York")).date().isoformat() == session]
    return Response({"status": "OK", "adjusted": True, "results": [
        dict(bar, T=ticker) for ticker in tickers for bar in matches
    ]})


def classify_news(monkeypatch, title, published=None):
    article = dict(title=title, description="", published_utc=published or datetime.now(timezone.utc).isoformat(),
                   insights=[dict(ticker="AUDT", sentiment="neutral")], article_url="https://example.invalid/audit")
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Response({"results": [article]}))
    return scanners._scan_biotech_news("offline-fixture", "AUDT")


def test_biotech_actual_approval_positive_control(monkeypatch):
    row = classify_news(monkeypatch, "Acme receives FDA approval for Drug X")
    assert row["catalyst_score"] == 30 and not row["negative_flags"]


@pytest.mark.parametrize("headline", [
    "FDA approval denied for Acme Drug X",
    "FDA approval was not granted to Acme Drug X",
    "Acme primary endpoint was not met in the pivotal trial",
])
def test_biotech_negation_after_keyword_is_not_a_positive_catalyst(monkeypatch, headline):
    row = classify_news(monkeypatch, headline)
    assert row["catalyst_score"] == 0, row
    assert row["best_catalyst"] is None, row


def test_biotech_future_publication_is_not_current_approval_evidence(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    row = classify_news(monkeypatch, "Acme receives FDA approval for Drug X", published=future)
    assert row["catalyst_score"] == 0, row


@pytest.mark.parametrize("missing", ["open", "high", "low"])
def test_penny_replay_does_not_fabricate_missing_ohlc(missing):
    bar = dict(open=1.30, high=1.35, low=1.29, close=1.32, volume=1000)
    assert evaluate_penny_signal_outcome(1., .9, 1.15, 1.25, [bar])["valid"] is True
    del bar[missing]
    result = evaluate_penny_signal_outcome(1., .9, 1.15, 1.25, [bar])
    assert result["valid"] is False, result


def turtle_run(monkeypatch, *, blank_current_session=False,
               snapshot_mutator=None, grouped_mutator=None, writes=None):
    from test_stock_audit_repair_contracts import daily_bars
    clock = NOW.replace(hour=9, minute=0)
    freeze(monkeypatch, clock)
    bars = daily_bars(30, session="2026-09-29")
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3_000_000)
    completed_day = {k: bars[-1][k] for k in ("o", "h", "l", "c", "v")}
    today = dict.fromkeys(("o", "h", "l", "c", "v"), 0) if blank_current_session else deepcopy(completed_day)
    snap = {"ticker": "AUDT", "day": today, "prevDay": completed_day,
            "lastTrade": {"p": 100.1}}
    if snapshot_mutator:
        snapshot_mutator(snap)
    requests, saved = [], [] if writes is None else writes
    def get(url, **kw):
        grouped = grouped_reply(url, bars)
        if grouped is not None:
            if grouped_mutator:
                grouped_mutator(grouped.payload)
            return grouped
        if "/aggs/" in url:
            requests.append(url)
            return Response({"results": bars})
        if url.endswith("/tickers"):
            return Response({"tickers": [snap]})
        return Response({"tickers": []})
    monkeypatch.setattr(api, "rate_limited_get", get)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    api._turtle_scan_wrapper()
    return saved, requests


def test_turtle_completed_swing_does_not_depend_on_empty_new_session_snapshot(monkeypatch):
    previous, prior_requests = turtle_run(monkeypatch)
    assert len(previous) == 1 and len(prior_requests) == 1
    assert previous[0]["swing_analysis_session"] == "2026-09-29"
    current, current_requests = turtle_run(monkeypatch, blank_current_session=True)
    assert [r["Ticker"] for r in current] == ["AUDT"], {
        "before": previous[0]["swing_analysis_session"], "after_rows": current,
        "historical_requests_after_midnight_reset": len(current_requests),
    }


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("spring", [False, True])
@pytest.mark.parametrize("scale", [.25, 4.])
def test_wyckoff_price_and_volume_unit_changes_preserve_event_chain(direction, spring, scale):
    from test_wyckoff_engine import analyze, textbook_bars, selected
    bars = textbook_bars(direction, spring=spring)
    base = selected(analyze(bars, direction), direction)
    altered = deepcopy(bars)
    for b in altered:
        for k in ("open", "high", "low", "close"):
            b[k] *= scale
        b["volume"] /= scale
    row = selected(analyze(altered, direction), direction)
    signature = lambda r: (r["phase"], r["trade_ready"], r["structure_state"],
                            [(e["name"], e["index"], e["confirmed_at"]) for e in r["events"]])
    assert signature(row) == signature(base)
    assert row["trade"]["entry"] == pytest.approx(base["trade"]["entry"]*scale)


@pytest.mark.parametrize("family", ["impulse", "zigzag", "regular_flat", "expanded_flat", "contracting_triangle"])
@pytest.mark.parametrize("scale", [.25, 4.])
def test_elliott_rescaling_does_not_change_verified_subwave_count(family, scale):
    from test_elliott_waves import pattern_bars, report_for
    bars = pattern_bars(family, subdivide=True)
    base = report_for(bars)
    assert any(p["family"] == family and p["pattern_status"] == "confirmed" for p in base["patterns"])
    for bar in bars:
        for key in ("open", "high", "low", "close"):
            bar[key] *= scale
    scaled = report_for(bars)
    signature = lambda r: sorted((p["family"], p["pattern_status"], p["confirmed_at"],
                                  tuple(w["observed_subwaves"] for w in p["waves"])) for p in r["patterns"])
    assert signature(base) == signature(scaled)
    assert validate_elliott_report(scaled)


@pytest.mark.parametrize("rvol", [1.499999, 1.5, 1.500001])
@pytest.mark.parametrize("price_offset", [-.000001, 0., .000001])
def test_momentum_raw_boundary_does_not_round_into_or_out_of_signal(rvol, price_offset):
    level = 100*1.001
    result = evaluate_momentum_breakout({"history_ok": True, "high_20d": 100., "high_10d": 100.,
                                        "ema20": 95., "ema50": 90., "rsi14": 60., "change_5d": 5.},
                                        price=level+price_offset, change_pct=3., rvol=rvol, close_pos=.8)
    assert result["eligible"] is (price_offset >= 0 and rvol >= 1.5), result


@pytest.mark.parametrize("seed", range(200, 250))
def test_bi_adx_new_random_sequences_match_independent_math_after_rescaling(seed):
    from test_gap_bi_repair_20260930 import adx_bars, reference_adx
    bars = adx_bars(seed)
    expected = reference_adx(bars)
    for scale in (.25, 4.):
        scaled = [{**b, **{k: b[k]*scale for k in ("open", "high", "low", "close")}} for b in bars]
        actual = calculate_adx(scaled, raw=True)
        assert actual == pytest.approx(expected, abs=1e-10), (seed, scale, actual, expected)


@pytest.mark.parametrize("family", ["impulse", "zigzag", "regular_flat", "expanded_flat", "contracting_triangle"])
def test_elliott_open_future_corruption_cannot_change_existing_count(family):
    from test_elliott_waves import pattern_bars, report_for
    bars = pattern_bars(family, subdivide=True)
    cutoff = datetime.fromisoformat(bars[-1]["close_time"]) + timedelta(seconds=1)
    before = analyze_elliott(bars, as_of=cutoff)
    future = dict(bars[-1], timestamp=(cutoff + timedelta(days=1)).isoformat(),
                  close_time=(cutoff+timedelta(days=2)).isoformat(), close=float("nan"))
    after = analyze_elliott(bars + [future], as_of=cutoff)
    assert after == before


@pytest.mark.parametrize("future_age", [-1, -300, -3600])
def test_new_listing_future_closed_trigger_never_trade_now(monkeypatch, future_age):
    from test_crypto_group_b_repair_contract import _new_listing_routes
    direct, combined = _new_listing_routes(monkeypatch, close_age_seconds=future_age,
                                          cached_at=datetime.now(timezone.utc).isoformat())
    assert direct["data"][0]["alertable_crypto"] is False, direct["data"][0]
    assert combined[0][0]["trade_action"] != "JETZT_SHORT", combined[0][0]


@pytest.mark.parametrize("scale", [.25, 4.])
@pytest.mark.parametrize("noisy", [False, True])
@pytest.mark.parametrize("offset", [-.000001, .000001])
def test_cup_close_boundary_survives_rescaling_and_normal_bottom_noise(scale, noisy, offset):
    from test_cup_repair_adversarial import _formation
    bars = _formation(noisy=noisy, scale=scale)
    threshold = 101.2 * scale * 1.002
    bars[-1]["close"] = threshold + offset * scale
    before = deepcopy(bars)
    result = api._detect_cup_handle_breakout(bars, current_price=102 * scale)
    assert (result is not None) is (offset > 0), result
    assert bars == before
    if result:
        anchors = result["cup_pattern_evidence"]["anchors"]
        assert anchors["breakout"]["price"] == bars[-1]["close"]


@pytest.mark.parametrize("timeframe_index", [0, 1, 2])
@pytest.mark.parametrize("future", [False, True])
def test_crypto_explosion_open_or_future_extreme_candle_cannot_change_plan(monkeypatch, timeframe_index, future):
    from test_crypto_explosion_scanner import _bars, _candidate, _btc_context
    freeze(monkeypatch)
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda *a: _btc_context(4.0))
    histories = [
        _bars(90, start=9.48, step=.004, volume=1000,
              last=dict(open=9.93, high=9.97, low=9.90, close=9.95, volume=1200)),
        _bars(60, start=9.42, step=.009, volume=3000, interval=900,
              last=dict(open=9.92, high=9.98, low=9.88, close=9.95, volume=3600)),
        _bars(60, start=9.4, step=.006, volume=5000, interval=14400),
    ]
    before = api._score_crypto_explosion_candidate(_candidate(), *histories)
    assert before is not None
    histories[timeframe_index].append(dict(
        timestamp=int(NOW.timestamp()) + (300 if future else -60),
        open=9.95, high=20., low=5., close=19., volume=10_000_000,
    ))
    after = api._score_crypto_explosion_candidate(_candidate(), *histories)
    assert after == before


@pytest.mark.parametrize("scale", [.25, 4.])
def test_early_mover_trigger_is_price_unit_invariant(monkeypatch, scale):
    from test_crypto_execution_integrity import _execution_bars, _early_row
    freeze(monkeypatch)
    bars, row = _execution_bars(), _early_row()
    before = api._score_early_mover_trigger_bars(row, bars, "5m", {})
    assert before["ok"] is True, before
    for bar in bars:
        for key in ("open", "high", "low", "close"):
            bar[key] *= scale
    for source in (row, row["trade_setup"]):
        for key in ("Price", "current_price", "entry", "stop_loss", "tp1", "tp2"):
            if key in source:
                source[key] *= scale
    after = api._score_early_mover_trigger_bars(row, bars, "5m", {})
    for key in ("ok", "reason", "execution_score", "matched"):
        assert after[key] == before[key], (key, before, after)
