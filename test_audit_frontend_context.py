"""Static UX contracts plus actual JS formatting; browser evidence is separate."""
import json
import re
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path("frontend/index.html").read_text(encoding="utf-8")


def test_bi_details_keep_selected_direction_snapshot_and_all_twenty_checks():
    assert "selected_snapshot: true" in SOURCE
    assert "if (cd.selected_snapshot && scannerSetup) return scannerSetup" in SOURCE
    assert "new URLSearchParams({ticker, direction: selectedDirection" in SOURCE
    assert "if (!controller.signal.aborted) setTickerData(payload)" in SOURCE
    assert "Warum dieses BI-Signal?" in SOURCE
    assert "Die Planlevels bleiben unverändert" in SOURCE
    assert "TP1: bestaetigte Zone · TP2: Projektion" in SOURCE
    assert "Aktuelle Detailabfrage" in SOURCE
    assert "Änderung unbekannt" in SOURCE


def test_missing_crash_data_and_performance_scope_are_not_claimed_as_success():
    assert "cm.fear_score || 0" not in SOURCE
    assert "fearData?.score ?? 50" not in SOURCE
    assert "kein CNN-Index und keine Verlustwahrscheinlichkeit" in SOURCE
    assert "Versandkohorte, keine Kontorendite" in SOURCE
    assert "nicht automatisch alle Treffer in der App" in SOURCE


@pytest.mark.parametrize("row,expected", [
    ({}, "Nicht verfügbar"), ({"funding_rate": None}, "Nicht verfügbar"),
    ({"funding_rate": True}, "Nicht verfügbar"), ({"funding_rate_pct": ""}, "Nicht verfügbar"),
    ({"funding_rate": 0, "funding_rate_unit": "fraction"}, "0.000%"),
    ({"funding_rate": .0001, "funding_rate_unit": "fraction"}, "0.010%"),
    ({"funding_rate": .01, "funding_rate_unit": "percent"}, "0.010%"),
    ({"funding_rate_pct": .01, "funding_available": False}, "Nicht verfügbar"),
])
def test_funding_formatter_executes_without_inventing_zero(row, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for executing frontend formatter")
    function = SOURCE.split("function formatFundingRate(row) {", 1)[1].split("\n}\n", 1)[0]
    code = "function formatFundingRate(row) {" + function + "\n}\nconsole.log(formatFundingRate(" + json.dumps(row) + "));"
    result = subprocess.run([node, "-e", code], check=True, capture_output=True, text=True, encoding="utf-8")
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("value", [1.012e-8, 9.9e-9, 1.1e-8, 1.18e-8, None, True, ""])
def test_all_crypto_price_displays_preserve_micro_level_geometry_and_unknown(value):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for executing frontend formatter")
    functions = re.findall(
        r"const (formatPrice|formatSidebarPrice|formatBtPrice) = \((\w+)\) => \{(.*?)\n    \};",
        SOURCE, re.S,
    )
    assert len(functions) == 4
    for name, arg, body in functions:
        code = f"const {name} = ({arg}) => {{{body}\n}}; console.log({name}({json.dumps(value)}));"
        result = subprocess.run([node, "-e", code], check=True, capture_output=True, text=True, encoding="utf-8")
        rendered = result.stdout.strip()
        if value is None or isinstance(value, bool) or value == "":
            assert rendered == "-"
        else:
            assert rendered.startswith("$")
            assert float(rendered[1:]) == pytest.approx(value, rel=1e-6, abs=0)


@pytest.mark.parametrize("value,expected,style", [
    (None, "—", "text-gray-500"), (True, "—", "text-gray-500"),
    ("", "—", "text-gray-500"), ("NaN", "—", "text-gray-500"),
    (0, "0.00", "color-positive"), (-1, "-1.00", "color-negative"),
])
def test_backtest_missing_r_is_not_displayed_as_zero(value, expected, style):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for executing frontend formatter")
    start = SOURCE.index("function formatBacktestMetric(")
    end = SOURCE.index("function formatFundingRate(", start)
    code = SOURCE[start:end] + f"\nconsole.log(JSON.stringify([formatBacktestMetric({json.dumps(value)}), backtestMetricClass({json.dumps(value)})]));"
    result = subprocess.run([node, "-e", code], check=True, capture_output=True, text=True, encoding="utf-8")
    assert json.loads(result.stdout) == [expected, style]
    assert "formatBacktestMetric(trade.r_multiple)" in SOURCE
    assert "trade.r_multiple ?? 0" not in SOURCE
    assert 'data-testid="backtest-data-coverage"' in SOURCE
    assert "Ungeklärte Fälle sind weder 0R noch abgeschlossene Trades" in SOURCE
