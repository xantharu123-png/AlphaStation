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
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
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
    HAS_STRIPE = False
    print("[Auth] WARNING: stripe not installed — run: pip install stripe")

# ── Config ──
AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", "/tmp/alpha_station_users.json")
JWT_SECRET = os.environ.get("JWT_SECRET", "as_jwt_2026_alpha_station_prod_key_x9k2m")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72  # Token valid for 3 days

# Stripe config (set via environment variables)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_IDS = {
    "basic_monthly": os.environ.get("STRIPE_PRICE_BASIC", "price_1THqyWEOIB5wAqvUrLNLCPZD"),
    "pro_monthly": os.environ.get("STRIPE_PRICE_PRO", "price_1THqysEOIB5wAqvU6MG9iywG"),
    "elite_monthly": os.environ.get("STRIPE_PRICE_ELITE", "price_1THqzjEOIB5wAqvUTrVwLzha"),
}

if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── Plan Definitions ──
PLANS = {
    "free": {
        "name": "Free Trial",
        "price": 0,
        "max_scanner_tabs": 2,
        "scan_interval_min": 60,
        "has_sidebar_detail": False,
        "has_email_alerts": False,
        "has_trade_setups": False,
        "has_orb_scanner": False,
        "has_backtest": False,
        "has_api_access": False,
        "max_ticker_detail_per_hour": 10,
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
        "max_ticker_detail_per_hour": 999,
    },
}

# Allowed scanner tabs per plan
SCANNER_TABS_BY_PLAN = {
    "free": ["scanner", "short-scanner"],
    "basic": ["scanner", "short-scanner", "bi-scanner", "crash-monitor"],
    "pro": None,   # None = all tabs
    "elite": None,  # None = all tabs
}


# ── User Database (JSON file-based, simple for now) ──
def _load_users() -> Dict:
    """Load user database from JSON file."""
    if os.path.exists(AUTH_DB_PATH):
        try:
            with open(AUTH_DB_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {"users": {}}
    return {"users": {}}


def _save_users(db: Dict):
    """Save user database to JSON file."""
    try:
        with open(AUTH_DB_PATH, "w") as f:
            json.dump(db, f, indent=2, default=str)
    except Exception as e:
        print(f"[Auth] Error saving users: {e}")


def _hash_password(password: str) -> str:
    """Hash password with salt using SHA-256."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, hashed = stored_hash.split(":")
        return hashlib.sha256((salt + password).encode()).hexdigest() == hashed
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
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


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
    if len(password) < 6:
        return {"success": False, "message": "Passwort muss mindestens 6 Zeichen haben"}

    db = _load_users()
    if email in db["users"]:
        return {"success": False, "message": "Email bereits registriert"}

    user_id = secrets.token_hex(8)
    user = {
        "id": user_id,
        "email": email,
        "name": name or email.split("@")[0],
        "password_hash": _hash_password(password),
        "plan": "free",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "created_at": datetime.utcnow().isoformat(),
        "last_login": datetime.utcnow().isoformat(),
        "trial_ends_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    }

    db["users"][email] = user
    _save_users(db)

    token = create_token(user_id, email, "free")
    return {
        "success": True,
        "message": "Account erstellt",
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": user["name"],
            "plan": "free",
            "trial_ends_at": user["trial_ends_at"],
        },
    }


def login_user(email: str, password: str) -> Dict[str, Any]:
    """Login user. Returns {success, message, token?, user?}."""
    email = email.strip().lower()
    db = _load_users()

    user = db["users"].get(email)
    if not user:
        return {"success": False, "message": "Email oder Passwort falsch"}

    if not _verify_password(password, user["password_hash"]):
        return {"success": False, "message": "Email oder Passwort falsch"}

    # Update last login
    user["last_login"] = datetime.utcnow().isoformat()
    db["users"][email] = user
    _save_users(db)

    plan = user.get("plan", "free")
    token = create_token(user["id"], email, plan)

    return {
        "success": True,
        "message": "Login erfolgreich",
        "token": token,
        "user": {
            "id": user["id"],
            "email": email,
            "name": user.get("name", ""),
            "plan": plan,
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

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"email": email, "plan": plan},
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

    db = _load_users()

    if event_type == "checkout.session.completed":
        email = data.get("metadata", {}).get("email", "")
        plan = data.get("metadata", {}).get("plan", "pro")
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")

        if email and email in db["users"]:
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
                    db["users"][email]["plan"] = "free"
                    print(f"[Auth] Subscription ended: {email} → free")
                _save_users(db)
                break

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        for email, user in db["users"].items():
            if user.get("stripe_customer_id") == customer_id:
                db["users"][email]["plan"] = "free"
                db["users"][email]["stripe_subscription_id"] = None
                _save_users(db)
                print(f"[Auth] Subscription deleted: {email} → free")
                break

    return {"success": True, "event_type": event_type}


# ── Feature Gating ──
def get_user_plan(token: str) -> str:
    """Get user's plan from token. Returns plan name or 'free'."""
    payload = verify_token(token)
    if not payload:
        return "free"
    email = payload.get("email", "")
    # Always check DB for latest plan (in case webhook updated it)
    db = _load_users()
    user = db["users"].get(email)
    if user:
        return user.get("plan", "free")
    return payload.get("plan", "free")


def get_plan_features(plan: str) -> Dict:
    """Get feature set for a plan."""
    return PLANS.get(plan, PLANS["free"])


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
    return {
        "plan": plan,
        "plan_name": features["name"],
        "price": features["price"],
        "allowed_tabs": allowed_tabs,  # None = all
        "scan_interval_min": features["scan_interval_min"],
        "has_sidebar_detail": features["has_sidebar_detail"],
        "has_email_alerts": features["has_email_alerts"],
        "has_trade_setups": features["has_trade_setups"],
        "has_orb_scanner": features["has_orb_scanner"],
        "has_backtest": features["has_backtest"],
        "has_api_access": features["has_api_access"],
        "max_ticker_detail_per_hour": features["max_ticker_detail_per_hour"],
    }
