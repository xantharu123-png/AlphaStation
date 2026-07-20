"""
Alpha Station — Authentication & Subscription System
V3.4: JWT Auth + Stripe Subscription + Tier-based Feature Gating

Plans:
  - basic ($29/mo): 5 scanner areas, scan every 30min, no sidebar detail, no email alerts
  - pro ($79/mo): Pro scanners, 5min scan interval, full sidebar, email alerts, trade setups
  - elite ($149/mo): Everything + priority scans, ORB scanner, backtest, API access
"""

import os
import json
import time
import hashlib
import hmac
import secrets
import sqlite3
import threading
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path

# JWT via PyJWT
try:
    import jwt as pyjwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    print("[Auth] WARNING: PyJWT not installed — run: pip install PyJWT")

# Stripe
try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    class _StripeFallback:
        class Webhook:
            @staticmethod
            def construct_event(*_args, **_kwargs):
                raise RuntimeError("stripe package is not installed")

    stripe = _StripeFallback()
    HAS_STRIPE = False
    print("[Auth] WARNING: stripe not installed — run: pip install stripe")

# ── Config ──
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("ALPHA_DATA_DIR", _REPO_ROOT / "data_cache"))
_AUTH_DIR = _DATA_DIR / "auth"
_AUTH_DIR.mkdir(parents=True, exist_ok=True)

AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", str(_AUTH_DIR / "alpha_station_auth.sqlite"))
AUTH_DB_LEGACY_JSON_PATH = os.environ.get("AUTH_DB_LEGACY_JSON_PATH", "/tmp/alpha_station_users.json")
AUTH_DB_IS_SQLITE = not str(AUTH_DB_PATH).lower().endswith(".json")
_AUTH_DB_LOCK = threading.RLock()

_COMPROMISED_JWT_SECRET_HASHES = {
    "38203b441fcccb5736da9ce57603d418752f835a48b5d1072d3f3d1b945ed02d",
}

# Compatibility sentinel for security tests and old integrations. This is not
# used to sign tokens; it only represents a deliberately rejected default.
_JWT_DEFAULT_SECRET = "__ALPHA_STATION_REJECTED_JWT_DEFAULT__"


def _secret_is_compromised(value: Any, blocked_hashes: set[str]) -> bool:
    if not value:
        return False
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest in blocked_hashes


def _jwt_secret_is_rejected(value: Any = None) -> bool:
    candidate = JWT_SECRET if value is None else value
    return (
        str(candidate or "") == _JWT_DEFAULT_SECRET
        or _secret_is_compromised(candidate, _COMPROMISED_JWT_SECRET_HASHES)
    )


# S-6 AUDIT FIX: Kein hartkodierter Default mehr. Fehlt JWT_SECRET, wird ein
# ephemerer Zufalls-Secret erzeugt (Tokens ueberleben dann KEINEN Neustart) und
# laut gewarnt. Im Commercial-Modus erzwingt enforce_commercial_boot_security()
# einen expliziten, sicheren Secret.
_JWT_ENV_SECRET = os.environ.get("JWT_SECRET", "")
if _JWT_ENV_SECRET:
    JWT_SECRET = _JWT_ENV_SECRET
    JWT_SECRET_IS_EPHEMERAL = False
else:
    JWT_SECRET = secrets.token_hex(32)
    JWT_SECRET_IS_EPHEMERAL = True
    print(
        "[Auth] WARNUNG: JWT_SECRET ist nicht gesetzt — ephemerer Zufalls-Secret aktiv. "
        "Alle Sessions werden bei jedem Neustart ungueltig. Setze JWT_SECRET als ENV-Variable!"
    )
JWT_SECRET_IS_DEFAULT = _jwt_secret_is_rejected(JWT_SECRET)
if JWT_SECRET_IS_DEFAULT:
    print("[Auth] WARNUNG: JWT_SECRET nutzt den unsicheren Repository-Default — sofort ersetzen!")
JWT_ALGORITHM = "HS256"
ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("ADMIN_EMAILS", "miroslav.mikulic@gmail.com").split(",")
    if email.strip()
}
ADMIN_MASTER_KEY = os.environ.get("ADMIN_MASTER_KEY", "")
ADMIN_MASTER_KEY_CONFIGURED = bool(ADMIN_MASTER_KEY)
# LB-1 AUDIT FIX (2026-06-10): Der frueher hier hartcodierte Master-Key
# ("AlphaStation2026!") steht im Git-Verlauf und gilt als KOMPROMITTIERT.
# Der Legacy-Key kommt nur noch aus der ENV und der kompromittierte Wert
# wird aktiv gesperrt — auch wenn ihn jemand per ENV wieder setzt.
_COMPROMISED_MASTER_KEY_HASHES = {
    "fc4a42cdfedf2c3068d559e37b730578bbe2b72a8feb4575d0ee5ccb30203fbd",
}
LEGACY_ADMIN_MASTER_KEY = os.environ.get("LEGACY_ADMIN_MASTER_KEY", "").strip()
# S-6 AUDIT FIX: Fail-closed — der Legacy-Bootstrap-Key ist standardmaessig AUS
# (Default war "1" = fail-open) und muss explizit aktiviert werden.
ALLOW_LEGACY_ADMIN_MASTER_KEY = os.environ.get(
    "ALLOW_LEGACY_ADMIN_MASTER_KEY", "0"
).strip().lower() not in {"0", "false", "no", "off", ""}
# S-6 AUDIT FIX: Token-Laufzeit via ENV steuerbar, Default 24h (vorher fix 72h).
try:
    JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
except (TypeError, ValueError):
    JWT_EXPIRE_HOURS = 24
if JWT_EXPIRE_HOURS <= 0:
    JWT_EXPIRE_HOURS = 24
PBKDF2_ITERATIONS = int(os.environ.get("AUTH_PBKDF2_ITERATIONS", "260000"))
try:
    PASSWORD_RESET_TTL_MINUTES = int(
        os.environ.get("AUTH_PASSWORD_RESET_TTL_MINUTES", "30")
    )
except (TypeError, ValueError):
    PASSWORD_RESET_TTL_MINUTES = 30
PASSWORD_RESET_TTL_MINUTES = min(max(PASSWORD_RESET_TTL_MINUTES, 10), 120)
NARRATIVE_EMAIL_FREQUENCIES = {"off", "daily", "twice_daily", "weekly"}
TRADE_HORIZON_OPTIONS = {"swing", "intraday", "both"}
_REGISTRATION_EMAIL_RE = re.compile(
    r"^[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f]+\.[^@\s\x00-\x1f\x7f]+$"
)
_MAX_EMAIL_LENGTH = 254
_MAX_PASSWORD_LENGTH = 1024
_MAX_DISPLAY_NAME_LENGTH = 100

_ALLOWED_CHECKOUT_PLANS = {"trial", "basic", "pro", "elite"}


