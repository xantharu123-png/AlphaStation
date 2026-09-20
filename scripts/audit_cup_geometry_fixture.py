"""Loopback-only visual Cup fixture: synthetic prices, no API import or credentials.

Serves the real frontend via audit_ui_fixture. This is NOT an EXPD replay.
Run with --port 8766. /__qa/cup?case=valid|legacy|mismatch|historical selects
local read-only display cases; no scans, provider requests, mail or orders.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from audit_ui_fixture import Handler, STAMP


CASE = "valid"
SYMBOL = "CUPQA"


def cup_fixture():
    closes = [100 - 24 * i / 27 for i in range(28)]
    closes += [75 + 2 * abs((i - 13) / 13) for i in range(26)]
    closes += [77 + 22.5 * i / 35 for i in range(36)]
    closes += [98.8, 97.2, 95.5, 94, 94.8, 95.6, 96.7, 97.5, 98.3, 101.7]
    sessions, cursor = [], date(2026, 9, 18)
    while len(sessions) < len(closes):
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    sessions.reverse()  # Synthetic weekday calendar, NOT an exchange replay.
    bars = [{"time": int(datetime.combine(day, time(4), timezone.utc).timestamp()),
             "open": price * .997, "high": price * 1.012,
             "low": price * .988, "close": price, "volume": 1_000_000}
            for day, price in zip(sessions, closes)]
    bars[-1].update(high=102.717, low=99.8, volume=2_400_000)
    roles = {"left_rim": (0, "high"), "bottom": (41, "low"),
             "right_rim": (89, "high"), "handle_start": (91, "close"),
             "handle_low": (93, "low"), "handle_end": (98, "close"),
             "breakout": (99, "close")}
    evidence = {"version": "cup_geometry_v1", "status": "available", "symbol": SYMBOL,
                "timeframe": "1D", "as_of_session": sessions[-1].isoformat(),
                "as_of_semantics": "selected_input_last_session",
                "window_start_session": sessions[0].isoformat(),
                "window_end_session": sessions[-1].isoformat(),
                "cup_length": 91, "handle_length": 9,
                "index_basis": "selected_window_zero_based", "handle_includes_breakout": True,
                "geometry_status": "ordered", "geometry_issues": [],
                "handle_low_on_breakout": False,
                "anchors": {role: {"session": sessions[index].isoformat(),
                                   "price": bars[index][field], "price_field": field,
                                   "index": index}
                            for role, (index, field) in roles.items()}}
    row = {"ticker": SYMBOL, "company_name": "Synthetischer Cup - kein echter Marktwert",
           "price": 101.7, "score": 95, "grade": "S", "rvol": 2.4, "change_pct": 1.2,
           "pattern": "Cup and Handle Breakout", "pattern_type": "cup_handle_breakout",
           "pattern_timeframe": "1D", "cup_pattern_evidence": evidence,
           "direction": "LONG", "trade_action": "LONG_TRIGGER", "entry_status": "SWING_PLAN",
           "swing_analysis_session": "2026-09-18", "data_mode": "swing_daily",
           "execution_mode": "swing_plan", "Breakout_Level": 101.2}
    return bars, row


class CupHandler(Handler):
    def do_POST(self):
        if self.path == "/__qa/error":
            return super().do_POST()
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        return self.send_json({"detail": "Cup fixture: mutations disabled"}, 403)

    def do_GET(self):
        global CASE
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/__qa/cup":
            selected = query.get("case", ["valid"])[0]
            if selected in {"valid", "legacy", "mismatch", "historical"}:
                CASE = selected
            return self.send_json({"synthetic_only": True, "case": CASE})
        bars, row = cup_fixture()
        if parsed.path == "/api/strategies":
            return self.send_json({"strategies": {"Cup and Handle Breakout": {
                "display_group": "Structure"}}, "categories": {}})
        if parsed.path == "/api/scan-results":
            if CASE == "legacy":
                row.pop("cup_pattern_evidence")
            return self.send_json({"status": "success", "data": [row], "count": 1,
                "cached_at": STAMP, "scan_running": False, "partial": False,
                "diagnostics": {"coverage": "complete", "final_results": 1,
                    "raw_cache_rows": 1, "validated_scanner_signals": 1,
                    "visible_scanner_signals": 1, "checked": 1, "universe_count": 1}})
        if parsed.path == "/api/ticker-detail":
            return self.send_json({"ticker": SYMBOL, "price": 101.7,
                "company_name": row["company_name"], "indicators": {},
                "signal_score": 0, "confluence": {"direction": "LONG"}})
        if parsed.path == "/api/chart-data":
            if CASE == "mismatch":
                bars[41]["low"] -= 10
            if CASE == "historical":
                extra = deepcopy(bars[-1])
                extra.update(time=bars[-1]["time"] + 3 * 86400, close=102)
                bars.append(extra)
            return self.send_json({"ticker": SYMBOL,
                "timeframe": query.get("timeframe", ["1D"])[0], "candles": bars,
                "source": "SYNTHETIC QA ONLY", "patterns": {}, "overlays": {}})
        return super().do_GET()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    print(f"Synthetic Cup UI only: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), CupHandler).serve_forever()
