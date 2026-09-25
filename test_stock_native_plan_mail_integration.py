"""Real OHLCV -> stock scan -> causal plan -> mail gates, in a safe subprocess.

The regressions catch reference-close self-barriers, missing scanner levels,
and accidental promotion of first-barrier-blocked plans into mail eligibility.
Positive LONG/SHORT fixtures traverse the real sender and delivery ledger;
only the SMTP transport is fake, with reserved .invalid fixture recipients.
No level builder, scoring function, quality gate, or revalidator is replaced.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _probe(direction, directory, scenario="barrier"):
    """Import the application only after isolating state and blocking I/O."""
    from datetime import datetime, timedelta, timezone
    import smtplib
    import socket
    from unittest.mock import patch

    target = Path(directory)
    # Source loader is repository-relative; persistent paths are isolated below.
    os.chdir(Path(__file__).resolve().parent)
    for key, name in {
        "ALPHA_DATA_DIR": "data", "ALPHA_RUNTIME_TMP_DIR": "runtime",
        "SIGNAL_TRACKER_DB_PATH": "tracker.sqlite",
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH": "acceptance.sqlite",
        "SUPPRESSION_TELEMETRY_DB_PATH": "suppression.sqlite",
        "MAIL_OUTBOX_DB_PATH": "outbox.sqlite", "AUTH_DB_PATH": "auth.sqlite",
        "EMAIL_DEDUPE_FILE": "dedupe.json",
    }.items():
        os.environ[key] = str(target / name)
    (target / "runtime").mkdir()
    os.environ["STOCK_SWING_DATA_MODE"] = "starter_swing"
    os.environ["POLYGON_KEY"] = "offline-fixture"
    os.environ["MAIL_OUTBOX_ENABLED"] = "0"
    os.environ["JWT_SECRET"] = "offline-native-plan-test-secret-not-for-deployment"
    os.environ["GMAIL_USER"] = "sender@example.invalid"
    os.environ["GMAIL_APP_PASSWORD"] = "offline-test-only"
    os.environ["ALERT_EMAIL"] = "recipient@example.invalid"
    os.environ["ALERT_SEND_TO_SUBSCRIBERS"] = "0"

    def forbidden(*args, **kwargs):
        raise AssertionError("External network or SMTP is forbidden in this test")

    original_connect = socket.socket.connect

    def loopback_only(sock, address):
        # Windows asyncio needs a local socketpair during imports.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return forbidden()

    original_exists = Path.exists

    def no_credentials(path):
        if path.name in {"secrets.toml", ".env"}:
            return False
        return original_exists(path)

    with patch.object(socket.socket, "connect", loopback_only), \
            patch.object(smtplib, "SMTP", forbidden), \
            patch.object(smtplib, "SMTP_SSL", forbidden), \
            patch.object(Path, "exists", no_credentials):
        import api
        from modules import stock_swing_contract as swing

        now = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)

        # Repeated wide sessions provide confirmed daily/weekly invalidation.
        # Latest gap is liquid and held, but its nearby day-high/low remains a
        # real first opposing barrier: the setup score cannot erase its risk.
        sessions = []
        day = datetime(2026, 9, 17, tzinfo=timezone.utc)
        while len(sessions) < 80:
            if swing.session_close(day.date().isoformat()) is not None:
                sessions.append(day)
            day -= timedelta(days=1)
        sessions.reverse()
        history = []
        for index, session in enumerate(sessions):
            values = {"open": 100., "high": 116., "low": 96.,
                      "close": 100., "volume": 1_000_000.}
            if scenario == "unreclaimed":
                values["high"] = 103.
            if scenario == "reclaimed":
                values.update(open=104.5, high=106., low=103., close=104.5)
                if index == len(sessions) - 3:
                    values.update(open=104.5, high=104.7, low=99.8, close=100.)
                if index == len(sessions) - 2:
                    values.update(open=100., high=101., low=99., close=100.)
            if index == len(sessions) - 1:
                values.update(open=104., high=110., low=99., close=107.5,
                              volume=4_000_000.)
                if scenario == "reclaimed":
                    values.update(open=104., high=110.3, low=98., close=107., volume=5_000_000.)
            if direction == "SHORT":
                values.update(open=200-values["open"], high=200-values["low"],
                              low=200-values["high"], close=200-values["close"])
            history.append({"time": session.timestamp(), **values})
        latest, previous = history[-1], history[-2]
        four_hour = []
        if scenario == "reclaimed":
            for hour, minute, count, values in [
                (13, 30, 8, {"open": 104., "high": 108., "low": 98., "close": 106.5}),
                (17, 30, 5, {"open": 106.5, "high": 110.3, "low": 106., "close": 107.}),
            ]:
                if direction == "SHORT":
                    values = {"open": 200-values["open"], "high": 200-values["low"],
                              "low": 200-values["high"], "close": 200-values["close"]}
                four_hour.append({"timestamp": int(datetime(2026, 9, 17, hour, minute,
                    tzinfo=timezone.utc).timestamp()*1000), "source_bar_count": count,
                    "volume": 2_500_000., **values})

        def grouped(row):
            date = datetime.fromtimestamp(row["time"], timezone.utc).date().isoformat()
            return {"status": "DELAYED", "adjusted": True, "results": [{
                "T": "TEST", "t": int(datetime.fromisoformat(date).replace(tzinfo=swing.NY).timestamp()*1000),
                **{key: row[source] for key, source in
                   {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}.items()},
            }]}

        def provider(url, **kwargs):
            if "/aggs/grouped/" in url:
                row = latest if url.endswith("2026-09-17") else previous
                return type("Response", (), {"status_code": 200,
                    "json": lambda self: grouped(row)})()
            return forbidden()

        with patch.object(api, "datetime", Clock), patch.object(swing, "datetime", Clock), \
                patch.object(api, "rate_limited_get", provider), \
                patch.object(api.req.sessions.Session, "request", forbidden), \
                patch.object(api, "fetch_stock_daily_history_strict", lambda *a, **k: [
                    {**row, "date": datetime.fromtimestamp(row["time"], timezone.utc).date().isoformat()}
                    for row in history]), \
                patch.object(api, "_fetch_recent_stock_4h_bars", lambda *a, **k: four_hour), \
                patch.object(api, "_load_common_stock_universe", lambda **k: ({"TEST"}, "fixture")), \
                patch.object(api, "fetch_business_quality", lambda *a, **k: {}), \
                patch.object(api, "_get_market_context_snapshot", lambda: {}), \
                patch.object(api, "_strategy_cache_path", lambda *a, **k: str(target / "strategy.json")):
            rows = api._strategy_scan_wrapper("Gap Momentum " + direction.title(),
                                              send_email=False, publish_generic_cache=False)
            assert len(rows) == 1, rows
            row = rows[0]
            levels = api._alert_trade_levels(row)
            state = api._classify_alert_candidate("stock_strategy", row, now.timestamp())
            quality_ok, quality_reason = api._stock_strategy_mail_quality_state(
                row, daily_close_confirmed_mode=True,
                market_status=api._stock_trade_email_status(now), now_utc=now)
            final = api._revalidate_stock_strategy_mail_candidate(row, now_ts=now.timestamp())
            smtp_messages = []
            delivery_outcome = None
            persisted_deliveries = []
            if scenario == "reclaimed":
                from modules import regime_filter

                class OfflineSMTP:
                    def __init__(self, host, port, **kwargs):
                        assert (host, port) == ("smtp.gmail.com", 587)

                    def ehlo(self):
                        return 250, b"offline"

                    def starttls(self, **kwargs):
                        return 220, b"offline"

                    def login(self, username, password):
                        assert username == "sender@example.invalid"
                        assert password == "offline-test-only"

                    def sendmail(self, sender, recipients, message):
                        assert sender == "sender@example.invalid"
                        assert recipients == ["recipient@example.invalid"]
                        smtp_messages.append(message)
                        return {}

                    def quit(self):
                        return 221, b"offline"

                    def close(self):
                        return None

                with patch.object(smtplib, "SMTP", OfflineSMTP), \
                        patch.object(api.time, "time", lambda: now.timestamp()), \
                        patch.object(api, "_EMAIL_STARTUP_TIME", now.timestamp()-600), \
                        patch.object(regime_filter, "DEFAULT_STATE_PATH", target / "regime.json"):
                    api._send_strategy_scan_alerts("Gap Momentum " + direction.title(), rows, "stocks")
                    delivery_outcome = api._last_delivery_outcome()
                # SMTP acceptance alone could also mean a downgraded WATCH.
                # Assert the actual durable delivery class after finalization.
                import sqlite3
                connection = sqlite3.connect((target / "tracker.sqlite").as_uri()+"?mode=ro", uri=True)
                try:
                    persisted_deliveries = connection.execute(
                        "SELECT mail_class,channel,mail_channel,delivery_state,fill_evidence_verified "
                        "FROM signals ORDER BY id"
                    ).fetchall()
                finally:
                    connection.close()
            print("NATIVE_PLAN_RESULT=" + json.dumps({
                "levels": levels, "raw_score": row["score"], "adjusted_score": state["score"],
                "structure_status": row.get("structure_status"),
                "suppression_reasons": state["suppression_reasons"],
                "alertable_now": state["alertable_now"],
                "final": {key: final[key] for key in ("ok", "reason") if key in final},
                "source": row.get("Trade_Setup_Source"),
                "session": row.get("swing_analysis_session"),
                "mail_quality_ok": quality_ok, "mail_quality_reason": quality_reason,
                "validated_fill": (final.get("candidate") or {}).get("fill_evidence_verified"),
                "validated_price_mode": (final.get("candidate") or {}).get("price_mode"),
                "smtp_messages": len(smtp_messages), "delivery_outcome": delivery_outcome,
                "persisted_deliveries": persisted_deliveries,
            }))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_gap_scanner_native_plan_preserves_first_barrier_mail_rejection(tmp_path, direction):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), direction, str(tmp_path)],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("NATIVE_PLAN_RESULT="))
    actual = json.loads(line.split("=", 1)[1])
    assert actual["session"] == "2026-09-17"
    assert actual["levels"]["valid"] is True
    assert actual["levels"]["native"] is True, actual
    assert actual["levels"]["estimated"] is False
    assert actual["levels"]["direction"] == direction
    assert actual["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert actual["levels"]["rr_tp1"] < 1.5
    assert actual["alertable_now"] is False
    assert actual["adjusted_score"] < 80
    assert actual["final"] == {"ok": False, "reason": "swing_trade_plan_invalid"}
    assert "estimated_trade_plan" not in actual["suppression_reasons"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_gap_confirmed_break_has_native_plan_but_keeps_target_risk_gate(tmp_path, direction):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), direction,
                             str(tmp_path), "unreclaimed"],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("NATIVE_PLAN_RESULT="))
    actual = json.loads(line.split("=", 1)[1])
    # The completed gap close is sufficient breakout evidence; its missing
    # retest no longer makes the structural plan estimated. Nearby target risk
    # remains an independent blocker, so this does not authorize a mail.
    assert actual["levels"]["native"] is True, actual
    assert actual["levels"]["estimated"] is False
    assert actual["levels"]["rr_tp1"] < 1.5
    assert actual["adjusted_score"] <= 45
    assert actual["alertable_now"] is False
    assert actual["final"] == {"ok": False, "reason": "swing_trade_plan_invalid"}
    assert "estimated_trade_plan" not in actual["suppression_reasons"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_completed_gap_retest_native_plan_passes_mail_classification(tmp_path, direction):
    """Confirmed retest plus measured target room can reach the real mail gate."""
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), direction,
                             str(tmp_path), "reclaimed"],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("NATIVE_PLAN_RESULT="))
    actual = json.loads(line.split("=", 1)[1])
    assert actual["levels"]["valid"] is True
    assert actual["levels"]["native"] is True
    assert actual["levels"]["estimated"] is False
    assert actual["levels"]["direction"] == direction
    assert actual["levels"]["rr_tp1"] >= 1.6
    assert actual["structure_status"] == "ACCEPT"
    assert actual["mail_quality_ok"] is True, actual
    assert actual["suppression_reasons"] == []
    assert actual["alertable_now"] is True
    assert actual["final"] == {"ok": True}
    assert actual["validated_fill"] is False
    assert actual["validated_price_mode"] == "swing_delayed_close"
    assert actual["smtp_messages"] == 1, actual
    assert actual["delivery_outcome"] == "accepted"
    assert actual["persisted_deliveries"] == [["trade", "email", "stocks_swing", "ACTIVE", 0]]


@pytest.mark.parametrize("direction,stop", [("LONG", 95.), ("SHORT", 105.)])
def test_reference_close_alone_cannot_be_its_own_opposing_barrier(direction, stop):
    """A price label at entry is not independently observed supply or demand."""
    from datetime import datetime, timezone
    from modules.level_zones import build_structure_snapshot, classify_for_trade, select_trade_structure

    cutoff = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)
    bars = [{"open_time": datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc),
             "close_time": cutoff, "open": 99., "high": 110., "low": 90.,
             "close": 100., "volume": 1_000_000., "is_closed": True}]
    snapshot = build_structure_snapshot({"1D": bars}, symbol="TEST", asset_class="stock",
        horizon="swing", as_of=cutoff, current_price=100., tick_size=.01)
    directional = classify_for_trade(snapshot, entry=100., direction=direction)
    # Preserve PDC as an informational zone, but never invent a zero-room wall.
    pdc = next(zone for zone in snapshot.zones
               if {item.source_name for item in zone.evidence} == {"PDC"})
    assert pdc.lower <= 100. <= pdc.upper
    assert pdc not in directional.opposing_barriers
    assert len(directional.opposing_barriers) == 1
    decision = select_trade_structure(directional, stop=stop, minimum_rr=1.5)
    assert decision.status == "ACCEPT"
    assert decision.barrier_r > 1.9


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_reference_close_beside_actual_session_extreme_stays_blocking(direction):
    """Separating reference annotations must not bypass a real PDH/PDL zone."""
    from datetime import datetime, timezone
    from modules.level_zones import build_structure_snapshot, classify_for_trade, select_trade_structure

    cutoff = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)
    bars = [{"open_time": datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc),
             "close_time": cutoff, "open": 100.,
             "high": 100. if direction == "LONG" else 110.,
             "low": 90. if direction == "LONG" else 100.,
             "close": 100., "volume": 1_000_000., "is_closed": True}]
    snapshot = build_structure_snapshot({"1D": bars}, symbol="TEST", asset_class="stock",
        horizon="swing", as_of=cutoff, current_price=100., tick_size=.01)
    directional = classify_for_trade(snapshot, entry=100., direction=direction)
    real = next(zone for zone in directional.opposing_barriers
                if ("PDH" if direction == "LONG" else "PDL") in zone.source_names)
    assert set(real.source_names) == ({"PDH"} if direction == "LONG" else {"PDL"})
    reference = next(zone for zone in snapshot.zones if zone.source_names == ("PDC",))
    assert reference not in directional.opposing_barriers
    decision = select_trade_structure(directional, stop=95. if direction == "LONG" else 105.)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.barrier_r == 0.


if __name__ == "__main__":
    _probe(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "barrier")
