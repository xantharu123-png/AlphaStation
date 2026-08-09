"""Regime-Filter (AUDIT 2026-08-01, F-14): Markt-Gate + Eigen-Performance-Breaker.

Anlass (bewiesen, Audit Kap. 2b/2c): Das System feuerte 8–12 Long-Signale/Tag,
waehrend die eigene 7-Tage-Bilanz von 43 % auf 0 % abglitt (16 % im 14d-
Fenster, ØR < -1R). Ein Senior-System drosselt Long-Momentum in so einem Tape.

Zwei Layer, bewusst getrennt:

Layer 1 MARKET (exogen): mappt das bestehende modules.market_context-Regime
(PANIC/RISK_OFF/RISK_OFF_LIGHT/NEUTRAL/RISK_ON) auf GREEN/YELLOW/RED.
Fail-open bei unbekanntem/fehlendem Regime — niemals ein erfundenes ROT.

Layer 2 BREAKER (endogen): voll beobachtete 30-Tage-Eigen-Performance je Scanner
aus dem Signal-Tracker (load_performance_summary). Trip: n >= 10 entschieden
UND ØR <= -0.3R UND Win% <= 25 %. Release erfolgt ausschliesslich mit nach
dem Trip entschiedener Trade-/Shadow-Evidenz: n >= 5, ØR > -0.1R und
Win% >= 30 %. Nach 5 Handelstagen wird nur eine manuelle Pruefung faellig;
Zeit allein hebt den Breaker niemals auf. Shadow-Signale werden waehrend ROT
weiter ausgewertet und liefern die Recovery-Evidenz ohne Blindflug.

Wirkung (Design-Entscheid Owner, 01.08.): Degradierung statt Abschaltung.
Der Scanner scannt weiter taeglich; bei ROT geht die Swing-Mail als
watch-Klasse mit Banner raus (statt swing_trade), und die Setups werden als
mail_class='shadow' mit block_reasons getrackt — die Gegenprobe ("haette das
Regime-Blocking gekostet oder gespart?") bleibt damit datenseitig messbar.

Dieses Modul ist rein (keine Netz-/DB-Aufrufe): Kontext, Summary und State
werden vom Aufrufer (api.py) uebergeben; Persistenz nur via load/save_state.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"

LAYER_MARKET = "market"
LAYER_BREAKER = "breaker"

# block_reasons-Tags (landen in der Shadow-Spalte des Trackers)
REASON_MARKET_YELLOW = "market_regime_yellow"
REASON_MARKET_RED = "market_regime_red"
REASON_BREAKER_COOLDOWN = "regime_cooldown"

# Breaker-Schwellen (AUDIT F-14, quantifiziertes Kontrafaktum -20.5R auf
# KW30/31-2026-Daten: Trip haette am 27.07. ausgeloest, 45 Signale, 0 % Wins)
BREAKER_MIN_DECIDED = 10
BREAKER_TRIP_AVG_R = -0.3
BREAKER_TRIP_WIN_PCT = 25.0
BREAKER_RELEASE_AVG_R = -0.1
BREAKER_RELEASE_TRADING_DAYS = 5
BREAKER_RELEASE_MIN_DECIDED = 5
BREAKER_RELEASE_MIN_WIN_PCT = 30.0

# Gelb-Verschaerfung (Layer 1 RISK_OFF_LIGHT)
YELLOW_SCORE_BOOST = 5
YELLOW_MAX_ROWS = 2

# Breaker-COOLDOWN: max. 1 Watch-Mail je Scanner und 20h (Spam-Schutz)
BREAKER_WATCH_CAP_SECONDS = 20 * 3600

DEFAULT_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "data_cache" / "regime_state.json"
)


# ── Layer 1: Markt-Regime ────────────────────────────────────────────────────

def market_layer_state(context: dict | None) -> dict:
    """Mappt market_context-Regime auf GREEN/YELLOW/RED (fail-open)."""
    regime = str((context or {}).get("regime") or "").strip().upper()
    risk = (context or {}).get("overall_risk_score")
    if regime in ("RISK_OFF", "PANIC"):
        return {"state": RED, "regime": regime, "risk": risk,
                "reason": f"Markt-Regime {regime} (Risiko {risk}/100)"}
    if regime == "RISK_OFF_LIGHT":
        return {"state": YELLOW, "regime": regime, "risk": risk,
                "reason": f"Markt-Regime {regime} (Risiko {risk}/100)"}
    # NEUTRAL / RISK_ON / unbekannt / fehlend => fail-open GREEN
    return {"state": GREEN, "regime": regime or "UNKNOWN", "risk": risk, "reason": ""}


# ── Layer 2: Eigen-Performance-Breaker ───────────────────────────────────────

def breaker_metrics(summary: dict | None, scanner_key: str) -> dict:
    """Belastbare Scanner-Metriken im tatsaechlich empfohlenen Exit-Modell."""
    per = (summary or {}).get("per_scanner") or {}
    row = per.get(scanner_key) or {}
    decided = row.get("managed_be_decided_signals")
    win_pct = row.get("managed_be_win_rate_pct")
    win_pct_upper = row.get("managed_be_win_rate_pct_upper")
    avg_r = row.get("avg_r_managed_50_50_be")
    avg_r_upper = row.get("avg_r_managed_50_50_be_upper")
    if not isinstance(decided, int):
        decided = int(row.get("decided_signals") or 0)
    if not isinstance(win_pct, (int, float)):
        win_pct = float(row.get("win_rate_pct") or 0.0)
    if not isinstance(avg_r, (int, float)):
        avg_r = float(row.get("avg_r") or 0.0)
    if not isinstance(win_pct_upper, (int, float)):
        fallback = row.get("win_rate_pct_upper")
        win_pct_upper = float(fallback) if isinstance(fallback, (int, float)) else win_pct
    if not isinstance(avg_r_upper, (int, float)):
        fallback = row.get("avg_r_upper")
        avg_r_upper = float(fallback) if isinstance(fallback, (int, float)) else avg_r
    return {
        "decided": int(decided),
        "win_pct": float(win_pct),
        "win_pct_upper": float(win_pct_upper),
        "avg_r": float(avg_r),
        "avg_r_upper": float(avg_r_upper),
        "r_model": "managed_50_50_plus_breakeven",
    }


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def trading_days_between(start_date, end_date) -> int:
    """Werktage (Mo–Fr) im Intervall (start, end] — fuer die Release-Frist."""
    if start_date is None or end_date is None or end_date <= start_date:
        return 0
    days = 0
    cursor = start_date
    while cursor < end_date:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def evaluate_breaker(metrics: dict | None, state_entry: dict | None, now=None, *,
                     recovery_metrics: dict | None = None,
                     min_decided: int = BREAKER_MIN_DECIDED,
                     trip_avg_r: float = BREAKER_TRIP_AVG_R,
                     trip_win_pct: float = BREAKER_TRIP_WIN_PCT,
                     release_avg_r: float = BREAKER_RELEASE_AVG_R,
                     release_trading_days: int = BREAKER_RELEASE_TRADING_DAYS,
                     release_min_decided: int = BREAKER_RELEASE_MIN_DECIDED,
                     release_min_win_pct: float = BREAKER_RELEASE_MIN_WIN_PCT) -> dict:
    """Trip-/Release-Logik des Breakers (rein).

    state_entry: {"tripped_at": iso} aus dem State-File (oder None).
    Rueckgabe: {"state": GREEN|RED, "tripped_at": iso|None, "reason": str,
                "metrics": {...}}. RED == COOLDOWN aktiv.
    """
    now = _parse_dt(now) or datetime.now(timezone.utc)
    m = metrics or {}
    decided = int(m.get("decided") or 0)
    win_pct = float(m.get("win_pct") or 0.0)
    win_pct_upper = float(m.get("win_pct_upper", win_pct) or 0.0)
    avg_r = float(m.get("avg_r") or 0.0)
    avg_r_upper = float(m.get("avg_r_upper", avg_r) or 0.0)
    tripped_at = _parse_dt((state_entry or {}).get("tripped_at"))
    recovery = recovery_metrics or {}
    recovery_available = recovery.get("available") is True
    recovery_decided = int(recovery.get("decided") or 0)
    try:
        recovery_avg_r = float(recovery.get("avg_r"))
    except (TypeError, ValueError):
        recovery_avg_r = None
    try:
        recovery_win_pct = float(recovery.get("win_pct"))
    except (TypeError, ValueError):
        recovery_win_pct = None
    recovery_view = {
        "available": recovery_available,
        "decided": recovery_decided,
        "win_pct": recovery_win_pct,
        "avg_r": recovery_avg_r,
        "trade_decided": int(recovery.get("trade_decided") or 0),
        "shadow_decided": int(recovery.get("shadow_decided") or 0),
        "error": recovery.get("error"),
    }
    base = {
        "metrics": {
            "decided": decided,
            "win_pct": win_pct,
            "win_pct_upper": win_pct_upper,
            "avg_r": avg_r,
            "avg_r_upper": avg_r_upper,
        },
        "recovery_metrics": recovery_view,
        "review_due": False,
    }

    if tripped_at is not None:
        recovery_passes = (
            recovery_available
            and recovery_decided >= release_min_decided
            and recovery_avg_r is not None
            and recovery_avg_r > release_avg_r
            and recovery_win_pct is not None
            and recovery_win_pct >= release_min_win_pct
        )
        if recovery_passes:
            return {
                **base,
                "state": GREEN,
                "tripped_at": None,
                "reason": (
                    "breaker_release_recovered "
                    f"(post-trip n={recovery_decided}, "
                    f"AvgR {recovery_avg_r:+.2f} > {release_avg_r:+.2f}, "
                    f"Win {recovery_win_pct:.0f}% >= {release_min_win_pct:.0f}%)"
                ),
            }

        review_due = (
            trading_days_between(tripped_at.date(), now.date())
            >= release_trading_days
        )
        if review_due:
            return {
                **base,
                "state": RED,
                "tripped_at": tripped_at.isoformat(),
                "review_due": True,
                "reason": (
                    f"breaker_review_due seit {tripped_at.date().isoformat()} "
                    f"({release_trading_days} Handelstage; Freigabe erst mit "
                    f"post-trip n>={release_min_decided}, "
                    f"AvgR>{release_avg_r:+.2f}, "
                    f"Win>={release_min_win_pct:.0f}%)"
                ),
            }

        return {
            **base,
            "state": RED,
            "tripped_at": tripped_at.isoformat(),
            "reason": (
                f"breaker_cooldown seit {tripped_at.date().isoformat()} "
                f"(30d reif: n={decided}, AvgR {avg_r:+.2f}, Win {win_pct:.0f}%; "
                f"post-trip n={recovery_decided})"
            ),
        }

    # A daily OHLC candle cannot reveal whether stop or target traded first.
    # Trip only when both conservative and favorable orderings are bad.
    if (
        decided >= min_decided
        and avg_r <= trip_avg_r
        and avg_r_upper <= trip_avg_r
        and win_pct <= trip_win_pct
        and win_pct_upper <= trip_win_pct
    ):
        return {**base, "state": RED, "tripped_at": now.isoformat(),
                "reason": (f"breaker_trip (30d reif: n={decided} >= {min_decided}, "
                           f"ØR {avg_r:+.2f} <= {trip_avg_r:+.2f}, "
                           f"Win-Band {win_pct:.0f}..{win_pct_upper:.0f}% <= "
                           f"{trip_win_pct:.0f}%, R-Upper {avg_r_upper:+.2f})")}
    return {**base, "state": GREEN, "tripped_at": None, "reason": ""}


# ── Persistenz (State-File, defensiv) ────────────────────────────────────────

def load_state(path=None) -> dict:
    try:
        data = json.loads(Path(path or DEFAULT_STATE_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict, path=None) -> bool:
    try:
        target = Path(path or DEFAULT_STATE_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
        return True
    except Exception:
        return False


# ── Banner (UX: Transparenz statt Funkstille) ────────────────────────────────

def _banner_html(color_bg: str, color_border: str, color_text: str, text: str) -> str:
    return (f'<p style="background:{color_bg};border:1px solid {color_border};'
            f'border-radius:8px;padding:10px;color:{color_text};font-size:13px">{text}</p>')


def build_banner(decision: dict) -> str:
    """Sichtbarer Regime-Hinweis oben in der degradierten/verschaerften Mail."""
    if decision.get("state") == RED and decision.get("layer") == LAYER_MARKET:
        return _banner_html(
            "#fef2f2", "#fca5a5", "#991b1b",
            f"🟥 MARKT-REGIME ROT: {decision.get('reason', '')}. Keine neuen Long-Trade-Signale — "
            "diese Mail ist NUR BEOBACHTUNG (kein Einstiegssignal). Die Setups laufen als "
            "Shadow-Messung weiter (Gegenprobe im Wochenreport).")
    if decision.get("state") == RED and decision.get("layer") == LAYER_BREAKER:
        return _banner_html(
            "#fef2f2", "#fca5a5", "#991b1b",
            f"🟥 EIGEN-PERFORMANCE COOLDOWN ({decision.get('scanner', '')}): "
            f"{decision.get('reason', '')}. Trip-Schwellen unterschritten — "
            "diese Mail ist NUR BEOBACHTUNG. Freigabe erst nach mindestens "
            f"{BREAKER_RELEASE_MIN_DECIDED} post-trip Ergebnissen mit AvgR > "
            f"{BREAKER_RELEASE_AVG_R:+.1f}R und Winrate >= "
            f"{BREAKER_RELEASE_MIN_WIN_PCT:.0f} %. "
            f"Nach {BREAKER_RELEASE_TRADING_DAYS} Handelstagen wird nur eine manuelle "
            "Pruefung faellig; Zeit allein gibt nicht frei. "
            "Setups laufen als Shadow-Messung weiter.")
    if decision.get("state") == YELLOW:
        return _banner_html(
            "#fffbeb", "#fcd34d", "#92400e",
            f"🟨 MARKT-REGIME GELB: {decision.get('reason', '')}. Verschärfte Auswahl — "
            f"Score-Schwelle +{decision.get('score_boost', YELLOW_SCORE_BOOST)}, "
            f"max. {decision.get('max_rows', YELLOW_MAX_ROWS)} Setups. Kleinere Positionen prüfen.")
    return ""


# ── Kombinierte Entscheidung ─────────────────────────────────────────────────

def decide_mail_regime(scanner_key: str, *, context: dict | None = None,
                       summary: dict | None = None, state: dict | None = None,
                       recovery_metrics: dict | None = None,
                       now=None, market_gate_enabled: bool = True,
                       breaker_enabled: bool = True) -> dict:
    """Kombiniert Layer 1 + 2 zu einer Mail-Entscheidung fuer einen Scanner.

    Dominanz: ROT-Markt > ROT-Breaker > GELB-Markt > GREEN.
    state: Breaker-State-Dict je Scanner ({"scanner": {"tripped_at": iso}}).
    Rueckgabe enthaelt new_state_entry (zu persistieren, siehe api.py-Helper)
    und reason_tag (Shadow-block_reasons) sowie das Banner via build_banner().
    """
    now = _parse_dt(now) or datetime.now(timezone.utc)

    market = (market_layer_state(context) if market_gate_enabled
              else {"state": GREEN, "regime": "GATE_DISABLED", "risk": None, "reason": ""})

    breaker_eval = {"state": GREEN, "tripped_at": None, "reason": "", "metrics": {}}
    if breaker_enabled:
        breaker_eval = evaluate_breaker(
            breaker_metrics(summary, scanner_key),
            (state or {}).get(scanner_key),
            now,
            recovery_metrics=recovery_metrics,
        )

    new_entry = ({"tripped_at": breaker_eval["tripped_at"]}
                 if breaker_eval.get("tripped_at") else None)

    base = {"scanner": scanner_key, "market": market, "breaker": breaker_eval,
            "new_state_entry": new_entry, "evaluated_at": now.isoformat()}

    if market["state"] == RED:
        decision = {**base, "state": RED, "layer": LAYER_MARKET,
                    "reason": market["reason"], "reason_tag": REASON_MARKET_RED}
    elif breaker_eval["state"] == RED:
        decision = {**base, "state": RED, "layer": LAYER_BREAKER,
                    "reason": breaker_eval["reason"],
                    "reason_tag": REASON_BREAKER_COOLDOWN,
                    "watch_cap_seconds": BREAKER_WATCH_CAP_SECONDS}
    elif market["state"] == YELLOW:
        decision = {**base, "state": YELLOW, "layer": LAYER_MARKET,
                    "reason": market["reason"], "reason_tag": REASON_MARKET_YELLOW,
                    "score_boost": YELLOW_SCORE_BOOST, "max_rows": YELLOW_MAX_ROWS}
    else:
        decision = {**base, "state": GREEN, "layer": None, "reason": "", "reason_tag": ""}

    decision["banner"] = build_banner(decision)
    return decision
