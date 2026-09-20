"""Public scanner error classes must survive persistence without leaking details."""
import pytest

import api
from modules.scanners import ScannerDataError
from test_stock_strategy_attempt_status import status_io


PUBLIC_RUNTIME_CODES = (
    "scan_cache_publish_failed", "scan_partial_cache", "scan_already_running",
    "scan_timeout", "scan_failed",
)


@pytest.mark.parametrize("code", PUBLIC_RUNTIME_CODES + tuple(sorted(ScannerDataError.CODES)))
def test_public_error_projection_preserves_exact_allowlisted_codes(code):
    assert api._public_scan_error_code(code) == code


@pytest.mark.parametrize("code", PUBLIC_RUNTIME_CODES)
def test_producer_persisted_attempt_and_result_keep_same_safe_error(status_io, code):
    _, cache = status_io
    name = "Cup and Handle Breakout"
    attempt = api._new_stock_strategy_attempt(name)
    assert attempt
    diagnostics = {"coverage": "incomplete", "checked": 4984, "universe_count": 12588,
                   "final_results": None}
    assert api._publish_stock_strategy_attempt(attempt, "error", diagnostics=diagnostics, error=code)
    stored = api._read_stock_strategy_attempt(name)
    assert stored["available"] is True
    assert stored["error_code"] == code
    response = api.get_scan_results(name, None, "stocks")
    assert response.scan_error == code
    assert response.cached_at == cache["stamp"]  # Do not pretend old rows are a new success.
    assert response.diagnostics["latest_attempt"]["error_code"] == code
    assert response.diagnostics["attempt_diagnostics"]["coverage"] == "incomplete"
    assert response.diagnostics["attempt_diagnostics"]["checked"] == 4984
    assert any(code in warning for warning in response.warnings)


@pytest.mark.parametrize("raw", [
    "PRIVATE_PROVIDER_BODY token=private-example /root/private-file",
    "scan_cache_publish_failed PRIVATE_PROVIDER_BODY",
    "scan_already_running\nPRIVATE_PROVIDER_BODY",
])
def test_unrecognized_error_text_is_not_exposed(raw):
    result = api._public_scan_error_code(raw)
    assert result == "scan_failed"
    assert "PRIVATE" not in result and "private" not in result


def test_unknown_error_persistence_and_response_never_include_original_text(status_io):
    name = "Cup and Handle Breakout"
    attempt = api._new_stock_strategy_attempt(name)
    assert api._publish_stock_strategy_attempt(
        attempt, "error", diagnostics={"coverage": "incomplete", "final_results": None},
        error="PRIVATE_PROVIDER_BODY token=private-example /root/private-file")
    stored = api._read_stock_strategy_attempt(name)
    assert stored["error_code"] == "scan_failed"
    response = api.get_scan_results(name, None, "stocks")
    assert response.scan_error == "scan_failed"
    assert "PRIVATE_PROVIDER_BODY" not in str(response.model_dump())
