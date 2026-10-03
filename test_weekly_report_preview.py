"""Preview must use the same qualified weekly contract without sending mail."""
import sys

import bg_service
from scripts import preview_weekly_report as preview
from test_weekly_report_delivery_presentation import summary


def test_preview_loads_accepted_activity_and_maturity_without_smtp(monkeypatch, tmp_path):
    calls = []
    def loader(**kwargs):
        calls.append(kwargs)
        return summary()
    monkeypatch.setattr(bg_service, "load_performance_summary", loader)
    monkeypatch.setattr(bg_service, "shadow_summary", lambda **kwargs: {}, raising=False)
    monkeypatch.setattr(bg_service, "_load_watchdog_events", lambda **kwargs: [])
    monkeypatch.setattr(bg_service, "_send_email_alert", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no mail")))
    monkeypatch.setattr(preview, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["preview_weekly_report.py"])
    assert preview.main() == 0
    assert calls == [
        {"days": 7, "require_delivery_evidence": True},
        {"days": 30, "mature_only": True, "require_delivery_evidence": True},
    ]
    body = (tmp_path / "weekly_report_preview.html").read_text(encoding="utf-8")
    assert "Bestandsabgleich" in body
    assert "Globaler Tracker: SMTP-Annahme" in body


def test_preview_read_error_preserves_previous_html(monkeypatch, tmp_path):
    old = tmp_path / "weekly_report_preview.html"
    old.write_text("known previous report", encoding="utf-8")
    monkeypatch.setattr(bg_service, "load_performance_summary", lambda **kwargs: {
        "error": "report_data_unavailable:RuntimeError", "report_data_available": False,
    })
    monkeypatch.setattr(preview, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["preview_weekly_report.py"])
    assert preview.main() == 1
    assert old.read_text(encoding="utf-8") == "known previous report"
