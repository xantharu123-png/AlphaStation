"""Live business-quality overlay for multi-day common-stock long setups.

The technical scanner decides whether price action offers a setup. This module
answers a different question: whether the underlying business and valuation
support holding a multi-day long. It intentionally does not modify technical
scores and is not suitable for point-in-time backtests without filing vintages.
"""

from __future__ import annotations

import os
import statistics
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
BUSINESS_QUALITY_MODEL_VERSION = 2

_cache_lock = threading.RLock()
_request_lock = threading.Lock()
_company_facts_cache: Dict[str, Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}
_error_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
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


def _preferred_annual_series(
    companyfacts: Dict[str, Any],
    tags: Sequence[str],
    *,
    units: Sequence[str] = ("USD",),
    instant: bool = False,
    limit: int = 5,
) -> List[Tuple[str, float]]:
    """Choose one coherent SEC concept instead of mixing alternative tags.

    Company Facts often exposes the same economic item under several tags. A
    merged series can silently combine overlapping concepts or let the latest
    filing overwrite a different component. Prefer the concept with the most
    recent observation, then the longest history, while retaining tag order as
    the final tie-breaker.
    """
    candidates: List[Tuple[str, int, int, List[Tuple[str, float]]]] = []
    for priority, tag in enumerate(tags):
        series = _annual_series(
            companyfacts,
            (tag,),
            units=units,
            instant=instant,
            limit=limit,
        )
        if series:
            candidates.append((series[-1][0], len(series), -priority, series))
    if not candidates:
        return []
    return max(candidates, key=lambda item: item[:3])[3]


def _sum_series_by_end(
    left: Sequence[Tuple[str, float]],
    right: Sequence[Tuple[str, float]],
    *,
    limit: int = 5,
) -> List[Tuple[str, float]]:
    """Sum components only where both belong to the same fiscal period.

    Treating a missing component as zero understates debt. If the components
    do not overlap, the caller must fall back to the best available series and
    expose the resulting coverage loss instead of inventing a total.
    """
    left_by_end = dict(left)
    right_by_end = dict(right)
    combined = []
    for end in sorted(set(left_by_end) & set(right_by_end)):
        combined.append((end, left_by_end[end] + right_by_end[end]))
    return combined[-limit:]


def _total_debt_series(companyfacts: Dict[str, Any], limit: int = 5) -> List[Tuple[str, float]]:
    """Return total debt without confusing current and non-current components."""
    direct = _preferred_annual_series(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligations",
            "LongTermDebtAndCapitalLeaseObligations",
            "DebtAndFinanceLeaseObligations",
            "DebtLongtermAndShorttermCombinedAmount",
        ),
        instant=True,
        limit=limit,
    )
    current = _preferred_annual_series(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "LongTermDebtCurrent",
        ),
        instant=True,
        limit=limit,
    )
    noncurrent = _preferred_annual_series(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
            "LongTermDebtNoncurrent",
            "LongTermDebt",
        ),
        instant=True,
        limit=limit,
    )
    components = (
        _sum_series_by_end(current, noncurrent, limit=limit)
        if current and noncurrent
        else list(noncurrent or current)[-limit:]
    )
    candidates = [series for series in (direct, components) if series]
    if not candidates:
        return []
    # Prefer the freshest fiscal period. On an equal period, prefer the direct
    # total because it cannot omit a current/non-current component.
    return max(
        candidates,
        key=lambda series: (series[-1][0], series is direct, len(series)),
    )


def _latest(series: Sequence[Tuple[str, float]]) -> Optional[float]:
    return series[-1][1] if series else None


def _positive_ratio(series: Sequence[Tuple[str, float]], limit: int = 3) -> Optional[float]:
    values = [value for _, value in series[-limit:]]
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def _annualized_growth(series: Sequence[Tuple[str, float]], limit: int = 5) -> Optional[float]:
    usable = [(end, value) for end, value in series[-limit:] if value > 0]
    if len(usable) < 2:
        return None
    try:
        first_date = datetime.fromisoformat(usable[0][0]).date()
        last_date = datetime.fromisoformat(usable[-1][0]).date()
    except ValueError:
        return None
    years = (last_date - first_date).days / 365.2425
    if years < 0.75:
        return None
    return (usable[-1][1] / usable[0][1]) ** (1.0 / years) - 1.0


