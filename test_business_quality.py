from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import api

from modules import business_quality
from modules.business_quality import analyze_company_facts


_YEARS = (
    ("2022-01-01", "2022-12-31", "2023-02-15"),
    ("2023-01-01", "2023-12-31", "2024-02-15"),
    ("2024-01-01", "2024-12-31", "2025-02-15"),
)


def _duration_fact(values, unit="USD"):
    return {
        "units": {
            unit: [
                {
                    "start": start,
                    "end": end,
                    "filed": filed,
                    "form": "10-K",
                    "val": value,
                }
                for (start, end, filed), value in zip(_YEARS, values)
            ]
        }
    }


def _instant_fact(values, unit="USD"):
    return {
        "units": {
            unit: [
                {
                    "end": end,
                    "filed": filed,
                    "form": "10-K",
                    "val": value,
                }
                for (_, end, filed), value in zip(_YEARS, values)
            ]
        }
    }


def _company_facts(*, weak=False, financial=False):
    if weak:
        revenue = [100.0, 95.0, 90.0]
        income = [-15.0, -18.0, -22.0]
        cashflow = [-8.0, -10.0, -12.0]
        equity = [5.0, -3.0, -12.0]
        debt = [40.0, 55.0, 70.0]
        cash = [8.0, 6.0, 4.0]
        shares = [10.0, 11.0, 14.0]
    else:
        revenue = [100.0, 125.0, 160.0]
        income = [12.0, 17.0, 24.0]
        cashflow = [18.0, 24.0, 32.0]
        equity = [75.0, 90.0, 110.0]
        debt = [25.0, 22.0, 20.0]
        cash = [8.0, 14.0, 24.0]
        shares = [10.0, 10.0, 9.8]
    gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": _duration_fact(revenue),
        "NetIncomeLoss": _duration_fact(income),
        "NetCashProvidedByUsedInOperatingActivities": _duration_fact(cashflow),
        "PaymentsToAcquirePropertyPlantAndEquipment": _duration_fact([3.0, 4.0, 5.0]),
        "StockholdersEquity": _instant_fact(equity),
        "LongTermDebt": _instant_fact(debt),
        "CashAndCashEquivalentsAtCarryingValue": _instant_fact(cash),
        "EntityCommonStockSharesOutstanding": _instant_fact(shares, "shares"),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _duration_fact(shares, "shares"),
    }
    if financial:
        gaap["LoansAndLeasesReceivableNetReportedAmount"] = _instant_fact([250.0, 280.0, 310.0])
    return {
        "entityName": "Example Bancorp" if financial else "Example Industries",
        "facts": {"us-gaap": gaap},
    }


def test_business_quality_rewards_durable_cash_generative_company():
    result = analyze_company_facts(_company_facts(), market_cap=240.0)

    assert result["status"] == "ok"
    assert result["score"] >= 75
    assert result["valuation_score"] >= 70
    assert result["severe_risk"] is False
    assert result["profile"] == "operating_company"


def test_business_quality_flags_persistent_losses_and_dilution():
    result = analyze_company_facts(_company_facts(weak=True), market_cap=180.0)

    assert result["score"] < 45
    assert result["severe_risk"] is True
    assert "Anhaltende Verluste und negativer freier Cashflow" in result["severe_reasons"]
    assert "Aktienverwaesserung ueber 25%" in result["severe_reasons"]


def test_financial_profile_does_not_apply_generic_cashflow_leverage_gate():
    result = analyze_company_facts(
        _company_facts(weak=True, financial=True),
        market_cap=60.0,
    )

    assert result["profile"] == "financial"
    assert result["dimensions"]["cashflow"]["applicable"] is False
    assert result["dimensions"]["leverage"]["applicable"] is False
    assert "Anhaltende Verluste und negativer freier Cashflow" not in result["severe_reasons"]
    assert result["status"] == "partial"
    assert result["holding_fit"] != "LONG_TERM_SUPPORT"


