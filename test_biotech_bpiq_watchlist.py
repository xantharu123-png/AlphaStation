import json

from api import _sanitize_biotech_public_results
import modules.data_fetchers as df


def test_bpiq_watchlist_filters_late_stage_rows(monkeypatch):
    sample_cache = {
        "AAAA": [
            {
                "company_name": "Alpha Therapeutics",
                "drug_name": "Alpha",
                "stage_label": "Phase 3",
                "event_label": "Topline readout",
                "full_label": "Phase 3 topline data",
                "catalyst_date": "2026-05-20",
                "catalyst_date_text": "Q2 2026",
                "days_until": 10,
                "category": "IMMINENT",
                "phase_mult": 3.0,
                "bpiq_score": 90,
                "indications": "Oncology",
                "source": "BPIQ",
                "is_new": True,
            }
        ],
        "BBBB": [
            {
                "drug_name": "Beta",
                "stage_label": "Phase 1",
                "event_label": "Safety update",
                "full_label": "Phase 1 safety update",
                "catalyst_date": "2026-05-25",
                "catalyst_date_text": "Q2 2026",
                "days_until": 15,
                "category": "IMMINENT",
                "phase_mult": 1.0,
                "bpiq_score": 40,
                "indications": "Rare disease",
                "source": "BPIQ",
            }
        ],
        "CCCC": [
            {
                "drug_name": "Gamma",
                "stage_label": "Phase 2",
                "event_label": "Readout",
                "full_label": "Phase 2 readout",
                "catalyst_date": "2026-12-01",
                "catalyst_date_text": "H2 2026",
                "days_until": 220,
                "category": "LATER",
                "phase_mult": 2.0,
                "bpiq_score": 80,
                "indications": "Immunology",
                "source": "BPIQ",
            }
        ],
    }
    monkeypatch.setattr(df, "_load_bpiq_catalyst_cache", lambda: sample_cache)
    monkeypatch.setattr(df, "_BPIQ_CATALYST_STATUS", {
        "status": "success",
        "http_status": 200,
        "error": None,
        "rows_loaded": 3,
        "ticker_count": 3,
        "timestamp": "2026-05-03T00:00:00",
    })

    result = df.get_bpiq_catalyst_watchlist(limit=85, window_days=60)

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["data"][0]["ticker"] == "AAAA"
    assert result["data"][0]["company_name"] == "Alpha Therapeutics"
    assert result["data"][0]["category"] == "IMMINENT"
    assert result["data"][0]["source"] == "Premium catalyst calendar"
    assert result["data"][0]["catalyst_score"] == 90
    assert "bpiq_score" not in result["data"][0]
    assert result["summary"]["new_catalysts"] == 1
    assert result["summary"]["total_catalysts"] == 1
    assert "BPIQ" not in json.dumps(result)


def test_premium_catalyst_tickers_seed_scanner_universe(monkeypatch):
    sample_cache = {
        "SEED": [
            {
                "stage_label": "Phase 3",
                "event_label": "Topline readout",
                "full_label": "Phase 3 topline data",
                "days_until": 14,
                "category": "IMMINENT",
                "phase_mult": 3.0,
            }
        ],
        "PHASE1": [
            {
                "stage_label": "Phase 1",
                "event_label": "Safety update",
                "full_label": "Phase 1 safety update",
                "days_until": 14,
                "category": "IMMINENT",
                "phase_mult": 1.0,
            }
        ],
        "FAR": [
            {
                "stage_label": "Phase 2",
                "event_label": "Readout",
                "full_label": "Phase 2 readout",
                "days_until": 180,
                "category": "LATER",
                "phase_mult": 2.0,
            }
        ],
    }
    monkeypatch.setattr(df, "_load_bpiq_catalyst_cache", lambda: sample_cache)

    tickers = df.get_premium_catalyst_tickers(window_days=90)

    assert tickers == {"SEED"}


