import api


def _row(name, ticker, c1, c5, c20, rvol=1.0, cmf=0.0, obv=0.0):
    return {
        "sector": name,
        "narrative": name,
        "ticker": ticker,
        "change_1d": c1,
        "change_5d": c5,
        "change_20d": c20,
        "rvol": rvol,
        "cmf": cmf,
        "obv_change": obv,
        "examples": ["AAA", "BBB", "CCC"],
    }


def test_narrative_pulse_sorts_bullish_and_bearish(monkeypatch):
    monkeypatch.setattr(api, "_narrative_representatives", lambda tickers, direction, max_items=3: [])
    payload = api._build_narrative_pulse([
        _row("Semiconductors", "SMH", 2.0, 8.0, 15.0, rvol=1.6, cmf=0.2, obv=12),
        _row("Regional Banks", "KRE", -1.5, -7.0, -12.0, rvol=1.4, cmf=-0.2, obv=-14),
        _row("Utilities", "XLU", 0.1, 0.4, 1.0, rvol=0.8),
    ])

    assert payload["bullish"][0]["sector"] == "Semiconductors"
    assert payload["bearish"][0]["sector"] == "Regional Banks"
    assert payload["bullish"][0]["bias"] == "BULLISCH"
    assert payload["bearish"][0]["bias"] == "BEARISCH"


def test_narrative_pulse_email_is_daily_and_does_not_use_etf_word(monkeypatch):
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_narrative_pulse_recipients", lambda frequency: ["narrative@example.com"] if frequency == "daily" else [])
    sent = {}

    def fake_send(subject, body, *args, **kwargs):
        sent["subject"] = subject
        sent["body"] = body
        sent["recipients"] = kwargs.get("recipient_emails")
        return True

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload) is True
    assert "Narrative Pulse" in sent["subject"]
    assert "Semiconductors" in sent["body"]
    assert "Regional Banks" in sent["body"]
    assert "ETF" not in sent["body"].upper()
    assert sent["recipients"] == ["narrative@example.com"]


def test_narrative_pulse_respects_frequency_recipients(monkeypatch):
    claimed = []
    sent = []

    def fake_claim(key, ttl, now=None):
        claimed.append((key, ttl))
        return True

    def fake_recipients(frequency):
        return ["two@example.com"] if frequency == "twice_daily" else []

    def fake_send(subject, body, *args, **kwargs):
        sent.append((subject, kwargs.get("recipient_emails")))
        return True

    monkeypatch.setattr(api, "_email_dedupe_claim", fake_claim)
    monkeypatch.setattr(api, "_narrative_pulse_recipients", fake_recipients)
    monkeypatch.setattr(api, "_send_email_alert", fake_send)

    payload = {
        "bullish": [_row("Semiconductors", "SMH", 2.0, 8.0, 15.0)],
        "bearish": [_row("Regional Banks", "KRE", -1.5, -7.0, -12.0)],
    }

    assert api._send_narrative_pulse_email(payload) is True
    assert len(sent) == 1
    assert "2x taeglich" in sent[0][0]
    assert sent[0][1] == ["two@example.com"]
    assert any(key.startswith("narrative_pulse_twice_daily_") for key, _ttl in claimed)