def test_missing_company_facts_are_neutral_not_a_trade_blocker():
    result = analyze_company_facts({})

    assert result["status"] == "missing"
    assert result["score"] is None
    assert result["severe_risk"] is False


def test_api_enrichment_keeps_technical_score_and_fundamentals_separate(monkeypatch):
    monkeypatch.setattr(
        api,
        "fetch_business_quality",
        lambda *args, **kwargs: {
            "status": "ok",
            "score": 60,
            "label": "NEUTRAL",
            "valuation_score": 55,
            "valuation_label": "FAIR",
            "holding_fit": "SWING_SUPPORT",
            "holding_fit_label": "Swing unterstuetzt",
            "position_size_cap": 0.85,
            "coverage_pct": 90,
            "data_age_days": 120,
            "freshness_status": "CURRENT",
            "severe_risk": False,
            "severe_reasons": [],
            "value_trap_risk": False,
            "metrics": {"roe_pct": 18.0},
            "backtest_eligible": False,
            "model_version": business_quality.BUSINESS_QUALITY_MODEL_VERSION,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "as_of": "2025-12-31",
            "company_name": "Example Corp",
        },
    )
    row = {
        "ticker": "EXMP",
        "Strategy": "Momentum Breakout Long",
        "score": 90,
        "price": 20.0,
    }

    enriched = api._ensure_stock_business_quality(row)

    assert enriched["score"] == 90
    assert enriched["Business_Quality_Score"] == 60
    assert "Investment_Score" not in enriched
    assert enriched["Business_Holding_Fit"] == "SWING_SUPPORT"
    assert enriched["Business_Holding_Fit_Label"] == "Swing unterstuetzt"
    assert enriched["Business_Position_Size_Cap"] == 0.85
    assert enriched["Business_Metrics"]["roe_pct"] == 18.0
    assert enriched["Business_Backtest_Eligible"] is False
    assert enriched["company_name"] == "Example Corp"


def test_cached_enrichment_removes_legacy_blended_score():
    row = {
        "ticker": "EXMP",
        "Strategy": "Momentum Breakout Long",
        "score": 90,
        "Investment_Score": 96,
        "Business_Data_Status": "ok",
        "Business_Quality": {
            "model_version": business_quality.BUSINESS_QUALITY_MODEL_VERSION,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "company_name": "Example Cached Corp",
            "holding_fit": "LONG_TERM_CANDIDATE",
            "holding_fit_label": "Langfristig pruefenswert; Moat offen",
            "position_size_cap": 1.0,
            "freshness_status": "CURRENT",
            "metrics": {},
        },
    }

    enriched = api._ensure_stock_business_quality(row)

    assert "Investment_Score" not in enriched
    assert enriched["score"] == 90
    assert enriched["Business_Holding_Fit"] == "LONG_TERM_CANDIDATE"
    assert enriched["company_name"] == "Example Cached Corp"


