"""Local UI audit fixture. No API import, credentials, market calls, mail or orders.

Serve the real built frontend with explicit synthetic responses on loopback only.
Not a production server. Run: python scripts/audit_ui_fixture.py
"""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import mimetypes
import time
import argparse
import threading

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
STAMP = datetime.now(timezone.utc).isoformat()
ERRORS = []
SCANNER_QA = {"bi": "normal", "momentum": "normal", "stamp": STAMP}
SCANNER_QA_LOCK = threading.Lock()


def qa_scanner_payload(kind, direction="LONG"):
    with SCANNER_QA_LOCK:
        state = dict(SCANNER_QA)
    mode, stamp = state[kind], state["stamp"]
    row = bi_row(direction)
    if kind == "momentum":
        row.update({"strategy": "Momentum Breakout Long", "scanner": "stock_strategy"})
    rows = [row] if mode in {"normal", "background_done", "failed_with_cache"} else []
    running = mode == "background_running"
    missing = mode in {"missing", "idle_empty"}
    return {
        "status": "success", "data": rows, "count": len(rows),
        "cached_at": None if missing else (STAMP if running else stamp),
        "scan_running": running, "partial": False,
        "scan_error": "scan_data_unavailable" if mode in {"failed", "failed_with_cache"} else None,
        "warnings": [], "checked": 20 if running else 100, "total": 100,
        "diagnostics": {"universe_count": 100, "raw_cache_rows": len(rows),
                        "validated_scanner_signals": len(rows), "visible_scanner_signals": len(rows),
                        "final_results": len(rows), "checked": 100, "total": 100,
                        "funnel": {"coverage": "incomplete" if mode in {"failed", "failed_with_cache"} else "complete",
                                   "checked": 100, "total": 100, "analysis_attempts": 100,
                                   "data_failures": {}, "legitimate_filters": {"below_17": 100-len(rows)}},
                        "stage_counts": {"priced_snapshot": 100, "momentum_breakout_gate": len(rows)},
                        "rejected": {"momentum_breakout_gate": 100-len(rows)},
                        "indicator_gate": {"minimum_green": 17, "total_indicators": 20}},
    }


