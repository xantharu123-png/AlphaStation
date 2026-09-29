"""Real UI counter helpers preserve missing and partial delivery evidence."""
import json
import re

import pytest

from test_frontend_scanner_lifecycle import SOURCE, node_run


def _helper(name, argument):
    line = re.search(r"    const " + name + r" = [^\n]+;", SOURCE)
    assert line, name
    return json.loads(node_run(line.group() + "\nconsole.log(JSON.stringify(" + name + "(" + json.dumps(argument) + ")));"))


@pytest.mark.parametrize("pipeline,expected", [
    ({"sent": 0, "partial": 1}, 1),
    ({"sent": 2, "partial": 3}, 5),
    ({"sent": 0, "partial": 0}, 0),
    ({"sent": 0}, None),
    ({"sent": None, "partial": 0}, None),
])
def test_counter_includes_partial_acceptance_but_does_not_invent_missing_receipts(pipeline, expected):
    assert _helper("mailAccepted", pipeline) == expected


@pytest.mark.parametrize("value,expected", [(None, "—"), (0, 0), (1, 1), ("0", "—")])
def test_unavailable_counter_is_not_a_zero(value, expected):
    assert _helper("mailNumber", value) == expected


def test_mail_operator_panel_uses_read_only_route_and_clears_failed_snapshot():
    component = SOURCE[SOURCE.index("function AdminTab()"):SOURCE.index("// Render App")]
    assert "fetch(`${API}/api/email-alert-audit`, { headers, signal:" in component
    assert "setMailAudit(null);" in component
    assert "SMTP-Annahme ist keine Bestätigung im Postfach" in component
    assert "Gespeicherte Setups, keine Versand- oder Zustellfreigabe" in component
    assert "/api/email-test" not in component
