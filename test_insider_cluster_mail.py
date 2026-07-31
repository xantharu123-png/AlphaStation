#!/usr/bin/env python3
"""Tests fuer den Insider-Cluster-Alarm (bg_service._run_insider_cluster_alert).

Beweist das Betreiber-Versprechen (31.07.): genau EINE ℹ️-Mail je NEUEM
KAUF-Cluster — Fenster Mo–Fr 16:30–23:00 ET, Tages-Markier-Key unabhaengig
vom Ergebnis, Cluster-Dedupe ueber Zusammensetzung (gewachsenes Cluster =
neue Info = neue Mail), Mark erst nach erfolgreichem Versand (B2),
Verkauf-Cluster mailen nie, Job wirft nie.

Komplett offline: Dedupe-Datei auf tmp_path, Cluster-Fetch + Versand gemockt.
"""
import os
import sys
import time
from datetime import datetime as _real_datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bg_service

MON_1645 = _real_datetime(2026, 7, 27, 16, 45)   # Montag im Fenster
MON_1600 = _real_datetime(2026, 7, 27, 16, 0)    # vor Fenster
SAT_1700 = _real_datetime(2026, 8, 1, 17, 0)     # Samstag
TUE_1645 = _real_datetime(2026, 7, 28, 16, 45)   # naechster Tag im Fenster


def _cluster(symbol="ACME", names=("Doe", "Smith", "Chen"), side="buy",
             total=1_000_000.0):
    return {"symbol": symbol, "issuer": f"{symbol} Corp", "side": side,
            "insiders": len(names), "names": list(names), "trades": len(names),
            "total_value_usd": total, "latest_date": "2026-07-27"}


def _setup(monkeypatch, tmp_path, clusters):
    """tmp-Dedupe + gemockter Cluster-Fetch + Mail-Recorder."""
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters",
                        lambda: {"status": "ok", "clusters": clusters})
    sent = []

    def _recorder(subject, body_html, secrets, mail_class="trade", **kwargs):
        sent.append({"subject": subject, "body": body_html,
                     "mail_class": mail_class})
        return True

    monkeypatch.setattr(bg_service, "_send_email_alert", _recorder)
    return sent


# ── Fenster-Gate ─────────────────────────────────────────────────────────────

def test_window_gate(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_cluster()])
    assert bg_service._insider_cluster_window_open(MON_1645) is True
    assert bg_service._insider_cluster_window_open(MON_1600) is False
    assert bg_service._insider_cluster_window_open(SAT_1700) is False
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1600) is False
    assert bg_service._run_insider_cluster_alert({}, now_et=SAT_1700) is False
    assert sent == []


# ── Kern-Versprechen ─────────────────────────────────────────────────────────

def test_new_buy_cluster_sends_one_info_mail(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_cluster()])
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is True
    assert len(sent) == 1
    mail = sent[0]
    assert mail["mail_class"] == "info"
    assert "ACME" in mail["subject"]
    assert "3 Insider" in mail["subject"]
    assert "Doe" in mail["body"] and "Chen" in mail["body"]
    assert "Kein Signal, kein Trigger" in mail["body"]


def test_same_cluster_not_mailed_twice(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_cluster()])
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is True
    # Naechster Tag, gleiches Cluster (gleiche Zusammensetzung) => keine Mail
    assert bg_service._run_insider_cluster_alert({}, now_et=TUE_1645) is False
    assert len(sent) == 1


def test_grown_cluster_mails_again(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_cluster()])
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is True
    # Cluster waechst (vierter Insider) => neue Information => neue Mail
    grown = [_cluster(names=("Doe", "Smith", "Chen", "Mueller"))]
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters",
                        lambda: {"status": "ok", "clusters": grown})
    assert bg_service._run_insider_cluster_alert({}, now_et=TUE_1645) is True
    assert len(sent) == 2
    assert "4 Insider" in sent[1]["subject"]


def test_sell_clusters_never_mail(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [_cluster(side="sell")])
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False
    assert sent == []


def test_no_clusters_marks_day_without_mail(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, tmp_path, [])
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False
    assert sent == []
    # Zweiter Anklopfer am selben Tag => sofort False (Tages-Key aktiv)
    called = {"n": 0}

    def _counting():
        called["n"] += 1
        return {"status": "ok", "clusters": [_cluster()]}
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters", _counting)
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False
    assert called["n"] == 0  # Fetch gar nicht erst aufgerufen


def test_send_failure_retries_without_day_mark(monkeypatch, tmp_path):
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters",
                        lambda: {"status": "ok", "clusters": [_cluster()]})
    monkeypatch.setattr(bg_service, "_send_email_alert",
                        lambda *a, **k: False)  # SMTP down
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False
    # Retry im selben Fenster: Fetch wird erneut aufgerufen (kein Tages-Key)
    called = {"n": 0}

    def _counting():
        called["n"] += 1
        return {"status": "ok", "clusters": []}
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters", _counting)
    bg_service._run_insider_cluster_alert({}, now_et=MON_1645)
    assert called["n"] == 1


def test_module_missing_and_exceptions_never_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters", None)
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False

    def _boom():
        raise RuntimeError("kaputt")
    monkeypatch.setattr(bg_service, "_fetch_insider_clusters", _boom)
    assert bg_service._run_insider_cluster_alert({}, now_et=MON_1645) is False


def test_scheduler_registered():
    """Der Runner kennt den Job (Wiring-Regression)."""
    src = open(os.path.join(_DIR, "bg_service.py"), encoding="utf-8").read()
    assert 'SCHEDULE_INTERVAL["insider_cluster_alert"]' in src
    assert '_run_insider_cluster_alert(secrets)' in src
    assert bg_service._INSIDER_CLUSTER_CHECK_INTERVAL_SEC == 15 * 60