def test_stale_embedded_quality_is_recalculated(monkeypatch):
    refreshed = {
        "status": "ok",
        "score": 72,
        "label": "SOLIDE",
        "valuation_score": 55,
        "valuation_label": "FAIR",
        "holding_fit": "SWING_SUPPORT",
        "holding_fit_label": "Unternehmensqualitaet stuetzt das Halten",
        "position_size_cap": 0.85,
        "coverage_pct": 90,
        "freshness_status": "CURRENT",
        "severe_risk": False,
        "severe_reasons": [],
        "value_trap_risk": False,
        "metrics": {},
        "backtest_eligible": False,
        "model_version": business_quality.BUSINESS_QUALITY_MODEL_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(api, "fetch_business_quality", lambda *args, **kwargs: refreshed)
    row = {
        "ticker": "EXMP",
        "Strategy": "Momentum Breakout Long",
        "price": 10.0,
        "Business_Data_Status": "ok",
        "Business_Quality": {
            "model_version": business_quality.BUSINESS_QUALITY_MODEL_VERSION,
            "fetched_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            "score": 99,
        },
    }

    enriched = api._ensure_stock_business_quality(row)

    assert enriched["Business_Quality_Score"] == 72
    assert enriched["Business_Quality"] is refreshed


def test_debt_components_are_summed_by_fiscal_period():
    facts = _company_facts()
    gaap = facts["facts"]["us-gaap"]
    gaap.pop("LongTermDebt")
    gaap["LongTermDebtCurrent"] = _instant_fact([4.0, 5.0, 7.0])
    gaap["LongTermDebtNoncurrent"] = _instant_fact([21.0, 25.0, 35.0])

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["metrics"]["latest_debt"] == 42.0


def test_direct_total_debt_is_not_double_counted_with_components():
    facts = _company_facts()
    gaap = facts["facts"]["us-gaap"]
    gaap.pop("LongTermDebt")
    gaap["LongTermDebtAndFinanceLeaseObligations"] = _instant_fact([32.0, 31.0, 30.0])
    gaap["LongTermDebtCurrent"] = _instant_fact([5.0, 5.0, 5.0])
    gaap["LongTermDebtNoncurrent"] = _instant_fact([27.0, 26.0, 25.0])

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["metrics"]["latest_debt"] == 30.0


def test_fresher_complete_debt_components_replace_stale_direct_total():
    facts = _company_facts()
    gaap = facts["facts"]["us-gaap"]
    gaap.pop("LongTermDebt")
    gaap["LongTermDebtAndFinanceLeaseObligations"] = _instant_fact([32.0, 31.0, 30.0])
    gaap["LongTermDebtAndFinanceLeaseObligations"]["units"]["USD"] = gaap[
        "LongTermDebtAndFinanceLeaseObligations"
    ]["units"]["USD"][:2]
    gaap["LongTermDebtCurrent"] = _instant_fact([5.0, 5.0, 7.0])
    gaap["LongTermDebtNoncurrent"] = _instant_fact([27.0, 26.0, 35.0])

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["metrics"]["latest_debt"] == 42.0


def test_unadjusted_point_in_time_share_jump_is_not_called_confirmed_dilution():
    facts = _company_facts(weak=True)
    facts["facts"]["us-gaap"].pop("WeightedAverageNumberOfDilutedSharesOutstanding")

    result = analyze_company_facts(facts, market_cap=180.0)

    assert result["metrics"]["share_dilution_basis"] == "point_in_time_unadjusted"
    assert result["metrics"]["share_dilution_reliable"] is False
    assert "Aktienverwaesserung ueber 25%" not in result["severe_reasons"]


def test_missing_debt_is_not_treated_as_debt_free():
    facts = _company_facts()
    facts["facts"]["us-gaap"].pop("LongTermDebt")

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["dimensions"]["leverage"]["applicable"] is False
    assert result["metrics"]["latest_debt"] is None
    assert result["metrics"]["net_debt"] is None
    assert result["metrics"]["leverage_basis"] == "unavailable"


def test_fresh_balance_sheet_does_not_hide_stale_income_and_cashflow():
    facts = _company_facts()
    gaap = facts["facts"]["us-gaap"]
    for tag in (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ):
        gaap[tag]["units"]["USD"] = gaap[tag]["units"]["USD"][:2]

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["as_of"] == "2023-12-31"


def test_insufficient_coverage_does_not_publish_precision_score():
    facts = _company_facts()
    gaap = facts["facts"]["us-gaap"]
    revenue = gaap["RevenueFromContractWithCustomerExcludingAssessedTax"]
    facts["facts"]["us-gaap"] = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": revenue,
    }

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["coverage_pct"] <= 35
    assert result["status"] == "limited"
    assert result["score"] is None


