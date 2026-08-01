"""Regime-Filter (AUDIT 2026-08-01, F-14): Markt-Gate + Eigen-Performance-Breaker.

Anlass (bewiesen, Audit Kap. 2b/2c): Das System feuerte 8–12 Long-Signale/Tag,
waehrend die eigene 7-Tage-Bilanz von 43 % auf 0 % abglitt (16 % im 14d-
Fenster, ØR < -1R). Ein Senior-System drosselt Long-Momentum in so einem Tape.

Zwei Layer, bewusst getrennt:

Layer 1 MARKET (exogen): mappt das bestehende modules.market_context-Regime
(PANIC/RISK_OFF/RISK_OFF_LIGHT/NEUTRAL/RISK_ON) auf GREEN/YELLOW/RED.
Fail-open bei unbekanntem/fehlendem Regime — niemals ein erfundenes ROT.

Layer 2 BREAKER (endogen): rollierende 7-Tage-Eigen-Performance je Scanner
aus dem Signal-Tracker (load_performance_summary). Trip: n >= 10 entschieden
UND ØR <= -0.3R UND Win% <= 25 %. Release: ØR > -0.1R ODER 5 Handelstage
verstrichen. Selbstheilend: waehrend COOLDOWN keine Trade-Mails => keine neuen
Trade-Signale => die alten Verlierer altern aus dem 7d-Fenster und ØR
normalisiert sich von selbst Richtung 0; die 5-Handelstage-Frist deckt den
Rest ab. Kein Deadlock moeglich.

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
    """7d-Metriken eines Scanners aus load_performance_summary(days=7)."""
    per = (summary or {}).get("per_scanner") or {}
    row = per.get(scanner_key) or {}
    return {
        "decided": int(row.get("decided_signals") or 0),
        "win_pct": float(row.get("win_rate_pct") or 0.0),
        "avg_r": float(row.get("avg_r") or 0.0),
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
                     min_decided: int = BREAKER_MIN_DECIDED,
                     trip_avg_r: float = BREAKER_TRIP_AVG_R,
                     trip_win_pct: float = BREAKER_TRIP_WIN_PCT,
                     release_avg_r: float = BREAKER_RELEASE_AVG_R,
                     release_trading_days: int = BREAKER_RELEASE_TRADING_DAYS) -> dict:
    """Trip-/Release-Logik des Breakers (rein).

    state_entry: {"tripped_at": iso} aus dem State-File (oder None).
    Rueckgabe: {"state": GREEN|RED, "tripped_at": iso|None, "reason": str,
                "metrics": {...}}. RED == COOLDOWN aktiv.
    """
    now = _parse_dt(now) or datetime.now(timezone.utc)
    m = metrics or {}
    decided = int(m.get("decided") or 0)
    win_pct = float(m.get("win_pct") or 0.0)
    avg_r = float(m.get("avg_r") or 0.0)
    tripped_at = _parse_dt((state_entry or {}).get("tripped_at"))
    base = {"metrics": {"decided": decided, "win_pct": win_pct, "avg_r": avg_r}}

    if tripped_at is not None:
        if avg_r > release_avg_r:
            return {**base, "state": GREEN, "tripped_at": None,
                    "reason": f"breaker_release_recovered (ØR {avg_r:+.2f} > {release_avg_r:+.2f})"}
        if trading_days_between(tripped_at.date(), now.date()) >= release_trading_days:
            return {**base, "state": GREEN, "tripped_at": None,
                    "reason": f"breaker_release_time ({release_trading_days} Handelstage)"}
        return {**base, "state": RED, "tripped_at": tripped_at.isoformat(),
                "reason": (f"breaker_cooldown seit {tripped_at.date().isoformat()} "
                           f"(7d: n={decided}, ØR {avg_r:+.2f}, Win {win_pct:.0f}%)")}

    if decided >= min_decided and avg_r <= trip_avg_r and win_pct <= trip_win_pct:
        return {**base, "state": RED, "tripped_at": now.isoformat(),
                "reason": (f"breaker_trip (7d: n={decided} >= {min_decided}, "
                           f"ØR {avg_r:+.2f} <= {trip_avg_r:+.2f}, "
                           f"Win {win_pct:.0f}% <= {trip_win_pct:.0f}%)")}
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
            "diese Mail ist NUR BEOBACHTUNG. Auto-Release bei Erholung (ØR > "
            f"{BREAKER_RELEASE_AVG_R:+.1f}R) oder nach {BREAKER_RELEASE_TRADING_DAYS} Handelstagen. "
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
