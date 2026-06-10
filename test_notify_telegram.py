#!/usr/bin/env python3
"""Pytest-Suite fuer modules/notify_telegram.py — komplett ohne Netz-Calls
(requests.post wird pro Test gemockt)."""
import os
import sys

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import modules.notify_telegram as nt


class _FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


@pytest.fixture()
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")


@pytest.fixture()
def post_recorder(monkeypatch):
    """Zeichnet alle requests.post-Aufrufe auf und antwortet mit HTTP 200."""
    calls = []

    def fake_post(url, json=None, data=None, timeout=None, **kwargs):
        calls.append({"url": url, "payload": json if json is not None else data, "timeout": timeout})
        return _FakeResponse(200)

    monkeypatch.setattr(nt.requests, "post", fake_post)
    return calls


def test_is_telegram_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert nt.is_telegram_configured() is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert nt.is_telegram_configured() is False   # Chat-ID fehlt noch
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "   ")  # nur Whitespace zaehlt nicht
    assert nt.is_telegram_configured() is False
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    assert nt.is_telegram_configured() is True


def test_unconfigured_returns_false_without_request(monkeypatch, post_recorder):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert nt.send_telegram_alert("Alert", "Body") is False
    assert post_recorder == []  # es wurde KEIN HTTP-Request abgesetzt


def test_send_success(telegram_env, post_recorder):
    assert nt.send_telegram_alert("Breakout: NVDA", "Entry 120") is True
    assert len(post_recorder) == 1
    call = post_recorder[0]
    assert call["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert call["timeout"] == 10
    payload = call["payload"]
    assert payload["chat_id"] == "424242"
    assert payload["parse_mode"] == "HTML"
    assert payload["disable_web_page_preview"] is True
    assert payload["text"] == "<b>Breakout: NVDA</b>\nEntry 120"


def test_explicit_token_beats_env(monkeypatch, post_recorder):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert nt.send_telegram_alert("Hi", "x", token="999:XYZ", chat_id="-100777") is True
    call = post_recorder[0]
    assert call["url"] == "https://api.telegram.org/bot999:XYZ/sendMessage"
    assert call["payload"]["chat_id"] == "-100777"


def test_http_error_returns_false(telegram_env, monkeypatch):
    monkeypatch.setattr(nt.requests, "post", lambda *a, **k: _FakeResponse(400, "Bad Request"))
    assert nt.send_telegram_alert("Alert", "Body") is False


def test_exception_returns_false(telegram_env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("connection reset by bot123:ABC endpoint")

    monkeypatch.setattr(nt.requests, "post", boom)
    assert nt.send_telegram_alert("Alert", "Body") is False


def test_html_escaping(telegram_env, post_recorder):
    nt.send_telegram_alert('<script>alert("x")</script>', "Risk < 1% & TP > 2R")
    text = post_recorder[0]["payload"]["text"]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "Risk &lt; 1% &amp; TP &gt; 2R" in text
    # einziges echtes Tag-Paar bleibt unser <b>...</b>
    assert text.startswith("<b>") and "</b>" in text


def test_truncation_to_4096(telegram_env, post_recorder):
    nt.send_telegram_alert("Langer Alert", "X" * 9000)
    text = post_recorder[0]["payload"]["text"]
    assert len(text) <= 4096
    assert text.endswith("…")
    assert text.startswith("<b>Langer Alert</b>\n")
    # Monster-Subject: <b>-Paar bleibt balanciert, Limit haelt
    nt.send_telegram_alert("S" * 6000, "Body")
    text2 = post_recorder[1]["payload"]["text"]
    assert len(text2) <= 4096
    assert text2.count("<b>") == 1 and text2.count("</b>") == 1
    assert text2.endswith("…</b>")


def test_formatter_rounding_and_aliases():
    rows = [
        {"Ticker": "nvda", "Entry": 123.456, "StopLoss": 119.0, "TP1": 130.0, "TP2": 140.119},
        {"symbol": "PEPE", "direction": "short", "entry": 0.00012345678,
         "stop_loss": 0.00013, "tp1": 0.00011, "tp2": 0.0001},
    ]
    out = nt.format_alert_rows_for_telegram(rows)
    lines = out.split("\n")
    # >= 1: zwei Dezimalstellen
    assert lines[0] == "NVDA LONG | Entry 123.46 | Stop 119.00 | TP1 130.00 | TP2 140.12"
    # < 1: sechs signifikante Stellen, keine Scientific-Notation
    assert lines[1].startswith("PEPE SHORT | Entry 0.000123457 | ")
    assert "Stop 0.00013" in lines[1]
    assert "e-" not in lines[1].lower()  # nirgends Scientific-Notation '1.23e-04'


def test_formatter_max_rows_and_missing_fields():
    rows = [{"Ticker": "T%d" % i, "Entry": 10.0 + i, "StopLoss": 9.0} for i in range(7)]
    out = nt.format_alert_rows_for_telegram(rows, max_rows=5)
    lines = out.split("\n")
    assert len(lines) == 6  # 5 Rows + Hinweiszeile
    assert lines[0] == "T0 LONG | Entry 10.00 | Stop 9.00 | TP1 - | TP2 -"
    assert lines[-1] == "… +2 weitere"
    assert nt.format_alert_rows_for_telegram([]) == ""
    assert nt.format_alert_rows_for_telegram(None) == ""
    assert nt.format_alert_rows_for_telegram([{"foo": 1}]) == ""  # ohne Ticker keine Zeile


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
