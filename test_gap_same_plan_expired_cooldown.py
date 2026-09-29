"""Two Swiss Gap slots: real native producer/gates/tracker, only fake SMTP."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_same_open_daily_gap_plan_is_not_remailed_after_eight_hour_cooldown(tmp_path, direction):
    fixture = Path(__file__).with_name("test_stock_native_plan_mail_integration.py")
    result = subprocess.run([sys.executable, "-B", str(fixture), direction,
                             str(tmp_path), "reclaimed", "10"],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("NATIVE_PLAN_RESULT="))
    actual = json.loads(line.split("=", 1)[1])
    second = actual["repeated_attempt"]
    assert actual["delivery_outcome"] == "accepted"
    assert actual["smtp_messages"] == 1  # First02:00 mail only, never second12:00.
    assert second["session"] == actual["session"] == "2026-09-17"
    for field in ("entry", "stop", "tp1", "tp2"):
        assert second["levels"][field] == actual["levels"][field]
    assert second["cooldown_remaining"] == 0
    assert second["alertable_now"] is True  # The elapsed TTL is not the blocker.
    assert second["open_equivalent"] is True
    assert second["last_decision"] == "open_equivalent_trade:1"
    assert len(actual["persisted_deliveries"]) == 1
    assert actual["persisted_deliveries"][0][:4] == ["trade", "email", "stocks_swing", "ACTIVE"]
