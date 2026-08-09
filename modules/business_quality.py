"""Live business-quality overlay for multi-day common-stock long setups.

The technical scanner decides whether price action offers a setup. This module
answers a different question: whether the underlying business and valuation
support holding a multi-day long. It intentionally does not modify technical
scores and is not suitable for point-in-time backtests without filing vintages.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
_SEC_BASE = "https://data.sec.gov"
_SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_SUCCESS_TTL_SECONDS = 24 * 3600
_ERROR_TTL_SECONDS = 3600
_MIN_SEC_INTERVAL_SECONDS = 0.12

_cache_lock = threading.RLock()
_request_lock = threading.Lock()
_quality_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_ticker_map_cache: Tuple[float, Dict[str, Dict[str, Any]]] = (0.0, {})
_last_sec_request_monotonic = 0.0


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _sec_headers() -> Dict[str, str]:
    contact = (
        os.environ.get("SEC_CONTACT_EMAIL")
        or os.environ.get("ALERT_EMAIL")
        or os.environ.get("GMAIL_USER")
        or "admin@alphastation.local"
    )
    user_agent = os.environ.get("SEC_USER_AGENT") or f"AlphaStation/1.0 {contact}"
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    }


def _sec_get_json(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    global _last_sec_request_monotonic
    with _request_lock:
        elapsed = time.monotonic() - _last_sec_request_monotonic
        if elapsed < _MIN_SEC_INTERVAL_SECONDS:
            time.sleep(_MIN_SEC_INTERVAL_SECONDS - elapsed)
        response = requests.get(url, headers=_sec_headers(), timeout=timeout)
        _last_sec_request_monotonic = time.monotonic()
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _load_sec_ticker_map() -> Dict[str, Dict[str, Any]]:
    global _ticker_map_cache
    now = time.time()
    with _cache_lock:
        cached_at, cached = _ticker_map_cache
        if cached and now - cached_at < _SUCCESS_TTL_SECONDS:
            return cached
    payload = _sec_get_json(_SEC_TICKER_URL)
    mapping: Dict[str, Dict[str, Any]] = {}
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        cik = str(item.get("cik_str") or "").strip()
        if ticker and cik:
            mapping[ticker] = {
                "cik": cik.zfill(10),
                "name": str(item.get("title") or "").strip(),
            }
    with _cache_lock:
        _ticker_map_cache = (now, mapping)
    return mapping


def _fact_entries(
    companyfacts: Dict[str, Any],
    tags: Sequence[str],
    units: Sequence[str],
) -> List[Dict[str, Any]]:
    facts = companyfacts.get("facts") if isinstance(companyfacts.get("facts"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    for taxonomy in ("us-gaap", "dei", "ifrs-full"):
        tax_facts = facts.get(taxonomy) if isinstance(facts.get(taxonomy), dict) else {}
        for tag in tags:
            fact = tax_facts.get(tag) if isinstance(tax_facts.get(tag), dict) else {}
            unit_map = fact.get("units") if isinstance(fact.get("units"), dict) else {}
            for unit in units:
                entries = unit_map.get(unit)
                if isinstance(entries, list):
                    candidates.extend(entry for entry in entries if isinstance(entry, dict))
    return candidates


def _annual_series(
    companyfacts: Dict[str, Any],
    tags: Sequence[str],
    *,
    units: Sequence[str] = ("USD",),
    instant: bool = False,
    limit: int = 5,
) -> List[Tuple[str, float]]:
    by_end: Dict[str, Tuple[str, float]] = {}
    for entry in _fact_entries(companyfacts, tags, units):
        form = str(entry.get("form") or "").upper()
        if form not in _ANNUAL_FORMS:
            continue
        end = str(entry.get("end") or "")
        filed = str(entry.get("filed") or "")
        value = _safe_float(entry.get("val"))
        if not end or value is None:
            continue
        start = str(entry.get("start") or "")
        if instant:
            if start:
                continue
        else:
            if not start:
                continue
            try:
                duration = (
                    datetime.fromisoformat(end).date()
                    - datetime.fromisoformat(start).date()
                ).days
            except ValueError:
                continue
            if duration < 250 or duration > 450:
                continue
        previous = by_end.get(end)
        if previous is None or filed >= previous[0]:
            by_end[end] = (filed, value)
    return [(end, by_end[end][1]) for end in sorted(by_end)[-limit:]]


def _latest(series: Sequence[Tuple[str, float]]) -> Optional[float]:
    return series[-1][1] if series else None


def _positive_ratio(series: Sequence[Tuple[str, float]], limit: int = 3) -> Optional[float]:
    values = [value for _, value in series[-limit:]]
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def _score_yield(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value >= 0.10:
        return 100.0
    if value >= 0.07:
        return 85.0
    if value >= 0.045:
        return 70.0
    if value >= 0.025:
        return 55.0
    if value > 0:
        return 35.0
    return 10.0


def _mean_available(values: Iterable[Optional[float]]) -> Optional[float]:
    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _industry_profile(companyfacts: Dict[str, Any], industry: str = "") -> str:
    facts = companyfacts.get("facts") if isinstance(companyfacts.get("facts"), dict) else {}
    gaap = facts.get("us-gaap") if isinstance(facts.get("us-gaap"), dict) else {}
    text = " ".join(
        [
            str(companyfacts.get("entityName") or ""),
            str(industry or ""),
        ]
    ).upper()
    if "REAL ESTATE INVESTMENT TRUST" in text or "REIT" in text or "InvestmentPropertyNet" in gaap:
        return "reit"
    financial_words = ("BANK", "BANCORP", "INSURANCE", "FINANCIAL", "CREDIT", "BROKER")
    financial_tags = {
        "LoansAndLeasesReceivableNetReportedAmount",
        "InterestAndFeesOnLoansAndLeases",
        "InsurancePremiumsRevenue",
    }
    if any(word in text for word in financial_words) or any(tag in gaap for tag in financial_tags):
        return "financial"
    return "operating_company"


def _label_for_score(score: Optional[float]) -> str:
    if score is None:
        return "DATEN FEHLEN"
    if score >= 80:
        return "STARK"
    if score >= 65:
        return "SOLIDE"
    if score >= 48:
        return "NEUTRAL"
    return "SCHWACH"


def _valuation_label(score: Optional[float]) -> str:
    if score is None:
        return "NICHT BEWERTBAR"
    if score >= 75:
        return "ATTRAKTIV"
    if score >= 55:
        return "FAIR"
    if score >= 35:
        return "ANSPRUCHSVOLL"
    return "TEUER/UNPROFITABEL"


def analyze_company_facts(
    companyfacts: Dict[str, Any],
    *,
    price: Optional[float] = None,
    market_cap: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
    industry: str = "",
) -> Dict[str, Any]:
    """Score current filing data without pretending it is a backtest signal."""
    if not isinstance(companyfacts, dict) or not companyfacts.get("facts"):
        return {
            "status": "missing",
            "score": None,
            "label": "DATEN FEHLEN",
            "valuation_score": None,
            "valuation_label": "NICHT BEWERTBAR",
            "coverage_pct": 0,
            "severe_risk": False,
            "severe_reasons": [],
            "strengths": [],
            "risks": ["Keine verwertbaren Unternehmensberichte"],
            "profile": "unknown",
            "methodology": "live_fundamentals_not_point_in_time_backtest",
        }

    revenue = _annual_series(
        companyfacts,
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    )
    net_income = _annual_series(companyfacts, ("NetIncomeLoss", "ProfitLoss"))
    operating_cash = _annual_series(companyfacts, ("NetCashProvidedByUsedInOperatingActivities",))
    capex = _annual_series(companyfacts, ("PaymentsToAcquirePropertyPlantAndEquipment",))
    equity = _annual_series(
        companyfacts,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        instant=True,
    )
    debt = _annual_series(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
            "LongTermDebt",
        ),
        instant=True,
    )
    cash = _annual_series(
        companyfacts,
        ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        instant=True,
    )
    shares = _annual_series(
        companyfacts,
        ("EntityCommonStockSharesOutstanding",),
        units=("shares",),
        instant=True,
    )
    if len(shares) < 2:
        shares = _annual_series(
            companyfacts,
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"),
            units=("shares",),
        )

    fcf: List[Tuple[str, float]] = []
    capex_by_end = dict(capex)
    for end, cfo_value in operating_cash:
        capex_value = capex_by_end.get(end)
        if capex_value is not None:
            fcf.append((end, cfo_value - abs(capex_value)))

    latest_revenue = _latest(revenue)
    latest_income = _latest(net_income)
    latest_cfo = _latest(operating_cash)
    latest_fcf = _latest(fcf)
    latest_equity = _latest(equity)
    latest_debt = _latest(debt) or 0.0
    latest_cash = _latest(cash) or 0.0
    latest_shares = shares_outstanding or _latest(shares)
    effective_market_cap = _safe_float(market_cap)
    if (effective_market_cap is None or effective_market_cap <= 0) and price and latest_shares:
        effective_market_cap = float(price) * float(latest_shares)

    profile = _industry_profile(companyfacts, industry)
    dimensions: Dict[str, Dict[str, Any]] = {}

    income_positive = _positive_ratio(net_income)
    margin = (
        latest_income / latest_revenue
        if latest_income is not None and latest_revenue and latest_revenue > 0
        else None
    )
    growth_score = None
    if len(revenue) >= 3 and revenue[-3][1] > 0 and revenue[-1][1] > 0:
        annualized_growth = (revenue[-1][1] / revenue[-3][1]) ** 0.5 - 1.0
        growth_score = _clamp(55.0 + annualized_growth * 220.0)
    margin_score = None if margin is None else _clamp(45.0 + margin * 250.0)
    earnings_score = _mean_available(
        [income_positive * 100.0 if income_positive is not None else None, margin_score, growth_score]
    )
    dimensions["earnings"] = {"score": earnings_score, "applicable": earnings_score is not None}

    cfo_positive = _positive_ratio(operating_cash)
    fcf_positive = _positive_ratio(fcf)
    conversion_score = None
    if latest_cfo is not None and latest_income is not None and latest_income > 0:
        conversion_score = _clamp((latest_cfo / latest_income) * 65.0)
    cashflow_score = _mean_available(
        [cfo_positive * 100.0 if cfo_positive is not None else None,
         fcf_positive * 100.0 if fcf_positive is not None else None,
         conversion_score]
    )
    cashflow_applicable = profile == "operating_company" and cashflow_score is not None
    dimensions["cashflow"] = {"score": cashflow_score, "applicable": cashflow_applicable}

    roe_score = None
    if latest_income is not None and latest_equity is not None and latest_equity > 0:
        roe = latest_income / latest_equity
        if roe >= 0.20:
            roe_score = 100.0
        elif roe >= 0.15:
            roe_score = 85.0
        elif roe >= 0.10:
            roe_score = 70.0
        elif roe >= 0.05:
            roe_score = 55.0
        elif roe > 0:
            roe_score = 40.0
        else:
            roe_score = 15.0
    dimensions["returns"] = {"score": roe_score, "applicable": roe_score is not None}

    leverage_score = None
    if profile == "operating_company" and latest_cfo is not None:
        net_debt = latest_debt - latest_cash
        if net_debt <= 0:
            leverage_score = 95.0
        elif latest_cfo <= 0:
            leverage_score = 10.0
        else:
            coverage = net_debt / latest_cfo
            if coverage <= 1:
                leverage_score = 90.0
            elif coverage <= 2:
                leverage_score = 78.0
            elif coverage <= 3:
                leverage_score = 62.0
            elif coverage <= 5:
                leverage_score = 40.0
            else:
                leverage_score = 15.0
    dimensions["leverage"] = {"score": leverage_score, "applicable": leverage_score is not None}

    dilution = None
    dilution_score = None
    if len(shares) >= 2 and shares[-2][1] > 0:
        dilution = shares[-1][1] / shares[-2][1] - 1.0
        if dilution <= 0:
            dilution_score = 92.0
        elif dilution <= 0.02:
            dilution_score = 82.0
        elif dilution <= 0.05:
            dilution_score = 66.0
        elif dilution <= 0.10:
            dilution_score = 45.0
        elif dilution <= 0.20:
            dilution_score = 25.0
        else:
            dilution_score = 5.0
    dimensions["dilution"] = {"score": dilution_score, "applicable": dilution_score is not None}

    earnings_yield = (
        latest_income / effective_market_cap
        if latest_income is not None and effective_market_cap and effective_market_cap > 0
        else None
    )
    fcf_yield = (
        latest_fcf / effective_market_cap
        if profile == "operating_company" and latest_fcf is not None and effective_market_cap and effective_market_cap > 0
        else None
    )
    price_to_book_score = None
    if profile in {"financial", "reit"} and latest_equity and latest_equity > 0 and effective_market_cap:
        price_to_book = effective_market_cap / latest_equity
        if price_to_book <= 1.0:
            price_to_book_score = 85.0
        elif price_to_book <= 1.8:
            price_to_book_score = 70.0
        elif price_to_book <= 3.0:
            price_to_book_score = 50.0
        else:
            price_to_book_score = 25.0
    valuation_score = _mean_available(
        [_score_yield(earnings_yield), _score_yield(fcf_yield), price_to_book_score]
    )
    dimensions["valuation"] = {"score": valuation_score, "applicable": valuation_score is not None}

    weights = (
        {"earnings": 0.35, "returns": 0.25, "dilution": 0.15, "valuation": 0.25}
        if profile in {"financial", "reit"}
        else {
            "earnings": 0.25,
            "cashflow": 0.20,
            "returns": 0.15,
            "leverage": 0.15,
            "dilution": 0.10,
            "valuation": 0.15,
        }
    )
    weighted = 0.0
    used_weight = 0.0
    for name, weight in weights.items():
        item = dimensions.get(name, {})
        score = item.get("score") if item.get("applicable") else None
        if score is not None:
            weighted += float(score) * weight
            used_weight += weight
    coverage_pct = round(used_weight * 100.0)
    raw_score = weighted / used_weight if used_weight > 0 else None
    score = None
    if raw_score is not None:
        confidence = min(1.0, used_weight / 0.65)
        score = round(_clamp(50.0 + (raw_score - 50.0) * confidence))

    severe_reasons: List[str] = []
    recent_income = [value for _, value in net_income[-3:]]
    recent_fcf = [value for _, value in fcf[-3:]]
    if (
        profile == "operating_company"
        and len(recent_income) >= 2
        and len(recent_fcf) >= 2
        and all(value < 0 for value in recent_income)
        and all(value < 0 for value in recent_fcf)
    ):
        severe_reasons.append("Anhaltende Verluste und negativer freier Cashflow")
    if dilution is not None and dilution > 0.25:
        severe_reasons.append("Aktienverwaesserung ueber 25%")
    if (
        profile == "operating_company"
        and latest_equity is not None
        and latest_equity <= 0
        and latest_debt > latest_cash
        and (latest_cfo is None or latest_cfo <= 0)
    ):
        severe_reasons.append("Negatives Eigenkapital, Nettoschulden und schwacher Cashflow")

    strengths: List[str] = []
    risks: List[str] = []
    if income_positive is not None and income_positive >= 2 / 3:
        strengths.append("Gewinne mehrheitlich positiv")
    elif income_positive is not None and income_positive <= 1 / 3:
        risks.append("Gewinnhistorie schwach")
    if fcf_positive is not None and fcf_positive >= 2 / 3:
        strengths.append("Freier Cashflow mehrheitlich positiv")
    elif profile == "operating_company" and fcf_positive is not None and fcf_positive <= 1 / 3:
        risks.append("Freier Cashflow schwach")
    if dilution is not None and dilution > 0.10:
        risks.append(f"Aktienverwaesserung {dilution * 100:.0f}%")
    if leverage_score is not None and leverage_score < 35:
        risks.append("Verschuldung im Verhaeltnis zum Cashflow hoch")

    latest_periods = [series[-1][0] for series in (revenue, net_income, operating_cash, equity) if series]
    as_of = max(latest_periods) if latest_periods else None
    status = "ok" if coverage_pct >= 70 else "partial" if coverage_pct >= 35 else "limited"
    return {
        "status": status,
        "score": score,
        "label": _label_for_score(score),
        "valuation_score": round(valuation_score) if valuation_score is not None else None,
        "valuation_label": _valuation_label(valuation_score),
        "coverage_pct": coverage_pct,
        "severe_risk": bool(severe_reasons),
        "severe_reasons": severe_reasons,
        "strengths": strengths[:3],
        "risks": list(dict.fromkeys(risks + severe_reasons))[:4],
        "profile": profile,
        "as_of": as_of,
        "market_cap": effective_market_cap,
        "methodology": "live_fundamentals_not_point_in_time_backtest",
        "dimensions": dimensions,
    }


def fetch_business_quality(
    ticker: str,
    *,
    price: Optional[float] = None,
    market_cap: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
    industry: str = "",
) -> Dict[str, Any]:
    """Fetch and cache official filing data for one listed company."""
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return analyze_company_facts({})
    now = time.time()
    with _cache_lock:
        cached = _quality_cache.get(symbol)
        if cached:
            cached_at, value = cached
            ttl = _SUCCESS_TTL_SECONDS if value.get("status") in {"ok", "partial", "limited"} else _ERROR_TTL_SECONDS
            if now - cached_at < ttl:
                return dict(value)
    try:
        ticker_info = _load_sec_ticker_map().get(symbol)
        if not ticker_info:
            raise LookupError("Ticker nicht in offizieller Unternehmensliste")
        cik = str(ticker_info["cik"]).zfill(10)
        facts = _sec_get_json(f"{_SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
        result = analyze_company_facts(
            facts,
            price=price,
            market_cap=market_cap,
            shares_outstanding=shares_outstanding,
            industry=industry,
        )
        result.update({
            "ticker": symbol,
            "cik": cik,
            "company_name": str(ticker_info.get("name") or facts.get("entityName") or ""),
            "source": "official_company_filings",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        result = analyze_company_facts({})
        result.update({
            "ticker": symbol,
            "source": "official_company_filings",
            "error": str(exc)[:180],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    with _cache_lock:
        _quality_cache[symbol] = (now, dict(result))
    return result
