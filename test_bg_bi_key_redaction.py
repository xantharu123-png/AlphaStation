"""Regression tests for Polygon-key hygiene in both BG BI entry points."""

import json
from pathlib import Path

import pytest
import requests

import bg_service
import api
from modules import data_fetchers, scanners


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_bg_bi_pagination_and_failures_never_expose_polygon_key(
    monkeypatch, tmp_path, caplog, capsys
):
    canary = "POLYGON_CANARY_7f19"
    calls = []
    statuses = []

    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: _Response({
            "tickers": [{
                "ticker": "SAFE",
                "lastTrade": {"p": 10.0},
                "day": {"v": 100_000},
                "prevDay": {"v": 90_000, "c": 9.9},
                "todaysChangePerc": 1.0,
            }]
        }),
    )

    def _reference_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if len(calls) == 1:
            return _Response({
                "results": [{"ticker": "SAFE"}],
                "next_url": "https://api.polygon.io/v3/reference/tickers?cursor=2",
            })
        raise RuntimeError(f"provider failed {url}&apiKey={canary}")

    monkeypatch.setattr(data_fetchers, "rate_limited_get", _reference_get)
    monkeypatch.setattr(
        bg_service,
        "_SCAN_CACHE_MAP",
        {**bg_service._SCAN_CACHE_MAP, "bi_long": str(tmp_path / "bi.json")},
    )
    monkeypatch.setattr(bg_service, "_ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(bg_service, "_clear_scan_cache", lambda *args: None)
    monkeypatch.setattr(
        bg_service, "_scanner_cache_snapshot", lambda *args: (None, None)
    )
    monkeypatch.setattr(
        bg_service,
        "_bg_run_bi_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(f"downstream?apiKey={canary}")
        ),
    )
    monkeypatch.setattr(
        bg_service,
        "_update_status",
        lambda name, status, detail="": statuses.append((name, status, detail)),
    )

    with pytest.raises(RuntimeError) as exc_info:
        bg_service._run_bi_scanner(canary, direction="long")

    combined = "\n".join([
        str(exc_info.value),
        caplog.text,
        capsys.readouterr().out,
        json.dumps(statuses),
    ])
    assert len(calls) == 2
    assert all(canary not in url for url, _params in calls)
    assert calls[1][1] == {"apiKey": canary}
    assert canary not in combined
    assert "apiKey=<redacted>" in combined


def test_bi_progress_paths_share_runtime_temp_directory():
    expected = str(Path(bg_service._ALPHA_RUNTIME_TMP_DIR) / "bi_scan_progress_long.json")
    assert bg_service._bi_progress_file("long") == expected
    assert scanners._bi_progress_path("long") == expected
    assert api._SCAN_PROGRESS_MAP["bi_long"] == expected


def test_scanner_bi_universe_progress_redacts_polygon_key(
    monkeypatch, capsys
):
    canary = "SCANNER_CANARY_4a2d"
    calls = []
    progress = []

    def _get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if len(calls) == 1:
            return _Response({
                "results": [{"ticker": "SAFE"}],
                "next_url": "https://api.polygon.io/v3/reference/tickers?cursor=2",
            })
        raise RuntimeError(f"provider failed {url}&apiKey={canary}")

    monkeypatch.setattr(scanners, "rate_limited_get", _get)
    monkeypatch.setattr(
        scanners,
        "_bi_progress_write",
        lambda direction, status, **kwargs: progress.append(
            (direction, status, kwargs.get("detail", ""))
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        scanners._bi_background_scan(canary, direction="long", candidates=None)

    combined = "\n".join([
        str(exc_info.value),
        capsys.readouterr().out,
        json.dumps(progress),
    ])
    assert len(calls) == 2
    assert all(canary not in url for url, _params in calls)
    assert calls[1][1] == {"apiKey": canary}
    assert canary not in combined
