"""Evidence-led weekly report UI/job regressions; all fixtures are synthetic."""
from datetime import datetime
import math

import pytest

import bg_service as bg
from test_weekly_report_mail import _setup, FRIDAY_1620


def summary(*, total=1, resolved=1, unknown=0, no_fill=0, opened=0, untracked=0,
            strict=True, excluded=0):
    bucket = {
        "signals": total, "open": opened, "decided_signals": resolved,
        "managed_be_decided_signals": resolved, "managed_be_unresolved": unknown,
        "be_decided_signals": resolved, "be_unresolved": unknown,
        "sum_r_managed_50_50_be": -1.0 if resolved else None,
        "sum_r_managed_50_50_be_upper": -1.0 if resolved else None,
        "avg_r_managed_50_50_be": -1.0 if resolved else None,
        "managed_be_wins": 0, "managed_be_losses": resolved,
        "managed_be_win_rate_pct": 0.0 if resolved else None,
        "managed_be_win_rate_wilson_95": {"lower_pct": 0.0, "upper_pct": 79.3},
        "report_counts": {
            "resolved": resolved, "evidence_unresolved": unknown,
            "no_fill": no_fill, "still_open": opened, "untracked": untracked,
            "report_total": total, "reconciled": True,
        },
    }
    return {
        "source_read_complete": True, "report_data_available": True,
        "total": bucket, "per_scanner": {"synthetic": bucket},
        "delivery_evidence": {"required": strict, "excluded_signals": excluded},
        "excluded_not_mature": 0,
        "recent": [{"ticker": "SYNTHETIC", "scanner": "synthetic",
                    "direction": "LONG", "status": "STOP_HIT",
                    "r_managed_50_50_be": -1.0}],
    }


def render(performance, activity=None):
    return bg._build_mature_weekly_report_mail(
        activity or performance, performance, now_et=FRIDAY_1620, watchdog_events=[]
    )


def test_global_accepted_not_personal_inbox_and_legacy_exclusion_visible():
    _, body = render(summary(excluded=473), summary(excluded=9))
    assert "Globaler Tracker: SMTP-Annahme" in body
    assert "keine persoenliche Posteingangsbilanz" in body
    assert "9 im 7-Tage-Aktivitaetsfenster, 473 im 30-Tage-Reifefenster" in body
    assert "Altbestand bleibt erhalten" in body


def test_legacy_direct_renderer_never_claims_accepted_delivery():
    _, body = render(summary(strict=False))
    assert "Versandnachweise nicht geprueft" in body
    assert "Globaler Tracker: SMTP-Annahme" not in body


def test_received_86_row_shape_is_explicitly_reconciled_not_fake_total_loss():
    s = summary(total=86, resolved=45, unknown=40, no_fill=1)
    s["total"]["sum_r_managed_50_50_be"] = -47.47
    s["total"]["sum_r_managed_50_50_be_upper"] = -47.47
    subject, body = render(s)
    assert "Teilbilanz" in subject and "-47.5R" not in subject
    text = " ".join(body.split())
    assert "45 ausgewertet · 40 Evidenz offen · 1 ohne Einstieg" in text
    assert "-47.5R" in body  # genuine recorded lower values are not hidden
    assert "Reliability gesperrt" in body
    assert "95%-KI konservativer Pfad" not in body


def test_missing_terminal_evidence_blocks_even_without_managed_unresolved():
    s = summary(total=1, resolved=0, unknown=1)
    s["total"]["managed_be_unresolved"] = s["total"]["be_unresolved"] = 0
    subject, body = render(s)
    assert "0 resolved / 1 unresolved" in subject
    assert "Reliability gesperrt" in body


def test_unknown_upper_order_blocks_even_if_lower_value_is_numerical():
    s = summary(total=1, resolved=0, unknown=1)
    s["total"].update(managed_be_decided_signals=1, be_decided_signals=1,
                      upper_unresolved=1, sum_r_managed_50_50_be=-1.0,
                      sum_r_managed_50_50_be_upper=-1.0)
    subject, body = render(s)
    assert "Teilbilanz" in subject
    assert "Kursreihenfolge ungeklaert" in body
    assert "Reliability gesperrt" in body


def test_no_fill_only_remains_classified_without_zero_performance_claim():
    subject, body = render(summary(total=1, resolved=0, no_fill=1))
    assert "Noch keine ausgewertete Bilanz" in subject
    assert "+0.0R" not in subject
    assert "1 ohne Einstieg" in " ".join(body.split())
    assert "synthetic" in body  # scanner is not dropped for no-fill-only rows


