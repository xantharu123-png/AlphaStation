from datetime import datetime, timedelta

import modules.scanners as scanners


def _bars(days=40):
    start = datetime(2026, 1, 1)
    data = []
    price = 20.0
    for idx in range(days):
        price += 0.05
        data.append({
            "t": int((start + timedelta(days=idx)).timestamp() * 1000),
            "o": price,
            "h": price + 0.4,
            "l": price - 0.4,
            "c": price,
            "v": 250_000,
        })
    return data


def test_bi_short_accepts_string_candidates_before_enrichment(monkeypatch):
    saved = {}

    class Response:
        status_code = 200

        def json(self):
            return {"results": _bars()}

    monkeypatch.setattr(scanners, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        scanners,
        "analyze_breakout_imminent",
        lambda bars, direction: (True, 120, 188, ["ok"], "high", "A", 4, 4),
    )
    monkeypatch.setattr(scanners, "calculate_short_bonus_signals", lambda *args, **kwargs: {"bonus_score": 0, "details": []})
    monkeypatch.setattr(scanners, "_detect_chart_patterns", lambda *args, **kwargs: [])
    monkeypatch.setattr(scanners, "_bi_cache_save", lambda results, direction: saved.update({"results": results, "direction": direction}))
    monkeypatch.setattr(scanners, "_bi_progress_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: False)
    monkeypatch.setattr(scanners, "_bi_clear_stop", lambda direction: None)

    scanners._bi_background_scan("test-key", direction="short", candidates=["TEST"])

    assert saved["direction"] == "short"
    assert saved["results"]
    assert saved["results"][0]["Ticker"] == "TEST"
    assert saved["results"][0]["above_sma20_pct"] is not None
