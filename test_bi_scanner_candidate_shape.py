from datetime import datetime, timedelta

import modules.scanners as scanners
import modules.bi_trade_plan as bi_plan


class _ContractResult(tuple):
    def __new__(cls, values):
        obj = super().__new__(cls, values)
        obj.indicator_checks = [
            {
                "id": idx,
                "key": f"indicator_{idx:02d}",
                "name": f"Indicator {idx:02d}",
                "available": True,
                "passed": idx <= 17,
                "points": 1 if idx <= 17 else 0,
                "max_points": 1,
                "reason": "test fixture",
            }
            for idx in range(1, 21)
        ]
        obj.green_count = 17
        obj.available_count = 20
        obj.required_green = 17
        obj.indicator_contract_ok = True
        obj.hard_gate_failures = []
        obj.weighted_score_pct = 63.8
        obj.contract_version = scanners.BI_STOCK_CONTRACT_VERSION
        return obj


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
    # This tests candidate shape, not whether these synthetic bars offer a
    # confirmed structural target. Plan/barrier integrity has its own tests.
    monkeypatch.setattr(bi_plan, "build_vrvp_structure", lambda *a, **k: None)
    monkeypatch.setattr(bi_plan, "apply_vrvp_to_trade_setup", lambda plan, *a, **k: plan)
    monkeypatch.setattr(
        scanners,
        "analyze_breakout_imminent",
        lambda bars, direction: _ContractResult(
            (True, 120, 188, ["ok"], 85.0, "A", 4, 4)
        ),
    )
    monkeypatch.setattr(scanners, "calculate_short_bonus_signals", lambda *args, **kwargs: {"bonus_score": 0, "details": []})
    monkeypatch.setattr(scanners, "_detect_chart_patterns", lambda *args, **kwargs: [])
    def fake_cache_save(results, direction, **kwargs):
        saved.update({"results": results, "direction": direction, "meta": kwargs})

    monkeypatch.setattr(scanners, "_bi_cache_save", fake_cache_save)
    monkeypatch.setattr(scanners, "_bi_progress_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: False)
    monkeypatch.setattr(scanners, "_bi_clear_stop", lambda direction: None)

    scanners._bi_background_scan("test-key", direction="short", candidates=["TEST"])

    assert saved["direction"] == "short"
    assert saved["meta"]["partial"] is False
    assert saved["results"]
    assert saved["results"][0]["Ticker"] == "TEST"
    assert saved["results"][0]["above_sma20_pct"] is not None
    assert saved["results"][0]["BI_IndicatorsGreen"] == 17
    assert saved["results"][0]["BI_IndicatorsTotal"] == 20