def test_reit_is_not_scored_without_ffo_affo_specialist_data():
    facts = _company_facts()
    facts["entityName"] = "Example Property REIT"
    facts["facts"]["us-gaap"]["InvestmentPropertyNet"] = _instant_fact([80.0, 90.0, 100.0])

    result = analyze_company_facts(facts, market_cap=240.0)

    assert result["profile"] == "reit"
    assert result["status"] == "limited"
    assert result["score"] is None
    assert result["valuation_score"] is None
    assert result["specialist_data_required"] is True
    assert result["holding_fit"] == "DATA_LIMITED"


def test_cashflow_is_explicitly_a_proxy_not_claimed_owner_earnings():
    result = analyze_company_facts(_company_facts(), market_cap=240.0)

    assert result["cashflow_methodology"] == "cfo_minus_total_capex_proxy_not_owner_earnings"
    assert result["methodology"] == "live_sec_filing_quality_not_point_in_time_backtest"


def test_cached_filings_recalculate_valuation_for_current_market_cap(monkeypatch):
    facts = _company_facts()
    calls = []
    business_quality._company_facts_cache.clear()
    business_quality._error_cache.clear()
    monkeypatch.setattr(
        business_quality,
        "_load_sec_ticker_map",
        lambda: {"EXMP": {"cik": "0000000001", "name": "Example Industries"}},
    )

    def fake_sec_get(url, timeout=10.0):
        calls.append(url)
        return facts

    monkeypatch.setattr(business_quality, "_sec_get_json", fake_sec_get)

    cheap = business_quality.fetch_business_quality("EXMP", market_cap=240.0)
    expensive = business_quality.fetch_business_quality("EXMP", market_cap=2400.0)

    assert len(calls) == 1
    assert cheap["score"] == expensive["score"]
    assert cheap["valuation_score"] > expensive["valuation_score"]


def test_growth_uses_actual_elapsed_years_and_roe_uses_average_equity():
    facts = _company_facts()
    facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
        "units": {
            "USD": [
                {
                    "start": "2021-01-01",
                    "end": "2021-12-31",
                    "filed": "2022-02-15",
                    "form": "10-K",
                    "val": 100.0,
                },
                {
                    "start": "2024-01-01",
                    "end": "2024-12-31",
                    "filed": "2025-02-15",
                    "form": "10-K",
                    "val": 133.1,
                },
            ]
        }
    }

    result = analyze_company_facts(facts, market_cap=240.0)

    assert 9.9 <= result["metrics"]["revenue_cagr_pct"] <= 10.1
    assert result["metrics"]["roe_pct"] == 24.0


def test_quality_score_is_independent_from_current_valuation():
    cheap = analyze_company_facts(_company_facts(), market_cap=240.0)
    expensive = analyze_company_facts(_company_facts(), market_cap=2400.0)

    assert cheap["score"] == expensive["score"]
    assert cheap["valuation_score"] > expensive["valuation_score"]
    assert cheap["quality_excludes_valuation"] is True
    assert cheap["backtest_eligible"] is False


def test_frontend_does_not_render_legacy_blended_investment_score():
    source = (Path(__file__).resolve().parent / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "Investment_Score" not in source


def test_only_confirmed_severe_business_risk_blocks_swing_long_mail():
    severe = {
        "Strategy": "MA Bounce Long",
        "Business_Severe_Risk": True,
    }
    missing = {
        "Strategy": "MA Bounce Long",
        "Business_Data_Status": "missing",
        "Business_Severe_Risk": False,
    }

    assert api._stock_strategy_mail_quality_state(severe) == (
        False,
        "stock_swing_mail_blocked_severe_business_risk",
    )
    assert api._stock_strategy_mail_quality_state(missing) == (True, "")


def test_business_quality_context_excludes_non_swing_products():
    assert api._stock_business_quality_context({"Strategy": "Momentum Breakout Long"}) is True
    assert api._stock_business_quality_context({"Strategy": "Momentum Breakout Short"}) is False
    assert api._stock_business_quality_context({"Strategy": "ORB Long"}) is False
    assert api._stock_business_quality_context({"Strategy": "Biotech Catalyst Long"}) is False