class CheckoutActivationConflict(RuntimeError):
    """A paid checkout must not overwrite existing commercial access."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
_DEFAULT_STRIPE_PRICE_IDS = {
    "trial": "price_1TI0SHEOIB5wAqvU3oFEI079",
    "basic_monthly": "price_1THqyWEOIB5wAqvUrLNLCPZD",
    "pro_monthly": "price_1THqysEOIB5wAqvU6MG9iywG",
    "elite_monthly": "price_1THqzjEOIB5wAqvUTrVwLzha",
}

# Stripe config (set via environment variables)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_IDS = {
    "trial": os.environ.get("STRIPE_PRICE_TRIAL", _DEFAULT_STRIPE_PRICE_IDS["trial"]),
    "basic_monthly": os.environ.get("STRIPE_PRICE_BASIC", _DEFAULT_STRIPE_PRICE_IDS["basic_monthly"]),
    "pro_monthly": os.environ.get("STRIPE_PRICE_PRO", _DEFAULT_STRIPE_PRICE_IDS["pro_monthly"]),
    "elite_monthly": os.environ.get("STRIPE_PRICE_ELITE", _DEFAULT_STRIPE_PRICE_IDS["elite_monthly"]),
}

if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── Plan Definitions ──
PLANS = {
    "trial": {
        "name": "$1 Trial (24h)",
        "price": 1,
        "max_scanner_tabs": 99,
        "scan_interval_min": 5,
        "has_sidebar_detail": True,
        "has_email_alerts": True,
        "has_trade_setups": True,
        "has_orb_scanner": True,
        "has_backtest": True,
        "has_api_access": False,
        "has_ai_analysis": True,        # Trial darf AI testen
        "ai_calls_per_day": 5,          # Aber max 5 pro Tag
        "max_ticker_detail_per_hour": 999,
        "duration_hours": 24,
    },
    "expired": {
        "name": "Trial abgelaufen",
        "price": 0,
        "max_scanner_tabs": 0,
        "scan_interval_min": 999,
        "has_sidebar_detail": False,
        "has_email_alerts": False,
        "has_trade_setups": False,
        "has_orb_scanner": False,
        "has_backtest": False,
        "has_api_access": False,
        "has_ai_analysis": False,
        "ai_calls_per_day": 0,
        "max_ticker_detail_per_hour": 0,
    },
    "basic": {
        "name": "Basic",
        "price": 29,
        "max_scanner_tabs": 5,
        "scan_interval_min": 30,
        "has_sidebar_detail": False,
        "has_email_alerts": False,
        "has_trade_setups": False,
        "has_orb_scanner": False,
        "has_backtest": False,
        "has_api_access": False,
        "has_ai_analysis": False,       # Basic: keine AI
        "ai_calls_per_day": 0,
        "max_ticker_detail_per_hour": 30,
    },
    "pro": {
        "name": "Pro",
        "price": 79,
        "max_scanner_tabs": 99,
        "scan_interval_min": 5,
        "has_sidebar_detail": True,
        "has_email_alerts": True,
        "has_trade_setups": True,
        "has_orb_scanner": False,
        "has_backtest": False,
        "has_api_access": False,
        "has_ai_analysis": True,        # Pro: AI erlaubt
        "ai_calls_per_day": 20,         # Max 20 pro Tag
        "max_ticker_detail_per_hour": 999,
    },
    "elite": {
        "name": "Elite",
        "price": 149,
        "max_scanner_tabs": 99,
        "scan_interval_min": 2,
        "has_sidebar_detail": True,
        "has_email_alerts": True,
        "has_trade_setups": True,
        "has_orb_scanner": True,
        "has_backtest": True,
        "has_api_access": True,
        "has_ai_analysis": True,        # Elite: AI unlimitiert
        "ai_calls_per_day": 999,
        "max_ticker_detail_per_hour": 999,
    },
}

# Customer-facing product tabs. Keep this list explicit: ``None`` means
# default-allow and would silently expose every future admin/global tab.
_ALL_CUSTOMER_TABS = [
    "scanner", "short-scanner", "bi-scanner", "crash-monitor",
    "chart-analyse", "biotech", "btc-divergenz", "early-movers",
    "crypto-signals", "crypto-explosion", "money-flow", "kalender",
    "watchlist", "strategie-guide", "new-listing", "volume-spikes",
    "penny-stocks", "orb", "backtest", "signal-performance",
]

# Allowed scanner tabs per plan. Admins receive ``None`` separately in
# get_user_limits() and bypass API tab gates; AutoTrader is never a customer tab.
SCANNER_TABS_BY_PLAN = {
    "trial": list(_ALL_CUSTOMER_TABS),
    "expired": [],  # No access after trial
    "basic": ["scanner", "short-scanner", "bi-scanner", "crash-monitor", "chart-analyse"],
    "pro": [
        "scanner", "short-scanner", "bi-scanner", "crash-monitor",
        "chart-analyse", "biotech", "btc-divergenz", "early-movers",
        "crypto-signals", "crypto-explosion", "money-flow", "kalender",
        "watchlist", "strategie-guide", "new-listing", "volume-spikes",
        "penny-stocks", "signal-performance",
    ],
    "elite": list(_ALL_CUSTOMER_TABS),
}


# ── User Database (JSON file-based, simple for now) ──
def _sqlite_conn() -> sqlite3.Connection:
    """Open the persistent auth database."""
    db_path = Path(AUTH_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stripe_webhook_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_password_reset_email
        ON password_reset_tokens(email)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL,
            revoked_at TEXT NOT NULL
        )
        """
    )
    return conn


def _maybe_migrate_legacy_json(force: bool = False) -> None:
    """One-time import from the old /tmp JSON auth store."""
    if not AUTH_DB_IS_SQLITE:
        return
    legacy = Path(AUTH_DB_LEGACY_JSON_PATH)
    marker = Path(AUTH_DB_PATH).with_suffix(".migrated")
    if (marker.exists() and not force) or not legacy.exists():
        return
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            legacy_db = json.load(f)
        if not isinstance(legacy_db, dict) or not isinstance(legacy_db.get("users"), dict):
            marker.write_text(_utc_iso(), encoding="utf-8")
            return
        current = _load_users(skip_migration=True)
        merged = current.get("users", {})
        for email, user in legacy_db.get("users", {}).items():
            if isinstance(user, dict) and email not in merged:
                merged[email] = user
        _save_users({"users": merged})
        marker.write_text(_utc_iso(), encoding="utf-8")
        print(f"[Auth] Migrated legacy auth JSON to SQLite: {legacy}")
    except Exception as exc:
        print(f"[Auth] Legacy auth migration skipped: {exc}")


def _load_users(skip_migration: bool = False) -> Dict:
    """Load user database as {'users': {email: user_dict}}."""
    if not AUTH_DB_IS_SQLITE:
        if os.path.exists(AUTH_DB_PATH):
            try:
                with open(AUTH_DB_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) and isinstance(data.get("users"), dict) else {"users": {}}
            except Exception:
                return {"users": {}}
        return {"users": {}}

    if not skip_migration:
        _maybe_migrate_legacy_json()
    try:
        with _sqlite_conn() as conn:
            rows = conn.execute("SELECT email, data FROM users").fetchall()
        users = {}
        for row in rows:
            try:
                data = json.loads(row["data"])
                if isinstance(data, dict):
                    users[row["email"]] = data
            except Exception:
                continue
        return {"users": users}
    except Exception as e:
        print(f"[Auth] Error loading users: {e}")
        return {"users": {}}


def _load_users_with_legacy_retry(email: str = "") -> Dict:
    """Load users and retry legacy import if the requested account is missing."""
    db = _load_users()
    if not AUTH_DB_IS_SQLITE or not email:
        return db
    if email in db.get("users", {}):
        return db
    _maybe_migrate_legacy_json(force=True)
    return _load_users(skip_migration=True)


def _save_users(db: Dict):
    """Upsert users without deleting accounts missing from a stale snapshot."""
    users = db.get("users") if isinstance(db, dict) else None
    if not isinstance(users, dict):
        raise ValueError("Auth store payload must contain a users mapping")

    try:
        with _AUTH_DB_LOCK:
            if not AUTH_DB_IS_SQLITE:
                path = Path(AUTH_DB_PATH)
                path.parent.mkdir(parents=True, exist_ok=True)
                current = _load_users().get("users", {}) if path.exists() else {}
                current.update(users)
                temp_path = path.with_name(
                    f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                try:
                    with open(temp_path, "w", encoding="utf-8") as handle:
                        json.dump({"users": current}, handle, indent=2, default=str)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, path)
                finally:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                return

            now = _utc_iso()
            with _sqlite_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for email, user in users.items():
                    normalized_email = str(email or "").strip().lower()
                    if not normalized_email or not isinstance(user, dict):
                        raise ValueError("Invalid user record in auth store")
                    conn.execute(
                        "INSERT OR REPLACE INTO users(email, data, updated_at) VALUES (?, ?, ?)",
                        (normalized_email, json.dumps(user, default=str), now),
                    )
                conn.commit()
    except Exception as exc:
        print(f"[Auth] Error saving users: {exc}")
        raise


def _update_user_atomic(
    email: str,
    updater: Callable[[Dict[str, Any]], Any],
) -> Optional[Dict[str, Any]]:
    """Update one freshly loaded user inside a single write transaction.

    Read-modify-write callers must use this helper instead of saving a user
    object loaded before the write lock. Otherwise a concurrent Stripe,
    coupon, login or settings request could overwrite newer account fields.
    """
    normalized_email = str(email or "").strip().lower()
    if not normalized_email or not callable(updater):
        raise ValueError("Invalid atomic user update")

    with _AUTH_DB_LOCK:
        if not AUTH_DB_IS_SQLITE:
            db = _load_users()
            user = db.get("users", {}).get(normalized_email)
            if not isinstance(user, dict):
                return None
            before = json.dumps(user, sort_keys=True, default=str)
            updater(user)
            if not isinstance(user, dict):
                raise ValueError("Atomic user updater produced an invalid record")
            after = json.dumps(user, sort_keys=True, default=str)
            if after != before:
                _save_users({"users": {normalized_email: user}})
            return dict(user)

        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM users WHERE email = ?", (normalized_email,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            user = json.loads(row["data"])
            if not isinstance(user, dict):
                raise ValueError("Stored auth user record is invalid")
            before = json.dumps(user, sort_keys=True, default=str)
            updater(user)
            if not isinstance(user, dict):
                raise ValueError("Atomic user updater produced an invalid record")
            after = json.dumps(user, sort_keys=True, default=str)
            if after != before:
                conn.execute(
                    "UPDATE users SET data = ?, updated_at = ? WHERE email = ?",
                    (json.dumps(user, default=str), _utc_iso(), normalized_email),
                )
            conn.commit()
            return dict(user)


