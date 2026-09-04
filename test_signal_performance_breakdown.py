import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from modules import signal_tracker as st
from scripts import signal_performance_breakdown as script


def test_cli_is_read_only_and_uses_decided_fill_denominator(tmp_path, monkeypatch, capsys):
    path = tmp_path / "archive.sqlite"
    created = (datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE signals (id INTEGER, created_at TEXT,scanner TEXT,mail_class TEXT,status TEXT,"
                     "r_realized REAL,entry_filled_at TEXT,entry_fill_price REAL,entry REAL,stop REAL,tp1 REAL,tp2 REAL,direction TEXT)")
        for ident, status, realized, fill in [(1,"STOP_HIT",-1,100),(2,"OPEN",99,100),(3,"STOP_HIT",50,None)]:
            conn.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ident,created,"bi_long","trade",status,realized,created if fill else None,
                          fill,100,95,110,120,"LONG"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(st,"SIGNAL_DB_PATH",str(path))
    monkeypatch.setattr(sys,"argv",["signal_performance_breakdown.py","--days","365"])
    monkeypatch.setattr(st,"_db_connection",lambda: (_ for _ in ()).throw(AssertionError("migration helper called")))
    assert script.main() == 0
    text = capsys.readouterr().out
    assert "Reifezeit, vollstaendig beobachtet" in text
    assert "-1.00" in text
    assert "+99.00" not in text and "+50.00" not in text
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
