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
    assert result["summary"]["new_catalysts"] == 1
    assert result["summary"]["total_catalysts"] == 1


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
    assert result["bpiq_status"]["http_status"] == 401
    assert "401" in result["warning"]