def _create_user_if_absent(email: str, user: Dict[str, Any]) -> bool:
    """Atomically create one user; return False when the account exists."""
    normalized_email = str(email or "").strip().lower()
    if not normalized_email or not isinstance(user, dict):
        raise ValueError("Invalid user record")

    with _AUTH_DB_LOCK:
        if not AUTH_DB_IS_SQLITE:
            db = _load_users()
            if normalized_email in db.get("users", {}):
                return False
            db.setdefault("users", {})[normalized_email] = user
            _save_users(db)
            return True

        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT OR IGNORE INTO users(email, data, updated_at) VALUES (?, ?, ?)",
                (normalized_email, json.dumps(user, default=str), _utc_iso()),
            )
            conn.commit()
            return cursor.rowcount == 1


def _delete_user(email: str) -> bool:
    """Delete exactly one account without replacing the remaining user store."""
    normalized_email = str(email or "").strip().lower()
    if not normalized_email:
        return False

    with _AUTH_DB_LOCK:
        if not AUTH_DB_IS_SQLITE:
            db = _load_users()
            if normalized_email not in db.get("users", {}):
                return False
            del db["users"][normalized_email]
            path = Path(AUTH_DB_PATH)
            temp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8") as handle:
                    json.dump(db, handle, indent=2, default=str)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return True

        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "DELETE FROM users WHERE email = ?", (normalized_email,)
            )
            conn.commit()
            return cursor.rowcount == 1


def _hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify PBKDF2 hashes and legacy salted SHA-256 hashes."""
    try:
        if stored_hash.startswith("pbkdf2_sha256$"):
            _, iterations, salt, expected = stored_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
            return hmac.compare_digest(digest.hex(), expected)

        salt, expected = stored_hash.split(":", 1)
        digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def _password_hash_needs_upgrade(stored_hash: str) -> bool:
    """Return True for legacy hashes or PBKDF2 hashes below current policy."""
    try:
        if not str(stored_hash or "").startswith("pbkdf2_sha256$"):
            return True
        _, iterations, _salt, _expected = str(stored_hash).split("$", 3)
        return int(iterations) < PBKDF2_ITERATIONS
    except (TypeError, ValueError):
        return True


def _password_validation_error(password: Any) -> str:
    value = str(password or "")
    if len(value) < 10:
        return "Passwort muss mindestens 10 Zeichen haben"
    if len(value) > _MAX_PASSWORD_LENGTH:
        return "Passwort ist zu lang"
    return ""


def _token_version(user: Any) -> int:
    try:
        return max(0, int((user or {}).get("token_version", 0)))
    except (TypeError, ValueError, AttributeError):
        return 0


# ── JWT Token Management ──
def create_token(user_id: str, email: str, plan: str = "free") -> Optional[str]:
    """Create JWT token for authenticated user."""
    if not HAS_JWT:
        return None
    normalized_email = str(email or "").strip().lower()
    user = _load_users_with_legacy_retry(normalized_email).get("users", {}).get(
        normalized_email, {}
    )
    payload = {
        "sub": user_id,
        "email": normalized_email,
        "plan": plan,
        "ver": _token_version(user),
        "jti": secrets.token_urlsafe(24),
        "iat": _utc_now(),
        "exp": _utc_now() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _is_admin_master_login(email: str, password: str) -> bool:
    """Allow configured admin master key and a temporary legacy bootstrap fallback.

    LB-1 AUDIT FIX: timing-safe Vergleiche; kompromittierte Keys (alter
    Repo-Default) werden in beiden Pfaden aktiv abgelehnt.
    """
    if email not in ADMIN_EMAILS or not password:
        return False
    if (
        ADMIN_MASTER_KEY_CONFIGURED
        and not _secret_is_compromised(
            ADMIN_MASTER_KEY, _COMPROMISED_MASTER_KEY_HASHES
        )
        and hmac.compare_digest(password.encode("utf-8"), ADMIN_MASTER_KEY.encode("utf-8"))
    ):
        return True
    return bool(
        ALLOW_LEGACY_ADMIN_MASTER_KEY
        and LEGACY_ADMIN_MASTER_KEY
        and not _secret_is_compromised(
            LEGACY_ADMIN_MASTER_KEY, _COMPROMISED_MASTER_KEY_HASHES
        )
        and hmac.compare_digest(password.encode("utf-8"), LEGACY_ADMIN_MASTER_KEY.encode("utf-8"))
    )


def verify_token(token: str) -> Optional[Dict]:
    """Verify signature, account binding, token version and revocation state."""
    if not HAS_JWT:
        return None
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email = str(payload.get("email") or "").strip().lower()
        user = _load_users_with_legacy_retry(email).get("users", {}).get(email)
        if not email or not isinstance(user, dict):
            return None
        if not hmac.compare_digest(
            str(payload.get("sub") or ""), str(user.get("id") or "")
        ):
            return None
        try:
            payload_version = int(payload.get("ver", 0))
        except (TypeError, ValueError):
            return None
        if payload_version != _token_version(user):
            return None

        jti = str(payload.get("jti") or "").strip()
        if jti and AUTH_DB_IS_SQLITE:
            with _sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)
                ).fetchone()
            if row is not None:
                return None
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def _clear_manual_plan_window(user: Dict[str, Any]) -> bool:
    """Remove temporary-plan metadata and report whether anything changed."""
    changed = False
    for key in ("manual_plan_ends_at", "manual_plan_source"):
        if key in user:
            user.pop(key, None)
            changed = True
    return changed


def _expire_temporary_access(user: Dict[str, Any], email: str = "") -> bool:
    """Fail closed for expired or malformed trial/coupon access windows."""
    if not isinstance(user, dict):
        return False
    normalized_email = str(email or user.get("email") or "").strip().lower()
    if normalized_email in ADMIN_EMAILS:
        changed = user.get("plan") != "elite"
        user["plan"] = "elite"
        return changed

    # A Stripe subscription is authoritative. Old coupon metadata must never
    # expire a later paid subscription.
    if user.get("stripe_subscription_id") and user.get("plan") in {"basic", "pro", "elite"}:
        return _clear_manual_plan_window(user)

    changed = False
    if user.get("plan") == "trial":
        trial_ends = user.get("trial_ends_at")
        try:
            trial_expired = not trial_ends or _utc_now() >= _parse_utc_datetime(trial_ends)
        except (TypeError, ValueError):
            trial_expired = True
        if trial_expired:
            user["plan"] = "expired"
            changed = True

    has_manual_window = bool(
        user.get("manual_plan_source") or user.get("manual_plan_ends_at")
    )
    if has_manual_window:
        try:
            manual_end = user.get("manual_plan_ends_at")
            manual_expired = not manual_end or _utc_now() >= _parse_utc_datetime(manual_end)
        except (TypeError, ValueError):
            manual_expired = True
        if manual_expired:
            user["plan"] = "expired"
            changed = True
            changed = _clear_manual_plan_window(user) or changed
    return changed


# ── User Registration & Login ──
def _load_effective_users_atomic() -> Dict[str, Dict[str, Any]]:
    """Load all users and expire temporary access in one write transaction."""
    with _AUTH_DB_LOCK:
        if not AUTH_DB_IS_SQLITE:
            db = _load_users()
            users = db.get("users", {})
            changed: Dict[str, Dict[str, Any]] = {}
            for email, user in users.items():
                if isinstance(user, dict) and _expire_temporary_access(user, email):
                    changed[email] = user
            if changed:
                _save_users({"users": changed})
            return {
                email: dict(user)
                for email, user in users.items()
                if isinstance(user, dict)
            }

        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT email, data FROM users").fetchall()
            users: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                try:
                    user = json.loads(row["data"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(user, dict):
                    continue
                email = str(row["email"] or "").strip().lower()
                before = json.dumps(user, sort_keys=True, default=str)
                _expire_temporary_access(user, email)
                after = json.dumps(user, sort_keys=True, default=str)
                if after != before:
                    conn.execute(
                        "UPDATE users SET data = ?, updated_at = ? WHERE email = ?",
                        (json.dumps(user, default=str), _utc_iso(), email),
                    )
                users[email] = dict(user)
            conn.commit()
            return users


def register_user(
    email: str,
    password: str,
    name: str = "",
    legal_consent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register a new user. Returns {success, message, token?, user?}."""
    email = str(email or "").strip().lower()
    password = str(password or "")
    name = str(name or "").strip()
    if (
        not email
        or len(email) > _MAX_EMAIL_LENGTH
        or not _REGISTRATION_EMAIL_RE.fullmatch(email)
    ):
        return {"success": False, "message": "Ungültige Email-Adresse"}
    password_error = _password_validation_error(password)
    if password_error:
        return {"success": False, "message": password_error}
    if len(name) > _MAX_DISPLAY_NAME_LENGTH or any(ord(char) < 32 for char in name):
        return {"success": False, "message": "Name ist ungültig oder zu lang"}

    user_id = secrets.token_hex(8)
    user = {
        "id": user_id,
        "email": email,
        "name": name or email.split("@")[0],
        "password_hash": _hash_password(password),
        "plan": "expired",  # No access until $1 trial or plan purchase
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "alert_email": email,
        "email_alerts_enabled": True,
        "narrative_email_frequency": "daily",
        "trade_alert_horizon": "swing",
        "scanner_trade_horizon": "swing",
        "watch_mail_optin": False,  # AUDIT H-3: Watch-Mails nur mit Opt-in
        "penny_show_watch_rows": False,
        "created_at": _utc_iso(),
        "last_login": _utc_iso(),
        "trial_ends_at": None,
        "trial_used_at": None,
        "token_version": 0,
        "legal_consent": dict(legal_consent or {}),
    }

    if not _create_user_if_absent(email, user):
        return {"success": False, "message": "Email bereits registriert"}

    token = create_token(user_id, email, "expired")
    return {
        "success": True,
        "message": "Account erstellt",
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": user["name"],
            "plan": "expired",
            "trial_ends_at": None,
        },
    }


