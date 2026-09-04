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

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
STAMP = datetime.now(timezone.utc).isoformat()
ERRORS = []


def bi_row(direction="LONG"):
    short = direction == "SHORT"
    return {
        "ticker": "QADEMO", "company_name": "Synthetischer QA-Fall", "price": 100,
        "direction": direction, "bi_direction": direction, "grade": "A", "score": 88,
        "bi_grade": "A", "bi_score": 88, "rvol": 2.1, "rsi": 56, "volume": 1500000,
        "change_pct": -2 if short else 2, "bi_indicators_green": 17,
        "bi_indicators_required": 17, "bi_indicators_total": 20,
        "bi_indicators_available": 20, "bi_indicator_contract_version": "stock-bi-20-v2",
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
        if self.path == "/api/run-backtest":
            # Return a fixture, never run any strategy or touch a database.
            return self.send_json({
                "total_trades": 1, "total_signals": 3, "unresolved": 2, "no_fill": 0,
                "win_rate": 100, "total_return": 1, "avg_pnl": 1, "max_drawdown": 0,
                "avg_r": None, "profit_factor_display": "∞", "n_tickers": 1,
                "data_quality": {"status": "PARTIAL", "failed_fetch_days": 1, "missing_expected_sessions": ["2026-09-02"]},
                "verdict": {"status": "data_incomplete", "label": "DATEN UNVOLLSTÄNDIG", "color": "orange", "tradable": False, "summary": "Synthetischer Teil-Datensatz, keine Freigabe.", "reasons": []},
                "out_of_sample": {"status": "data_incomplete", "total_trades": 1},
                "trades": [{"ticker": "QADEMO", "entry_date": "2026-09-01", "exit_date": "2026-09-03", "entry_price": 1.012e-8, "exit_price": 1.02212e-8, "pnl_pct": 1, "r_multiple": None, "outcome": "EOD", "type": "LONG"}],
            })
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
            return self.send_json({"strategies": [], "categories": {}})
        if path == "/api/backtest-strategies":
            return self.send_json({"strategies": [{"id": "sma_crossover", "name": "QA – fehlende R / partielle Daten", "requires_ticker": False, "category": "QA", "direction": "long"}]})
        if path == "/api/scheduler-status":
            return self.send_json({"running": False, "scans": {}})
        if path == "/api/bi-results":
            direction = query.get("direction", ["long"])[0].upper()
            return self.send_json({"status": "success", "data": [bi_row(direction)], "count": 1, "cached_at": STAMP, "scan_running": False, "partial": False, "diagnostics": {}})
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
    print("Synthetic audit UI only: http://127.0.0.1:8765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
