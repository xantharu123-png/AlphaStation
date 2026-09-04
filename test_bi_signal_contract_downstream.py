import json

import pytest

import api


def _bi_row(ticker="VALID", *, green=17, available=20, contract_ok=True):
    checks = []
    for index in range(1, 21):
        checks.append({
            "id": index,
            "key": f"signal_{index}",
            "name": f"Signal {index}",
            "available": index <= available,
            "passed": index <= green,
            "points": 1 if index <= green else 0,
            "max_points": 1,
            "reason": "unit",
        })
    return {
        "Ticker": ticker,
        "BI_Grade": "S",
        "BI_Score": 95,
        "BI_IndicatorChecks": checks,
        "BI_IndicatorsGreen": green,
        "BI_IndicatorsAvailable": available,
        "BI_IndicatorsTotal": 20,
        "BI_IndicatorsRequired": 17,
        "BI_IndicatorContractOK": contract_ok,
        "BI_IndicatorContractVersion": api._BI_INDICATOR_CONTRACT_VERSION,
    }


def _patch_result_decoration(monkeypatch):
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *_a, **_k: rows)
    monkeypatch.setattr(api, "_apply_signal_only_policy", lambda _scanner, rows: rows)
    monkeypatch.setattr(
        api,
        "_scan_quality_payload",
        lambda *_a, **_k: {
            "data_source": "unit",
            "warnings": [],
            "exclusion_policy": [],
        },
    )


def test_bi_row_contract_accepts_exact_complete_17_of_20():
    row = _bi_row()

    assert api._bi_row_meets_signal_contract(row) is True
    normalized = api._normalize_keys([row], api._BI_KEY_MAP)[0]
    assert api._bi_row_meets_signal_contract(normalized) is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.pop("BI_IndicatorsGreen"),
        lambda row: row.update(BI_IndicatorsGreen=16),
        lambda row: row.update(BI_IndicatorsAvailable=19),
        lambda row: row.update(BI_IndicatorsTotal=19),
        lambda row: row.update(BI_IndicatorsRequired=16),
        lambda row: row.update(BI_IndicatorContractOK=False),
        lambda row: row.update(BI_IndicatorContractVersion="legacy-score-only"),
        lambda row: row.update(BI_IndicatorContractVersion="stock-bi-20-v1"),
        lambda row: row["BI_IndicatorChecks"].pop(),
        lambda row: row["BI_IndicatorChecks"][0].update(passed=False),
        lambda row: row["BI_IndicatorChecks"][1].update(id=1),
        lambda row: row["BI_IndicatorChecks"][1].update(key="signal_1"),
    ],
)
def test_bi_row_contract_rejects_legacy_incomplete_or_inconsistent_rows(mutation):
    row = _bi_row()
    mutation(row)

    assert api._bi_row_meets_signal_contract(row) is False


def test_bi_results_endpoint_drops_legacy_rows_before_decoration(monkeypatch):
    legacy = {"Ticker": "LEGACY", "BI_Grade": "S", "BI_Score": 173}
    valid = _bi_row("GOOD")
    monkeypatch.setattr(
        api,
        "load_live_cache_file",
        lambda *_a, **_k: ([legacy, valid], None, {"partial": False}, False),
    )
    _patch_result_decoration(monkeypatch)

    response = api.get_bi_results(direction="long")

    assert response.count == 1
    assert [row["ticker"] for row in response.data] == ["GOOD"]
    assert response.diagnostics["raw_cache_rows"] == 2
    assert response.diagnostics["validated_scanner_signals"] == 1
    assert response.diagnostics["indicator_gate"]["legacy_or_invalid_rows_rejected"] == 1


def test_generic_scan_results_endpoint_applies_same_bi_contract(monkeypatch):
    legacy = {"Ticker": "OLD", "BI_Grade": "S", "BI_Score": 173}
    valid = _bi_row("NEW")
    monkeypatch.setattr(
        api,
        "load_live_cache_file",
        lambda *_a, **_k: ([legacy, valid], None, {}, False),
    )
    _patch_result_decoration(monkeypatch)

    response = api.get_scan_results(direction="long", market_type="stocks")

    assert response.count == 1
    assert [row["ticker"] for row in response.data] == ["NEW"]


def test_chart_cache_lookup_never_resurrects_legacy_bi_row(monkeypatch):
    rows = [
        {"Ticker": "LEGACY", "BI_Grade": "S", "BI_Score": 173},
        _bi_row("GOOD"),
    ]
    monkeypatch.setattr(api, "load_cache_file", lambda *_a, **_k: (rows, None))

    legacy, legacy_direction = api._find_bi_signal_cache_row("LEGACY")
    valid, valid_direction = api._find_bi_signal_cache_row("GOOD", direction="LONG")

    assert legacy is None and legacy_direction is None
    assert valid["ticker"] == "GOOD"
    assert valid["bi_indicators_green"] == 17
    assert valid_direction == "LONG"


def test_chart_cache_lookup_requires_direction_for_ambiguous_ticker(monkeypatch):
    monkeypatch.setattr(api, "load_cache_file", lambda *_a, **_k: ([_bi_row("BOTH")], None))
    assert api._find_bi_signal_cache_row("BOTH") == (None, None)
    row, direction = api._find_bi_signal_cache_row("BOTH", direction="SHORT")
    assert row["ticker"] == "BOTH"
    assert direction == "SHORT"


def test_check_and_alert_rejects_invalid_bi_cache_before_any_mail_side_effect(
    monkeypatch, tmp_path
):
    cache = tmp_path / "bi.json"
    cache.write_text(
        json.dumps({"results": [{"Ticker": "LEGACY", "BI_Grade": "S", "BI_Score": 173}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        api,
        "_stock_trade_email_allowed",
        lambda *_a, **_k: pytest.fail("invalid BI row reached the session/mail path"),
    )
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda *_a, **_k: pytest.fail("invalid BI row reached SMTP"),
    )

    api._check_and_alert("bi_long", str(cache))


def test_check_and_alert_classifies_only_current_contract_bi_rows(
    monkeypatch, tmp_path
):
    cache = tmp_path / "bi.json"
    cache.write_text(
        json.dumps({
            "results": [
                {"Ticker": "LEGACY", "BI_Grade": "S", "BI_Score": 173},
                _bi_row("GOOD"),
            ]
        }),
        encoding="utf-8",
    )
    classified = []

    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *_a, **_k: (True, "unit"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **_k: None)
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda _scanner, row: row)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *_a, **_k: 0)
    monkeypatch.setattr(api, "_record_email_event", lambda *_a, **_k: None)

    def classify(_scanner, row, _now=None):
        ticker = api._extract_alert_ticker(row)
        classified.append(ticker)
        return {
            "ticker": ticker,
            "grade": "S",
            "score": 95,
            "rvol": 1.0,
            "alertable_now": False,
            "suppression_reasons": ["unit_block"],
        }

    monkeypatch.setattr(api, "_classify_alert_candidate", classify)

    api._check_and_alert("bi_long", str(cache))

    assert classified == ["GOOD"]


def test_safe_tracking_boundary_rejects_bi_rows_without_indicator_contract(monkeypatch):
    tracked = []
    monkeypatch.setattr(
        api,
        "record_alert_signals",
        lambda scanner, rows, **kwargs: tracked.append((scanner, rows, kwargs)),
    )

    api._safe_record_alert_signals(
        "bi_long",
        [{"ticker": "LEGACY", "BI_Grade": "S", "BI_Score": 173}],
        mail_class="shadow",
    )

    assert tracked == []
