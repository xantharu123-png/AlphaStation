import api


def _orb_row(
    ticker,
    *,
    current,
    entry,
    stop,
    target1,
    target2,
    score=95,
    entry_quality="GOOD",
    rvol=2.4,
    vol_confirmed=True,
    breakout_state="active_breakout",
    breakout_age_bars=2,
):
    return {
        "ticker": ticker,
        "direction": "LONG",
        "current_price": current,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "score": score,
        "grade": "A",
        "rvol": rvol,
        "vol_confirmed": vol_confirmed,
        "breakout_state": breakout_state,
        "breakout_age_bars": breakout_age_bars,
        "volume_ratio": 1.6 if vol_confirmed else 0.9,
        "vwap_aligned": True,
        "close_pos": 0.82,
        "recent_hold_pct": 1.0,
        "dollar_volume": 12_000_000,
        "entry_quality": entry_quality,
    }


def test_orb_breakouts_keeps_all_rows_visible_and_sorts_tradeable_first(monkeypatch):
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda **_kwargs: ({"GOOD", "WAIT", "NOPE"}, "test"),
    )
    payload = [{
        "breakouts": [
            _orb_row("NOPE", current=11.25, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=99, rvol=1.0),
            _orb_row("WAIT", current=10.10, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=92, entry_quality="EXTENDED"),
            _orb_row("GOOD", current=10.05, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=90),
        ],
        "failed_breakouts": [],
        "candidates": [],
    }]

    decorated = api._decorate_orb_results(payload, cache_age_seconds=3)[0]
    tickers = [row["ticker"] for row in decorated["breakouts"]]

    assert tickers == ["GOOD", "WAIT", "NOPE"]
    assert decorated["breakout_decision_counts"]["tradeable"] == 1
    assert decorated["breakout_decision_counts"]["wait"] == 1
    assert decorated["breakout_decision_counts"]["no_trade"] == 1
    assert [row["ticker"] for row in decorated["actionable_breakouts"]] == ["GOOD", "WAIT"]
    assert [row["ticker"] for row in decorated["rejected_range_breaks"]] == ["NOPE"]
    nope = next(row for row in decorated["breakouts"] if row["ticker"] == "NOPE")
    assert nope["trade_decision"] == "NO_TRADE"
    assert nope["entry_quality_raw"] == "GOOD"
    assert nope["entry_quality"] == "BLOCKED"
    assert nope["entry_badge_label"] == "TP1 schon gelaufen"
    assert "Entry GOOD" not in nope["score_details"]
    assert "Nicht traden" in nope["score_details"]


def test_orb_unconfirmed_range_break_is_not_an_actionable_signal(monkeypatch):
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda **_kwargs: ({"RAW"}, "test"),
    )
    payload = [{
        "breakouts": [
            _orb_row(
                "RAW",
                current=10.05,
                entry=10.0,
                stop=9.5,
                target1=11.0,
                target2=12.0,
                score=64,
                rvol=4.2,
                vol_confirmed=False,
                breakout_state="range_break_unconfirmed",
            ),
        ],
        "failed_breakouts": [],
        "candidates": [],
    }]

    decorated = api._decorate_orb_results(payload, cache_age_seconds=1)[0]

    assert decorated["actionable_breakouts"] == []
    assert [row["ticker"] for row in decorated["rejected_range_breaks"]] == ["RAW"]
    assert decorated["breakout_decision_counts"] == {
        "tradeable": 0,
        "wait": 0,
        "no_trade": 1,
    }
    rejected = decorated["rejected_range_breaks"][0]
    assert "orb_breakout_volume_unconfirmed" in rejected["orb_gate_reasons"]
    assert "orb_no_active_breakout" in rejected["orb_gate_reasons"]


def test_orb_excursion_volume_uses_recent_5m_baseline_not_opening_auction():
    or_bars = [
        {"c": 10.0, "v": 10_000},
        {"c": 10.1, "v": 100},
        {"c": 10.2, "v": 100},
    ]
    post_or_bars = [
        {"c": 10.25, "v": 100},
        {"c": 10.30, "v": 110},
        {"c": 10.55, "v": 180, "t": 123},
        {"c": 10.60, "v": 120, "t": 456},
    ]

    result = api._orb_active_excursion_volume(
        or_bars,
        post_or_bars,
        "LONG",
        or_high=10.4,
        or_low=9.8,
        day_rvol=1.4,
    )

    assert result["confirmed"] is True
    assert result["launch_volume"] == 180
    assert result["baseline_volume"] == 100
    assert result["volume_ratio"] == 1.8
    assert result["breakout_age_bars"] == 2
    assert result["launch_timestamp"] == 123


def test_orb_candidate_selection_does_not_truncate_before_asset_validation(monkeypatch):
    ranked = [
        {"ticker": f"ETF{i:03d}", "rank": i}
        for i in range(130)
    ] + [
        {"ticker": "GOOD1", "rank": 130},
        {"ticker": "GOOD2", "rank": 131},
    ]
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda: ({"GOOD1", "GOOD2"}, "test_reference"),
    )

    selected, excluded, diagnostics = api._select_orb_common_stock_candidates(
        ranked,
        limit=2,
    )

    assert [row["ticker"] for row in selected] == ["GOOD1", "GOOD2"]
    assert len(excluded) == 130
    assert diagnostics == {
        "prefiltered": 132,
        "selected": 2,
        "excluded_checked": 130,
        "reference_checked": 0,
        "candidate_limit": 2,
        "asset_universe_source": "test_reference",
    }


def test_orb_candidate_limit_applies_after_reference_validation(monkeypatch):
    ranked = [
        {"ticker": "ETF1"},
        {"ticker": "GOOD1"},
        {"ticker": "ETF2"},
        {"ticker": "GOOD2"},
        {"ticker": "GOOD3"},
    ]
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda: (None, "unavailable"),
    )
    monkeypatch.setattr(
        api,
        "_is_orb_common_stock_candidate",
        lambda ticker: (
            str(ticker).startswith("GOOD"),
            "CS" if str(ticker).startswith("GOOD") else "ETF",
        ),
    )

    selected, excluded, diagnostics = api._select_orb_common_stock_candidates(
        ranked,
        limit=2,
    )

    assert [row["ticker"] for row in selected] == ["GOOD1", "GOOD2"]
    assert [row["ticker"] for row in excluded] == ["ETF1", "ETF2"]
    assert diagnostics["reference_checked"] == 4
    assert diagnostics["selected"] == 2
