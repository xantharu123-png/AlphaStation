"""
Alpha Station — Authentication & Subscription System
V3.4: JWT Auth + Stripe Subscription + Tier-based Feature Gating

Plans:
  - basic ($29/mo): 3 scanner tabs, scan every 30min, no sidebar detail, no email alerts
  - pro ($79/mo): All scanners, real-time scans, full sidebar, email alerts, trade setups
  - elite ($149/mo): Everything + priority scans, ORB scanner, backtest, API access
"""

import os
import json
import time
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
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

_JWT_DEFAULT_SECRET = "as_jwt_2026_alpha_station_prod_key_x9k2m"
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
JWT_SECRET_IS_DEFAULT = JWT_SECRET == _JWT_DEFAULT_SECRET
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
_COMPROMISED_MASTER_KEYS = {"AlphaStation2026!"}
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
NARRATIVE_EMAIL_FREQUENCIES = {"off", "daily", "twice_daily", "weekly"}
TRADE_HORIZON_OPTIONS = {"swing", "intraday", "both"}

# Stripe config (set via environment variables)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_IDS = {
    "trial": os.environ.get("STRIPE_PRICE_TRIAL", "price_1TI0SHEOIB5wAqvU3oFEI079"),  # $1 one-time payment
    "basic_monthly": os.environ.get("STRIPE_PRICE_BASIC", "price_1THqyWEOIB5wAqvUrLNLCPZD"),
    "pro_monthly": os.environ.get("STRIPE_PRICE_PRO", "price_1THqysEOIB5wAqvU6MG9iywG"),
    "elite_monthly": os.environ.get("STRIPE_PRICE_ELITE", "price_1THqzjEOIB5wAqvUTrVwLzha"),
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
        "max_scanner_tabs": 4,
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