def _up_year_ratio(series: Sequence[Tuple[str, float]], limit: int = 5) -> Optional[float]:
    values = [value for _, value in series[-limit:]]
    if len(values) < 2:
        return None
    return sum(1 for previous, current in zip(values, values[1:]) if current >= previous) / (len(values) - 1)


def _matched_ratios(
    numerator: Sequence[Tuple[str, float]],
    denominator: Sequence[Tuple[str, float]],
    *,
    limit: int = 5,
) -> List[float]:
    denominator_by_end = dict(denominator)
    ratios = []
    for end, value in numerator[-limit:]:
        base = denominator_by_end.get(end)
        if base is not None and base > 0:
            ratios.append(value / base)
    return ratios


def _margin_stability_score(margins: Sequence[float]) -> Optional[float]:
    if len(margins) < 3:
        return None
    volatility = statistics.pstdev(margins)
    if volatility <= 0.02:
        return 95.0
    if volatility <= 0.05:
        return 80.0
    if volatility <= 0.10:
        return 60.0
    if volatility <= 0.20:
        return 35.0
    return 15.0


def _data_freshness(as_of: Optional[str]) -> Tuple[Optional[int], str]:
    if not as_of:
        return None, "UNKNOWN"
    try:
        period_end = datetime.fromisoformat(as_of).date()
    except ValueError:
        return None, "UNKNOWN"
    age_days = max(0, (datetime.now(timezone.utc).date() - period_end).days)
    return age_days, "STALE" if age_days > 550 else "CURRENT"


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


