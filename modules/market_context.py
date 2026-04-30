"""Market regime and headline-risk layer for scanner guardrails.

This module deliberately does not produce buy/sell signals. It describes the
market weather so scanners can adjust aggressiveness, sizing and chase rules.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


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
        "keywords": ("sec", "crypto regulation", "ai regulation", "antitrust", "lawsuit", "ban"),
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
    if score >= 75:
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


def analyze_headlines(headlines: Iterable[Dict[str, Any]], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Score market-moving political/macro headline risk from recent headlines."""
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    category_scores: Dict[str, float] = {key: 0.0 for key in HEADLINE_CATEGORIES}
    matched: List[Dict[str, Any]] = []
    headline_count = 0
    calming_score = 0.0

    for item in headlines or []:
        if not isinstance(item, dict):
            continue
        headline_count += 1
        title = str(item.get("title") or item.get("headline") or "")
        description = str(item.get("description") or item.get("summary") or "")
        text = f"{title} {description}".lower()
        published = _parse_dt(item.get("published_utc") or item.get("published_at") or item.get("timestamp"))
        recency = _recency_weight(published, now_utc)
        matched_categories = []

        if any(_keyword_matches(text, keyword) for keyword in CALMING_KEYWORDS):
            calming_score += 8 * recency

        for category, cfg in HEADLINE_CATEGORIES.items():
            if any(_keyword_matches(text, keyword) for keyword in cfg["keywords"]):
                points = cfg["weight"] * recency
                category_scores[category] += points
                matched_categories.append(category)

        if matched_categories:
            matched.append({
                "title": title[:180],
                "published_utc": published.isoformat() if published else item.get("published_utc"),
                "categories": matched_categories,
                "source": item.get("publisher", {}).get("name") if isinstance(item.get("publisher"), dict) else item.get("source"),
                "url": item.get("article_url") or item.get("url"),
            })

    raw_score = sum(min(35.0, score) for score in category_scores.values()) - calming_score
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
        "data_status": "ok",
        "categories": active_categories,
        "top_headlines": matched[:8],
        "calming_score": int(round(calming_score)),
        "source": "Polygon news headline keyword risk",
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
        if not dt:
            date_text = event.get("date")
            if not date_text:
                continue
            try:
                dt = datetime.fromisoformat(str(date_text)).replace(tzinfo=timezone.utc)
            except Exception:
                continue
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
) -> Dict[str, Any]:
    """Combine crash/fear, headline and event risk into one trading context."""
    crash_data = crash_data or {}
    headline_risk = headline_risk or {"score": 0, "level": "LOW", "top_headlines": []}
    event_risk = event_risk or {"score": 0, "level": "LOW", "upcoming_events": []}

    fear_score = _to_float(crash_data.get("fear_score"), 50)
    vix = _to_float((crash_data.get("vix") or {}).get("price"), 0)
    breadth = crash_data.get("breadth") or {}
    ad_ratio = _to_float(breadth.get("ad_ratio"), 1.0)
    advancing_pct = _to_float(breadth.get("advancing_pct"), 50)

    market_risk = max(0.0, min(100.0, 100.0 - fear_score))
    if vix >= 35:
        market_risk += 30
    elif vix >= 30:
        market_risk += 24
    elif vix >= 25:
        market_risk += 18
    elif vix >= 20:
        market_risk += 10
    elif 0 < vix <= 14:
        market_risk -= 8
    if ad_ratio < 0.5 or advancing_pct < 30:
        market_risk += 16
    elif ad_ratio < 0.7 or advancing_pct < 40:
        market_risk += 10
    elif ad_ratio > 1.5 and advancing_pct > 55:
        market_risk -= 8
    market_risk = max(0.0, min(100.0, market_risk))

    headline_status = str(headline_risk.get("data_status", "ok")).lower()
    headline_score = _to_float(headline_risk.get("score"), 0)
    if headline_status not in {"ok", "fresh"}:
        headline_score = max(headline_score, 35)
    event_score = _to_float(event_risk.get("score"), 0)
    overall_risk = int(round(market_risk * 0.50 + headline_score * 0.30 + event_score * 0.20))

    if overall_risk >= 75:
        regime = "PANIC"
        trade_mode = "PROTECT_CAPITAL"
        size_multiplier = 0.25
    elif overall_risk >= 55:
        regime = "RISK_OFF"
        trade_mode = "DEFENSIVE"
        size_multiplier = 0.50
    elif overall_risk >= 32:
        regime = "NEUTRAL"
        trade_mode = "SELECTIVE"
        size_multiplier = 0.75
    else:
        regime = "RISK_ON"
        trade_mode = "AGGRESSIVE_SELECTIVE"
        size_multiplier = 1.00

    if headline_status not in {"ok", "fresh"} and regime == "RISK_ON":
        regime = "NEUTRAL"
        trade_mode = "SELECTIVE"
        size_multiplier = 0.75

    warnings = []
    if headline_status not in {"ok", "fresh"}:
        warnings.append("Headline-Daten unbekannt/fehlerhaft: defensiver Modus statt blind Risk-On.")
    if headline_score >= 50:
        warnings.append("Headline-/Politikrisiko hoch: keine FOMO-Market-Entries.")
    if event_score >= 50:
        warnings.append("High-Impact-Event nah: Positionsgroesse reduzieren und News-Spikes meiden.")
    if regime in {"RISK_OFF", "PANIC"}:
        warnings.append("Risk-Off-Regime: Long-Breakouts nur mit Retest/VWAP-Hold, Shorts bevorzugt beobachten.")

    return {
        "status": "success",
        "regime": regime,
        "trade_mode": trade_mode,
        "overall_risk_score": overall_risk,
        "size_multiplier": size_multiplier,
        "long_bias": "normal" if regime == "RISK_ON" else ("retest_only" if regime == "NEUTRAL" else "defensive"),
        "short_bias": "normal" if regime in {"NEUTRAL", "RISK_ON"} else "favored_but_no_chase",
        "market_risk": {
            "score": int(round(market_risk)),
            "fear_score": int(round(fear_score)),
            "vix": vix or None,
            "ad_ratio": ad_ratio,
            "advancing_pct": advancing_pct,
        },
        "headline_risk": headline_risk,
        "event_risk": event_risk,
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
            "fear_score": int(round(fear_score)),
            "vix": vix or None,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
