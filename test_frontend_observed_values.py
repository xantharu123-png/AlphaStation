"""Execute the shipped display expressions; missing observations are not zero.

No API/provider calls or bundle writes. Rendered browser QA is a separate gate.
"""
import json
import re

import pytest

from test_frontend_scanner_lifecycle import SOURCE, node_run


START = SOURCE.index("function observedMetricNumber(")
HELPERS = SOURCE[START:SOURCE.index("// Penny Stock active trade signals", START)]
PENNY = SOURCE[SOURCE.index("function PennyStocksTab("):SOURCE.index("function VolumeSpikesTab(")]
ORB = SOURCE[SOURCE.index("function ORBScannerTab("):SOURCE.index("function AutoTraderTab(")]
CHART = SOURCE[SOURCE.index("function ChartAnalyseTab("):SOURCE.index("function AdminTab(")]


def evaluate(expression):
    return json.loads(node_run(HELPERS + "\nconsole.log(JSON.stringify(" + expression + "));"))


def _line(block, marker):
    return next(line.strip() for line in block.splitlines() if marker in line)


def _chart_probe(value):
    declarations = CHART[CHART.index("    const d = tickerData || {};"):CHART.index("    const signalColor =")]
    price_line = _line(CHART, "fontSize: 22, fontWeight: 700 }}>")
    price = re.search(r"}}>{(.*)}</span>", price_line).group(1)
    change_line = _line(CHART, "formatObservedMetric(change, 2,")
    change = re.search(r">\({(.*)}\)</span>", change_line).group(1)
    tone = re.search(r"color: (.*?) }}>", change_line).group(1)
    score_line = _line(CHART, "Grade {d.signal_grade}")
    score = re.search(r"\({(.*)}\)", score_line).group(1)
    return evaluate(
        "(() => { const tickerData = {price: VALUE, change_1d: VALUE, signal_score: VALUE};"
        .replace("VALUE", value)
        + declarations
        + f"return {{price: ({price}), change: ({change}), tone: ({tone}), score: ({score})}}; }})()"
    )


@pytest.mark.parametrize("value", [
    "undefined", "null", "''", "'  '", "'\\t'", "NaN", "Infinity", "-Infinity",
    "'unknown'", "true", "false", "[]", "{}", "[0]",
])
def test_missing_or_invalid_observations_never_become_measured_zero(value):
    assert evaluate(f"observedMetricNumber({value})") is None
    assert evaluate(
        f"[formatObservedMetric({value}, 1, '%'), formatObservedMetric({value}, 1, 'x'),"
        f"formatObservedMetric({value}, 0, ' bps'), formatObservedMetric({value}, null, 'R')]"
    ) == ["—"] * 4
    assert _chart_probe(value) == {"price": "—", "change": "—", "tone": "#6b7280", "score": "—"}


@pytest.mark.parametrize("value", ["0", "'0'", "-0", "' 0 '"])
def test_real_zero_remains_an_observation_with_its_units(value):
    assert evaluate(
        f"[formatObservedMetric({value}, 1, '%'), formatObservedMetric({value}, 1, 'x'),"
        f"formatObservedMetric({value}, 0, ' bps'), formatObservedMetric({value}, null, 'R'),"
        f"formatObservedMetric({value}, 2, 'x Basis')]"
    ) == ["0.0%", "0.0x", "0 bps", "0R", "0.00x Basis"]
    assert _chart_probe(value) == {"price": "$0.00", "change": "+0.00%", "tone": "#10b981", "score": "0P"}


@pytest.mark.parametrize("value,expected", [
    ("'12.345'", {"price": "$12.35", "change": "+12.35%", "tone": "#10b981", "score": "12P"}),
    ("-2.5", {"price": "$-2.50", "change": "-2.50%", "tone": "#ef4444", "score": "-2P"}),
    ("0.00123456", {"price": "$0.001235", "change": "+0.00%", "tone": "#10b981", "score": "0P"}),
])
def test_chart_retains_signed_change_precision_and_score_rounding(value, expected):
    assert _chart_probe(value) == expected


@pytest.mark.parametrize("value,penny,orb", [
    ("null", "text-gray-500", "text-gray-500"),
    ("undefined", "text-gray-500", "text-gray-500"),
    ("' '", "text-gray-500", "text-gray-500"),
    ("false", "text-gray-500", "text-gray-500"),
    ("NaN", "text-gray-500", "text-gray-500"),
    ("0", "color-positive", "text-red-500"),
    ("-1", "color-negative", "text-red-500"),
    ("1.15", "color-positive", "text-green-600 font-medium"),
])
def test_penny_and_orb_unknown_values_have_neutral_not_directional_tone(value, penny, orb):
    penny_line = _line(PENNY, "formatObservedMetric(item.change_pct,")
    penny_tone = re.search(r"\$\{(.*?)\}`", penny_line).group(1)
    orb_line = _line(ORB, "<div>Ausbruch-Vol:")
    orb_tone = re.search(r"className={(.*?)}>", orb_line).group(1)
    assert evaluate(
        "(() => { const item = {change_pct: VALUE}; const b = {volume_ratio: VALUE};"
        .replace("VALUE", value)
        + f"return [({penny_tone}), ({orb_tone})]; }})()"
    ) == [penny, orb]


def test_every_scoped_desktop_mobile_orb_display_uses_missing_safe_formatter():
    assert PENNY.count("formatObservedMetric(item.rvol, 1, 'x')") == 2
    assert PENNY.count("formatObservedMetric(item.execution_cost_bps, 0, ' bps')") == 2
    assert "formatObservedMetric(item.change_pct, 1, '%')" in PENNY
    assert "formatObservedMetric(b.volume_ratio, 2, 'x')" in ORB
    assert "formatObservedMetric(b.distance_to_entry_r, null, 'R')" in ORB
    assert "const volumeRatio = observedMetricNumber(b?.volume_ratio);" in ORB
    assert "formatObservedMetric(volumeRatio, 2, 'x Basis')" in ORB
    for field in ("item.change_pct", "item.rvol", "item.execution_cost_bps"):
        assert f"{field} || 0" not in PENNY
    assert "b.volume_ratio || 0" not in ORB
    assert "b?.volume_ratio || 0" not in ORB
    assert "Number(d.price) || 0" not in CHART
    assert "Number(d.change_1d) || 0" not in CHART
    assert "d.signal_score != null ? Math.round(d.signal_score) : 0" not in CHART
