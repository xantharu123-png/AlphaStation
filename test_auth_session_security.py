import asyncio
import json
from datetime import timedelta
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import api
import modules.auth as auth


def _isolate_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setattr(auth, "AUTH_DB_IS_SQLITE", True)
    monkeypatch.setattr(
        auth, "AUTH_DB_LEGACY_JSON_PATH", str(tmp_path / "missing-legacy.json")
    )
    monkeypatch.setattr(auth, "HAS_JWT", True)
    monkeypatch.setattr(auth, "JWT_SECRET", "unit-test-jwt-secret-not-for-production")


async def _asgi_request(method, path, payload=None, cookie=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(body)).encode("ascii")))
    if cookie:
        headers.append((b"cookie", cookie.encode("latin-1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    request_sent = False
    messages = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await api.app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = [
        (key.decode("latin-1"), value.decode("latin-1"))
        for key, value in start.get("headers", [])
    ]
    return start["status"], response_headers, json.loads(response_body or b"{}")


def _response_cookie(headers, name):
    jar = SimpleCookie()
    for key, value in headers:
        if key.lower() == "set-cookie":
            jar.load(value)
    return jar.get(name)


def test_password_reset_is_one_time_and_revokes_old_sessions(monkeypatch, tmp_path):
    _isolate_auth(monkeypatch, tmp_path)
    registered = auth.register_user(
        "reset@example.com", "old-secret-123", "Reset User"
    )
    old_token = registered["token"]
    assert auth.verify_token(old_token)

    request = auth.create_password_reset_request("reset@example.com")
    reset_token = request["reset_token"]
    assert reset_token not in str(auth._load_users())

    result = auth.confirm_password_reset(reset_token, "new-secret-456")

    assert result["success"] is True
    assert auth.verify_token(old_token) is None
    assert auth.login_user("reset@example.com", "old-secret-123")["success"] is False
    assert auth.login_user("reset@example.com", "new-secret-456")["success"] is True
    assert auth.confirm_password_reset(reset_token, "another-secret-789")["success"] is False


def test_expired_password_reset_is_rejected(monkeypatch, tmp_path):
    _isolate_auth(monkeypatch, tmp_path)
    assert auth.register_user(
        "expired@example.com", "old-secret-123", "Expired User"
    )["success"] is True
    request = auth.create_password_reset_request("expired@example.com")

    with auth._sqlite_conn() as conn:
        conn.execute(
            "UPDATE password_reset_tokens SET expires_at = ?",
            ((auth._utc_now() - timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()

    result = auth.confirm_password_reset(request["reset_token"], "new-secret-456")

    assert result["success"] is False
    assert auth.login_user("expired@example.com", "old-secret-123")["success"] is True


def test_change_password_and_logout_invalidate_the_expected_tokens(monkeypatch, tmp_path):
    _isolate_auth(monkeypatch, tmp_path)
    registered = auth.register_user(
        "session@example.com", "old-secret-123", "Session User"
    )
    old_token = registered["token"]

    assert auth.change_password(old_token, "wrong-secret", "new-secret-456")["success"] is False
    changed = auth.change_password(old_token, "old-secret-123", "new-secret-456")
    fresh_token = changed["token"]

    assert changed["success"] is True
    assert auth.verify_token(old_token) is None
    assert auth.verify_token(fresh_token)
    assert auth.revoke_token(fresh_token)["success"] is True
    assert auth.verify_token(fresh_token) is None


def test_reset_api_does_not_expose_account_existence_or_token(monkeypatch):
    class Tasks:
        def __init__(self):
            self.calls = []

        def add_task(self, func, *args, **kwargs):
            self.calls.append((func, args, kwargs))

    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "_RESET_ATTEMPTS", {})
    monkeypatch.setattr(api, "_RESET_IP_ATTEMPTS", {})
    tasks = Tasks()
    http_request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))

    monkeypatch.setattr(
        api,
        "create_password_reset_request",
        lambda email: {
            "success": True,
            "delivery_email": "known@example.com",
            "reset_token": "internal-one-time-secret",
        },
    )
    known = asyncio.run(
        api.api_password_reset_request(
            api.PasswordResetRequest(email="known@example.com"), tasks, http_request
        )
    )
    monkeypatch.setattr(
        api,
        "create_password_reset_request",
        lambda email: {"success": True, "message": "generic"},
    )
    missing = asyncio.run(
        api.api_password_reset_request(
            api.PasswordResetRequest(email="missing@example.com"), tasks, http_request
        )
    )

    assert known == missing
    assert "internal-one-time-secret" not in repr(known)
    assert "known@example.com" not in repr(known)
    assert len(tasks.calls) == 1


def test_reset_throttle_caps_cross_account_requests_per_ip(monkeypatch):
    monkeypatch.setattr(api, "_RESET_ATTEMPTS", {})
    monkeypatch.setattr(api, "_RESET_IP_ATTEMPTS", {})

    for index in range(api._RESET_THROTTLE_MAX_IP_ATTEMPTS):
        assert api._password_reset_throttle_allows(
            f"user-{index}@example.com", "203.0.113.20", now=1000.0 + index
        ) is True

    assert api._password_reset_throttle_allows(
        "another@example.com", "203.0.113.20", now=1015.0
    ) is False
    assert api._password_reset_throttle_allows(
        "another@example.com", "203.0.113.21", now=1015.0
    ) is True


def test_reset_routes_are_public_but_session_mutations_are_protected():
    assert "/api/auth/password-reset/request" in api._PUBLIC_API_PATHS
    assert "/api/auth/password-reset/confirm" in api._PUBLIC_API_PATHS
    assert "/api/auth/change-password" not in api._PUBLIC_API_PATHS
    assert "/api/auth/logout" not in api._PUBLIC_API_PATHS


def test_frontend_exposes_reset_change_password_and_revoking_logout():
    source = (Path(__file__).parent / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "/api/auth/password-reset/request" in source
    assert "/api/auth/password-reset/confirm" in source
    assert "/api/auth/change-password" in source
    assert "rawFetch(`${API}/api/auth/logout`" in source
    assert "credentials: 'include'" in source
    assert "localStorage.getItem('as_token')" not in source
    assert "localStorage.setItem('as_token'" not in source


def test_commercial_session_cookie_is_httponly_secure_and_lax(monkeypatch):
    monkeypatch.setattr(api, "COMMERCIAL_STRICT_MODE", True)
    response = api.Response()

    api._set_auth_session_cookie(response, "unit-session-token")

    cookie = response.headers.get("set-cookie", "")
    assert "as_session=unit-session-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


def test_auth_cookie_is_promoted_into_existing_bearer_path():
    request = api.Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/auth/me",
            "raw_path": b"/api/auth/me",
            "query_string": b"",
            "headers": [(b"cookie", b"as_session=cookie-token")],
            "client": ("127.0.0.1", 12345),
            "server": ("app.example.com", 443),
        }
    )

    api._promote_auth_cookie_to_bearer(request)

    assert request.headers.get("authorization") == "Bearer cookie-token"


def test_explicit_bearer_header_has_priority_over_cookie():
    request = api.Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/auth/me",
            "raw_path": b"/api/auth/me",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer api-client-token"),
                (b"cookie", b"as_session=browser-token"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("app.example.com", 443),
        }
    )

    api._promote_auth_cookie_to_bearer(request)

    assert request.headers.get("authorization") == "Bearer api-client-token"


def test_logout_expires_browser_session_cookie(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "revoke_token", lambda token: {"success": True})
    response = api.Response()

    result = asyncio.run(
        api.api_logout(authorization="Bearer unit-token", response=response)
    )

    cookie = response.headers.get("set-cookie", "")
    assert result["success"] is True
    assert "as_session=" in cookie
    assert "Max-Age=0" in cookie
    assert "HttpOnly" in cookie


def test_browser_cookie_session_register_me_logout_round_trip(monkeypatch, tmp_path):
    _isolate_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCIAL_STRICT_MODE", False)
    monkeypatch.setattr(api, "PUBLIC_APP_URL", "")
    monkeypatch.setattr(api, "_REGISTER_ATTEMPTS", {})

    async def exercise_browser_session():
        status, headers, _ = await _asgi_request(
            "POST",
            "/api/auth/register",
            {
                "email": "cookie-session@example.com",
                "password": "secure-cookie-pass-123",
                "name": "Cookie Session",
            },
        )
        session = _response_cookie(headers, "as_session")
        assert status == 200
        assert session and session.value
        cookie_header = f"as_session={session.value}"

        status, _, profile = await _asgi_request(
            "GET", "/api/auth/me", cookie=cookie_header
        )
        assert status == 200
        assert profile["user"]["email"] == "cookie-session@example.com"

        status, headers, _ = await _asgi_request(
            "POST", "/api/auth/logout", cookie=cookie_header
        )
        cleared = _response_cookie(headers, "as_session")
        assert status == 200
        assert cleared is not None
        assert cleared["max-age"] == "0"

        status, _, _ = await _asgi_request(
            "GET", "/api/auth/me", cookie=cookie_header
        )
        assert status == 401

    asyncio.run(exercise_browser_session())
