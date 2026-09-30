"""Read-only mail audits must not rebuild the same reference universe per row."""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest

import api
from modules.mail_diagnostics import mail_diagnostic_snapshot, read_diagnostic_reference, transport_diagnostic_code


@pytest.fixture
def audit_batch(monkeypatch, tmp_path):
    tickers = {f"QA{i:05d}" for i in range(6000)}
    names = {ticker: "Ordinary Industrial Technology Corporation" for ticker in tickers}
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "tickers": tickers, "names": names, "loaded_at": time.time(),
    })
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    rows = [{"Ticker": ticker, "Grade": "B", "Score": 30, "RVOL": 1.0}
            for ticker in sorted(tickers)[:40]]
    monkeypatch.setattr(api, "_extract_cache_rows_for_alert_audit", lambda *a, **kw: rows)
    monkeypatch.setattr(api, "_require_admin", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_admin_mail_delivery_status", lambda: {"recent_decisions": []})
    monkeypatch.setattr(api, "_email_alert_status", lambda: {})
    monkeypatch.setattr(api, "_common_stock_guard_status", lambda **kw: {})
    monkeypatch.setattr(api, "_summarize_email_alert_audit", lambda scanners: {})
    monkeypatch.setattr(api, "_scan_cache_payload", lambda *a: None)
    targets = ["stock_strategy", "stock_strategy"]
    original_build = api._build_alert_audit_for_cache
    seen = []
    def build(name, path, *, read_only=False):
        if len(seen) >= len(targets):
            return {"rows_checked": 0}
        seen.append(name)
        return original_build("stock_strategy", str(tmp_path / "missing-cache.json"), read_only=read_only)
    monkeypatch.setattr(api, "_build_alert_audit_for_cache", build)
    classifications = []
    original_name_check = api._name_has_non_stock_product_keyword
    def name_check(name):
        if name == "Ordinary Industrial Technology Corporation":
            classifications.append(name)
        return original_name_check(name)
    monkeypatch.setattr(api, "_name_has_non_stock_product_keyword", name_check)
    return classifications, seen, tickers


def test_one_readonly_request_filters_common_stock_universe_once(audit_batch):
    classifications, seen, tickers = audit_batch
    started = time.perf_counter()
    result = api.get_email_alert_audit("offline")
    duration = time.perf_counter() - started
    print(f"Two 40-row cache audits: {len(classifications)} name checks in {duration:.3f}s")
    assert len(seen) == 2 and result["status"] == "ok"
    # Also allow the cheap per-row name validation; disallow 80 full rebuilds.
    assert len(classifications) <= len(tickers) + 80


def test_route_timestamp_has_explicit_utc_offset(audit_batch):
    result = api.get_email_alert_audit("offline")
    assert datetime.fromisoformat(result["timestamp"]).utcoffset() == timedelta(0)


def test_reference_is_refreshed_between_requests_and_unscoped_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(tmp_path / "missing.json"))
    memory = {"tickers": {"AAA"}, "names": {}, "loaded_at": time.time()}
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", memory)

    @mail_diagnostic_snapshot
    def audit():
        first = api._load_common_stock_universe_cached()
        memory["tickers"] = {"BBB"}
        assert api._load_common_stock_universe_cached() is first
        return first

    assert audit()[0] == {"AAA"}
    assert audit()[0] == {"BBB"}
    # A sender/other reader must not inherit the admin's old admission evidence.
    memory["loaded_at"] = 0
    assert api._load_common_stock_universe_cached()[0] is None


def test_age_limits_are_part_of_request_local_reference_key(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "tickers": {"AAA"}, "names": {}, "loaded_at": time.time() - 60,
    })

    @mail_diagnostic_snapshot
    def audit():
        assert api._load_common_stock_universe_cached(300)[0] == {"AAA"}
        assert api._load_common_stock_universe_cached(1)[0] is None
    audit()


def test_unauthorized_route_does_not_read_references(monkeypatch):
    def deny(*args, **kwargs):
        raise api.HTTPException(status_code=403)
    monkeypatch.setattr(api, "_require_admin", deny)
    monkeypatch.setattr(api, "_build_alert_audit_for_cache",
                        lambda *a, **k: pytest.fail("unauthorized cache read"))
    with pytest.raises(api.HTTPException) as exc:
        api.get_email_alert_audit("invalid")
    assert exc.value.status_code == 403


def test_parallel_diagnostics_have_independent_scopes():
    barrier = Barrier(2)

    @mail_diagnostic_snapshot
    def audit(value):
        assert read_diagnostic_reference("key", lambda: value) == value
        barrier.wait(timeout=5)
        assert read_diagnostic_reference("key", lambda: "unexpected reread") == value
        return value

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(audit, ("first request", "second request"))) == [
            "first request", "second request"]


def test_failed_scope_does_not_leak_and_failed_read_can_retry():
    @mail_diagnostic_snapshot
    def audit():
        def broken():
            raise ValueError("read failed")
        with pytest.raises(ValueError):
            read_diagnostic_reference("key", broken)
        assert read_diagnostic_reference("key", lambda: "recovered") == "recovered"
        raise RuntimeError("request failed")

    with pytest.raises(RuntimeError):
        audit()
    assert read_diagnostic_reference("key", lambda: "outside") == "outside"


def test_nested_scope_restores_parent():
    @mail_diagnostic_snapshot
    def child():
        return read_diagnostic_reference("key", lambda: "child")

    @mail_diagnostic_snapshot
    def parent():
        assert read_diagnostic_reference("key", lambda: "parent") == "parent"
        assert child() == "child"
        assert read_diagnostic_reference("key", lambda: "wrong") == "parent"
    parent()


@pytest.mark.parametrize("raw", [
    None, {}, "SMTPAuthenticationError: private@example.invalid",
    "SMTPDataError:not_delivered password=SECRET", "Name:not_delivered:secret",
    "private@example.invalid:not_delivered", "SMTPDataError", ":not_delivered",
    "x" * 130 + ":not_delivered",
])
def test_transport_diagnostic_does_not_parse_arbitrary_exception_text(raw):
    assert transport_diagnostic_code(raw) is None
