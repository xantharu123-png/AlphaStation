"""Execute the shipped time formatter; scanner progress stays informational."""

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("timestamp,expected", [
    ("2026-09-08T08:00:00", "vor 5 min"),
    ("2026-09-08T08:00:00Z", "vor 5 min"),
    ("2026-09-08T08:00:00+00:00", "vor 5 min"),
    ("2026-09-08T10:00:00+02:00", "vor 5 min"),
    ("broken", "unbekannt"), (None, "nie"),
])
def test_scheduler_dates_accept_naive_utc_and_explicit_offsets(timestamp, expected):
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    helper = source[source.index("function getRelativeTime("):source.index("function getScanTiming(")]
    js = "const RealDate=Date; global.Date=class extends RealDate { constructor(...args) { super(...(args.length ? args : ['2026-09-08T08:05:00Z'])); } };\n"
    js += helper + "\nprocess.stdout.write(JSON.stringify(getRelativeTime(" + json.dumps(timestamp) + ")));"
    result = subprocess.run(["node", "-e", js], capture_output=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == expected


def test_crypto_progress_uses_existing_control_without_candidate_list():
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert 'progressOverride={schedulerStatus?.scans?.crypto_explosion?.progress}' in source
    assert 'noch kein fertiges Scan-Ergebnis' in source
    assert "prog.hits_label || 'Treffer'" in source
    assert "stuck: ['Warnbudget ueberschritten'" in source