# Allowed scanner tabs per plan
SCANNER_TABS_BY_PLAN = {
    "trial": None,  # Full access during trial
    "expired": [],  # No access after trial
    "basic": ["scanner", "short-scanner", "bi-scanner", "crash-monitor", "chart-analyse"],
    "pro": ["scanner", "short-scanner", "bi-scanner", "crash-monitor", "chart-analyse", "biotech", "btc-divergenz", "early-movers", "crypto-signals", "crypto-explosion", "money-flow", "kalender", "watchlist", "strategie-guide", "new-listing", "volume-spikes", "penny-stocks"],
    "elite": None,  # None = all tabs (inkl. autotrader, orb, backtest)
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
    """Persist full user database."""
    if not AUTH_DB_IS_SQLITE:
        try:
            Path(AUTH_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(AUTH_DB_PATH, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=2, default=str)
        except Exception as e:
            print(f"[Auth] Error saving users: {e}")
        return

    try:
        users = db.get("users", {}) if isinstance(db, dict) else {}
        now = _utc_iso()
        with _sqlite_conn() as conn:
            existing = {row["email"] for row in conn.execute("SELECT email FROM users").fetchall()}
            incoming = set(users.keys())
            for email in sorted(existing - incoming):
                conn.execute("DELETE FROM users WHERE email = ?", (email,))
            for email, user in users.items():
                conn.execute(
                    "INSERT OR REPLACE INTO users(email, data, updated_at) VALUES (?, ?, ?)",
                    (email, json.dumps(user, default=str), now),
                )
            conn.commit()
    except Exception as e:
        print(f"[Auth] Error saving users: {e}")


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


# ── JWT Token Management ──
def create_token(user_id: str, email: str, plan: str = "free") -> Optional[str]:
    """Create JWT token for authenticated user."""
    if not HAS_JWT:
        return None
    payload = {
        "sub": user_id,
        "email": email,
        "plan": plan,
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
        and ADMIN_MASTER_KEY not in _COMPROMISED_MASTER_KEYS
        and hmac.compare_digest(password.encode("utf-8"), ADMIN_MASTER_KEY.encode("utf-8"))
    ):
        return True
    return bool(
        ALLOW_LEGACY_ADMIN_MASTER_KEY
        and LEGACY_ADMIN_MASTER_KEY
        and LEGACY_ADMIN_MASTER_KEY not in _COMPROMISED_MASTER_KEYS
        and hmac.compare_digest(password.encode("utf-8"), LEGACY_ADMIN_MASTER_KEY.encode("utf-8"))
    )


def verify_token(token: str) -> Optional[Dict]:
    """Verify and decode JWT token. Returns payload or None."""
    if not HAS_JWT:
        return None
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


# ── User Registration & Login ──
def register_user(email: str, password: str, name: str = "") -> Dict[str, Any]:
    """Register a new user. Returns {success, message, token?, user?}."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return {"success": False, "message": "Ungültige Email-Adresse"}
    # S-6 AUDIT FIX: Mindestlaenge 10 statt 6 (Commercial-Launch-Anforderung)
    if len(password) < 10:
        return {"success": False, "message": "Passwort muss mindestens 10 Zeichen haben"}

    db = _load_users()
    if email in db["users"]:
        return {"success": False, "message": "Email bereits registriert"}

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
    }

    db["users"][email] = user
    _save_users(db)

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
        }
        db["users"][email] = user
        _save_users(db)

    if not user:
        return {"success": False, "message": "Email oder Passwort falsch"}

    # Admin Master-Key bypass is disabled unless ADMIN_MASTER_KEY is explicitly set.
    is_admin_login = _is_admin_master_login(email, password)
    if not is_admin_login and not _verify_password(password, user["password_hash"]):
        return {"success": False, "message": "Email oder Passwort falsch"}

    # Transparently upgrade legacy SHA-256 password hashes after a successful login.
    if not is_admin_login and not str(user.get("password_hash", "")).startswith("pbkdf2_sha256$"):
        user["password_hash"] = _hash_password(password)

    # Update last login + admin always gets elite
    user["last_login"] = _utc_iso()
    if email in ADMIN_EMAILS:
        user["plan"] = "elite"
    db["users"][email] = user
    _save_users(db)

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
        },
    }


# ── Stripe Checkout ──
def create_checkout_session(email: str, plan: str, success_url: str, cancel_url: str) -> Optional[str]:
    """Create Stripe Checkout Session. Returns checkout URL or None."""
    if not HAS_STRIPE or not STRIPE_SECRET_KEY:
        print("[Auth] Stripe not configured")
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
            )
            customer_id = customer.id
            user["stripe_customer_id"] = customer_id
            db["users"][email] = user
            _save_users(db)

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
        session = stripe.checkout.Session.create(**session_params)
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


def _webhook_events_file() -> Path:
    """Persistenter Event-ID-Store neben der Auth-DB (folgt AUTH_DB_PATH)."""
    return Path(AUTH_DB_PATH).parent / "stripe_webhook_events.json"


def _load_processed_webhook_events() -> List[str]:
    try:
        data = json.loads(_webhook_events_file().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    return []


def _remember_webhook_event(event_id: str) -> None:
    if not event_id:
        return
    events = _load_processed_webhook_events()
    events.append(str(event_id))
    if len(events) > _WEBHOOK_EVENT_LIMIT:
        # Rotation: nur die juengsten ~5000 IDs behalten
        events = events[-_WEBHOOK_EVENT_LIMIT:]
    try:
        path = _webhook_events_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(events), encoding="utf-8")
    except Exception as exc:
        print(f"[Auth] Webhook-Event-Store konnte nicht geschrieben werden: {exc}")


def handle_stripe_webhook(payload: bytes, sig_header: str) -> Dict[str, Any]:
    """Handle Stripe webhook events. Returns {success, event_type}."""
    if not HAS_STRIPE or not STRIPE_WEBHOOK_SECRET:
        return {"success": False, "error": "Stripe not configured"}

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return {"success": False, "error": str(e)}

    event_type = event["type"]
    data = event["data"]["object"]

    # S-6 AUDIT FIX: Bereits verarbeitete Event-IDs idempotent mit 200 quittieren
    # (Stripe retried Webhooks aggressiv; doppelte Plan-Updates vermeiden).
    try:
        event_id = str(event.get("id", "") if isinstance(event, dict) else event["id"])
    except Exception:
        event_id = ""
    if event_id and event_id in set(_load_processed_webhook_events()):
        return {"success": True, "event_type": event_type, "duplicate": True}

    db = _load_users()

    if event_type == "checkout.session.completed":
        email = data.get("metadata", {}).get("email", "")
        plan = data.get("metadata", {}).get("plan", "pro")
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")

        if email and email in db["users"]:
            if plan == "trial":
                # $1 Trial — activate 24h full access
                db["users"][email]["plan"] = "trial"
                db["users"][email]["trial_ends_at"] = (_utc_now() + timedelta(hours=24)).isoformat()
                db["users"][email]["stripe_customer_id"] = customer_id
                _save_users(db)
                print(f"[Auth] Trial activated: {email} → 24h until {db['users'][email]['trial_ends_at']}")
            else:
                db["users"][email]["plan"] = plan
                db["users"][email]["stripe_subscription_id"] = subscription_id
                db["users"][email]["stripe_customer_id"] = customer_id
                _save_users(db)
                print(f"[Auth] Subscription activated: {email} → {plan}")

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        status = data.get("status")
        # Find user by customer ID
        for email, user in db["users"].items():
            if user.get("stripe_customer_id") == customer_id:
                if status == "active":
                    # Check which price → which plan
                    items = data.get("items", {}).get("data", [])
                    if items:
                        price_id = items[0].get("price", {}).get("id")
                        for plan_key, pid in STRIPE_PRICE_IDS.items():
                            if pid == price_id:
                                new_plan = plan_key.replace("_monthly", "")
                                db["users"][email]["plan"] = new_plan
                                print(f"[Auth] Plan updated: {email} → {new_plan}")
                elif status in ("canceled", "unpaid", "past_due"):
                    db["users"][email]["plan"] = "expired"
                    print(f"[Auth] Subscription ended: {email} → expired")
                _save_users(db)
                break

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        for email, user in db["users"].items():
            if user.get("stripe_customer_id") == customer_id:
                db["users"][email]["plan"] = "expired"
                db["users"][email]["stripe_subscription_id"] = None
                _save_users(db)
                print(f"[Auth] Subscription deleted: {email} → expired")
                break

    # S-6 AUDIT FIX: Event-ID erst NACH erfolgreicher Verarbeitung persistieren
    _remember_webhook_event(event_id)

    return {"success": True, "event_type": event_type}


# ── Feature Gating ──
def get_user_plan(token: str) -> str:
    """Get user's plan from token. Checks trial expiry."""
    payload = verify_token(token)
    if not payload:
        return "expired"
    email = payload.get("email", "")
    if email in ADMIN_EMAILS:
        return "elite"
    db = _load_users()
    user = db["users"].get(email)
    if not user:
        return "expired"
    plan = user.get("plan", "expired")
    # Check if trial has expired
    if plan == "trial":
        trial_ends = user.get("trial_ends_at")
        if trial_ends:
            try:
                end_dt = _parse_utc_datetime(trial_ends)
                if _utc_now() > end_dt:
                    # Trial expired — update DB
                    user["plan"] = "expired"
                    _save_users(db)
                    return "expired"
            except (ValueError, TypeError):
                pass
    return plan


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
    db = _load_users()
    changed = False
    alert_type = str(alert_type or "").strip().lower()
    frequency = _normalize_narrative_email_frequency(frequency) if frequency else ""
    trade_horizon = _normalize_trade_horizon(trade_horizon) if trade_horizon else ""
    mail_class = str(mail_class or "trade").strip().lower()
    for email, user in db.get("users", {}).items():
        if not isinstance(user, dict):
            continue
        plan = user.get("plan", "expired")
        if plan == "trial":
            trial_ends = user.get("trial_ends_at")
            try:
                if trial_ends and _utc_now() > _parse_utc_datetime(trial_ends):
                    user["plan"] = "expired"
                    changed = True
                    continue
            except Exception:
                pass
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
    if changed:
        _save_users(db)
    return sorted(set(recipients))


def get_user_alert_settings(token: str) -> Dict[str, Any]:
    payload = verify_token(token)
    if not payload:
        return {}
    email = payload.get("email", "")
    user = _load_users().get("users", {}).get(email, {})
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
        "has_email_alerts": get_plan_features(get_user_plan(token)).get("has_email_alerts", False),
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
    email = payload.get("email", "")
    db = _load_users()
    user = db.get("users", {}).get(email)
    if not user:
        return {"success": False, "message": "User not found"}
    if enabled is not None:
        user["email_alerts_enabled"] = bool(enabled)
    if alert_email is not None:
        candidate = str(alert_email).strip().lower()
        if candidate and "@" not in candidate:
            return {"success": False, "message": "Invalid alert email"}
        user["alert_email"] = candidate or email
    if narrative_email_frequency is not None:
        frequency = _normalize_narrative_email_frequency(narrative_email_frequency)
        if frequency not in NARRATIVE_EMAIL_FREQUENCIES:
            return {"success": False, "message": "Invalid narrative email frequency"}
        user["narrative_email_frequency"] = frequency
    if trade_alert_horizon is not None:
        horizon = _normalize_trade_horizon(trade_alert_horizon)
        if horizon not in TRADE_HORIZON_OPTIONS:
            return {"success": False, "message": "Invalid trade alert horizon"}
        user["trade_alert_horizon"] = horizon
    if scanner_trade_horizon is not None:
        horizon = _normalize_trade_horizon(scanner_trade_horizon)
        if horizon not in TRADE_HORIZON_OPTIONS:
            return {"success": False, "message": "Invalid scanner trade horizon"}
        user["scanner_trade_horizon"] = horizon
    if watch_mail_optin is not None:
        # AUDIT H-3: explizites Opt-in/Opt-out fuer Watch-Mails.
        user["watch_mail_optin"] = bool(watch_mail_optin)
    if penny_show_watch_rows is not None:
        user["penny_show_watch_rows"] = bool(penny_show_watch_rows)
    db["users"][email] = user
    _save_users(db)
    return {"success": True, "settings": get_user_alert_settings(token)}


def auth_security_status() -> Dict[str, Any]:
    """Commercial-readiness snapshot for auth/billing storage and secrets."""
    warnings = []
    critical = []
    stripe_mode = "not_configured"
    if JWT_SECRET_IS_DEFAULT:
        critical.append("JWT_SECRET uses fallback demo value")
    elif JWT_SECRET_IS_EPHEMERAL:
        critical.append("JWT_SECRET not configured; ephemeral secret invalidates all sessions on restart")
    if not ADMIN_MASTER_KEY_CONFIGURED:
        warnings.append("ADMIN_MASTER_KEY not configured; admin bypass disabled")
    if ALLOW_LEGACY_ADMIN_MASTER_KEY:
        critical.append("Legacy admin bootstrap key is enabled; set ALLOW_LEGACY_ADMIN_MASTER_KEY=0 before commercial launch")
    if not AUTH_DB_IS_SQLITE:
        critical.append("AUTH_DB_PATH still points to JSON; use SQLite for production")
    if STRIPE_SECRET_KEY.startswith("sk_live_"):
        stripe_mode = "live"
    elif STRIPE_SECRET_KEY.startswith("sk_test_"):
        stripe_mode = "test"
        warnings.append("STRIPE_SECRET_KEY is a test key; paid launch needs live Stripe keys")
    elif STRIPE_SECRET_KEY:
        stripe_mode = "unknown"
        warnings.append("STRIPE_SECRET_KEY is configured but does not look like a standard Stripe key")
    else:
        warnings.append("STRIPE_SECRET_KEY not configured")
    if not STRIPE_WEBHOOK_SECRET:
        warnings.append("STRIPE_WEBHOOK_SECRET not configured")
    default_price_ids = {
        "trial": "price_1TI0SHEOIB5wAqvU3oFEI079",
        "basic_monthly": "price_1THqyWEOIB5wAqvUrLNLCPZD",
        "pro_monthly": "price_1THqysEOIB5wAqvU6MG9iywG",
        "elite_monthly": "price_1THqzjEOIB5wAqvUTrVwLzha",
    }
    inherited_default_prices = sorted(
        plan for plan, default_id in default_price_ids.items()
        if STRIPE_PRICE_IDS.get(plan) == default_id
    )
    if inherited_default_prices:
        warnings.append(
            "Stripe price IDs use repository defaults; verify they are your live products before launch"
        )
    return {
        "auth_db_path": AUTH_DB_PATH,
        "auth_db_type": "sqlite" if AUTH_DB_IS_SQLITE else "json",
        "jwt_secret_configured": bool(JWT_SECRET) and not JWT_SECRET_IS_DEFAULT and not JWT_SECRET_IS_EPHEMERAL,
        "jwt_secret_ephemeral": JWT_SECRET_IS_EPHEMERAL,
        "admin_master_key_configured": ADMIN_MASTER_KEY_CONFIGURED,
        "legacy_admin_bootstrap_enabled": ALLOW_LEGACY_ADMIN_MASTER_KEY,
        "stripe_secret_configured": bool(STRIPE_SECRET_KEY),
        "stripe_key_mode": stripe_mode,
        "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
        "stripe_default_price_ids": inherited_default_prices,
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
    if JWT_SECRET == _JWT_DEFAULT_SECRET:
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
