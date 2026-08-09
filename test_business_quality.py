from __future__ import annotations

import api

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


def test_missing_company_facts_are_neutral_not_a_trade_blocker():
    result = analyze_company_facts({})

    assert result["status"] == "missing"
    assert result["score"] is None
    assert result["severe_risk"] is False


def test_api_enrichment_keeps_technical_score_and_builds_separate_total(monkeypatch):
    monkeypatch.setattr(
        api,
        "fetch_business_quality",
        lambda *args, **kwargs: {
            "status": "ok",
            "score": 60,
            "label": "NEUTRAL",
            "valuation_score": 55,
            "valuation_label": "FAIR",
            "coverage_pct": 90,
            "severe_risk": False,
            "severe_reasons": [],
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
    assert enriched["Investment_Score"] == 81.6
    assert enriched["company_name"] == "Example Corp"


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
