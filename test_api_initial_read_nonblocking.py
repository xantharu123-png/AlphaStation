"""Offline barriers: slow auth reads must not freeze unrelated API reads."""
import asyncio
import contextvars
import threading

import pytest

import api
from modules import auth


_REQUEST_CONTEXT = contextvars.ContextVar("initial_read_probe", default="missing")


def _request(path, *, method="GET", host="203.0.113.10"):
    return api.Request({
        "type": "http", "http_version": "1.1", "method": method,
        "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"authorization", b"Bearer offline-token")],
        "client": (host, 4321), "server": ("fixture.invalid", 443),
    })


def _assert_health_can_run_while_blocked(reader, blocked_call):
    """No timing threshold: health must release the barrier before its timeout."""
    entered, release = threading.Event(), threading.Event()
    observations = []
    unspecified_readonly = object()

    def blocking(*args, read_only=unspecified_readonly, **kwargs):
        entered.set()
        released_by_health = release.wait(timeout=0.5)
        observations.append((released_by_health, _REQUEST_CONTEXT.get(), threading.get_ident()))
        if read_only is not unspecified_readonly:
            kwargs["read_only"] = read_only
        return blocked_call(*args, **kwargs)

    async def next_handler(request):
        return api.JSONResponse({"path": request.url.path})

    async def exercise():
        token = _REQUEST_CONTEXT.set("request-context")
        loop_thread = threading.get_ident()
        task = asyncio.create_task(reader(blocking, next_handler))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set(), "slow auth stub was never entered"
            health = await api.commerce_auth_gate(_request("/api/health"), next_handler)
            assert health.status_code == 200
            release.set()
            result = await task
            assert observations
            assert all(released for released, _, _ in observations), "auth I/O blocked the ASGI event loop"
            assert all(context == "request-context" for _, context, _ in observations)
            assert all(thread != loop_thread for _, _, thread in observations)
            return result
        finally:
            release.set()
            if not task.done():
                await task
            _REQUEST_CONTEXT.reset(token)

    return asyncio.run(exercise())


@pytest.mark.parametrize("path", [
    "/api/scan-status", "/api/scan-results", "/api/system-health",
    "/api/email-alert-audit", "/api/commercial-readiness",
])
def test_middleware_verification_does_not_block_health(monkeypatch, path):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    monkeypatch.setattr(api, "ADMIN_EMAILS", {"admin@example.invalid"})
    monkeypatch.setattr(api, "_commerce_gate_denial", lambda *args: None)
    calls = []

    def verify(token, *, read_only=False):
        calls.append((token, read_only))
        return {"email": "admin@example.invalid"}

    async def reader(blocking, next_handler):
        monkeypatch.setattr(api, "verify_token", blocking)
        return await api.commerce_auth_gate(_request(path), next_handler)

    result = _assert_health_can_run_while_blocked(reader, verify)
    assert result.status_code == 200
    assert calls == [("offline-token", path == "/api/email-alert-audit")]


def test_commerce_plan_gate_does_not_block_health(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "customer@example.invalid"})

    async def reader(blocking, next_handler):
        monkeypatch.setattr(api, "_commerce_gate_denial", blocking)
        return await api.commerce_auth_gate(_request("/api/scan-results"), next_handler)

    denied = api.JSONResponse({"detail": "Plan upgrade required"}, status_code=403)
    assert _assert_health_can_run_while_blocked(reader, lambda *args: denied) is denied


@pytest.mark.parametrize("slow_stage", ["verify_token", "get_user_limits", "_load_users"])
def test_auth_me_reads_do_not_block_health(monkeypatch, slow_stage):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    values = {
        "verify_token": {"email": "customer@example.invalid", "sub": "offline-user"},
        "get_user_limits": {"plan": "basic", "is_admin": False},
        "_load_users": {"users": {"customer@example.invalid": {"name": "Offline User"}}},
    }
    monkeypatch.setattr(api, "verify_token", lambda *args: values["verify_token"])
    monkeypatch.setattr(api, "get_user_limits", lambda *args: values["get_user_limits"])
    monkeypatch.setattr(auth, "_load_users", lambda: values["_load_users"])

    async def reader(blocking, next_handler):
        target = auth if slow_stage == "_load_users" else api
        monkeypatch.setattr(target, slow_stage, blocking)
        return await api.api_get_me("Bearer offline-token")

    result = _assert_health_can_run_while_blocked(reader, lambda *args: values[slow_stage])
    assert result["user"]["email"] == "customer@example.invalid"
    assert result["user"]["name"] == "Offline User"
    assert result["limits"] == values["get_user_limits"]


@pytest.mark.parametrize("endpoint, accessor", [
    ("api_get_alert_settings", "get_user_alert_settings"),
    ("api_get_personal_positions", "get_user_personal_positions"),
])
@pytest.mark.parametrize("slow_verification", [True, False])
def test_initial_account_reads_do_not_block_health(monkeypatch, endpoint, accessor, slow_verification):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(api, "COMMERCE_ENFORCE_AUTH", True)
    monkeypatch.setattr(api, "verify_token", lambda token: {"email": "customer@example.invalid"})
    monkeypatch.setattr(api, accessor, lambda token: {"success": True, "offline": True})

    async def reader(blocking, next_handler):
        monkeypatch.setattr(api, "verify_token" if slow_verification else accessor, blocking)
        return await getattr(api, endpoint)("Bearer offline-token")

    response = {"email": "customer@example.invalid"} if slow_verification else {"success": True, "offline": True}
    result = _assert_health_can_run_while_blocked(reader, lambda token: response)
    assert result == {"success": True, "offline": True}