def bi_row(direction="LONG"):
    short = direction == "SHORT"
    return {
        "ticker": "QADEMO", "company_name": "Synthetischer QA-Fall", "price": 100,
        "direction": direction, "bi_direction": direction, "grade": "A", "score": 88,
        "bi_grade": "A", "bi_score": 88, "rvol": 2.1, "rsi": 56, "volume": 1500000,
        "change_pct": -2 if short else 2, "bi_indicators_green": 17,
        "bi_indicators_required": 17, "bi_indicators_total": 20,
        "bi_indicators_available": 20, "bi_indicator_contract_version": "stock-bi-20-v3",
        "bi_indicator_checks": [{"id": i, "name": f"QA-Faktor {i}", "available": True, "passed": i <= 17, "reason": "Synthetischer Test"} for i in range(1, 21)],
        "trade_action": "WAIT_FOR_TRIGGER", "data_as_of": STAMP,
        "trade_setup": {
            "direction": direction, "entry": 100, "stop": 105 if short else 95,
            "tp1": 90 if short else 110, "tp2": 85 if short else 115,
            "rr": 2.5, "rr_tp1": 2, "rr_tp2": 3,
            "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2",
            "tp1_source": "QA bestätigte Zone", "tp2_source": "QA Projektion",
            "tp1_is_projection": False, "tp2_is_projection": True,
            "stop_source": "QA Invalidation", "model": "QA ONLY",
        },
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/__qa/scanners":
            request = json.loads(body or b"{}")
            allowed = {"normal", "idle_empty", "background_running", "background_done", "empty",
                       "failed", "failed_with_cache", "cooldown", "busy", "missing", "fetch_error"}
            with SCANNER_QA_LOCK:
                for kind in ("bi", "momentum"):
                    if request.get(kind) in allowed:
                        SCANNER_QA[kind] = request[kind]
                SCANNER_QA["stamp"] = datetime.now(timezone.utc).isoformat()
            return self.send_json({"ok": True, "synthetic_only": True})
        if self.path in {"/api/bi-scan", "/api/scan"}:
            kind = "bi" if self.path == "/api/bi-scan" else "momentum"
            with SCANNER_QA_LOCK:
                mode = SCANNER_QA[kind]
                if mode == "cooldown":
                    return self.send_json({"detail": "Scan cooldown active", "retry_after_seconds": 60}, 429)
                if mode == "busy":
                    return self.send_json({"status": "already_running", "accepted": False})
                SCANNER_QA["stamp"] = datetime.now(timezone.utc).isoformat()
            return self.send_json({"status": "started", "accepted": True, "synthetic_only": True})
        if self.path == "/api/run-backtest":
            # Return a fixture, never run any strategy or touch a database.
            payload = {
                "total_trades": 1, "total_signals": 3, "unresolved": 2, "no_fill": 0,
                "win_rate": 100, "total_return": 1, "avg_pnl": 1, "max_drawdown": 0,
                "avg_r": None, "profit_factor_display": "∞", "n_tickers": 1,
                "data_quality": {"status": "PARTIAL", "failed_fetch_days": 1, "missing_expected_sessions": ["2026-09-02"]},
                "verdict": {"status": "data_incomplete", "label": "DATEN UNVOLLSTÄNDIG", "color": "orange", "tradable": False, "summary": "Synthetischer Teil-Datensatz, keine Freigabe.", "reasons": []},
                "out_of_sample": {"status": "data_incomplete", "total_trades": 1},
                "trades": [{"ticker": "QADEMO", "entry_date": "2026-09-01", "exit_date": "2026-09-03", "entry_price": 1.012e-8, "exit_price": 1.02212e-8, "pnl_pct": 1, "r_multiple": None, "outcome": "EOD", "type": "LONG"}],
            }
            request = json.loads(body or b"{}")
            if request.get("strategy") == "Momentum Breakout Long":
                # Deliberately contradictory verdict proves the frontend cannot
                # promote a proxy result to a live/paper release.
                payload.update({
                    "methodology_label": "QA: Tagesdaten-Näherungsmodell – kein Live-Replay",
                    "methodology_warnings": [
                        "Nächster Tagesopen, fester 5%-Stop, Ziele 1,5R/2,5R; keine Live-Struktur-Exits.",
                        "Historische 5-Minuten-Trigger, 4H-Prüfungen und tatsächliche Fills fehlen.",
                    ],
                    "live_equivalent": False,
                    "live_validation_eligible": False,
                    "paper_autotrade_release_eligible": False,
                    "verdict": {"status": "approved", "tradable": True, "label": "QA ABSICHTLICH WIDERSPRÜCHLICH", "reasons": []},
                })
            return self.send_json(payload)
        if self.path == "/__qa/error":
            ERRORS.append(body.decode(errors="replace")[:2000])
            return self.send_json({"ok": True})
        return self.send_json({"detail": "QA: mutations disabled"}, 403)

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/__qa/errors":
            return self.send_json(ERRORS)
        if path == "/api/auth/me":
            return self.send_json({"user": {"name": "QA – synthetisch", "email": "qa@example.invalid", "plan": "elite", "is_admin": True}, "limits": {"plan_name": "QA", "allowed_tabs": None, "max_scans_per_day": 999}})
        if path == "/api/health":
            return self.send_json({"status": "healthy", "revision": "LOCAL-QA-NOT-PRODUCTION"})
        if path == "/api/strategies":
            return self.send_json({"strategies": {"Momentum Breakout Long": {"display_group": "Momentum"}}, "categories": {}})
        if path == "/api/scan-results":
            with SCANNER_QA_LOCK:
                mode = SCANNER_QA["momentum"]
            if mode == "fetch_error":
                return self.send_json({"detail": "QA unavailable"}, 503)
            return self.send_json(qa_scanner_payload("momentum"))
        if path == "/api/backtest-strategies":
            return self.send_json({"strategies": [
                {"id": "sma_crossover", "name": "QA – fehlende R / partielle Daten", "requires_ticker": False, "category": "QA", "direction": "long"},
                {"id": "Momentum Breakout Long", "name": "QA – Momentum-Modellgrenze", "requires_ticker": False, "category": "QA", "direction": "long"},
            ]})
        if path == "/api/scheduler-status":
            return self.send_json({"running": False, "scans": {}})
        if path == "/api/scan-status":
            return self.send_json({"scheduler_running": True, "scans": {
                **{f"bi_{direction}": {
                    "running": SCANNER_QA["bi"] == "background_running",
                    "last_run": SCANNER_QA["stamp"] if SCANNER_QA["bi"] in {"background_done", "empty"} else STAMP,
                    "next_run": None, "interval_min": 180, "cache_health": "ok",
                } for direction in ("long", "short")},
                "crypto_explosion": {"running": True, "last_run": STAMP, "next_run": None,
                    "interval_min": 15, "cache_health": "stuck", "running_since_sec": 2102,
                    "timeout_minutes": 35, "progress": {"running": True, "checked": 430,
                        "total": 1000, "hits": 12, "hits_label": "Setups vor Endpruefung",
                        "seconds_since_progress": 2, "status": "scanning",
                        "detail": "Crypto Long Engine: bybit 130/300 | binance 120/300 | mexc 100/250 | bitget 80/150; 0 Prueffehler"}},
                "crypto_trade_signals": {"running": False, "last_run": STAMP, "next_run": None,
                    "interval_min": 15, "cache_health": "ok"},
            }})
        if path == "/api/bi-results":
            direction = query.get("direction", ["long"])[0].upper()
            with SCANNER_QA_LOCK:
                mode = SCANNER_QA["bi"]
            if mode == "fetch_error":
                return self.send_json({"detail": "QA unavailable"}, 503)
            return self.send_json(qa_scanner_payload("bi", direction))
        if path == "/api/ticker-detail":
            # Deliberately contradictory live enrichment: selected SHORT must
            # retain its original snapshot, not this generic LONG setup.
            time.sleep(0.3)
            return self.send_json({"ticker": "QADEMO", "price": 101, "company_name": "Live QA enrichment", "signal_grade": "B", "signal_score": 61, "trade_setup": bi_row("LONG")["trade_setup"], "confluence": {"direction": "LONG"}, "bi_scanner": {"grade": "B", "direction": "LONG"}, "indicators": {}})
        if path == "/api/chart-data":
            return self.send_json({"status": "success", "data": [], "candles": [], "overlays": {}, "direction": query.get("direction", ["LONG"])[0]})
        if path == "/api/crash-monitor-results":
            return self.send_json({"status": "success", "cached_at": STAMP, "data": [{"fear_score": None, "data_status": "partial", "fear_level": "UNBEKANNT", "vix": {}, "vix_proxy": {"symbol": "UVXY", "price": 12.3}, "indices": [], "breadth": {"advancing": 0, "declining": 10, "advancing_pct": 0, "declining_pct": 100}}]})
        if path.startswith("/api/"):
            return self.send_json({"status": "success", "data": [], "count": 0, "strategies": [], "plans": {}, "reminders": [], "positions": [], "scans": {}})
        file = (FRONTEND / (path.lstrip("/") or "index.html")).resolve()
        if not file.is_relative_to(FRONTEND) or not file.is_file():
            return self.send_json({"error": "not found"}, 404)
        data = file.read_bytes()
        if file.name == "index.html":
            injection = """<script>window.ALPHA_API_BASE=location.origin;window.addEventListener('error',e=>fetch('/__qa/error',{method:'POST',body:String(e.message)}));window.addEventListener('unhandledrejection',e=>fetch('/__qa/error',{method:'POST',body:String(e.reason)}));</script><style>body:before{content:'LOKALE QA · SYNTHETISCHE DATEN · KEINE ORDERS';display:block;background:#fef08a;color:#000;text-align:center;font:12px monospace;padding:4px;}</style>"""
            data = data.replace(b"</head>", injection.encode() + b"</head>")
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Synthetic audit UI only: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
