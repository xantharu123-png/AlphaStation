from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_sidebar_exposes_consistent_trigger_and_retest_reminders():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "const explicitReminderWait" in source
    assert "const hardReminderBlock" in source
    assert "Mail-Reminder bei bestaetigtem" in source
    assert "Pruefung jede Minute" in source
    assert "renderReminderControls()" in source
    assert "{ hours: 1, label: '1h' }" in source
    assert "{ hours: 3, label: '3h' }" in source
    assert "{ hours: 24, label: '1 Tag' }" in source
    assert "finalWatchOnly ||" not in source[source.index("const hardReminderBlock"):source.index("const reminderCondition")]


def test_sidebar_persists_and_can_cancel_active_reminders():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "fetch(`${API}/api/trade-reminders?status=active`)" in source
    assert "fetch(`${API}/api/trade-reminders/${activeReminder.id}`" in source
    assert "typeof reminderExpiryValue === 'number' ? reminderExpiryValue * 1000" in source
    assert "Aktiv bis" in source
    assert "Beenden" in source
