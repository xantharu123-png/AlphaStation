import json
from datetime import datetime, timedelta, timezone

from modules.market_context import analyze_headlines, build_event_risk, build_market_context, missing_headline_risk
from modules.trade_health import calculate_trade_health


def test_political_tariff_headlines_raise_headline_risk():
    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    headlines = [
        {
            "title": "Trump announces new China tariff plan, markets brace for trade war",
            "published_utc": "2026-04-30T10:30:00Z",
            "publisher": {"name": "Test Wire"},
        },
        {
            "title": "Fed chair Powell says interest rate path remains uncertain",
            "published_utc": "2026-04-30T09:00:00Z",
        },
    ]

    risk = analyze_headlines(headlines, now)

    assert risk["level"] in {"HIGH", "EXTREME"}
    assert risk["matched_count"] == 2
    assert "politics" in risk["categories"]
    assert "tariffs_trade" in risk["categories"]


def test_headline_keywords_do_not_match_inside_harmless_words():
    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    headlines = [{
        "title": "Software sector rallies after second quarter results",
        "published_utc": "2026-04-30T11:30:00Z",
    }]

    risk = analyze_headlines(headlines, now)

    assert risk["level"] == "LOW"
    assert risk["matched_count"] == 0
    assert "war_geopolitics" not in risk["categories"]
    assert "regulation_ai_crypto" not in risk["categories"]


def test_missing_headline_data_is_unknown_and_defensive():
    context = build_market_context(
        {"fear_score": 60, "vix": {"price": 16}, "breadth": {"ad_ratio": 1.2, "advancing_pct": 52}},
        missing_headline_risk("Polygon 429"),
        {"score": 0, "level": "LOW", "data_status": "ok", "upcoming_events": []},
    )

    assert context["summary"]["headline_level"] == "UNKNOWN"
    assert context["summary"]["headline_status"] == "error"
    assert context["regime"] != "RISK_ON"
    assert any("Headline-Daten" in warning for warning in context["warnings"])


def test_near_high_impact_event_sets_event_risk():
    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    events = [{
        "event": "FED Zinsentscheid USA",
        "importance": "high",
        "impact": "Sehr Hoch",
        "datetime_et": "2026-04-30T10:00:00-04:00",
        "time_et": "10:00 AM ET",
        "source": "Federal Reserve",
    }]

    risk = build_event_risk(events, now)

    assert risk["level"] in {"HIGH", "EXTREME"}
    assert risk["upcoming_events"][0]["event"] == "FED Zinsentscheid USA"


def test_market_context_turns_panic_into_protect_capital():
    context = build_market_context(
        {
            "fear_score": 12,
            "vix": {"price": 34},
            "breadth": {"ad_ratio": 0.45, "advancing_pct": 25},
        },
        {"score": 80, "level": "EXTREME", "top_headlines": []},
        {"score": 60, "level": "HIGH", "upcoming_events": []},
    )

    assert context["regime"] == "PANIC"
    assert context["trade_mode"] == "PROTECT_CAPITAL"
    assert context["size_multiplier"] == 0.25


def test_trade_health_uses_market_context_to_reduce_long_aggression():
    row = {
        "ticker": "HOT",
        "direction": "LONG",
        "current_price": 10.05,
        "entry": 10.00,
        "stop": 9.50,
        "target1": 11.00,
        "target2": 12.00,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.86,
        "dollar_volume": 8_000_000,
    }
    neutral = calculate_trade_health(row, "orb")
    panic_context = build_market_context(
        {"fear_score": 15, "vix": {"price": 35}, "breadth": {"ad_ratio": 0.4, "advancing_pct": 22}},
        {"score": 75, "level": "EXTREME", "top_headlines": []},
        {"score": 65, "level": "HIGH", "upcoming_events": []},
    )
    defensive = calculate_trade_health(row, "orb", market_context=panic_context)

    assert neutral["decision"] == "TRADEABLE"
    assert defensive["decision"] != "TRADEABLE"
    assert defensive["market_context"]["trade_mode"] == "PROTECT_CAPITAL"
    assert defensive["health_score"] < neutral["health_score"]


def test_stale_market_context_cache_is_not_used_for_scanner_decisions(monkeypatch, tmp_path):
    import api

    cache_file = tmp_path / "market_context_cache.json"
    old_cached_at = (datetime.now() - timedelta(hours=3)).isoformat()
    cache_file.write_text(json.dumps({
        "cached_at": old_cached_at,
        "results": [{
            "regime": "RISK_ON",
            "trade_mode": "AGGRESSIVE_SELECTIVE",
            "overall_risk_score": 5,
            "summary": {
                "regime": "RISK_ON",
                "trade_mode": "AGGRESSIVE_SELECTIVE",
                "overall_risk_score": 5,
                "size_multiplier": 1.0,
                "headline_level": "LOW",
                "event_level": "LOW",
            },
        }],
    }))
    monkeypatch.setattr(api, "MARKET_CONTEXT_CACHE", str(cache_file))

    context = api._get_market_context_snapshot()

    assert context["cache_status"] == "stale"
    assert context["summary"]["headline_level"] == "UNKNOWN"
    assert context["summary"]["regime"] != "RISK_ON"