def test_bpiq_cache_paginates_past_legacy_800_row_limit(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self, results):
            self._results = results

        def json(self):
            return {"results": self._results}

    offsets = []

    def fake_get(url, headers=None, timeout=None):
        offset = int(url.split("offset=", 1)[1])
        offsets.append(offset)
        size = 200 if offset < 800 else 50
        rows = []
        for i in range(size):
            idx = offset + i
            rows.append({
                "ticker": f"T{idx}",
                "drug_name": "Alpha",
                "stage_event": {"stage_label": "Phase 3", "event_label": "Readout", "label": "Phase 3 readout", "score": 80},
                "catalyst_date": "2026-05-20",
                "catalyst_date_text": "May 20, 2026",
            })
        return FakeResponse(rows)

    monkeypatch.setattr(df, "_get_config_value", lambda key: "test-key")
    monkeypatch.setattr(df, "rate_limited_get", fake_get)
    monkeypatch.setattr(df, "_BPIQ_CATALYST_CACHE", {})
    monkeypatch.setattr(df, "_BPIQ_CACHE_TIMESTAMP", 0)

    cache = df._load_bpiq_catalyst_cache()

    assert offsets == [0, 200, 400, 600, 800]
    assert len(cache) == 850
    assert "T849" in cache


def test_bpiq_watchlist_surfaces_api_warning(monkeypatch):
    monkeypatch.setattr(df, "_load_bpiq_catalyst_cache", lambda: {})
    monkeypatch.setattr(df, "_BPIQ_CATALYST_STATUS", {
        "status": "warning",
        "http_status": 401,
        "error": "BPIQ returned HTTP 401",
        "rows_loaded": 0,
        "ticker_count": 0,
        "timestamp": "2026-05-03T00:00:00",
    })

    result = df.get_bpiq_catalyst_watchlist()

    assert result["status"] == "warning"
    assert result["count"] == 0
    assert result["provider_status"]["http_status"] == 401
    assert "autorisiert" in result["warning"]
    public_payload = json.dumps(result)
    assert "BPIQ" not in public_payload
    assert "bpiq_status" not in result


def test_bpiq_watchlist_warns_on_partial_api_rows(monkeypatch):
    monkeypatch.setattr(df, "_load_bpiq_catalyst_cache", lambda: {
        "AAAA": [
            {
                "company_name": "Alpha Therapeutics",
                "drug_name": "Alpha",
                "stage_label": "Phase 3",
                "event_label": "Topline readout",
                "full_label": "Phase 3 topline data",
                "catalyst_date": "2026-05-20",
                "catalyst_date_text": "Q2 2026",
                "days_until": 10,
                "category": "IMMINENT",
                "phase_mult": 3.0,
                "bpiq_score": 90,
                "indications": "Oncology",
                "source": "BPIQ",
            }
        ]
    })
    monkeypatch.setattr(df, "_BPIQ_CATALYST_STATUS", {
        "status": "warning",
        "http_status": 429,
        "error": "BPIQ returned HTTP 429",
        "rows_loaded": 200,
        "ticker_count": 120,
        "timestamp": "2026-05-03T00:00:00",
    })

    result = df.get_bpiq_catalyst_watchlist(limit=85, window_days=60)

    assert result["count"] == 1
    assert result["status"] == "warning"
    assert "limitiert" in result["warning"]


def test_biotech_public_results_hide_provider_event_fields():
    rows = [{
        "ticker": "TEST",
        "bpiq_available": True,
        "bpiq_catalysts": [{"source": "BPIQ", "bpiq_score": 55}],
        "catalyst_events": [{"source": "BPIQ", "bpiq_score": 88, "drug_name": "Alpha"}],
        "readout_details": [{"source": "BPIQ", "bpiq_score": 77, "drug_name": "Beta"}],
    }]

    sanitized = _sanitize_biotech_public_results(rows)
    payload = json.dumps(sanitized)

    assert "BPIQ" not in payload
    assert "bpiq" not in payload.lower()
    assert sanitized[0]["catalyst_events"][0]["catalyst_score"] == 88
    assert sanitized[0]["readout_details"][0]["source"] == "Premium catalyst calendar"