def login_user(email: str, password: str) -> Dict[str, Any]:
    """Login user. Returns {success, message, token?, user?}."""
    email = email.strip().lower()
    if not HAS_JWT:
        return {"success": False, "message": "Login-System unvollstaendig: PyJWT fehlt auf dem Server"}

    db = _load_users_with_legacy_retry(email)

    user = db["users"].get(email)

    # Admin auto-create only when an explicit ADMIN_MASTER_KEY is configured.
    if not user and _is_admin_master_login(email, password):
        user_id = secrets.token_hex(8)
        user = {
            "id": user_id, "email": email, "name": "Admin",
            "password_hash": _hash_password(password),
            "plan": "elite", "stripe_customer_id": None, "stripe_subscription_id": None,
            "alert_email": email, "email_alerts_enabled": True,
            "narrative_email_frequency": "daily",
            "trade_alert_horizon": "swing", "scanner_trade_horizon": "swing",
            "watch_mail_optin": False,  # AUDIT H-3
            "penny_show_watch_rows": False,
            "created_at": _utc_iso(), "last_login": _utc_iso(),
            "trial_ends_at": None,
            "trial_used_at": None,
            "token_version": 0,
        }
        if not _create_user_if_absent(email, user):
            db = _load_users_with_legacy_retry(email)
            user = db["users"].get(email)

    if not user:
        return {"success": False, "message": "Email oder Passwort falsch"}

    # Admin Master-Key bypass is disabled unless ADMIN_MASTER_KEY is explicitly set.
    is_admin_login = _is_admin_master_login(email, password)
    if not is_admin_login and not _verify_password(password, user["password_hash"]):
        return {"success": False, "message": "Email oder Passwort falsch"}

    def _apply_login(current: Dict[str, Any]) -> None:
        # Verify again against the record loaded inside the write transaction.
        # A concurrent password/account change must invalidate the stale login.
        if not is_admin_login and not _verify_password(
            password, str(current.get("password_hash") or "")
        ):
            raise PermissionError("credentials_changed")
        if not is_admin_login and _password_hash_needs_upgrade(
            str(current.get("password_hash", ""))
        ):
            current["password_hash"] = _hash_password(password)
        current.setdefault("token_version", 0)
        current["last_login"] = _utc_iso()
        if email in ADMIN_EMAILS:
            current["plan"] = "elite"
        else:
            _expire_temporary_access(current, email)

    try:
        user = _update_user_atomic(email, _apply_login)
    except PermissionError:
        return {"success": False, "message": "Email oder Passwort falsch"}
    if not isinstance(user, dict):
        return {"success": False, "message": "Email oder Passwort falsch"}

    plan = user.get("plan", "free")
    token = create_token(user["id"], email, plan)
    if not token:
        return {"success": False, "message": "Login-System konnte kein Token erstellen"}

    return {
        "success": True,
        "message": "Login erfolgreich",
        "token": token,
        "user": {
            "id": user["id"],
            "email": email,
            "name": user.get("name", ""),
            "plan": plan,
            "is_admin": email in ADMIN_EMAILS,
            "stripe_customer_id": user.get("stripe_customer_id"),
            "stripe_subscription_id": user.get("stripe_subscription_id"),
            "trial_ends_at": user.get("trial_ends_at"),
            "manual_plan_ends_at": user.get("manual_plan_ends_at"),
        },
    }


def change_password(token: str, current_password: str, new_password: str) -> Dict[str, Any]:
    """Change a password and revoke every previously issued account token."""
    payload = verify_token(token)
    if not payload:
        return {"success": False, "message": "Ungueltige oder abgelaufene Sitzung"}
    password_error = _password_validation_error(new_password)
    if password_error:
        return {"success": False, "message": password_error}
    if hmac.compare_digest(
        str(current_password or "").encode("utf-8"),
        str(new_password or "").encode("utf-8"),
    ):
        return {"success": False, "message": "Das neue Passwort muss sich unterscheiden"}

    email = str(payload.get("email") or "").strip().lower()

    def _change(current: Dict[str, Any]) -> None:
        if not _verify_password(
            str(current_password or ""), str(current.get("password_hash") or "")
        ):
            raise PermissionError("invalid_current_password")
        current["password_hash"] = _hash_password(str(new_password))
        current["token_version"] = _token_version(current) + 1
        current["password_changed_at"] = _utc_iso()

    try:
        user = _update_user_atomic(email, _change)
    except PermissionError:
        return {"success": False, "message": "Aktuelles Passwort ist falsch"}
    if not isinstance(user, dict):
        return {"success": False, "message": "Account nicht gefunden"}
    fresh_token = create_token(
        str(user.get("id") or ""), email, str(user.get("plan") or "expired")
    )
    return {
        "success": bool(fresh_token),
        "message": "Passwort geaendert; alte Sitzungen wurden beendet",
        "token": fresh_token,
    }