def test_false_reconciliation_flag_cannot_hide_overlapping_counts():
    s = summary(total=1, resolved=1, no_fill=1)
    subject, body = render(s)
    assert "Teilbilanz" in subject
    assert "Reliability gesperrt" in body


def test_filled_untracked_position_still_blocks_control_reliability():
    s = summary(total=2, resolved=1, untracked=1)
    s["total"]["control_unresolved"] = 1
    subject, body = render(s)
    assert "1 resolved / 1 unresolved" in subject
    assert "Reliability gesperrt" in body
    assert "95%-KI konservativer Pfad" not in body
    assert '#fffbeb' in body


def test_unavailable_data_has_no_zero_or_stale_activity_performance_recent():
    s = summary()
    s["report_data_unavailable"] = True
    subject, body = render(s)
    assert subject == "Wochenreport Signal-Tracker: Berichtsdaten nicht verfuegbar"
    assert "Aktivitaetsdaten nicht verfuegbar" in body
    assert "<b>0</b>" not in body and "<b>1</b>" not in body
    assert "SYNTHETIC" not in body and "Reife Bilanz je Scanner" not in body


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), float("-inf"), "not-a-number"])
def test_nonfinite_boolean_and_text_values_not_rendered_as_r_or_ci(bad):
    s = summary()
    s["total"].update(sum_r_managed_50_50_be=bad,
                      sum_r_managed_50_50_be_upper=bad, alerts_per_day=bad,
                      managed_be_win_rate_wilson_95={"lower_pct": bad, "upper_pct": bad})
    s["recent"][0]["r_managed_50_50_be"] = bad
    _, body = render(s)
    assert "+1.00R" not in body
    assert "nanR" not in body and "infR" not in body
    assert "nan%" not in body and "inf%" not in body


def test_old_loader_is_not_silently_used_for_signal_delivery_report(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bg, "load_performance_summary", lambda days=7: summary(strict=False))
    assert bg._run_weekly_report({}) is False
    assert sent == []


@pytest.mark.parametrize("data", [summary(strict=False), {"error": "RuntimeError", **summary()}])
def test_unqualified_or_failed_loader_does_not_send_zero_report(monkeypatch, tmp_path, data):
    sent = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bg, "load_performance_summary", lambda **kwargs: data)
    assert bg._run_weekly_report({}) is False
    assert sent == []
    assert not bg._email_dedupe_active(bg._weekly_report_dedupe_key(FRIDAY_1620), bg._WEEKLY_REPORT_DEDUPE_SEC)


def test_untrusted_ticker_and_scanner_markup_is_escaped():
    s = summary()
    s["recent"][0].update(ticker="<script>T</script>", scanner="<script>S</script>")
    _, body = render(s)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.parametrize("flag", ["source_read_complete", "report_data_available"])
def test_incomplete_qualified_loader_is_not_successful_null_cohort(monkeypatch, tmp_path, flag):
    data = summary()
    data[flag] = False
    sent = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bg, "load_performance_summary", lambda **kwargs: data)
    assert bg._run_weekly_report({}) is False
    assert sent == []


def test_weekly_html_render_artifact(tmp_path):
    """Pure illustrative HTML for local desktop/mobile browser verification."""
    s = summary(total=86, resolved=45, unknown=40, no_fill=1, excluded=473)
    s["total"].update(sum_r_managed_50_50_be=-47.47, sum_r_managed_50_50_be_upper=-47.47,
                      managed_be_wins=1, managed_be_losses=44, managed_be_win_rate_pct=2.2,
                      avg_r_managed_50_50_be=-1.055, profit_factor_managed_be=0.012,
                      breakeven_win_rate_managed_be_pct=65.3)
    s["grade_calibration_cells"] = [{
        "reporting_only": True, "sample_reliable": True, "n": 30, "unresolved": 0,
        "scanner": "synthetic", "grade": "A", "direction": "LONG",
        "horizon": "swing:5bars", "market_regime": "NEUTRAL",
        "hit_rate_pct": 50.0, "avg_r": 0.25, "sum_r": 7.5, "profit_factor": 1.5,
        "win_rate_wilson_95": {"lower_pct": 33.2, "upper_pct": 66.8},
    }]
    _, body = render(s)
    preview = tmp_path / "weekly-repair-preview.html"
    preview.write_text(body, encoding="utf-8")
    assert preview.is_file()
    assert body.count('class="report-grid"') == 5
    assert "direkter Post-Send-Erfassung" not in body
    print("Synthetic weekly preview:", preview)
