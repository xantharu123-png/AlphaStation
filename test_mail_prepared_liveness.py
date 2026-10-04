"""No-send reservations never become phantom mail or block a fresh retry.

Real SQLite intent state and real SMTP ownership guards, with fake transport.
Run only through scripts/run_offline_tests.py; no provider or production state.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

import api
from modules import signal_tracker as tracker
from test_alert_delivery_intent_api import _AcceptedSMTP, _row, _setup


def _configure(monkeypatch, tmp_path):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    database = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    monkeypatch.setattr(tracker, "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
                        str(tmp_path / "delivery_acceptance.sqlite"))
    monkeypatch.setattr(api, "_resolve_email_alert_recipients",
                        lambda **kwargs: ["recipient@example.invalid"])
    return database


def _send(candidate):
    return api._send_email_alert(
        "Isolated prepared liveness", "<p>probe</p>",
        bypass_startup_cooldown=True, mail_class="trade",
        mail_channel="stocks_swing", tracking_scanner="stock_strategy",
        tracking_rows=[candidate],
    )


def _states(database):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT status, delivery_state, delivery_attempted_at, delivery_accepted_at FROM signals"
        ).fetchall()


def _stored_rows(database):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM signals")]


def test_prepared_snapshot_rejects_evidence_of_an_earlier_attempt(monkeypatch, tmp_path):
    database, _ = _prepare(monkeypatch, tmp_path)
    prepared = _stored_rows(database)[0]
    assert tracker._prepared_delivery_snapshot(prepared) is not None
    prepared["delivery_attempted_at"] = prepared["delivery_prepared_at"]
    assert tracker._prepared_delivery_snapshot(prepared) is None


@pytest.mark.parametrize("expected", ["original", "tainted", "none"])
def test_claim_never_reuses_prepared_with_prior_attempt(monkeypatch, tmp_path, expected):
    database, intent = _prepare(monkeypatch, tmp_path)
    original_rows = _stored_rows(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE signals SET delivery_attempted_at=delivery_prepared_at")
    supplied = (original_rows if expected == "original" else
                _stored_rows(database) if expected == "tainted" else None)
    before = _states(database)
    result = tracker.mark_alert_delivery_attempted(intent, expected_prepared_rows=supplied)
    assert not result["send_allowed"]
    assert not result["claimed_this_call"]
    assert _states(database) == before


def test_sender_does_not_replay_malformed_prepared_attempt(monkeypatch, tmp_path):
    database = _configure(monkeypatch, tmp_path)
    original = api.prepare_alert_delivery_intent
    def earlier_attempt(*args, **kwargs):
        prepared = original(*args, **kwargs)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE signals SET delivery_attempted_at=delivery_prepared_at")
        return prepared
    monkeypatch.setattr(api, "prepare_alert_delivery_intent", earlier_attempt)
    assert _send(_row("PREPARED-PRIOR-ATTEMPT")) is False
    assert _AcceptedSMTP.calls == 0
    assert _states(database)[0][2] is not None


def test_old_sender_cancellation_preserves_replacement_preparation(monkeypatch, tmp_path):
    database, intent = _prepare(monkeypatch, tmp_path)
    old_rows = _stored_rows(database)
    old_time = datetime.fromisoformat(old_rows[0]["delivery_prepared_at"])
    assert tracker.cleanup_stale_prepared_delivery_intents(30, now=old_time + timedelta(minutes=31)) == 1
    fresh = tracker.prepare_alert_delivery_intent(
        "stock_strategy", [_row("CANCEL-OWNERSHIP")], intent,
        delivery_recipient_keys=["a" * 64],
    )
    assert fresh["prepared"]
    assert fresh["signal_ids"] != [row["id"] for row in old_rows]
    assert tracker.cancel_alert_delivery_intent(intent, expected_prepared_rows=old_rows) == 0
    assert tracker.mark_alert_delivery_attempted(
        intent, expected_prepared_rows=fresh["signals"],
    )["send_allowed"]


def test_sender_cancel_binds_original_not_new_owner(monkeypatch, tmp_path):
    database = _configure(monkeypatch, tmp_path)
    prepare = api.prepare_alert_delivery_intent
    captured = {}
    def save_preparation(*args, **kwargs):
        result = prepare(*args, **kwargs)
        captured.update(args=args, kwargs=kwargs, original=result)
        return result
    monkeypatch.setattr(api, "prepare_alert_delivery_intent", save_preparation)
    def replace_then_fail(*args, **kwargs):
        original = captured["original"]
        old_time = datetime.fromisoformat(original["signals"][0]["delivery_prepared_at"])
        assert tracker.cleanup_stale_prepared_delivery_intents(
            30, now=old_time + timedelta(minutes=31),
        ) == 1
        captured["replacement"] = prepare(*captured["args"], **captured["kwargs"])
        raise RuntimeError("isolated old renderer failed")
    monkeypatch.setattr(api, "_brand_email_html", replace_then_fail)
    with pytest.raises(RuntimeError, match="old renderer failed"):
        _send(_row("REPLACEMENT-PREPARED-OWNER"))
    fresh = captured["replacement"]
    assert _AcceptedSMTP.calls == 0
    assert [row["id"] for row in _stored_rows(database)] == fresh["signal_ids"]
    assert tracker.mark_alert_delivery_attempted(
        fresh["intent_key"], expected_prepared_rows=fresh["signals"],
    )["send_allowed"]


@pytest.mark.parametrize("operation", ["cleanup", "cancel", "attested_cancel", "claim"])
def test_durable_journal_acceptance_protects_legacy_prepared(monkeypatch, tmp_path, operation):
    database, intent = _prepare(monkeypatch, tmp_path)
    prepared_rows = _stored_rows(database)
    prepared_time = datetime.fromisoformat(prepared_rows[0]["delivery_prepared_at"])
    journal = tracker.journal_alert_delivery_acceptance(intent, ["a" * 64], accepted_at=prepared_time)
    assert journal["durable_acceptance"]
    assert _states(database) == [(tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)]
    if operation == "cleanup":
        assert tracker.cleanup_stale_prepared_delivery_intents(
            30, now=prepared_time + timedelta(minutes=31),
        ) == 0
    elif operation == "claim":
        assert not tracker.mark_alert_delivery_attempted(
            intent, expected_prepared_rows=prepared_rows,
        )["send_allowed"]
    else:
        assert tracker.cancel_alert_delivery_intent(
            intent, delivery_definitively_not_accepted=operation == "attested_cancel",
        ) == 0
    assert _states(database) == [(tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)]


def test_definite_pre_smtp_optout_allows_fresh_authorized_retry(monkeypatch, tmp_path):
    database = _configure(monkeypatch, tmp_path)
    authorization = {"active": True}
    monkeypatch.setattr(api, "_resolve_email_alert_recipients", lambda **kwargs:
                        ["recipient@example.invalid"] if authorization["active"] else [])
    original_brand = api._brand_email_html
    def revoke_during_render(*args, **kwargs):
        value = original_brand(*args, **kwargs)
        authorization["active"] = False
        return value
    monkeypatch.setattr(api, "_brand_email_html", revoke_during_render)
    candidate = _row("NO-SEND-LIVENESS")
    assert _send(candidate) is False
    assert _AcceptedSMTP.calls == 0
    assert _states(database) == []
    authorization["active"] = True
    monkeypatch.setattr(api, "_brand_email_html", original_brand)
    assert not api._has_open_equivalent_trade_safe("stock_strategy", candidate)
    assert _send(candidate) is True
    assert _AcceptedSMTP.calls == 1
    assert _states(database)[0][:2] == (tracker.STATUS_OPEN, "ACTIVE")
    assert api._has_open_equivalent_trade_safe("stock_strategy", candidate)
    assert _send(candidate) is False
    assert _AcceptedSMTP.calls == 1  # Accepted intent is still never replayed.


@pytest.mark.parametrize("failure", ["brand", "mime", "authorization", "ownership"])
def test_pre_smtp_exception_cancels_only_unattempted_reservation(monkeypatch, tmp_path, failure):
    database = _configure(monkeypatch, tmp_path)
    candidate = _row("PRE-SMTP-" + failure.upper())
    def fail(*args, **kwargs):
        raise RuntimeError("isolated pre-SMTP failure")
    if failure == "brand":
        monkeypatch.setattr(api, "_brand_email_html", fail)
    elif failure == "mime":
        monkeypatch.setattr(api, "make_msgid", fail)
    elif failure == "ownership":
        monkeypatch.setattr(api, "mark_alert_delivery_attempted", fail)
    else:
        calls = []
        def authorize(**kwargs):
            calls.append(True)
            if len(calls) > 1:
                fail()
            return ["recipient@example.invalid"]
        monkeypatch.setattr(api, "_resolve_email_alert_recipients", authorize)
    with pytest.raises(RuntimeError, match="isolated pre-SMTP"):
        _send(candidate)
    assert _AcceptedSMTP.calls == 0
    assert _states(database) == []
    assert not api._has_open_equivalent_trade_safe("stock_strategy", candidate)


@pytest.mark.parametrize("corruption", ["public_ref", "plan"])
def test_invalid_prepared_payload_does_not_leave_reservation(monkeypatch, tmp_path, corruption):
    database = _configure(monkeypatch, tmp_path)
    original = api.prepare_alert_delivery_intent
    def corrupt_result(*args, **kwargs):
        prepared = original(*args, **kwargs)
        prepared["signals"][0]["public_signal_ref" if corruption == "public_ref" else "tp1"] = None
        return prepared
    monkeypatch.setattr(api, "prepare_alert_delivery_intent", corrupt_result)
    assert _send(_row("INVALID-PREPARED")) is False
    assert _AcceptedSMTP.calls == 0
    assert _states(database) == []


def test_pre_smtp_cleanup_preserves_concurrently_attempted_owner(monkeypatch, tmp_path):
    database = _configure(monkeypatch, tmp_path)
    def another_owner_then_fail(*args, **kwargs):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE signals SET delivery_state='ATTEMPTED', delivery_attempted_at=delivery_prepared_at"
            )
        raise RuntimeError("another owner won")
    monkeypatch.setattr(api, "_brand_email_html", another_owner_then_fail)
    with pytest.raises(RuntimeError, match="another owner won"):
        _send(_row("CONCURRENT-OWNER"))
    assert _AcceptedSMTP.calls == 0
    assert _states(database)[0][:2] == (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED")
    assert _states(database)[0][2] is not None


def _prepare(monkeypatch, tmp_path, ticker="CANCEL-OWNERSHIP"):
    database = _configure(monkeypatch, tmp_path)
    candidate = _row(ticker)
    intent = tracker.build_alert_delivery_intent_key("stock_strategy", [candidate],
                                                   delivery_recipient_keys=["a" * 64])
    prepared = tracker.prepare_alert_delivery_intent("stock_strategy", [candidate], intent,
                                                     delivery_recipient_keys=["a" * 64])
    assert prepared["prepared"]
    return database, intent


def test_prepared_only_cancellation_rechecks_state_at_atomic_delete(monkeypatch, tmp_path):
    database, intent = _prepare(monkeypatch, tmp_path)
    original = tracker._db_connection
    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM signals"):
                # Deterministically interleave the ownership transition before
                # DELETE: its predicate must not use an earlier state snapshot.
                self.connection.execute(
                    "UPDATE signals SET delivery_state='ATTEMPTED', delivery_attempted_at=delivery_prepared_at"
                )
            return self.connection.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.connection, name)
    @contextmanager
    def raced_connection():
        with original() as connection:
            yield ConnectionProxy(connection)
    monkeypatch.setattr(tracker, "_db_connection", raced_connection)
    assert tracker.cancel_alert_delivery_intent(intent) == 0
    assert _states(database)[0][:2] == (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED")


@pytest.mark.parametrize("state,attempted,accepted,status,attested,deleted", [
    ("PREPARED", False, False, tracker.STATUS_PENDING_DELIVERY, False, 1),
    ("PREPARED", True, False, tracker.STATUS_PENDING_DELIVERY, False, 0),
    ("ATTEMPTED", True, False, tracker.STATUS_PENDING_DELIVERY, False, 0),
    ("ATTEMPTED", True, False, tracker.STATUS_PENDING_DELIVERY, True, 1),
    ("ACCEPTED_PENDING", True, True, tracker.STATUS_PENDING_DELIVERY, True, 0),
    ("ACTIVE", True, True, tracker.STATUS_OPEN, True, 0),
    ("PREPARED", False, True, tracker.STATUS_PENDING_DELIVERY, False, 0),
])
def test_cancellation_never_guesses_attempt_or_acceptance(monkeypatch, tmp_path,
                                                       state, attempted, accepted,
                                                       status, attested, deleted):
    database, intent = _prepare(monkeypatch, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE signals SET delivery_state=?, status=?, "
            "delivery_attempted_at=CASE WHEN ? THEN delivery_prepared_at ELSE NULL END, "
            "delivery_accepted_at=CASE WHEN ? THEN delivery_prepared_at ELSE NULL END",
            (state, status, attempted, accepted),
        )
    assert tracker.cancel_alert_delivery_intent(
        intent, delivery_definitively_not_accepted=attested,
    ) == deleted
    assert len(_states(database)) == 1 - deleted


def test_cancellation_matches_exact_intent_prefix_only(monkeypatch, tmp_path):
    database, intent = _prepare(monkeypatch, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE signals SET delivery_intent_key = ?",
                           (intent + "extra:row",))
    assert tracker.cancel_alert_delivery_intent(intent) == 0
    assert len(_states(database)) == 1


@pytest.mark.parametrize("prepared_age,attempted,accepted,state,deleted", [
    (60, False, False, "PREPARED", 1),
    (5, False, False, "PREPARED", 0),
    (-60, False, False, "PREPARED", 0),
    (60, True, False, "PREPARED", 0),
    (60, True, False, "ATTEMPTED", 0),
    (60, True, True, "ACCEPTED_PENDING", 0),
    (60, False, True, "PREPARED", 0),
])
def test_stale_cleanup_deletes_only_old_untouched_prepared(monkeypatch, tmp_path,
                                                         prepared_age, attempted,
                                                         accepted, state, deleted):
    database, _ = _prepare(monkeypatch, tmp_path)
    now = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
    prepared_at = (now - timedelta(minutes=prepared_age)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE signals SET delivery_state=?, delivery_prepared_at=?, "
            "delivery_attempted_at=CASE WHEN ? THEN delivery_prepared_at ELSE NULL END, "
            "delivery_accepted_at=CASE WHEN ? THEN delivery_prepared_at ELSE NULL END",
            (state, prepared_at, attempted, accepted),
        )
    assert tracker.cleanup_stale_prepared_delivery_intents(30, now=now) == deleted
    assert len(_states(database)) == 1 - deleted


@pytest.mark.parametrize("prepared_at,deleted", [
    ("2026-10-04T17:00:00+00:00", 1),
    ("2026-10-04T19:00:00+02:00", 1),
    ("2026-10-04T13:00:00-04:00", 1),
    ("2026-10-04T19:00:00-04:00", 0),
    ("2026-10-04T15:00:00-04:00", 0),
    ("2026-10-04T17:30:00+00:00", 0),
    ("2459100", 0),
    ("unproven-chronology", 0),
    (None, 0),
])
def test_stale_cleanup_compares_instants_not_lexical_dates(monkeypatch, tmp_path, prepared_at, deleted):
    database, _ = _prepare(monkeypatch, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE signals SET delivery_prepared_at=?", (prepared_at,))
    assert tracker.cleanup_stale_prepared_delivery_intents(
        30, now=datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc),
    ) == deleted


def test_stale_cleanup_cannot_delete_winning_claim(monkeypatch, tmp_path):
    database, _ = _prepare(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE signals SET delivery_prepared_at=?",
                           ((now - timedelta(hours=1)).isoformat(),))
    original = tracker._db_connection
    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM signals"):
                self.connection.execute(
                    "UPDATE signals SET delivery_state='ATTEMPTED', delivery_attempted_at=delivery_prepared_at"
                )
            return self.connection.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.connection, name)
    @contextmanager
    def raced_connection():
        with original() as connection:
            yield ConnectionProxy(connection)
    monkeypatch.setattr(tracker, "_db_connection", raced_connection)
    assert tracker.cleanup_stale_prepared_delivery_intents(30, now=now) == 0
    assert _states(database)[0][:2] == (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED")


def test_stale_cleanup_and_sqlite_claim_have_one_winner(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    database, _ = _prepare(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE signals SET delivery_prepared_at=?",
                           ((now - timedelta(hours=1)).isoformat(),))
    start = Barrier(2)
    def claim():
        with sqlite3.connect(database, timeout=5) as connection:
            start.wait(timeout=5)
            return connection.execute(
                "UPDATE signals SET delivery_state='ATTEMPTED', "
                "delivery_attempted_at=? WHERE status=? AND delivery_state='PREPARED' "
                "AND delivery_attempted_at IS NULL AND delivery_accepted_at IS NULL",
                (now.isoformat(), tracker.STATUS_PENDING_DELIVERY),
            ).rowcount
    def cleanup():
        start.wait(timeout=5)
        return tracker.cleanup_stale_prepared_delivery_intents(30, now=now)
    with ThreadPoolExecutor(max_workers=2) as workers:
        claimed = workers.submit(claim)
        released = workers.submit(cleanup)
        claimed_count, released_count = claimed.result(timeout=10), released.result(timeout=10)
    assert (claimed_count, released_count) in {(1, 0), (0, 1)}
    assert _states(database) == ([] if released_count else [
        (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED", now.isoformat(), None)
    ])


@pytest.mark.parametrize("cleanup_available,cleanup_raises", [(True, False), (True, True), (False, False)])
def test_background_eval_releases_prepared_without_replaying_mail(monkeypatch, cleanup_available, cleanup_raises):
    import bg_service as background
    calls = []
    monkeypatch.setattr(background, "_reconcile_pending_accepted_deliveries",
                        lambda: calls.append("reconcile"))
    def cleanup(age):
        assert age == 30
        calls.append("cleanup")
        if cleanup_raises:
            raise RuntimeError("isolated cleanup unavailable")
        return 2
    monkeypatch.setattr(background, "cleanup_stale_prepared_delivery_intents",
                        cleanup if cleanup_available else None)
    def evaluate(**kwargs):
        calls.append("evaluate")
        return {"evaluated": 0, "transitions": [], "be_activations": []}
    monkeypatch.setattr(background, "evaluate_open_signals", evaluate)
    monkeypatch.setattr(background, "load_pending_terminal_updates", lambda: [])
    monkeypatch.setattr(background, "load_pending_be_activations", lambda: [])
    def no_mail(*args, **kwargs):
        raise AssertionError("Reservation cleanup never sends or replays mail")
    monkeypatch.setattr(background, "_send_signal_update_mail", no_mail)
    monkeypatch.setattr(background, "_send_be_alert_mail", no_mail)
    assert background._run_signal_eval_job(secrets={})["evaluated"] == 0
    assert calls == (["reconcile", "cleanup", "evaluate"] if cleanup_available
                     else ["reconcile", "evaluate"])


def test_claim_predicate_rechecks_attempt_even_after_snapshot(monkeypatch, tmp_path):
    database, intent = _prepare(monkeypatch, tmp_path)
    prepared_rows = _stored_rows(database)
    original = tracker._db_connection
    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, params=()):
            if sql.startswith("UPDATE signals SET delivery_state='ATTEMPTED'"):
                # A separate durable attempt arrives after the SELECT. Even
                # if its state string is damaged, the atomic NULL predicate
                # must protect that attempt evidence from a second owner.
                self.connection.execute(
                    "UPDATE signals SET delivery_attempted_at=delivery_prepared_at"
                )
                self.connection.commit()
            return self.connection.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.connection, name)
    @contextmanager
    def raced_connection():
        with original() as connection:
            yield ConnectionProxy(connection)
    monkeypatch.setattr(tracker, "_db_connection", raced_connection)
    assert not tracker.mark_alert_delivery_attempted(
        intent, expected_prepared_rows=prepared_rows,
    )["send_allowed"]
    assert _states(database)[0][:2] == (tracker.STATUS_PENDING_DELIVERY, "PREPARED")
    assert _states(database)[0][2] is not None


@pytest.mark.parametrize("field", ["id", "delivery_prepared_at", "delivery_intent_key"])
def test_cancel_atomically_binds_original_reservation_fields(monkeypatch, tmp_path, field):
    database, intent = _prepare(monkeypatch, tmp_path)
    original_rows = _stored_rows(database)
    original = tracker._db_connection
    replacement = (original_rows[0]["id"] + 100 if field == "id" else
                   "2026-10-05T00:00:00+00:00" if field == "delivery_prepared_at" else
                   intent + ":row-different-owner")
    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM signals"):
                self.connection.execute(f"UPDATE signals SET {field}=?", (replacement,))
            return self.connection.execute(sql, params)
        def __getattr__(self, name):
            return getattr(self.connection, name)
    @contextmanager
    def raced_connection():
        with original() as connection:
            yield ConnectionProxy(connection)
    monkeypatch.setattr(tracker, "_db_connection", raced_connection)
    assert tracker.cancel_alert_delivery_intent(intent, expected_prepared_rows=original_rows) == 0
    assert len(_states(database)) == 1


@pytest.mark.parametrize("corruption", ["empty", "not_mapping", "duplicate", "bool_id", "bad_time", "foreign_key"])
def test_invalid_expected_cancel_owner_never_falls_back_to_base_key(monkeypatch, tmp_path, corruption):
    database, intent = _prepare(monkeypatch, tmp_path)
    expected = _stored_rows(database)
    if corruption == "empty":
        expected = []
    elif corruption == "not_mapping":
        expected = [None]
    elif corruption == "duplicate":
        expected *= 2
    elif corruption == "bool_id":
        expected[0]["id"] = True
    elif corruption == "bad_time":
        expected[0]["delivery_prepared_at"] = "unproven-chronology"
    else:
        expected[0]["delivery_intent_key"] = "other-intent:row-owner"
    assert tracker.cancel_alert_delivery_intent(intent, expected_prepared_rows=expected) == 0
    assert len(_states(database)) == 1


@pytest.mark.parametrize("attempted", [False, True])
def test_cancel_original_reservation_after_definite_no_send(monkeypatch, tmp_path, attempted):
    database, intent = _prepare(monkeypatch, tmp_path)
    original_rows = _stored_rows(database)
    if attempted:
        assert tracker.mark_alert_delivery_attempted(
            intent, expected_prepared_rows=original_rows,
        )["send_allowed"]
    assert tracker.cancel_alert_delivery_intent(
        intent, expected_prepared_rows=original_rows,
        delivery_definitively_not_accepted=attempted,
    ) == 1
    assert _states(database) == []


@pytest.mark.parametrize("operation", ["cleanup", "cancel", "attested_cancel", "claim"])
def test_journal_lookup_failure_preserves_prepared_and_prevents_claim(monkeypatch, tmp_path, operation):
    database, intent = _prepare(monkeypatch, tmp_path)
    original_rows = _stored_rows(database)
    prepared_time = datetime.fromisoformat(original_rows[0]["delivery_prepared_at"])
    @contextmanager
    def broken_journal():
        raise sqlite3.OperationalError("isolated acceptance journal unavailable")
        yield  # pragma: no cover
    monkeypatch.setattr(tracker, "_delivery_journal_connection", broken_journal)
    if operation == "cleanup":
        assert tracker.cleanup_stale_prepared_delivery_intents(
            30, now=prepared_time + timedelta(minutes=31),
        ) == 0
    elif operation == "claim":
        assert not tracker.mark_alert_delivery_attempted(
            intent, expected_prepared_rows=original_rows,
        )["send_allowed"]
    else:
        assert tracker.cancel_alert_delivery_intent(
            intent, expected_prepared_rows=original_rows,
            delivery_definitively_not_accepted=operation == "attested_cancel",
        ) == 0
    assert _states(database) == [(tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)]


@pytest.mark.parametrize("operation", ["cleanup", "cancel", "claim"])
def test_two_sqlite_databases_serialize_prior_acceptance_and_tracker_guard(monkeypatch, tmp_path, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    database, intent = _prepare(monkeypatch, tmp_path)
    original_rows = _stored_rows(database)
    prepared_time = datetime.fromisoformat(original_rows[0]["delivery_prepared_at"])
    original_journal = tracker._delivery_journal_connection
    journal_written = Event()
    competing_lookup_started = Event()
    @contextmanager
    def guarded_journal():
        competing_lookup_started.set()
        with original_journal() as connection:
            yield connection
    monkeypatch.setattr(tracker, "_delivery_journal_connection", guarded_journal)
    def accept_in_independent_sqlite_transaction():
        # Intentionally bypass the process-local Python journal lock to model
        # a second process. The real SQLite BEGIN IMMEDIATE must serialize it.
        with original_journal() as connection:
            connection.execute(
                "INSERT INTO delivery_acceptance_journal "
                "(intent_key, accepted_at, recipient_keys_json, journaled_at, state) "
                "VALUES (?, ?, ?, ?, 'PENDING')",
                (intent, prepared_time.isoformat(), '["' + "a" * 64 + '"]', prepared_time.isoformat()),
            )
            journal_written.set()
            assert competing_lookup_started.wait(5), "guard never checked the acceptance journal"
    def guard():
        assert journal_written.wait(5)
        if operation == "cleanup":
            return tracker.cleanup_stale_prepared_delivery_intents(
                30, now=prepared_time + timedelta(minutes=31),
            )
        if operation == "claim":
            return tracker.mark_alert_delivery_attempted(
                intent, expected_prepared_rows=original_rows,
            )["send_allowed"]
        return tracker.cancel_alert_delivery_intent(intent, expected_prepared_rows=original_rows)
    with ThreadPoolExecutor(max_workers=2) as workers:
        acceptance = workers.submit(accept_in_independent_sqlite_transaction)
        guarded = workers.submit(guard)
        acceptance.result(timeout=10)
        assert guarded.result(timeout=10) in (False, 0)
    assert _states(database) == [(tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)]


def test_cleanup_keeps_journalized_owner_but_releases_unaccepted_other(monkeypatch, tmp_path):
    database, accepted_intent = _prepare(monkeypatch, tmp_path, "JOURNALIZED-OWNER")
    prepared_time = datetime.fromisoformat(_stored_rows(database)[0]["delivery_prepared_at"])
    tracker.prepare_alert_delivery_intent(
        "stock_strategy", [_row("UNACCEPTED-OTHER")],
        tracker.build_alert_delivery_intent_key(
            "stock_strategy", [_row("UNACCEPTED-OTHER")], delivery_recipient_keys=["a" * 64],
        ), delivery_recipient_keys=["a" * 64],
    )
    assert tracker.journal_alert_delivery_acceptance(
        accepted_intent, ["a" * 64], accepted_at=prepared_time,
    )["durable_acceptance"]
    assert tracker.cleanup_stale_prepared_delivery_intents(
        30, now=prepared_time + timedelta(minutes=31),
    ) == 1
    assert [row["ticker"] for row in _stored_rows(database)] == ["JOURNALIZED-OWNER"]


@pytest.mark.parametrize("prepared_at", [
    "2026-02-30T12:00:00+00:00",
    "2026-02-28T24:00:00+00:00",
])
def test_cleanup_preserves_full_but_python_invalid_prepared_timestamp(monkeypatch, tmp_path, prepared_at):
    database, _ = _prepare(monkeypatch, tmp_path)
    # SQLite julianday normalizes impossible calendar dates and hour 24;
    # neither supplies the valid prepared instant required by our contract.
    with pytest.raises(ValueError):
        datetime.fromisoformat(prepared_at)
    assert tracker._parse_utc_datetime(prepared_at) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT julianday(?)", (prepared_at,)).fetchone()[0] is not None
        connection.execute("UPDATE signals SET delivery_prepared_at=?", (prepared_at,))
    assert tracker.cleanup_stale_prepared_delivery_intents(
        30, now=datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc),
    ) == 0
    assert _states(database) == [(tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)]
