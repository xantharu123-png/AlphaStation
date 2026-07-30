"""Market regime and headline-risk layer for scanner guardrails.

This module deliberately does not produce buy/sell signals. It describes the
market weather so scanners can adjust aggressiveness, sizing and chase rules.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo


HEADLINE_CATEGORIES = {
    "politics": {
        "weight": 16,
        "keywords": ("trump", "white house", "president", "election", "congress", "senate", "tariff"),
    },
    "tariffs_trade": {
        "weight": 22,
        "keywords": ("tariff", "trade war", "sanction", "export control", "import ban", "china trade"),
    },
    "fed_rates": {
        "weight": 18,
        "keywords": ("federal reserve", "fed", "fomc", "rate decision", "powell", "interest rate"),
    },
    "war_geopolitics": {
        "weight": 24,
        "keywords": ("war", "attack", "missile", "iran", "russia", "ukraine", "taiwan", "middle east"),
    },
    "oil_energy": {
        "weight": 14,
        "keywords": ("oil", "crude", "opec", "energy shock", "shipping lane", "strait"),
    },
    "banking_credit": {
        "weight": 20,
        "keywords": ("bank failure", "credit crisis", "liquidity crisis", "default", "contagion"),
    },
    "regulation_ai_crypto": {
        "weight": 14,
        "keywords": (
            "crypto regulation", "ai regulation", "antitrust probe", "antitrust crackdown",
            "sec sues", "sec charges", "sec investigation", "crypto ban", "ai ban",
        ),
    },
    "shutdown_debt": {
        "weight": 18,
        "keywords": ("government shutdown", "debt ceiling", "budget standoff", "treasury funding"),
    },
}

CALMING_KEYWORDS = (
    "deal reached",
    "ceasefire",
    "tariff pause",
    "rate cut",
    "dovish",
    "stimulus",
    "rescues",
)

NOISE_HEADLINE_PATTERNS = (
    "class action", "law firm", "legal inquiry", "shareholder alert", "lost money",
    "contact ", "deadline", "sec filing", "recent sec filing", "13f", "stake",
    "reports since-inception performance", "announces first national bank",
)

MACRO_OVERRIDE_KEYWORDS = (
    "iran", "hormuz", "strait", "oil", "crude", "opec", "federal reserve",
    "fomc", "powell", "tariff", "trade war", "missile", "war", "attack",
    "bank failure", "credit crisis", "debt ceiling", "government shutdown",
)

SOURCE_WEIGHT_HINTS = (
    ("reuters", 1.15),
    ("associated press", 1.10),
    ("ap", 1.10),
    ("bloomberg", 1.05),
    ("wall street journal", 1.05),
    ("benzinga", 0.90),
    ("investing.com", 0.55),
    ("the motley fool", 0.65),
    ("globenewswire", 0.45),
    ("pr newswire", 0.45),
)

SINGLE_STOCK_ANALYSIS_PATTERNS = (
    "for investors", "1 ai chip stock", "why ", "shares slumped", "stock is",
    "stock falls", "plummeting today", "reports earnings", "here's what's next",
)

LOW_RISK_ANALYSIS_PATTERNS = (
    "not this year", "leads the charge", "room to rise", "buying the nasdaq",
    "rallies", "sets sail",
)

OIL_RISK_CONTEXT = (
    "jumps", "jumped", "spikes", "spiked", "surges", "surged", "shock",
    "disrupted", "strike", "strikes", "attack", "tensions", "crisis",
    "above $", "shut", "closed", "blocked", "pressuring",
)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _recency_weight(published: Optional[datetime], now_utc: datetime) -> float:
    if not published:
        return 0.8
    age_hours = max(0.0, (now_utc - published).total_seconds() / 3600)
    if age_hours <= 3:
        return 1.25
    if age_hours <= 12:
        return 1.0
    if age_hours <= 36:
        return 0.7
    return 0.4


def _risk_level(score: float) -> str:
    if score >= 80:
        return "EXTREME"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


def _keyword_matches(text: str, keyword: str) -> bool:
    """Match a keyword or phrase on token boundaries, not inside other words."""
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _headline_source(item: Dict[str, Any]) -> str:
    publisher = item.get("publisher")
    if isinstance(publisher, dict):
        return str(publisher.get("name") or "")
    return str(item.get("source") or item.get("publisher") or "")


def _source_weight(source: str) -> float:
    src = str(source or "").lower()
    for needle, weight in SOURCE_WEIGHT_HINTS:
        if needle in src:
            return weight
    return 0.75


def _is_noise_headline(title: str, description: str, source: str) -> bool:
    text = f"{title} {description}".lower()
    title_text = str(title or "").lower()
    source_text = str(source or "").lower()
    broad_market_title = any(term in title_text for term in ("stock market", "wall street", "s&p", "nasdaq", "dow", "oil"))
    if "the motley fool" in source_text and not broad_market_title and any(pattern in title_text for pattern in SINGLE_STOCK_ANALYSIS_PATTERNS):
        return True
    if any(pattern in text for pattern in LOW_RISK_ANALYSIS_PATTERNS) and not any(term in text for term in ("drops", "falls", "selloff", "crash")):
        return True
    if any(_keyword_matches(text, keyword) for keyword in MACRO_OVERRIDE_KEYWORDS):
        return False
    noisy_source = any(src in source_text for src in ("globenewswire", "pr newswire"))
    return noisy_source or any(pattern in text for pattern in NOISE_HEADLINE_PATTERNS)


def _category_matches(category: str, text: str, title_text: str, keywords: Iterable[str]) -> bool:
    if category == "fed_rates":
        if any(_keyword_matches(title_text, keyword) for keyword in ("federal reserve", "fed", "fomc", "powell")):
            return True
        return any(_keyword_matches(text, keyword) for keyword in ("rate decision", "interest rate"))
    if category == "oil_energy":
        if not any(_keyword_matches(text, keyword) for keyword in keywords):
            return False
        return any(pattern in text for pattern in OIL_RISK_CONTEXT) or "hormuz" in text or "shipping lane" in text
    return any(_keyword_matches(text, keyword) for keyword in keywords)


def _story_key(text: str, categories: Iterable[str]) -> str:
    category_set = set(categories)
    if (
        ("war_geopolitics" in category_set or "oil_energy" in category_set)
        and any(_keyword_matches(text, keyword) for keyword in ("iran", "hormuz", "uae", "strait"))
    ):
        return "iran_hormuz_oil"
    if "tariffs_trade" in category_set and any(_keyword_matches(text, keyword) for keyword in ("china", "tariff", "trade war")):
        return "tariffs_trade"
    if "fed_rates" in category_set:
        return "fed_rates"
    if "banking_credit" in category_set:
        return "banking_credit"
    if "shutdown_debt" in category_set:
        return "shutdown_debt"
    return "_".join(sorted(category_set)) or "uncategorized"


def _story_cap(story_key: str, categories: Iterable[str]) -> float:
    category_set = set(categories)
    if story_key == "iran_hormuz_oil":
        return 58.0
    if "banking_credit" in category_set or "shutdown_debt" in category_set:
        return 60.0
    if "tariffs_trade" in category_set:
        return 54.0
    return 42.0


def missing_headline_risk(error: str = "Headline data unavailable") -> Dict[str, Any]:
    """Defensive unknown state when headline data cannot be trusted."""
    now_utc = datetime.now(timezone.utc)
    return {
        "score": 35,
        "level": "UNKNOWN",
        "data_status": "error",
        "error": error,
        "headline_count": 0,
        "matched_count": 0,
        "categories": {},
        "top_headlines": [],
        "calming_score": 0,
        "source": "Polygon news headline keyword risk",
        "timestamp": now_utc.isoformat(),
    }


def missing_event_risk(error: str = "Economic calendar unavailable") -> Dict[str, Any]:
    """Defensive unknown state when scheduled event data cannot be trusted."""
    now_utc = datetime.now(timezone.utc)
    return {
        "score": 30,
        "level": "UNKNOWN",
        "data_status": "error",
        "error": error,
        "upcoming_events": [],
        "source": "Alpha Station economic calendar",
        "timestamp": now_utc.isoformat(),
    }


def analyze_headlines(headlines: Iterable[Dict[str, Any]], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Score market-moving political/macro headline risk from recent headlines."""
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    category_scores: Dict[str, float] = {key: 0.0 for key in HEADLINE_CATEGORIES}
    matched: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    story_totals: Dict[str, float] = {}
    story_category_scores: Dict[str, Dict[str, float]] = {}
    story_categories: Dict[str, set[str]] = {}
    headline_count = 0
    calming_score = 0.0

    for item in headlines or []:
        if not isinstance(item, dict):
            continue
        headline_count += 1
        title = str(item.get("title") or item.get("headline") or "")
        description = str(item.get("description") or item.get("summary") or "")
        text = f"{title} {description}".lower()
        title_text = title.lower()
        source = _headline_source(item)
        source_weight = _source_weight(source)
        published = _parse_dt(item.get("published_utc") or item.get("published_at") or item.get("timestamp"))
        recency = _recency_weight(published, now_utc)
        category_points: Dict[str, float] = {}

        if _is_noise_headline(title, description, source):
            ignored.append({
                "title": title[:160],
                "source": source,
                "reason": "company_pr_or_legal_noise",
            })
            continue

        if any(_keyword_matches(text, keyword) for keyword in CALMING_KEYWORDS):
            calming_score += 8 * recency * source_weight

        for category, cfg in HEADLINE_CATEGORIES.items():
            if _category_matches(category, text, title_text, cfg["keywords"]):
                category_points[category] = cfg["weight"] * recency * source_weight

        matched_categories = list(category_points.keys())
        if matched_categories:
            key = _story_key(text, matched_categories)
            for category, points in category_points.items():
                story_totals[key] = story_totals.get(key, 0.0) + points
                story_category_scores.setdefault(key, {})
                story_category_scores[key][category] = story_category_scores[key].get(category, 0.0) + points
                story_categories.setdefault(key, set()).add(category)

        if matched_categories:
            key = _story_key(text, matched_categories)
            matched.append({
                "title": title[:180],
                "published_utc": published.isoformat() if published else item.get("published_utc"),
                "categories": matched_categories,
                "source": source,
                "story_key": key,
                "source_weight": round(source_weight, 2),
                "url": item.get("article_url") or item.get("url"),
            })

    story_scores = []
    for key, total in story_totals.items():
        categories = story_categories.get(key, set())
        cap = _story_cap(key, categories)
        adjusted = min(cap, total)
        scale = adjusted / total if total > 0 else 0
        for category, value in story_category_scores.get(key, {}).items():
            category_scores[category] += value * scale
        story_scores.append({
            "story_key": key,
            "score": int(round(adjusted)),
            "raw_score": int(round(total)),
            "cap": int(round(cap)),
            "categories": sorted(categories),
        })

    raw_score = sum(item["score"] for item in story_scores) - calming_score
    score = int(round(max(0.0, min(100.0, raw_score))))
    active_categories = {
        key: int(round(min(100.0, value)))
        for key, value in category_scores.items()
        if value >= 5
    }

    return {
        "score": score,
        "level": _risk_level(score),
        "headline_count": headline_count,
        "matched_count": len(matched),
        "story_count": len(story_scores),
        "data_status": "ok",
        "categories": active_categories,
        "top_headlines": matched[:8],
        "story_scores": sorted(story_scores, key=lambda item: item["score"], reverse=True)[:6],
        "ignored_count": len(ignored),
        "ignored_headlines": ignored[:5],
        "calming_score": int(round(calming_score)),
        "source": "Polygon news headline story risk",
        "scoring_note": "Story-deduped and PR/legal/company-filing noise filtered.",
        "timestamp": now_utc.isoformat(),
    }