def _holding_fit(
    score: Optional[float],
    valuation_score: Optional[float],
    *,
    status: str,
    freshness_status: str,
    severe_risk: bool,
) -> Tuple[str, str, float]:
    """Translate slow-moving fundamentals into a holding profile, not an entry."""
    if severe_risk:
        return "AVOID_LONG_TERM", "Nicht langfristig halten", 0.0
    if score is None or status in {"missing", "limited"} or freshness_status in {"STALE", "UNKNOWN"}:
        return "DATA_LIMITED", "Unternehmensdaten pruefen", 0.5
    if status == "partial":
        if score >= 65:
            return "SWING_SUPPORT", "Qualitaet stuetzt das Halten; Spezialdaten fehlen", 0.75
        return "DATA_LIMITED", "Unternehmensdaten pruefen", 0.5
    if score >= 80 and valuation_score is not None and valuation_score >= 55:
        return "LONG_TERM_CANDIDATE", "Langfristig pruefenswert; Moat offen", 1.0
    if score >= 70 and valuation_score is not None and valuation_score < 40:
        return "QUALITY_EXPENSIVE", "Qualitaet, aber teuer", 0.65
    if score >= 65:
        return "SWING_SUPPORT", "Unternehmensqualitaet stuetzt das Halten", 0.85
    if score < 50:
        return "TRADE_ONLY", "Nur technischer Trade", 0.5
    return "NEUTRAL", "Fundamental neutral", 0.75


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
            "holding_fit": "DATA_LIMITED",
            "holding_fit_label": "Daten pruefen",
            "position_size_cap": 0.5,
            "coverage_pct": 0,
            "data_age_days": None,
            "freshness_status": "UNKNOWN",
            "severe_risk": False,
            "severe_reasons": [],
            "value_trap_risk": False,
            "strengths": [],
            "risks": ["Keine verwertbaren Unternehmensberichte"],
            "profile": "unknown",
            "metrics": {},
            "methodology": "live_sec_filing_quality_not_point_in_time_backtest",
            "cashflow_methodology": "cfo_minus_total_capex_proxy_not_owner_earnings",
            "specialist_data_required": False,
            "qualitative_limits": [
                "Wettbewerbsvorteil (Moat) nicht automatisch bewertet",
                "Management und Kapitalallokation nicht automatisch bewertet",
            ],
            "quality_excludes_valuation": True,
            "backtest_eligible": False,
            "model_version": BUSINESS_QUALITY_MODEL_VERSION,
        }

    revenue = _preferred_annual_series(
        companyfacts,
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    )
    net_income = _preferred_annual_series(companyfacts, ("NetIncomeLoss", "ProfitLoss"))
    operating_cash = _preferred_annual_series(
        companyfacts,
        ("NetCashProvidedByUsedInOperatingActivities",),
    )
    capex = _preferred_annual_series(
        companyfacts,
        ("PaymentsToAcquirePropertyPlantAndEquipment",),
    )
    equity = _preferred_annual_series(
        companyfacts,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        instant=True,
    )
    debt = _total_debt_series(companyfacts)
    cash = _preferred_annual_series(
        companyfacts,
        ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        instant=True,
    )
    entity_shares = _preferred_annual_series(
        companyfacts,
        ("EntityCommonStockSharesOutstanding",),
        units=("shares",),
        instant=True,
    )
    weighted_shares = _preferred_annual_series(
        companyfacts,
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"),
        units=("shares",),
    )
    # Weighted-average EPS shares are normally restated for stock splits and
    # are therefore the safer dilution series. Point-in-time entity shares are
    # retained for the current market-cap fallback only.
    dilution_shares = weighted_shares if len(weighted_shares) >= 2 else entity_shares
    dilution_basis = "weighted_average_diluted" if len(weighted_shares) >= 2 else "point_in_time_unadjusted"

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
    latest_debt = _latest(debt)
    latest_cash = _latest(cash)
    net_debt = (
        latest_debt - latest_cash
        if latest_debt is not None and latest_cash is not None
        else None
    )
    # Missing cash is handled conservatively with gross debt. Missing debt is
    # not debt-free: in that case the leverage dimension remains unavailable.
    debt_for_leverage = (
        latest_debt - latest_cash
        if latest_debt is not None and latest_cash is not None
        else latest_debt
    )
    leverage_basis = (
        "net_debt"
        if latest_debt is not None and latest_cash is not None
        else "gross_debt"
        if latest_debt is not None
        else "unavailable"
    )
    latest_shares = shares_outstanding or _latest(entity_shares) or _latest(weighted_shares)
    effective_market_cap = _safe_float(market_cap)
    if (effective_market_cap is None or effective_market_cap <= 0) and price and latest_shares:
        effective_market_cap = float(price) * float(latest_shares)

    profile = _industry_profile(companyfacts, industry)
    dimensions: Dict[str, Dict[str, Any]] = {}

    income_positive = _positive_ratio(net_income, limit=5)
    margin = (
        latest_income / latest_revenue
        if latest_income is not None and latest_revenue and latest_revenue > 0
        else None
    )
    annualized_growth = _annualized_growth(revenue)
    growth_score = (
        _clamp(55.0 + annualized_growth * 220.0)
        if annualized_growth is not None
        else None
    )
    margin_score = None if margin is None else _clamp(45.0 + margin * 250.0)
    earnings_inputs = [
        income_positive * 100.0 if income_positive is not None else None,
        growth_score,
    ]
    # Bank and insurer margins are not comparable with operating-company net
    # margins. Their specialist quality remains partial below.
    if profile == "operating_company":
        earnings_inputs.append(margin_score)
    earnings_score = _mean_available(earnings_inputs)
    dimensions["earnings"] = {"score": earnings_score, "applicable": earnings_score is not None}

    cfo_positive = _positive_ratio(operating_cash, limit=5)
    fcf_positive = _positive_ratio(fcf, limit=5)
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

    roe = None
    roe_score = None
    recent_equity = [value for _, value in equity[-2:] if value > 0]
    average_equity = sum(recent_equity) / len(recent_equity) if recent_equity else None
    if latest_income is not None and average_equity is not None and average_equity > 0:
        roe = latest_income / average_equity
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
    debt_to_cfo = None
    if profile == "operating_company" and latest_cfo is not None and debt_for_leverage is not None:
        if debt_for_leverage <= 0:
            leverage_score = 95.0
        elif latest_cfo <= 0:
            leverage_score = 10.0
        else:
            debt_to_cfo = debt_for_leverage / latest_cfo
            if debt_to_cfo <= 1:
                leverage_score = 90.0
            elif debt_to_cfo <= 2:
                leverage_score = 78.0
            elif debt_to_cfo <= 3:
                leverage_score = 62.0
            elif debt_to_cfo <= 5:
                leverage_score = 40.0
            else:
                leverage_score = 15.0
    dimensions["leverage"] = {"score": leverage_score, "applicable": leverage_score is not None}

    dilution = None
    dilution_score = None
    dilution_reliable = dilution_basis == "weighted_average_diluted"
    if len(dilution_shares) >= 2 and dilution_shares[-2][1] > 0:
        dilution = dilution_shares[-1][1] / dilution_shares[-2][1] - 1.0
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
    dimensions["dilution"] = {
        "score": dilution_score,
        "applicable": dilution_score is not None and dilution_reliable,
    }

    revenue_up_ratio = _up_year_ratio(revenue, limit=5)
    income_up_ratio = _up_year_ratio(net_income, limit=5)
    margins = _matched_ratios(net_income, revenue, limit=5)
    margin_stability = _margin_stability_score(margins)
    if profile == "financial":
        # Net margins are not comparable between banks/insurers and operating
        # companies. Use profitability persistence and income direction instead.
        consistency_inputs = [
            income_positive * 100.0 if income_positive is not None else None,
            income_up_ratio * 100.0 if income_up_ratio is not None else None,
        ]
    else:
        consistency_inputs = [
            income_positive * 100.0 if income_positive is not None else None,
            revenue_up_ratio * 100.0 if revenue_up_ratio is not None else None,
            margin_stability,
        ]
    if profile == "operating_company":
        consistency_inputs.append(fcf_positive * 100.0 if fcf_positive is not None else None)
    consistency_score = _mean_available(consistency_inputs)
    dimensions["consistency"] = {
        "score": consistency_score,
        "applicable": consistency_score is not None,
    }

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

    # Quality and price are separate decisions. A cheap weak company must not
    # receive a high business score merely because its valuation multiple is low.
    weights = (
        {"earnings": 0.30, "returns": 0.25, "dilution": 0.15, "consistency": 0.30}
        if profile in {"financial", "reit"}
        else {
            "earnings": 0.20,
            "cashflow": 0.25,
            "returns": 0.15,
            "leverage": 0.15,
            "dilution": 0.10,
            "consistency": 0.15,
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
    preliminary_status = "ok" if coverage_pct >= 70 else "partial" if coverage_pct > 35 else "limited"
    if preliminary_status == "limited":
        # A precise-looking score from one isolated fact is less useful than an
        # explicit data limitation.
        score = None

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
    if dilution_reliable and dilution is not None and dilution > 0.25:
        severe_reasons.append("Aktienverwaesserung ueber 25%")
    if (
        profile == "operating_company"
        and latest_equity is not None
        and latest_equity <= 0
        and latest_debt is not None
        and (latest_cash is None or latest_debt > latest_cash)
        and (latest_cfo is None or latest_cfo <= 0)
    ):
        severe_reasons.append("Negatives Eigenkapital, Nettoschulden und schwacher Cashflow")

    strengths: List[str] = []
    risks: List[str] = []
    if income_positive is not None and income_positive >= 2 / 3:
        strengths.append("Gewinne mehrheitlich positiv")
    elif income_positive is not None and income_positive <= 1 / 3:
        risks.append("Gewinnhistorie schwach")
    if profile == "operating_company" and fcf_positive is not None and fcf_positive >= 2 / 3:
        strengths.append("Freier Cashflow mehrheitlich positiv")
    elif profile == "operating_company" and fcf_positive is not None and fcf_positive <= 1 / 3:
        risks.append("Freier Cashflow schwach")
    if dilution_reliable and dilution is not None and dilution > 0.10:
        risks.append(f"Aktienverwaesserung {dilution * 100:.0f}%")
    if leverage_score is not None and leverage_score < 35:
        risks.append("Verschuldung im Verhaeltnis zum Cashflow hoch")
    if consistency_score is not None and consistency_score >= 75:
        strengths.append("Mehrjaehrige Ertragsqualitaet stabil")
    elif consistency_score is not None and consistency_score < 40:
        risks.append("Ertragsentwicklung unbestaendig")

    quality_source_series: List[Sequence[Tuple[str, float]]] = [revenue, net_income]
    if cashflow_applicable:
        quality_source_series.extend((operating_cash, capex))
    if roe_score is not None:
        quality_source_series.append(equity)
    if leverage_score is not None:
        quality_source_series.append(debt)
        if cash:
            quality_source_series.append(cash)
    if dimensions["dilution"]["applicable"]:
        quality_source_series.append(dilution_shares)
    latest_periods = [series[-1][0] for series in quality_source_series if series]
    # Freshness is bounded by the oldest component actually used. A recent
    # balance-sheet fact must not make older earnings/cash-flow history look new.
    as_of = min(latest_periods) if latest_periods else None
    status = preliminary_status
    specialist_data_required = profile in {"financial", "reit"}
    if profile == "reit":
        # GAAP net income and book value do not replace FFO/AFFO, lease expiry
        # and debt-maturity analysis. Do not publish a pseudo-precise REIT
        # quality/valuation verdict from generic Company Facts.
        score = None
        valuation_score = None
        status = "limited"
        risks.append("REIT erfordert FFO/AFFO und Laufzeitdaten")
    elif profile == "financial" and status == "ok":
        # Bank/insurer quality additionally needs capital, credit/underwriting
        # and funding data. Keep the numeric snapshot visible but never label it
        # as fully covered for long-term holding decisions.
        status = "partial"
        risks.append("Finanzwert erfordert Kapital- und Kreditqualitaetsdaten")
    data_age_days, freshness_status = _data_freshness(as_of)
    value_trap_risk = bool(
        score is not None
        and score < 50
        and valuation_score is not None
        and valuation_score >= 70
    )
    if value_trap_risk:
        risks.append("Niedrige Bewertung bei schwacher Qualitaet: Value-Trap-Risiko")
    holding_fit, holding_fit_label, position_size_cap = _holding_fit(
        score,
        valuation_score,
        status=status,
        freshness_status=freshness_status,
        severe_risk=bool(severe_reasons),
    )

    def percentage(value: Optional[float]) -> Optional[float]:
        return round(value * 100.0, 2) if value is not None else None

    return {
        "status": status,
        "score": score,
        "label": _label_for_score(score),
        "valuation_score": round(valuation_score) if valuation_score is not None else None,
        "valuation_label": _valuation_label(valuation_score),
        "holding_fit": holding_fit,
        "holding_fit_label": holding_fit_label,
        # Informational downside cap only. It is deliberately not wired into
        # broker sizing until forward results calibrate it.
        "position_size_cap": position_size_cap,
        "coverage_pct": coverage_pct,
        "data_age_days": data_age_days,
        "freshness_status": freshness_status,
        "severe_risk": bool(severe_reasons),
        "severe_reasons": severe_reasons,
        "value_trap_risk": value_trap_risk,
        "strengths": strengths[:3],
        "risks": list(dict.fromkeys(risks + severe_reasons))[:4],
        "profile": profile,
        "as_of": as_of,
        "market_cap": effective_market_cap,
        "methodology": "live_sec_filing_quality_not_point_in_time_backtest",
        "cashflow_methodology": "cfo_minus_total_capex_proxy_not_owner_earnings",
        "specialist_data_required": specialist_data_required,
        "qualitative_limits": [
            "Wettbewerbsvorteil (Moat) nicht automatisch bewertet",
            "Management und Kapitalallokation nicht automatisch bewertet",
        ],
        "quality_excludes_valuation": True,
        "backtest_eligible": False,
        "model_version": BUSINESS_QUALITY_MODEL_VERSION,
        "metrics": {
            "revenue_cagr_pct": percentage(annualized_growth),
            "net_margin_pct": percentage(margin),
            "roe_pct": percentage(roe),
            "earnings_yield_pct": percentage(earnings_yield),
            "fcf_yield_pct": percentage(fcf_yield),
            "share_dilution_pct": percentage(dilution),
            "share_dilution_basis": dilution_basis,
            "share_dilution_reliable": dilution_reliable,
            "latest_debt": latest_debt,
            "latest_cash": latest_cash,
            "net_debt": net_debt,
            "leverage_basis": leverage_basis,
            "debt_to_cfo": round(debt_to_cfo, 2) if debt_to_cfo is not None else None,
            "net_debt_to_cfo": (
                round(debt_to_cfo, 2)
                if debt_to_cfo is not None and leverage_basis == "net_debt"
                else None
            ),
        },
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
        cached_error = _error_cache.get(symbol)
        if cached_error and now - cached_error[0] < _ERROR_TTL_SECONDS:
            return dict(cached_error[1])
        cached_facts = _company_facts_cache.get(symbol)
    try:
        if cached_facts and now - cached_facts[0] < _SUCCESS_TTL_SECONDS:
            _, facts, ticker_info = cached_facts
        else:
            ticker_info = _load_sec_ticker_map().get(symbol)
            if not ticker_info:
                raise LookupError("Ticker nicht in offizieller Unternehmensliste")
            cik = str(ticker_info["cik"]).zfill(10)
            facts = _sec_get_json(f"{_SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
            with _cache_lock:
                _company_facts_cache[symbol] = (now, dict(facts), dict(ticker_info))
        cik = str(ticker_info["cik"]).zfill(10)
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
            _error_cache[symbol] = (now, dict(result))
    return result
