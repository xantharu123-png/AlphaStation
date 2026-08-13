"""Regime-Filter (AUDIT 2026-08-01, F-14): Markt-Gate + Eigen-Performance-Breaker.

Anlass (bewiesen, Audit Kap. 2b/2c): Das System feuerte 8–12 Long-Signale/Tag,
waehrend die eigene 7-Tage-Bilanz von 43 % auf 0 % abglitt (16 % im 14d-
Fenster, ØR < -1R). Ein Senior-System drosselt Long-Momentum in so einem Tape.

Zwei Layer, bewusst getrennt:

Layer 1 MARKET (exogen): mappt das bestehende modules.market_context-Regime
(PANIC/RISK_OFF/RISK_OFF_LIGHT/NEUTRAL/RISK_ON) auf GREEN/YELLOW/RED.
Fail-open bei unbekanntem/fehlendem Regime — niemals ein erfundenes ROT.

Layer 2 BREAKER (endogen): voll beobachtete 30-Tage-Eigen-Performance aus dem
Signal-Tracker (load_performance_summary), strikt je gemeinsamer Zelle aus
Scanner, Richtung, Horizont und Marktregime. Trip: n >= 10 entschieden UND
ØR <= -0.3R UND Win% <= 25 %. Release erfolgt ausschliesslich mit nach dem
Trip entschiedener Trade-/Shadow-Evidenz derselben Zelle: n >= 30,
ØR > -0.1R und Win% >= 30 %. Nach 5 Handelstagen wird nur eine manuelle
Pruefung faellig;
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
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

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
BREAKER_RELEASE_MIN_DECIDED = 30
BREAKER_RELEASE_MIN_WIN_PCT = 30.0

# Gelb-Verschaerfung (Layer 1 RISK_OFF_LIGHT)
YELLOW_SCORE_BOOST = 5
YELLOW_MAX_ROWS = 2

# Breaker-COOLDOWN: max. 1 Watch-Mail je Scanner und 20h (Spam-Schutz)
BREAKER_WATCH_CAP_SECONDS = 20 * 3600

# A performance cell may only control mail when every dimension is real,
# explicit and externally stable.  GREEN/YELLOW/RED are the *combined gate*
# result and therefore must never be recycled as a market-regime dimension.
_INELIGIBLE_CELL_TOKENS = {
    "", "UNKNOWN", "LEGACY", "LEGACY_UNKNOWN", "UNSPECIFIED", "NONE",
    "NULL", "N/A", "NA", "GATE_DISABLED", GREEN, YELLOW, RED,
}

DEFAULT_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "data_cache" / "regime_state.json"
)

_STATE_THREAD_LOCK = threading.RLock()


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

def _cell_token_eligible(value, *, reject_gate_state: bool = False) -> bool:
    token = str(value or "").strip().upper()
    if token in _INELIGIBLE_CELL_TOKENS:
        return False
    if any(marker in token for marker in ("UNKNOWN", "LEGACY", "UNSPECIFIED")):
        return False
    if reject_gate_state and token in {GREEN, YELLOW, RED}:
        return False
    return True


def calibration_cell_eligible(cell: dict | None, *, require_explicit: bool = True) -> bool:
    """Return whether a complete joint cell may trip or release a breaker.

    ``trip_release_eligible`` is supplied by the mail producer after checking
    that direction and horizon were present in the source row and that regime
    came from an exogenous context/row field.  This prevents the tracker's
    useful presentation fallbacks (LONG/swing/UNKNOWN) from becoming control
    evidence.
    """
    cell = cell or {}
    if require_explicit and cell.get("trip_release_eligible") is not True:
        return False
    direction = str(cell.get("direction") or "").strip().upper()
    cell_id = str(cell.get("cell_id") or "").strip()
    scanner = str(cell.get("scanner") or "").strip()
    horizon = str(cell.get("horizon") or "").strip()
    regime = str(cell.get("market_regime") or "").strip()
    if direction not in {"LONG", "SHORT"}:
        return False
    if not (
        cell_id
        and _cell_token_eligible(scanner)
        and _cell_token_eligible(horizon)
        and _cell_token_eligible(regime, reject_gate_state=True)
    ):
        return False
    identity = "|".join((scanner, direction, horizon, regime.upper()))
    return cell_id.casefold() == identity.casefold()


def _breaker_row_metrics(row: dict | None) -> dict:
    """Normalize one joint calibration cell without inventing missing data."""
    row = row or {}
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
    unresolved = int(row.get("managed_be_unresolved") or 0)
    return {
        "decided": int(decided),
        "win_pct": float(win_pct),
        "win_pct_upper": float(win_pct_upper),
        "avg_r": float(avg_r),
        "avg_r_upper": float(avg_r_upper),
        "managed_be_unresolved": unresolved,
        "joint_cell_verified": False,
        "trip_release_eligible": False,
        "cell_id": row.get("cell_id"),
        "scanner": row.get("scanner"),
        "direction": row.get("direction"),
        "horizon": row.get("horizon"),
        "market_regime": row.get("market_regime"),
        "r_model": "managed_50_50_plus_breakeven",
    }


def breaker_metrics(
    summary: dict | None,
    scanner_key: str,
    *,
    cell_id: str | None = None,
    calibration_cell: dict | None = None,
) -> dict:
    """Return the one explicitly requested joint calibration cell.

    Scanner aggregates and "worst cell" selection are intentionally forbidden:
    a bad LONG/RISK_OFF cell must not degrade a good SHORT/RISK_ON mail row.
    """
    requested = dict(calibration_cell or {})
    requested_id = str(cell_id or requested.get("cell_id") or "").strip()
    if not requested_id or not calibration_cell_eligible(requested):
        empty = _breaker_row_metrics(None)
        empty.update({
            "cell_id": requested_id or None,
            "scanner": requested.get("scanner"),
            "direction": requested.get("direction"),
            "horizon": requested.get("horizon"),
            "market_regime": requested.get("market_regime"),
        })
        return empty
    cells = [
        row
        for row in ((summary or {}).get("calibration_cells") or [])
        if isinstance(row, dict)
        and str(row.get("scanner") or "") == str(scanner_key or "")
        and str(row.get("cell_id") or "") == requested_id
    ]
    # Duplicate rows for one identity indicate a broken summary contract.  Do
    # not guess or pool them into control evidence.
    if len(cells) != 1:
        empty = _breaker_row_metrics(None)
        empty.update({
            "cell_id": requested_id,
            "scanner": requested.get("scanner"),
            "direction": requested.get("direction"),
            "horizon": requested.get("horizon"),
            "market_regime": requested.get("market_regime"),
        })
        return empty
    normalized = _breaker_row_metrics(cells[0])
    exact_identity = all(
        str(normalized.get(key) or "").casefold()
        == str(requested.get(key) or "").casefold()
        for key in (
            "cell_id", "scanner", "direction", "horizon", "market_regime"
        )
    )
    normalized["joint_cell_verified"] = bool(exact_identity)
    normalized["trip_release_eligible"] = bool(exact_identity)
    return normalized


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
    managed_be_unresolved = int(m.get("managed_be_unresolved") or 0)
    joint_cell_verified = m.get("joint_cell_verified") is True
    trip_release_eligible = m.get("trip_release_eligible") is True
    tripped_at = _parse_dt((state_entry or {}).get("tripped_at"))
    state_identity = {
        "cell_id": (state_entry or {}).get("cell_id"),
        "scanner": (state_entry or {}).get("scanner"),
        "direction": (state_entry or {}).get("direction"),
        "horizon": (state_entry or {}).get("horizon"),
        "market_regime": (state_entry or {}).get("market_regime"),
        # A persisted state predates this transient producer flag.  Complete,
        # non-placeholder dimensions are the durable proof here.
        "trip_release_eligible": True,
    }
    state_identity_eligible = calibration_cell_eligible(state_identity)
    recovery = recovery_metrics or {}
    recovery_available = recovery.get("available") is True
    recovery_joint_cell_verified = recovery.get("joint_cell_verified") is True
    recovery_unresolved = int(recovery.get("managed_be_unresolved") or 0)
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
        "joint_cell_verified": recovery_joint_cell_verified,
        "cell_id": recovery.get("cell_id"),
        "direction": recovery.get("direction"),
        "horizon": recovery.get("horizon"),
        "market_regime": recovery.get("market_regime"),
        "managed_be_unresolved": recovery_unresolved,
    }
    recovery_identity_matches = (
        state_identity_eligible
        and recovery.get("cell_id") == state_identity.get("cell_id")
        and recovery.get("direction") == state_identity.get("direction")
        and recovery.get("horizon") == state_identity.get("horizon")
        and recovery.get("market_regime") == state_identity.get("market_regime")
    )
    base = {
        "metrics": {
            "decided": decided,
            "win_pct": win_pct,
            "win_pct_upper": win_pct_upper,
            "avg_r": avg_r,
            "avg_r_upper": avg_r_upper,
            "managed_be_unresolved": managed_be_unresolved,
            "joint_cell_verified": joint_cell_verified,
            "trip_release_eligible": trip_release_eligible,
            "cell_id": m.get("cell_id"),
            "scanner": m.get("scanner"),
            "direction": m.get("direction"),
            "horizon": m.get("horizon"),
            "market_regime": m.get("market_regime"),
        },
        "recovery_metrics": recovery_view,
        "review_due": False,
    }

    if tripped_at is not None:
        recovery_passes = (
            recovery_available
            and recovery_joint_cell_verified
            and recovery_unresolved == 0
            and recovery_identity_matches
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
        joint_cell_verified
        and trip_release_eligible
        and managed_be_unresolved == 0
        and decided >= min_decided
        and avg_r <= trip_avg_r
        and avg_r_upper <= trip_avg_r
        and win_pct <= trip_win_pct
        and win_pct_upper <= trip_win_pct
    ):
        return {**base, "state": RED, "tripped_at": now.isoformat(),
                "reason": (f"breaker_trip ({m.get('cell_id') or 'joint-cell'}, "
                           f"30d reif: n={decided} >= {min_decided}, "
                           f"ØR {avg_r:+.2f} <= {trip_avg_r:+.2f}, "
                           f"Win-Band {win_pct:.0f}..{win_pct_upper:.0f}% <= "
                           f"{trip_win_pct:.0f}%, R-Upper {avg_r_upper:+.2f})")}
    return {**base, "state": GREEN, "tripped_at": None, "reason": ""}


# ── Persistenz (State-File, defensiv) ────────────────────────────────────────

@contextmanager
def _state_file_lock(target: Path):
    """Serialize state access in this process and across local workers."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    with _STATE_THREAD_LOCK:
        lock_file = lock_path.open("a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _load_state_unlocked(target: Path) -> dict:
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state_unlocked(state: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def load_state(path=None) -> dict:
    target = Path(path or DEFAULT_STATE_PATH)
    try:
        with _state_file_lock(target):
            return _load_state_unlocked(target)
    except Exception:
        return {}


def save_state(state: dict, path=None) -> bool:
    target = Path(path or DEFAULT_STATE_PATH)
    if not isinstance(state, dict):
        return False
    try:
        with _state_file_lock(target):
            _write_state_unlocked(state, target)
        return True
    except Exception:
        return False


def update_state(mutator: Callable[[dict], dict | None], path=None) -> bool:
    """Atomically load, mutate and durably replace the regime state.

    ``mutator`` may edit the supplied dictionary in place and return ``None``,
    or return a replacement dictionary. Its complete read/merge/write cycle
    is protected so concurrent cells cannot overwrite one another.
    """
    target = Path(path or DEFAULT_STATE_PATH)
    if not callable(mutator):
        return False
    try:
        with _state_file_lock(target):
            current = _load_state_unlocked(target)
            replacement = mutator(current)
            next_state = current if replacement is None else replacement
            if not isinstance(next_state, dict):
                return False
            _write_state_unlocked(next_state, target)
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
                       calibration_cell: dict | None = None,
                       now=None, market_gate_enabled: bool = True,
                       breaker_enabled: bool = True) -> dict:
    """Kombiniert Layer 1 + 2 zu einer Mail-Entscheidung fuer einen Scanner.

    Dominanz je Richtung: ROT-Markt > ROT-Breaker > GELB-Markt > GREEN.
    RISK_OFF/PANIC ist ein Long-Risikogate. Eine vollstaendig explizite
    SHORT-Kalibrierzelle bleibt davon unberuehrt und wird nur von ihrem
    eigenen Performance-Breaker kontrolliert. Fehlende/ungueltige Richtung
    bleibt fail-closed und wird bei Markt-ROT weiterhin degradiert.
    state: Breaker-State-Dict je vollstaendiger Zell-ID.
    Rueckgabe enthaelt new_state_entry (zu persistieren, siehe api.py-Helper)
    und reason_tag (Shadow-block_reasons) sowie das Banner via build_banner().
    """
    now = _parse_dt(now) or datetime.now(timezone.utc)

    market = (market_layer_state(context) if market_gate_enabled
              else {"state": GREEN, "regime": "GATE_DISABLED", "risk": None, "reason": ""})

    requested_cell = calibration_cell or {}
    requested_cell_id = str(requested_cell.get("cell_id") or "").strip() or None
    requested_cell_eligible = calibration_cell_eligible(requested_cell)
    requested_direction = str(requested_cell.get("direction") or "").strip().upper()
    market_red_applies = not (
        requested_cell_eligible and requested_direction == "SHORT"
    )
    # Scanner-wide legacy state is deliberately not applied to a row: doing so
    # would recreate the cross-cell contamination this implementation removes.
    # It remains untouched on disk for audit/manual migration.
    state_key = requested_cell_id if requested_cell_eligible else None
    state_entry = (state or {}).get(state_key) or {} if state_key else {}
    breaker_eval = {"state": GREEN, "tripped_at": None, "reason": "", "metrics": {}}
    breaker_evaluated = bool(breaker_enabled and requested_cell_eligible)
    if breaker_evaluated:
        breaker_eval = evaluate_breaker(
            breaker_metrics(
                summary,
                scanner_key,
                cell_id=requested_cell_id,
                calibration_cell=requested_cell,
            ),
            state_entry,
            now,
            recovery_metrics=recovery_metrics,
        )

    new_entry = None
    if breaker_eval.get("tripped_at"):
        metrics_view = breaker_eval.get("metrics") or {}
        previous = state_entry
        new_entry = {
            "tripped_at": breaker_eval["tripped_at"],
            "cell_id": metrics_view.get("cell_id") or previous.get("cell_id"),
            "scanner": metrics_view.get("scanner") or previous.get("scanner") or scanner_key,
            "direction": metrics_view.get("direction") or previous.get("direction"),
            "horizon": metrics_view.get("horizon") or previous.get("horizon"),
            "market_regime": (
                metrics_view.get("market_regime") or previous.get("market_regime")
            ),
        }

    base = {"scanner": scanner_key, "market": market, "breaker": breaker_eval,
            "calibration_cell": requested_cell or None,
            "state_key": state_key,
            "breaker_evaluated": breaker_evaluated,
            "market_red_applies": market_red_applies,
            "new_state_entry": new_entry, "evaluated_at": now.isoformat()}

    if market["state"] == RED and market_red_applies:
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
