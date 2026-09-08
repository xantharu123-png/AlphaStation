"""Opt-in HTTP pacing for the bounded Crypto Explosion workers.

No orders, retries or credentials. Other callers retain their existing request
behaviour. Venue cooldowns are shared across scans in this API process, not
across other services/IP users; this is not a global exchange quota guarantee.
"""

from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import math
import threading
import time
from urllib.parse import urlsplit


class ScanRequestError(RuntimeError):
    def __init__(self, reason, host):
        self.reason = reason
        super().__init__(f"crypto scan {reason}: {host}")


_LOCAL = threading.local()
_STATES_LOCK = threading.Lock()
_STATES = {}
MIN_REQUEST_GAP_SECONDS = 0.25


@contextmanager
def paced_scan_requests():
    previous = getattr(_LOCAL, "paced", False)
    _LOCAL.paced = True
    try:
        yield
    finally:
        _LOCAL.paced = previous


def _positive_seconds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(headers, default):
    """Never shorten a server-specified Retry-After, including HTTP dates."""
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = headers.get("retry-after")
    delay = _positive_seconds(raw)
    if delay is None and raw:
        try:
            delay = max(0.0, parsedate_to_datetime(str(raw)).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            pass
    reset_ms = _positive_seconds(headers.get("x-bapi-limit-reset-timestamp"))
    reset_delay = max(0.0, reset_ms / 1000 - time.time()) if reset_ms is not None else 0.0
    return max(default, delay or 0.0, reset_delay)


def scan_http_get(getter, url, **kwargs):
    """Pace real HTTP requests (not just candidates); stop on provider limits.

    A blocked venue fails fast until its cooldown expires. The orchestrator
    stops that venue's remaining batch and never publishes it as a full scan.
    Exceptions contain only the host/reason, never query strings or keys.
    """
    if not getattr(_LOCAL, "paced", False):
        return getter(url, **kwargs)
    host = (urlsplit(url).hostname or "unknown").lower()
    with _STATES_LOCK:
        state = _STATES.setdefault(host, {"lock": threading.Lock(), "next": 0.0, "blocked": 0.0})
    with state["lock"]:
        now = time.monotonic()
        if state["blocked"] > now:
            raise ScanRequestError("rate_limit", host)
        delay = state["next"] - now
        if delay > 0:
            time.sleep(delay)
        state["next"] = time.monotonic() + MIN_REQUEST_GAP_SECONDS
        try:
            response = getter(url, **kwargs)
        except Exception as exc:
            raise ScanRequestError("transport_error", host) from exc
        status = response.status_code
        # Bybit asks for at least ten minutes after an IP/WAF block. Use
        # the same conservative floor for 403/418, respecting longer headers.
        if status in (403, 418, 429):
            floor = 600.0 if status in (403, 418) else 60.0
            state["blocked"] = time.monotonic() + _retry_delay(getattr(response, "headers", {}), floor)
            raise ScanRequestError("rate_limit", host)
        if status != 200:
            raise ScanRequestError(f"http_{status}", host)
        try:
            payload = response.json()
        except Exception as exc:
            raise ScanRequestError("invalid_json", host) from exc
        if isinstance(payload, dict) and (
            str(payload.get("retCode")) == "10006"
            or str(payload.get("code")) in {"-1003", "429"}
        ):
            state["blocked"] = time.monotonic() + _retry_delay(getattr(response, "headers", {}), 60.0)
            raise ScanRequestError("rate_limit", host)
        return response
