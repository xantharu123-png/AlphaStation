from modules.scanners import _calculate_biotech_catalyst_edge


def _base_inputs():
    trial_data = {
        "catalyst_readouts": [
            {
                "stage_label": "Phase 3",
                "event_label": "Topline readout",
                "full_label": "Phase 3 topline data",
                "days_until": 24,
                "bpiq_score": 90,
                "is_big_mover": True,
                "is_hedge_fund_pick": True,
            }
        ]
    }
    news_data = {
        "catalyst_score": 20,
        "news": [{"title": "Company announces upcoming topline data", "description": "", "sentiment": "positive"}],
        "negative_flags": [],
    }
    tech_data = {
        "technical_score": 12,
        "details": {"price": 8.0, "pos_90d": 65, "range_10d%": 6, "RVOL": 1.8, "rvol_up_day": True, "chart_health": 8},
    }
    details = {"market_cap_millions": 750, "shares_millions": 35}
    return trial_data, news_data, tech_data, details


def test_biotech_edge_prioritizes_late_stage_prime_window():
    trial_data, news_data, tech_data, details = _base_inputs()

    edge = _calculate_biotech_catalyst_edge(trial_data, news_data, tech_data, details)

    assert edge["bio_edge_score"] >= 75
    assert edge["score_adjustment"] > 0
    assert edge["trade_mode"] == "PRIORITY_WATCH"
    assert "prime_catalyst_window" in edge["positive_factors"]


def test_biotech_edge_blocks_dilution_news():
    trial_data, news_data, tech_data, details = _base_inputs()
    news_data["news"] = [{"title": "Company prices public offering and warrants", "description": "", "sentiment": "negative"}]
    news_data["negative_flags"] = [{"flag": "stock offering", "penalty": -10, "date": "2026-05-01"}]

    edge = _calculate_biotech_catalyst_edge(trial_data, news_data, tech_data, details)

    assert edge["trade_mode"] == "AVOID_NEWS_RISK"
    assert edge["score_adjustment"] < 0
    assert "dilution_or_offering_risk" in edge["risk_flags"]


def test_biotech_edge_marks_sell_the_news_extension():
    trial_data, news_data, tech_data, details = _base_inputs()
    tech_data["details"].update({"pos_90d": 92, "range_10d%": 18, "RVOL": 4.0, "rvol_up_day": False})

    edge = _calculate_biotech_catalyst_edge(trial_data, news_data, tech_data, details)

    assert edge["trade_mode"] == "WAIT_PULLBACK"
    assert edge["sell_the_news_risk"] >= 25
    assert "sell_the_news_risk_extended_chart" in edge["risk_flags"]
    assert "distribution_volume" in edge["risk_flags"]


def test_biotech_edge_preserves_explicit_zero_chart_health():
    trial_data, news_data, tech_data, details = _base_inputs()
    tech_data["details"]["chart_health"] = 0

    edge = _calculate_biotech_catalyst_edge(trial_data, news_data, tech_data, details)

    assert "weak_chart_before_catalyst" in edge["risk_flags"]
    assert edge["sell_the_news_risk"] >= 10
    assert edge["trade_mode"] == "WAIT_CHART_CONFIRMATION"
