from pathlib import Path


ROOT = Path(__file__).resolve().parent
API_SOURCE = (ROOT / "api.py").read_text(encoding="utf-8")
FRONTEND_SOURCE = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def test_long_scanners_publish_isolated_partial_results():
    assert "def save_partial_cache_file(" in API_SOURCE
    assert "def load_live_cache_file(" in API_SOURCE
    assert "def finalize_cache_file(" in API_SOURCE
    assert "fetch_early_movers(_prefetched_perps=None, _progress_callback=None)" in API_SOURCE
    assert "partial_revalidation_pending" in API_SOURCE


def test_partial_penny_rows_are_display_only_until_final_revalidation():
    assert "effective_include_watch = bool(include_watch or is_partial)" in API_SOURCE
    assert '"partial_watch_rows": bool(is_partial and not include_watch)' in API_SOURCE
    assert "finalize_cache_file(PENNY_STOCKS_CACHE" in API_SOURCE


def test_frontend_renders_and_polls_live_scan_results():
    assert "function LiveScanStatus(" in FRONTEND_SOURCE
    assert "Live-Zwischenstand:" in FRONTEND_SOURCE
    assert "finale Freigaben und Alerts erst nach abgeschlossenem Scan" in FRONTEND_SOURCE
    lifecycle = FRONTEND_SOURCE[
        FRONTEND_SOURCE.index("function useScannerFeed("):
        FRONTEND_SOURCE.index("function ScannerEvidence(")
    ]
    # Both tabs now use one shared lifecycle. Scope the timing assertions to
    # that implementation, not an unrelated scanner with a matching substring.
    # Executed async/race/error regressions live in test_frontend_scanner_lifecycle.py.
    assert "pollRef.current = setTimeout(() => poll(0), 500)" in lifecycle
    assert "pollRef.current = setTimeout(() => poll(attempt + 1), 2000)" in lifecycle
    assert "scannerPollOutcome(data, previousStamp, observedRunning, expectedRunId)" in lifecycle
    scanner_tab = FRONTEND_SOURCE[
        FRONTEND_SOURCE.index("function ScannerTab("):
        FRONTEND_SOURCE.index("function BIScannerTab(")
    ]
    bi_tab = FRONTEND_SOURCE[
        FRONTEND_SOURCE.index("function BIScannerTab("):
        FRONTEND_SOURCE.index("function BiotechTab(")
    ]
    assert "const feed = useScannerFeed({" in scanner_tab
    assert "const feed = useScannerFeed({" in bi_tab
    # BI uses the scheduler-bound ScanControl progress only; four other
    # long-running scanners still render their dedicated live result panels.
    assert FRONTEND_SOURCE.count("<LiveScanStatus") >= 4
