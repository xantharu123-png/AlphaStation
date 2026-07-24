import api


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_ai_analysis_provider_401_returns_clear_fallback(monkeypatch):
    monkeypatch.setattr(api, "ANTHROPIC_API_KEY", "expired-test-key")
    monkeypatch.setattr(api, "POLYGON_KEY", "polygon-test-key")

    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "results": [
                    {"c": 10.0, "h": 10.2, "l": 9.8, "v": 100000},
                    {"c": 9.7, "h": 9.9, "l": 9.5, "v": 90000},
                ]
                + [{"c": 9.5, "h": 10.0, "l": 9.0, "v": 80000} for _ in range(20)]
            },
        ),
    )
    monkeypatch.setattr(api.req, "post", lambda *args, **kwargs: _FakeResponse(401, {}))

    result = api.get_ai_analysis("TEST", authorization=None)

    assert result["error"] == "ai_provider_auth_failed"
    assert result["provider_status"] == 401
    assert result["fallback"] is True
    assert "API Fehler: 401" not in result["analysis"]
    assert "KI-Provider lehnt den Server-Key ab" in result["analysis"]
    assert "Regelbasierter Schnellcheck" in result["analysis"]


def test_ai_analysis_provider_unreachable_hides_exception_details(monkeypatch):
    """AUDIT 2026-07-24 (T3): Der Exception-Pfad darf keine internen Details
    (Hostnamen, URLs, Proxy-Adressen) an den Client leaken."""
    monkeypatch.setattr(api, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "POLYGON_KEY", "polygon-test-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {
                "results": [{"c": 10.0, "h": 10.2, "l": 9.8, "v": 100000}]
                + [{"c": 9.5, "h": 10.0, "l": 9.0, "v": 80000} for _ in range(21)]
            },
        ),
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("Connection to http://internal-proxy.local:3128 refused")

    monkeypatch.setattr(api.req, "post", _boom)

    result = api.get_ai_analysis("TEST", authorization=None)

    assert result["error"] == "ai_provider_unreachable"
    assert result["fallback"] is True
    assert "internal-proxy" not in result["analysis"]
    assert "3128" not in result["analysis"]
    assert "nicht erreichbar" in result["analysis"]