def create_password_reset_request(email: str) -> Dict[str, Any]:
    """Create one short-lived reset token without exposing account existence."""
    normalized_email = str(email or "").strip().lower()
    generic = {
        "success": True,
        "message": "Falls der Account existiert, wurde eine Reset-Mail versendet",
    }
    if (
        not AUTH_DB_IS_SQLITE
        or not normalized_email
        or len(normalized_email) > _MAX_EMAIL_LENGTH
        or not _REGISTRATION_EMAIL_RE.fullmatch(normalized_email)
    ):
        return generic

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = _utc_now()
    expires_at = now + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES)
    with _AUTH_DB_LOCK:
        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute(
                "SELECT 1 FROM users WHERE email = ?", (normalized_email,)
            ).fetchone()
            if account is None:
                conn.commit()
                return generic
            conn.execute(
                "DELETE FROM password_reset_tokens WHERE email = ? OR expires_at < ?",
                (normalized_email, now.isoformat()),
            )
            conn.execute(
                """
                INSERT INTO password_reset_tokens(
                    token_hash, email, expires_at, used_at, created_at
                ) VALUES (?, ?, ?, NULL, ?)
                """,
                (
                    token_hash,
                    normalized_email,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
    return {
        **generic,
        "delivery_email": normalized_email,
        "reset_token": raw_token,
        "expires_at": expires_at.isoformat(),
    }


def confirm_password_reset(reset_token: str, new_password: str) -> Dict[str, Any]:
    """Consume a reset token exactly once and revoke all account sessions."""
    password_error = _password_validation_error(new_password)
    if password_error:
        return {"success": False, "message": password_error}
    raw_token = str(reset_token or "").strip()
    if not AUTH_DB_IS_SQLITE or len(raw_token) < 32 or len(raw_token) > 512:
        return {"success": False, "message": "Reset-Link ist ungueltig oder abgelaufen"}
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = _utc_now()
    with _AUTH_DB_LOCK:
        with _sqlite_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT email, expires_at, used_at
                FROM password_reset_tokens WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["used_at"]:
                conn.rollback()
                return {"success": False, "message": "Reset-Link ist ungueltig oder abgelaufen"}
            try:
                expires_at = _parse_utc_datetime(row["expires_at"])
            except (TypeError, ValueError):
                expires_at = now - timedelta(seconds=1)
            if now >= expires_at:
                conn.execute(
                    "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
                    (now.isoformat(), token_hash),
                )
                conn.commit()
                return {"success": False, "message": "Reset-Link ist ungueltig oder abgelaufen"}
            email = str(row["email"] or "").strip().lower()
            user_row = conn.execute(
                "SELECT data FROM users WHERE email = ?", (email,)
            ).fetchone()
            if user_row is None:
                conn.rollback()
                return {"success": False, "message": "Reset-Link ist ungueltig oder abgelaufen"}
            user = json.loads(user_row["data"])
            if not isinstance(user, dict):
                conn.rollback()
                return {"success": False, "message": "Reset-Link ist ungueltig oder abgelaufen"}
            user["password_hash"] = _hash_password(str(new_password))
            user["token_version"] = _token_version(user) + 1
            user["password_changed_at"] = now.isoformat()
            conn.execute(
                "UPDATE users SET data = ?, updated_at = ? WHERE email = ?",
                (json.dumps(user, default=str), now.isoformat(), email),
            )
            conn.execute(
                """
                UPDATE password_reset_tokens SET used_at = ?
                WHERE email = ? AND used_at IS NULL
                """,
                (now.isoformat(), email),
            )
            conn.commit()
    return {
        "success": True,
        "message": "Passwort geaendert. Bitte neu anmelden.",
    }


def revoke_token(token: str) -> Dict[str, Any]:
    """Revoke one current JWT; legacy tokens fall back to account-wide revoke."""
    payload = verify_token(token)
    if not payload:
        return {"success": True, "message": "Sitzung beendet"}
    jti = str(payload.get("jti") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if jti and AUTH_DB_IS_SQLITE:
        try:
            expires_at = datetime.fromtimestamp(float(payload.get("exp")), timezone.utc)
        except (TypeError, ValueError, OSError):
            expires_at = _utc_now() + timedelta(hours=JWT_EXPIRE_HOURS)
        now = _utc_now()
        with _AUTH_DB_LOCK:
            with _sqlite_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM revoked_tokens WHERE expires_at < ?",
                    (now.isoformat(),),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO revoked_tokens(jti, expires_at, revoked_at)
                    VALUES (?, ?, ?)
                    """,
                    (jti, expires_at.isoformat(), now.isoformat()),
                )
                conn.commit()
    else:
        _update_user_atomic(
            email,
            lambda current: current.update(
                {"token_version": _token_version(current) + 1}
            ),
        )
    return {"success": True, "message": "Sitzung beendet"}


# ── Stripe Checkout ──
def get_checkout_eligibility(email: str, plan: str) -> Dict[str, Any]:
    """Return a fail-closed checkout decision for one account and plan."""
    email = str(email or "").strip().lower()
    plan = str(plan or "").strip().lower()
    if plan not in _ALLOWED_CHECKOUT_PLANS:
        return {
            "allowed": False,
            "code": "unknown_plan",
            "message": "Unbekanntes Abonnement",
        }

    users = _load_effective_users_atomic()
    user = users.get(email)
    if not isinstance(user, dict):
        return {
            "allowed": False,
            "code": "account_not_found",
            "message": "Account nicht gefunden",
        }
    if email in ADMIN_EMAILS:
        return {
            "allowed": False,
            "code": "admin_account",
            "message": "Der Admin-Account benoetigt kein Abonnement",
        }

    current_plan = str(user.get("plan") or "expired").strip().lower()
    subscription_id = str(user.get("stripe_subscription_id") or "").strip()
    if subscription_id:
        return {
            "allowed": False,
            "code": "active_subscription",
            "message": "Ein Abonnement ist bereits vorhanden. Bitte im Kundenportal verwalten.",
        }

    if plan == "trial":
        if current_plan in {"basic", "pro", "elite"}:
            return {
                "allowed": False,
                "code": "paid_access_active",
                "message": "Mit aktivem Tarif kann kein Trial gestartet werden",
            }
        # trial_ends_at remains as evidence after expiry for legacy accounts;
        # trial_used_at covers users who later purchased a paid plan.
        if user.get("trial_used_at") or user.get("trial_ends_at"):
            return {
                "allowed": False,
                "code": "trial_already_used",
                "message": "Der Trial wurde fuer diesen Account bereits verwendet",
            }

    return {"allowed": True, "code": "ok", "message": "Checkout erlaubt"}


def create_checkout_session(email: str, plan: str, success_url: str, cancel_url: str) -> Optional[str]:
    """Create Stripe Checkout Session. Returns checkout URL or None."""
    if not HAS_STRIPE or not STRIPE_SECRET_KEY:
        print("[Auth] Stripe not configured")
        return None

    email = str(email or "").strip().lower()
    plan = str(plan or "").strip().lower()
    if plan not in _ALLOWED_CHECKOUT_PLANS:
        print(f"[Auth] Rejected unknown checkout plan: {plan!r}")
        return None

    eligibility = get_checkout_eligibility(email, plan)
    if not eligibility.get("allowed"):
        print(f"[Auth] Checkout blocked ({eligibility.get('code')}): {email}")
        return None

    is_trial = (plan == "trial")
    if is_trial:
        price_id = STRIPE_PRICE_IDS.get("trial")
    else:
        price_key = f"{plan}_monthly"
        price_id = STRIPE_PRICE_IDS.get(price_key)

    if not price_id:
        print(f"[Auth] No Stripe price ID for plan: {plan}")
        return None

    db = _load_users()
    user = db["users"].get(email)
    if not user:
        return None

    try:
        # Get or create Stripe customer
        customer_id = user.get("stripe_customer_id")
        if not customer_id:
            customer = stripe.Customer.create(
                email=email,
                name=user.get("name", ""),
                metadata={"user_id": user["id"]},
                idempotency_key=f"alphastation-customer-{user['id']}",
            )
            created_customer_id = customer.id

            def _attach_customer(current: Dict[str, Any]) -> None:
                if not current.get("stripe_customer_id"):
                    current["stripe_customer_id"] = created_customer_id

            user = _update_user_atomic(email, _attach_customer)
            if not isinstance(user, dict):
                return None
            customer_id = user.get("stripe_customer_id")
            if not customer_id:
                return None

        # Trial = one-time payment, Plans = subscription
        session_params = {
            "customer": customer_id,
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "payment" if is_trial else "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {"email": email, "plan": plan},
        }
        # Repeated clicks within one 30-minute window resolve to the same
        # Stripe Checkout Session instead of creating duplicate purchases.
        session = stripe.checkout.Session.create(
            **session_params,
            idempotency_key=(
                f"alphastation-checkout-{user['id']}-{plan}-{int(time.time() // 1800)}"
            ),
        )
        return session.url
    except Exception as e:
        print(f"[Auth] Stripe checkout error: {e}")
        return None


def create_billing_portal(email: str, return_url: str) -> Optional[str]:
    """Create Stripe Billing Portal URL for subscription management."""
    if not HAS_STRIPE or not STRIPE_SECRET_KEY:
        return None

    db = _load_users()
    user = db["users"].get(email)
    if not user or not user.get("stripe_customer_id"):
        return None

    try:
        session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=return_url,
        )
        return session.url
    except Exception as e:
        print(f"[Auth] Billing portal error: {e}")
        return None


# ── S-6 AUDIT FIX: Stripe-Webhook Event-ID-Dedupe (Replay-/Retry-Schutz) ──
_WEBHOOK_EVENT_LIMIT = 5000
_WEBHOOK_LOCK = threading.RLock()


def _webhook_events_file() -> Path:
    """Persistenter Event-ID-Store neben der Auth-DB (folgt AUTH_DB_PATH)."""
    return Path(AUTH_DB_PATH).parent / "stripe_webhook_events.json"


def _load_processed_webhook_events() -> List[str]:
    if AUTH_DB_IS_SQLITE:
        try:
            with _sqlite_conn() as conn:
                rows = conn.execute(
                    "SELECT event_id FROM stripe_webhook_events ORDER BY processed_at DESC LIMIT ?",
                    (_WEBHOOK_EVENT_LIMIT,),
                ).fetchall()
            return [str(row["event_id"]) for row in rows]
        except Exception as exc:
            print(f"[Auth] Could not read Stripe event store: {exc}")
            raise
    try:
        data = json.loads(_webhook_events_file().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[Auth] Could not read Stripe event store: {exc}")
        raise
    return []

def _remember_webhook_event(event_id: str) -> None:
    if not event_id:
        return
    if AUTH_DB_IS_SQLITE:
        try:
            with _sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO stripe_webhook_events(event_id, processed_at) VALUES (?, ?)",
                    (str(event_id), _utc_iso()),
                )
                conn.execute(
                    """
                    DELETE FROM stripe_webhook_events
                    WHERE event_id NOT IN (
                        SELECT event_id FROM stripe_webhook_events
                        ORDER BY processed_at DESC LIMIT ?
                    )
                    """,
                    (_WEBHOOK_EVENT_LIMIT,),
                )
                conn.commit()
            return
        except Exception as exc:
            print(f"[Auth] Could not write Stripe event store: {exc}")
            raise
    events = _load_processed_webhook_events()
    if str(event_id) not in events:
        events.append(str(event_id))
    if len(events) > _WEBHOOK_EVENT_LIMIT:
        # Rotation: nur die juengsten ~5000 IDs behalten
        events = events[-_WEBHOOK_EVENT_LIMIT:]
    try:
        path = _webhook_events_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(events), encoding="utf-8")
        os.replace(temp_path, path)
    except Exception as exc:
        print(f"[Auth] Webhook-Event-Store konnte nicht geschrieben werden: {exc}")
        raise


def handle_stripe_webhook(payload: bytes, sig_header: str) -> Dict[str, Any]:
    """Handle Stripe webhook events. Returns {success, event_type}."""
    if not HAS_STRIPE or not STRIPE_WEBHOOK_SECRET:
        return {"success": False, "error": "Stripe not configured"}

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return {"success": False, "error": str(e)}

    try:
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        event_created_raw = event.get("created")
        event_data = event.get("data") or {}
        data = event_data.get("object") or {}
    except Exception as exc:
        return {"success": False, "error": f"Invalid Stripe event: {exc}"}

    if not event_id or not event_type or not hasattr(data, "get"):
        return {"success": False, "error": "Invalid Stripe event envelope"}

    with _WEBHOOK_LOCK:
        try:
            if event_id in set(_load_processed_webhook_events()):
                return {"success": True, "event_type": event_type, "duplicate": True}

            db = _load_users()
            users = db.get("users")
            if not isinstance(users, dict):
                return {"success": False, "error": "Auth user store is invalid"}

            changed = False
            ignored_reason = None

            if event_type == "checkout.session.completed":
                metadata = data.get("metadata") or {}
                email = str(metadata.get("email") or "").strip().lower()
                plan = str(metadata.get("plan") or "").strip().lower()
                payment_status = str(data.get("payment_status") or "").strip().lower()
                subscription_id = data.get("subscription")
                customer_id = data.get("customer")

                if payment_status not in {"paid", "no_payment_required"}:
                    return {"success": False, "error": "Checkout is not paid"}
                if plan not in _ALLOWED_CHECKOUT_PLANS:
                    return {"success": False, "error": "Checkout contains unknown plan"}
                if not email or email not in users:
                    return {"success": False, "error": "Checkout user not found"}
                if not customer_id:
                    return {"success": False, "error": "Checkout customer missing"}
                if plan != "trial" and not subscription_id:
                    return {"success": False, "error": "Checkout subscription missing"}

                try:
                    event_created = datetime.fromtimestamp(
                        float(event_created_raw), tz=timezone.utc
                    )
                except (TypeError, ValueError, OSError):
                    event_created = _utc_now()

                def _activate_checkout(current: Dict[str, Any]) -> None:
                    current["stripe_customer_id"] = customer_id
                    if plan == "trial":
                        if (
                            current.get("trial_used_at")
                            or current.get("trial_ends_at")
                            or current.get("stripe_subscription_id")
                            or current.get("plan") in {"basic", "pro", "elite"}
                        ):
                            raise CheckoutActivationConflict("trial_already_used")
                        current["plan"] = "trial"
                        current["trial_used_at"] = event_created.isoformat()
                        current["trial_ends_at"] = (
                            event_created + timedelta(hours=24)
                        ).isoformat()
                        current["stripe_subscription_id"] = None
                        current.pop("stripe_subscription_event_created", None)
                    else:
                        existing_subscription = str(
                            current.get("stripe_subscription_id") or ""
                        ).strip()
                        if (
                            existing_subscription
                            and existing_subscription != str(subscription_id)
                        ):
                            raise CheckoutActivationConflict(
                                "different_subscription_already_active"
                            )
                        current["plan"] = plan
                        current["trial_ends_at"] = None
                        current["stripe_subscription_id"] = subscription_id
                        current["stripe_subscription_event_created"] = int(
                            event_created.timestamp()
                        )
                    _clear_manual_plan_window(current)

                try:
                    updated_user = _update_user_atomic(email, _activate_checkout)
                except CheckoutActivationConflict as exc:
                    _remember_webhook_event(event_id)
                    return {
                        "success": True,
                        "event_type": event_type,
                        "changed": False,
                        "ignored_reason": exc.reason,
                    }
                if updated_user is None:
                    return {"success": False, "error": "Checkout user not found"}
                changed = True
                print(f"[Auth] Checkout activated: {email} -> {plan}")

            elif event_type == "customer.subscription.updated":
                customer_id = data.get("customer")
                status = str(data.get("status") or "").strip().lower()
                event_subscription_id = str(data.get("id") or "").strip()
                matched_email = next(
                    (email for email, user in users.items() if user.get("stripe_customer_id") == customer_id),
                    None,
                )
                if not matched_email:
                    return {"success": False, "error": "Subscription customer not found"}
                if not event_subscription_id:
                    return {"success": False, "error": "Subscription ID missing"}

                current_user = users.get(matched_email, {})
                current_subscription_id = str(
                    current_user.get("stripe_subscription_id") or ""
                ).strip()
                if current_subscription_id and current_subscription_id != event_subscription_id:
                    _remember_webhook_event(event_id)
                    return {
                        "success": True,
                        "event_type": event_type,
                        "changed": False,
                        "ignored_reason": "subscription_not_current",
                    }

                try:
                    subscription_event_created = int(event_created_raw)
                except (TypeError, ValueError, OverflowError):
                    subscription_event_created = None
                try:
                    last_subscription_event_created = int(
                        current_user.get("stripe_subscription_event_created")
                    )
                except (TypeError, ValueError, OverflowError):
                    last_subscription_event_created = None
                if (
                    subscription_event_created is not None
                    and last_subscription_event_created is not None
                    and subscription_event_created < last_subscription_event_created
                ):
                    _remember_webhook_event(event_id)
                    return {
                        "success": True,
                        "event_type": event_type,
                        "changed": False,
                        "ignored_reason": "subscription_event_stale",
                    }

                if status in {"active", "trialing"}:
                    items = (data.get("items") or {}).get("data") or []
                    first_item = items[0] if items else {}
                    price_id = (first_item.get("price") or {}).get("id")
                    matched_plan = next(
                        (
                            key.replace("_monthly", "")
                            for key, configured_id in STRIPE_PRICE_IDS.items()
                            if key != "trial" and configured_id and configured_id == price_id
                        ),
                        None,
                    )
                    if matched_plan not in _ALLOWED_CHECKOUT_PLANS - {"trial"}:
                        return {"success": False, "error": "Subscription price is not mapped to a sellable plan"}
                    def _activate_subscription(current: Dict[str, Any]) -> None:
                        if current.get("stripe_customer_id") != customer_id:
                            raise LookupError("Subscription customer changed")
                        current["plan"] = matched_plan
                        current["stripe_subscription_id"] = event_subscription_id
                        if subscription_event_created is not None:
                            current["stripe_subscription_event_created"] = (
                                subscription_event_created
                            )
                        current["trial_ends_at"] = None
                        _clear_manual_plan_window(current)

                    if _update_user_atomic(matched_email, _activate_subscription) is None:
                        return {"success": False, "error": "Subscription user not found"}
                    changed = True
                    print(f"[Auth] Plan updated: {matched_email} -> {matched_plan}")
                else:
                    def _expire_subscription(current: Dict[str, Any]) -> None:
                        if current.get("stripe_customer_id") != customer_id:
                            raise LookupError("Subscription customer changed")
                        current["plan"] = "expired"
                        if subscription_event_created is not None:
                            current["stripe_subscription_event_created"] = (
                                subscription_event_created
                            )
                        current["trial_ends_at"] = None
                        _clear_manual_plan_window(current)

                    if _update_user_atomic(matched_email, _expire_subscription) is None:
                        return {"success": False, "error": "Subscription user not found"}
                    changed = True
                    print(f"[Auth] Subscription ended: {matched_email} -> expired")

            elif event_type == "customer.subscription.deleted":
                customer_id = data.get("customer")
                matched_email = next(
                    (email for email, user in users.items() if user.get("stripe_customer_id") == customer_id),
                    None,
                )
                if not matched_email:
                    return {"success": False, "error": "Deleted subscription customer not found"}
                event_subscription_id = str(data.get("id") or "").strip()
                current_subscription_id = str(
                    users.get(matched_email, {}).get("stripe_subscription_id") or ""
                ).strip()
                if current_subscription_id and current_subscription_id != event_subscription_id:
                    _remember_webhook_event(event_id)
                    return {
                        "success": True,
                        "event_type": event_type,
                        "changed": False,
                        "ignored_reason": "subscription_not_current",
                    }
                try:
                    subscription_event_created = int(event_created_raw)
                except (TypeError, ValueError, OverflowError):
                    subscription_event_created = None
                try:
                    last_subscription_event_created = int(
                        users.get(matched_email, {}).get(
                            "stripe_subscription_event_created"
                        )
                    )
                except (TypeError, ValueError, OverflowError):
                    last_subscription_event_created = None
                if (
                    subscription_event_created is not None
                    and last_subscription_event_created is not None
                    and subscription_event_created < last_subscription_event_created
                ):
                    _remember_webhook_event(event_id)
                    return {
                        "success": True,
                        "event_type": event_type,
                        "changed": False,
                        "ignored_reason": "subscription_event_stale",
                    }
                def _delete_subscription(current: Dict[str, Any]) -> None:
                    if current.get("stripe_customer_id") != customer_id:
                        raise LookupError("Subscription customer changed")
                    current["plan"] = "expired"
                    current["stripe_subscription_id"] = None
                    if subscription_event_created is not None:
                        current["stripe_subscription_event_created"] = (
                            subscription_event_created
                        )
                    current["trial_ends_at"] = None
                    _clear_manual_plan_window(current)

                if _update_user_atomic(matched_email, _delete_subscription) is None:
                    return {"success": False, "error": "Subscription user not found"}
                changed = True
                print(f"[Auth] Subscription deleted: {matched_email} -> expired")
            else:
                ignored_reason = "event_type_not_used_for_access"

            _remember_webhook_event(event_id)
            result = {"success": True, "event_type": event_type, "changed": changed}
            if ignored_reason:
                result["ignored_reason"] = ignored_reason
            return result
        except Exception as exc:
            return {"success": False, "error": f"Webhook processing failed: {exc}"}


# ── Feature Gating ──
def get_user_plan(token: str) -> str:
    """Get the effective plan after applying every temporary-access window."""
    payload = verify_token(token)
    if not payload:
        return "expired"
    email = str(payload.get("email") or "").strip().lower()
    if email in ADMIN_EMAILS:
        return "elite"
    user = _update_user_atomic(
        email, lambda current: _expire_temporary_access(current, email)
    )
    if not isinstance(user, dict):
        return "expired"
    return str(user.get("plan") or "expired")


def get_plan_features(plan: str) -> Dict:
    """Get feature set for a plan."""
    return PLANS.get(plan, PLANS["expired"])


def check_feature(token: str, feature: str) -> bool:
    """Check if user has access to a specific feature."""
    plan = get_user_plan(token)
    features = get_plan_features(plan)
    return features.get(feature, False)


def check_tab_access(token: str, tab_id: str) -> bool:
    """Check if user has access to a specific scanner tab."""
    plan = get_user_plan(token)
    allowed = SCANNER_TABS_BY_PLAN.get(plan)
    if allowed is None:  # None = all tabs allowed
        return True
    return tab_id in allowed


def get_user_limits(token: str) -> Dict:
    """Get all limits for user based on their plan."""
    plan = get_user_plan(token)
    features = get_plan_features(plan)
    allowed_tabs = SCANNER_TABS_BY_PLAN.get(plan)
    # Check admin
    payload = verify_token(token)
    email = payload.get("email", "") if payload else ""
    is_admin = email in ADMIN_EMAILS
    # Admins get all tabs
    if is_admin:
        allowed_tabs = None
    return {
        "plan": plan,
        "plan_name": features["name"],
        "price": features["price"],
        "allowed_tabs": allowed_tabs,  # None = all
        "is_admin": is_admin,
        "scan_interval_min": features["scan_interval_min"],
        "has_sidebar_detail": features["has_sidebar_detail"],
        "has_email_alerts": features["has_email_alerts"],
        "has_trade_setups": features["has_trade_setups"],
        "has_orb_scanner": features["has_orb_scanner"],
        "has_backtest": features["has_backtest"],
        "has_api_access": features["has_api_access"],
        "max_ticker_detail_per_hour": features["max_ticker_detail_per_hour"],
    }


def _normalize_narrative_email_frequency(value: Any) -> str:
    freq = str(value or "daily").strip().lower().replace("-", "_")
    aliases = {
        "aus": "off",
        "off": "off",
        "none": "off",
        "daily": "daily",
        "taeglich": "daily",
        "taglich": "daily",
        "twice": "twice_daily",
        "twice_daily": "twice_daily",
        "2x": "twice_daily",
        "weekly": "weekly",
        "woechentlich": "weekly",
        "wochentlich": "weekly",
    }
    return aliases.get(freq, freq if freq in NARRATIVE_EMAIL_FREQUENCIES else "daily")


def _normalize_trade_horizon(value: Any) -> str:
    horizon = str(value or "swing").strip().lower().replace("-", "_")
    aliases = {
        "swing": "swing",
        "swingtrading": "swing",
        "swing_trading": "swing",
        "daily": "swing",
        "mehrtagig": "swing",
        "intraday": "intraday",
        "daytrade": "intraday",
        "daytrading": "intraday",
        "day_trading": "intraday",
        "5m": "intraday",
        "both": "both",
        "beides": "both",
        "all": "both",
        "alle": "both",
    }
    return aliases.get(horizon, horizon if horizon in TRADE_HORIZON_OPTIONS else "swing")


def get_email_alert_recipients(alert_type: str = "", frequency: str = "", trade_horizon: str = "", mail_class: str = "trade") -> List[str]:
    """Return unique alert recipients for active plans with email-alert access.

    AUDIT H-3: mail_class steuert das Abonnenten-Routing. "watch"-Mails
    (Beobachtungslisten ohne Einstiegssignal) gehen nur an Abonnenten mit
    explizitem Opt-in (watch_mail_optin, Default False). "trade"/"info"
    verhalten sich wie bisher; der Betreiber-Fallback-Empfaenger wird in
    api._send_email_alert separat behandelt und bekommt alle Klassen.
    """
    recipients: List[str] = []
    users = _load_effective_users_atomic()
    alert_type = str(alert_type or "").strip().lower()
    frequency = _normalize_narrative_email_frequency(frequency) if frequency else ""
    trade_horizon = _normalize_trade_horizon(trade_horizon) if trade_horizon else ""
    mail_class = str(mail_class or "trade").strip().lower()
    for email, user in users.items():
        if not isinstance(user, dict):
            continue
        features = get_plan_features(user.get("plan", "expired"))
        if not features.get("has_email_alerts"):
            continue
        if user.get("email_alerts_enabled", True) is False:
            continue
        if mail_class == "watch" and not bool(user.get("watch_mail_optin", False)):
            # AUDIT H-3: Watch-Mails nur mit explizitem Opt-in.
            continue
        if alert_type == "narrative_pulse":
            user_frequency = _normalize_narrative_email_frequency(user.get("narrative_email_frequency", "daily"))
            if user_frequency == "off":
                continue
            if frequency and user_frequency != frequency:
                continue
        if trade_horizon and alert_type != "narrative_pulse":
            user_horizon = _normalize_trade_horizon(user.get("trade_alert_horizon", "swing"))
            if user_horizon != "both" and user_horizon != trade_horizon:
                continue
        alert_email = str(user.get("alert_email") or email).strip().lower()
        if "@" in alert_email:
            recipients.append(alert_email)
    return sorted(set(recipients))

# User alert settings
def get_user_alert_settings(token: str) -> Dict[str, Any]:
    payload = verify_token(token)
    if not payload:
        return {}
    email = str(payload.get("email") or "").strip().lower()
    user = _update_user_atomic(
        email, lambda current: _expire_temporary_access(current, email)
    )
    if not isinstance(user, dict):
        return {}
    effective_plan = "elite" if email in ADMIN_EMAILS else str(
        user.get("plan") or "expired"
    )
    return {
        "email_alerts_enabled": user.get("email_alerts_enabled", True),
        "alert_email": user.get("alert_email") or email,
        "narrative_email_frequency": _normalize_narrative_email_frequency(user.get("narrative_email_frequency", "daily")),
        "narrative_email_frequency_options": ["off", "daily", "twice_daily", "weekly"],
        "trade_alert_horizon": _normalize_trade_horizon(user.get("trade_alert_horizon", "swing")),
        "scanner_trade_horizon": _normalize_trade_horizon(user.get("scanner_trade_horizon", "swing")),
        "trade_horizon_options": ["swing", "intraday", "both"],
        "watch_mail_optin": bool(user.get("watch_mail_optin", False)),  # AUDIT H-3
        "penny_show_watch_rows": bool(user.get("penny_show_watch_rows", False)),
        "has_email_alerts": get_plan_features(effective_plan).get("has_email_alerts", False),
    }


def update_user_alert_settings(
    token: str,
    enabled: Optional[bool] = None,
    alert_email: Optional[str] = None,
    narrative_email_frequency: Optional[str] = None,
    trade_alert_horizon: Optional[str] = None,
    scanner_trade_horizon: Optional[str] = None,
    watch_mail_optin: Optional[bool] = None,
    penny_show_watch_rows: Optional[bool] = None,
) -> Dict[str, Any]:
    payload = verify_token(token)
    if not payload:
        return {"success": False, "message": "Invalid token"}
    email = str(payload.get("email") or "").strip().lower()
    updates: Dict[str, Any] = {}
    if enabled is not None:
        updates["email_alerts_enabled"] = bool(enabled)
    if alert_email is not None:
        candidate = str(alert_email).strip().lower()
        if candidate and "@" not in candidate:
            return {"success": False, "message": "Invalid alert email"}
        updates["alert_email"] = candidate or email
    if narrative_email_frequency is not None:
        frequency = _normalize_narrative_email_frequency(narrative_email_frequency)
        if frequency not in NARRATIVE_EMAIL_FREQUENCIES:
            return {"success": False, "message": "Invalid narrative email frequency"}
        updates["narrative_email_frequency"] = frequency
    if trade_alert_horizon is not None:
        horizon = _normalize_trade_horizon(trade_alert_horizon)
        if horizon not in TRADE_HORIZON_OPTIONS:
            return {"success": False, "message": "Invalid trade alert horizon"}
        updates["trade_alert_horizon"] = horizon
    if scanner_trade_horizon is not None:
        horizon = _normalize_trade_horizon(scanner_trade_horizon)
        if horizon not in TRADE_HORIZON_OPTIONS:
            return {"success": False, "message": "Invalid scanner trade horizon"}
        updates["scanner_trade_horizon"] = horizon
    if watch_mail_optin is not None:
        # AUDIT H-3: explizites Opt-in/Opt-out fuer Watch-Mails.
        updates["watch_mail_optin"] = bool(watch_mail_optin)
    if penny_show_watch_rows is not None:
        updates["penny_show_watch_rows"] = bool(penny_show_watch_rows)

    user = _update_user_atomic(email, lambda current: current.update(updates))
    if not isinstance(user, dict):
        return {"success": False, "message": "User not found"}
    return {"success": True, "settings": get_user_alert_settings(token)}


def auth_security_status() -> Dict[str, Any]:
    """Commercial-readiness snapshot for auth/billing storage and secrets."""
    warnings = []
    critical = []
    stripe_mode = "not_configured"
    auth_db_operational = False

    def _looks_placeholder(value: Any) -> bool:
        normalized = str(value or "").strip().lower()
        return not normalized or any(
            marker in normalized
            for marker in ("replace", "example", "your_", "your-", "changeme", "placeholder")
        )

    jwt_secret_is_default = _jwt_secret_is_rejected()
    if jwt_secret_is_default:
        critical.append("JWT_SECRET uses fallback demo value")
    elif JWT_SECRET_IS_EPHEMERAL:
        critical.append("JWT_SECRET not configured; ephemeral secret invalidates all sessions on restart")
    if not ADMIN_MASTER_KEY_CONFIGURED:
        warnings.append("ADMIN_MASTER_KEY not configured; admin bypass disabled")
    if ALLOW_LEGACY_ADMIN_MASTER_KEY:
        critical.append("Legacy admin bootstrap key is enabled; set ALLOW_LEGACY_ADMIN_MASTER_KEY=0 before commercial launch")
    if not AUTH_DB_IS_SQLITE:
        critical.append("AUTH_DB_PATH still points to JSON; use SQLite for production")
    else:
        try:
            with _sqlite_conn() as conn:
                conn.execute("SELECT 1").fetchone()
            auth_db_operational = True
        except Exception as exc:
            critical.append(f"SQLite auth store is not operational: {exc}")
    if not HAS_STRIPE:
        critical.append("Stripe package is not installed")
    if STRIPE_SECRET_KEY.startswith("sk_live_"):
        stripe_mode = "live"
    elif STRIPE_SECRET_KEY.startswith("sk_test_"):
        stripe_mode = "test"
        warnings.append("STRIPE_SECRET_KEY is a test key; paid launch needs live Stripe keys")
        critical.append("STRIPE_SECRET_KEY is not a live key")
    elif STRIPE_SECRET_KEY:
        stripe_mode = "unknown"
        warnings.append("STRIPE_SECRET_KEY is configured but does not look like a standard Stripe key")
        critical.append("STRIPE_SECRET_KEY format is not recognized as a live key")
    else:
        warnings.append("STRIPE_SECRET_KEY not configured")
        critical.append("STRIPE_SECRET_KEY not configured")
    if _looks_placeholder(STRIPE_WEBHOOK_SECRET) or not STRIPE_WEBHOOK_SECRET.startswith("whsec_"):
        warnings.append("STRIPE_WEBHOOK_SECRET not configured")
        critical.append("STRIPE_WEBHOOK_SECRET is missing or invalid")
    inherited_default_prices = sorted(
        plan for plan, default_id in _DEFAULT_STRIPE_PRICE_IDS.items()
        if STRIPE_PRICE_IDS.get(plan) == default_id
    )
    invalid_price_ids = sorted(
        plan
        for plan, price_id in STRIPE_PRICE_IDS.items()
        if _looks_placeholder(price_id) or not str(price_id).startswith("price_")
    )
    if inherited_default_prices:
        warnings.append(
            "Stripe price IDs use repository defaults; verify they are your live products before launch"
        )
        critical.append("Stripe price IDs still use repository defaults")
    if invalid_price_ids:
        critical.append("Stripe price IDs are missing or placeholders: " + ", ".join(invalid_price_ids))
    stripe_catalog_verified = False
    stripe_catalog_errors: List[str] = []
    expected_catalog = {
        "trial": {"amount": 100, "recurring": False},
        "basic_monthly": {"amount": 2900, "recurring": True},
        "pro_monthly": {"amount": 7900, "recurring": True},
        "elite_monthly": {"amount": 14900, "recurring": True},
    }

    def _stripe_value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    if (
        HAS_STRIPE
        and stripe_mode == "live"
        and not inherited_default_prices
        and not invalid_price_ids
    ):
        for catalog_key, expected in expected_catalog.items():
            try:
                price = stripe.Price.retrieve(STRIPE_PRICE_IDS[catalog_key])
                amount = _stripe_value(price, "unit_amount")
                currency = str(_stripe_value(price, "currency", "")).lower()
                active = bool(_stripe_value(price, "active", False))
                recurring = _stripe_value(price, "recurring")
                interval = _stripe_value(recurring, "interval") if recurring else None
                if amount != expected["amount"]:
                    stripe_catalog_errors.append(
                        f"{catalog_key} amount is {amount}, expected {expected['amount']} cents"
                    )
                if currency != "usd":
                    stripe_catalog_errors.append(
                        f"{catalog_key} currency is {currency or 'missing'}, expected usd"
                    )
                if not active:
                    stripe_catalog_errors.append(f"{catalog_key} Stripe price is inactive")
                if expected["recurring"] and interval != "month":
                    stripe_catalog_errors.append(
                        f"{catalog_key} interval is {interval or 'one-time'}, expected month"
                    )
                if not expected["recurring"] and recurring:
                    stripe_catalog_errors.append(f"{catalog_key} must be a one-time price")
            except Exception as exc:
                stripe_catalog_errors.append(f"{catalog_key} could not be verified: {exc}")
        stripe_catalog_verified = not stripe_catalog_errors
        if stripe_catalog_errors:
            critical.append("Stripe catalog verification failed")
    elif stripe_mode == "live":
        critical.append("Stripe catalog could not be verified until all live price IDs are configured")
    return {
        "auth_db_type": "sqlite" if AUTH_DB_IS_SQLITE else "json",
        "auth_db_operational": auth_db_operational,
        "auth_db_persistent": AUTH_DB_IS_SQLITE,
        "jwt_secret_configured": bool(JWT_SECRET) and not jwt_secret_is_default and not JWT_SECRET_IS_EPHEMERAL,
        "jwt_secret_ephemeral": JWT_SECRET_IS_EPHEMERAL,
        "admin_master_key_configured": ADMIN_MASTER_KEY_CONFIGURED,
        "legacy_admin_bootstrap_enabled": ALLOW_LEGACY_ADMIN_MASTER_KEY,
        "stripe_secret_configured": bool(STRIPE_SECRET_KEY),
        "stripe_key_mode": stripe_mode,
        "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET)
        and STRIPE_WEBHOOK_SECRET.startswith("whsec_")
        and not _looks_placeholder(STRIPE_WEBHOOK_SECRET),
        "stripe_default_price_ids": inherited_default_prices,
        "stripe_invalid_price_ids": invalid_price_ids,
        "stripe_catalog_verified": stripe_catalog_verified,
        "stripe_catalog_errors": stripe_catalog_errors,
        "warnings": warnings,
        "critical": critical,
        "commercial_ready": not critical,
    }


# ── S-6 AUDIT FIX: Fail-closed Boot-Gate für den kommerziellen Betrieb ──
def enforce_commercial_boot_security() -> None:
    """Wirft RuntimeError, wenn COMMERCE_ENFORCE_AUTH=1 und die Auth-Konfiguration
    unsicher ist (fehlender/Default-JWT-Secret oder aktiver Legacy-Master-Key).

    Wird am Modul-Ende automatisch aufgerufen, sobald COMMERCE_ENFORCE_AUTH=1
    gesetzt ist — wirkt damit ohne Aenderung an api.py (fail-closed statt
    fail-open).
    """
    if os.environ.get("COMMERCE_ENFORCE_AUTH", "0").strip() != "1":
        return
    problems = []
    if JWT_SECRET_IS_EPHEMERAL:
        problems.append("JWT_SECRET fehlt (ephemerer Zufallswert aktiv)")
    if _jwt_secret_is_rejected():
        problems.append("JWT_SECRET nutzt den unsicheren Repository-Default")
    if ALLOW_LEGACY_ADMIN_MASTER_KEY:
        problems.append("Legacy-Admin-Master-Key ist aktiv (ALLOW_LEGACY_ADMIN_MASTER_KEY=0 setzen)")
    if problems:
        raise RuntimeError(
            "COMMERCE_ENFORCE_AUTH=1: Boot abgebrochen wegen unsicherer Auth-Konfiguration: "
            + "; ".join(problems)
        )


if os.environ.get("COMMERCE_ENFORCE_AUTH", "0").strip() == "1":
    enforce_commercial_boot_security()