def build_event_risk(events: Iterable[Dict[str, Any]], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Score near-term scheduled event risk from the economic calendar."""
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    upcoming: List[Dict[str, Any]] = []
    score = 0

    for event in events or []:
        if not isinstance(event, dict):
            continue
        dt = _parse_dt(event.get("datetime_et") or event.get("datetime_local"))
        date_only = False
        if not dt:
            date_text = event.get("date")
            if not date_text:
                continue
            try:
                # A calendar row without an exact time represents an ET event
                # day, not midnight UTC. Keep it active for the whole US day so
                # it cannot disappear before the cash session starts.
                event_day = datetime.fromisoformat(str(date_text)[:10]).date()
                eastern = ZoneInfo("America/New_York")
                now_et = now_utc.astimezone(eastern)
                day_start_et = datetime.combine(event_day, datetime.min.time(), tzinfo=eastern)
                day_end_et = datetime.combine(event_day, datetime.max.time(), tzinfo=eastern)
                date_only = True
                if now_et < day_start_et:
                    hours = (day_start_et - now_et).total_seconds() / 3600
                elif now_et <= day_end_et:
                    hours = 0.0
                else:
                    hours = -(now_et - day_end_et).total_seconds() / 3600
                dt = day_start_et.astimezone(timezone.utc)
            except Exception:
                continue
        else:
            hours = (dt - now_utc).total_seconds() / 3600
        if hours < -2 or hours > 48:
            continue
        importance = str(event.get("importance", "")).lower()
        impact = str(event.get("impact", "")).lower()
        base = 34 if importance == "high" or "sehr" in impact else 18
        if -0.5 <= hours <= 2:
            event_score = base + 18
        elif 0 <= hours <= 8:
            event_score = base + 10
        elif 0 <= hours <= 24:
            event_score = base
        else:
            event_score = max(10, base - 8)
        score = max(score, event_score)
        upcoming.append({
            "event": event.get("event"),
            "date": event.get("date"),
            "time_et": event.get("time_et"),
            "importance": event.get("importance"),
            "hours_until": round(hours, 1),
            "date_only": date_only,
            "source": event.get("source"),
        })

    score = int(round(max(0, min(100, score))))
    return {
        "score": score,
        "level": _risk_level(score),
        "data_status": "ok",
        "upcoming_events": sorted(upcoming, key=lambda e: e.get("hours_until", 999))[:6],
        "timestamp": now_utc.isoformat(),
    }


def build_market_context(
    crash_data: Optional[Dict[str, Any]] = None,
    headline_risk: Optional[Dict[str, Any]] = None,
    event_risk: Optional[Dict[str, Any]] = None,
    rates_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Combine crash/fear, headline, event risk and rates annotation into one context.

    rates_data (Zins-Block aus modules/treasury_rates) ist reine Annotation:
    Er fliesst als context["rates"] durch, aendert aber weder Score noch
    Regime (Mess-First, 2026-07-30).
    """
    crash_data = crash_data if isinstance(crash_data, dict) else {}
    headline_risk = (
        dict(headline_risk)
        if isinstance(headline_risk, dict) and headline_risk
        else missing_headline_risk("Headline data missing")
    )
    event_risk = (
        dict(event_risk)
        if isinstance(event_risk, dict) and event_risk
        else missing_event_risk("Economic calendar missing")
    )

    raw_fear_score = crash_data.get("fear_score")
    fear_score_available = raw_fear_score is not None and str(raw_fear_score).strip() != ""
    fear_score = _to_float(raw_fear_score, 50)
    raw_vix = (crash_data.get("vix") or {}).get("price")
    vix_available = raw_vix is not None and str(raw_vix).strip() != ""
    vix = _to_float(raw_vix, 0)
    breadth = crash_data.get("breadth") or {}
    breadth_available = (
        breadth.get("ad_ratio") is not None
        and breadth.get("advancing_pct") is not None
    )
    ad_ratio = _to_float(breadth.get("ad_ratio"), 1.0)
    advancing_pct = _to_float(breadth.get("advancing_pct"), 50)

    # fear_score already includes VIX level/trend, breadth and index momentum.
    # Applying VIX and breadth again made the final regime needlessly nervous.
    market_risk = max(0.0, min(100.0, 100.0 - fear_score))
    risk_basis = "fear_score"
    if not fear_score_available:
        # Fall back to direct market inputs only when the aggregate is missing.
        proxies = []
        if vix_available and vix > 0:
            proxies.append(max(0.0, min(100.0, (vix - 12.0) / 23.0 * 100.0)))
        if breadth_available and ad_ratio > 0:
            breadth_risk = max(0.0, min(100.0, (1.5 - ad_ratio) / 1.2 * 100.0))
            advancing_risk = max(0.0, min(100.0, (60.0 - advancing_pct) / 40.0 * 100.0))
            proxies.append((breadth_risk + advancing_risk) / 2.0)
        market_risk = sum(proxies) / len(proxies) if proxies else 55.0
        risk_basis = "direct_market_proxy" if proxies else "market_data_unknown"
    market_risk = max(0.0, min(100.0, market_risk))
    if fear_score_available:
        market_status = "ok"
    elif vix_available or breadth_available:
        market_status = "partial"
    else:
        market_status = "missing"

    headline_status = str(headline_risk.get("data_status", "ok")).lower()
    headline_score = _to_float(headline_risk.get("score"), 0)
    if headline_status not in {"ok", "fresh"}:
        headline_score = max(headline_score, 35)
    headline_confirmers = []
    if market_risk >= 60:
        headline_confirmers.append("market_risk")
    if vix >= 20:
        headline_confirmers.append("vix")
    if ad_ratio < 0.7 or advancing_pct < 40:
        headline_confirmers.append("breadth")
    if headline_status in {"ok", "fresh"} and headline_score >= 85 and not headline_confirmers:
        headline_risk = dict(headline_risk)
        headline_risk["score_adjusted_from"] = int(round(headline_score))
        headline_risk["score"] = 75
        headline_risk["level"] = _risk_level(75)
        headline_risk["confirmation"] = "capped_without_market_confirmation"
        headline_score = 75
    elif headline_status in {"ok", "fresh"}:
        headline_risk = dict(headline_risk)
        headline_risk["confirmation"] = "confirmed_by_" + ",".join(headline_confirmers) if headline_confirmers else "headline_only"
    event_status = str(event_risk.get("data_status", "ok")).lower()
    event_score = _to_float(event_risk.get("score"), 0)
    if event_status not in {"ok", "fresh"}:
        event_score = max(event_score, 30)
    overall_risk = int(round(market_risk * 0.50 + headline_score * 0.30 + event_score * 0.20))

    if overall_risk >= 75:
        regime = "PANIC"
        trade_mode = "PROTECT_CAPITAL"
        size_multiplier = 0.25
    elif overall_risk >= 65:
        regime = "RISK_OFF"
        trade_mode = "DEFENSIVE"
        size_multiplier = 0.50
    elif overall_risk >= 55:
        regime = "RISK_OFF_LIGHT"
        trade_mode = "CAUTIOUS"
        size_multiplier = 0.65
    elif overall_risk >= 32:
        regime = "NEUTRAL"
        trade_mode = "SELECTIVE"
        size_multiplier = 0.75
    else:
        regime = "RISK_ON"
        trade_mode = "AGGRESSIVE_SELECTIVE"
        size_multiplier = 1.00

    if (
        market_status != "ok"
        or headline_status not in {"ok", "fresh"}
        or event_status not in {"ok", "fresh"}
    ) and regime == "RISK_ON":
        regime = "NEUTRAL"
        trade_mode = "SELECTIVE"
        size_multiplier = 0.75

    warnings = []
    if market_status == "missing":
        warnings.append("Marktdaten fehlen: defensiver Unknown-Modus statt erfundenem Neutralwert.")
    elif market_status == "partial":
        warnings.append("Marktdaten nur teilweise verfuegbar: Positionsgroesse defensiv behandeln.")
    if headline_status not in {"ok", "fresh"}:
        warnings.append("Headline-Daten unbekannt/fehlerhaft: defensiver Modus statt blind Risk-On.")
    if event_status not in {"ok", "fresh"}:
        warnings.append("Kalender-/Event-Daten unbekannt: defensiver Modus statt blind Risk-On.")
    if headline_score >= 50:
        warnings.append("Headline-/Politikrisiko hoch: keine FOMO-Market-Entries.")
    if event_score >= 50:
        warnings.append("High-Impact-Event nah: Positionsgroesse reduzieren und News-Spikes meiden.")
    if regime == "RISK_OFF_LIGHT":
        warnings.append("Risk-Off-Light: selektiv bleiben, Longs bevorzugt nur mit Retest/VWAP-Hold.")
    if regime in {"RISK_OFF", "PANIC"}:
        warnings.append("Risk-Off-Regime: Long-Breakouts nur mit Retest/VWAP-Hold, Shorts bevorzugt beobachten.")

    if isinstance(rates_data, dict) and rates_data.get("status") == "ok":
        rates_block = rates_data
    else:
        rates_block = {
            "status": "missing",
            "reason": (rates_data.get("reason") if isinstance(rates_data, dict) else None)
            or "Zinsdaten nicht abgefragt (nur im Scheduler-Lauf verfuegbar)",
            "regime": None,
        }

    return {
        "status": "success",
        "regime": regime,
        "trade_mode": trade_mode,
        "overall_risk_score": overall_risk,
        "size_multiplier": size_multiplier,
        "long_bias": "normal" if regime == "RISK_ON" else ("retest_only" if regime in {"NEUTRAL", "RISK_OFF_LIGHT"} else "defensive"),
        "short_bias": "normal" if regime in {"NEUTRAL", "RISK_ON"} else "favored_but_no_chase",
        "market_risk": {
            "score": int(round(market_risk)),
            "basis": risk_basis,
            "data_status": market_status,
            "fear_score": int(round(fear_score)) if fear_score_available else None,
            "vix": vix or None,
            "ad_ratio": ad_ratio if breadth_available else None,
            "advancing_pct": advancing_pct if breadth_available else None,
        },
        "headline_risk": headline_risk,
        "event_risk": event_risk,
        "rates": rates_block,
        "warnings": warnings,
        "summary": {
            "regime": regime,
            "trade_mode": trade_mode,
            "overall_risk_score": overall_risk,
            "size_multiplier": size_multiplier,
            "headline_level": headline_risk.get("level", "LOW"),
            "headline_status": headline_risk.get("data_status", "ok"),
            "event_level": event_risk.get("level", "LOW"),
            "event_status": event_risk.get("data_status", "ok"),
            "market_status": market_status,
            "fear_score": int(round(fear_score)) if fear_score_available else None,
            "vix": vix or None,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
