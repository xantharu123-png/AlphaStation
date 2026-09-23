"""Loopback-only pause/resume UI fixture; synthetic, no production API imports.

Run: python scripts/audit_scan_control_fixture.py --port 8767
Select /__qa/control?case=running|paused|weekend|nonadmin|restart_required|round|unverified|timeout.
Append &scanner=bi_long or &scanner=bi_short for a single active BI worker.
State changes affect only this in-memory QA process. No scans, mail or orders.
"""
import argparse
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

from audit_ui_fixture import Handler, STAMP, bi_row
from audit_cup_geometry_fixture import cup_fixture


CUP_KEY = "strat_cup_and_handle_breakout"
OWNERS = (CUP_KEY, "strategy_scan", "bi_long", "bi_short")
CASES = {"running", "pause_requested", "paused", "weekend", "nonadmin",
         "restart_required", "round", "finishing", "unverified", "timeout"}
RESUME = "2026-09-21T13:30:00Z"  # Fixed synthetic schedule, not a live exchange claim.


class ControlFixture:
    def __init__(self):
        self.lock = threading.RLock()
        self.generation = 0
        self.reset("running")

    def reset(self, case, owner=None):
        with self.lock:
            if case not in CASES:
                return {"detail": "Unknown synthetic case"}, 400
            self.case = case
            if owner not in {None, CUP_KEY, "bi_long", "bi_short"}:
                return {"detail": "Unknown synthetic scanner"}, 400
            self.active_owner = "strategy_scan" if case == "round" else (owner or CUP_KEY)
            self.generation += 1
            state = case if case in {"paused", "pause_requested", "restart_required", "finishing"} else "running"
            if case == "weekend":
                state = "finished"
            self.controls = {key: {
                "supported": True, "owner_scan_key": key,
                "run_id": f"qa-{key}-{self.generation}", "state": state,
                "worker_alive": state not in {"finished", "restart_required"},
                "paused_seconds": 120 if state == "paused" else 0,
                "paused_at": STAMP if state == "paused" else None,
                "auto_resume": True, "resume_at": RESUME,
                "scope": "strategy_round" if key == "strategy_scan" else "scanner",
            } for key in OWNERS}
            self.pending = {}
            for key, control in self.controls.items():
                if key != self.active_owner:
                    control.update(state="finished", worker_alive=False, paused_at=None, paused_seconds=0)
            return {"synthetic_only": True, "case": case}, 200

    def stock_owner(self):
        return "strategy_scan" if self.case == "round" else CUP_KEY

    def schedule(self):
        return {"automatic_paused": self.case == "weekend", "reason": "weekend" if self.case == "weekend" else None,
                "timezone": "America/New_York", "next_eligible_at": RESUME}

    def read(self, owner):
        with self.lock:
            target = self.pending.pop(owner, None)
            control = self.controls[owner]
            if target:
                control.update(state=target, paused_at=STAMP if target == "paused" else None,
                               paused_seconds=120 if target == "paused" else 0)
            return deepcopy(control)

    def action(self, request):
        with self.lock:
            if self.case == "nonadmin":
                return {"detail": "Admin required"}, 403
            if not isinstance(request, dict):
                return {"detail": "Invalid synthetic request"}, 400
            owner = request.get("scanner")
            if owner not in OWNERS or request.get("run_id") != self.controls[owner]["run_id"]:
                return {"detail": "Scan run changed"}, 409
            action = request.get("action")
            control = self.controls[owner]
            required = "running" if action == "pause" else "paused"
            if action not in {"pause", "resume"} or control["state"] != required or not control["worker_alive"]:
                return {"detail": "Control state changed"}, 409
            control["auto_resume"] = request.get("auto_resume") is True
            control["resume_at"] = RESUME if control["auto_resume"] else None
            if action == "pause":
                control["state"] = "pause_requested"
            self.pending[owner] = "paused" if action == "pause" else "running"
            # The acknowledgement is deliberately not the subsequent GET state.
            return {"status": f"{action}_requested", "control": deepcopy(control)}, 200

    def start(self, owner):
        with self.lock:
            if any(control["worker_alive"] for control in self.controls.values()):
                return {"detail": "Synthetic heavy stock slot is occupied"}, 409
            self.generation += 1
            control = self.controls[owner]
            control.update(run_id=f"qa-{owner}-{self.generation}", state="running", worker_alive=True,
                           paused_at=None, paused_seconds=0)
            self.pending.pop(owner, None)
            return {"status": "started", "run_id": control["run_id"], "scanner": owner}, 200

    def result(self, owner):
        with self.lock:
            control = self.read(owner)
            row = bi_row("SHORT" if owner == "bi_short" else "LONG") if owner.startswith("bi_") else cup_fixture()[1]
            active = control["worker_alive"]
            return {"status": "success", "data": [row], "count": 1,
                "cached_at": STAMP, "scan_running": active, "partial": active,
                "scan_run_id": control["run_id"], "scan_control": None if self.case == "unverified" else control,
                "scan_schedule": self.schedule(), "scan_error": "scan_timeout" if self.case == "timeout" else None,
                "scan_last_attempt_at": STAMP, "scan_last_completed_at": None if active else STAMP,
                "checked": 72 if active else 180, "total": 180,
                "progress_detail": "Synthetische Kontroll-Fixture; kein echter Scan",
                "diagnostics": {"coverage": "incomplete" if active else "complete", "checked": 72 if active else 180,
                    "total": 180, "universe_count": 180, "raw_cache_rows": 1,
                    "validated_scanner_signals": 1, "visible_scanner_signals": 1, "final_results": 1}}

    def status(self):
        with self.lock:
            selected = self.stock_owner()
            scans = {}
            for key in (selected, "bi_long", "bi_short"):
                control = self.read(key)
                scans[key] = {"running": control["worker_alive"], "run_id": control["run_id"],
                    "last_run": STAMP, "last_attempt_at": STAMP, "next_run": RESUME,
                    "cache_health": "ok", "interval_min": 180, "control": None if self.case == "unverified" else control,
                    "schedule": self.schedule(), "running_since_sec": 360,
                    "progress": {"running": control["worker_alive"], "checked": 72, "total": 180,
                        "hits": 1, "detail": "Synthetische Kontroll-Fixture", "seconds_since_progress": 2}}
            scans["crypto_explosion"] = {"running": False, "last_run": STAMP, "next_run": RESUME,
                                          "cache_health": "ok", "interval_min": 15}
            return {"scheduler_running": True, "scans": scans}


FIXTURE = ControlFixture()


class ControlHandler(Handler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/__qa/control":
            payload, status = FIXTURE.reset(query.get("case", ["running"])[0], query.get("scanner", [None])[0])
            return self.send_json(payload, status)
        if parsed.path == "/api/auth/me":
            return self.send_json({"user": {"name": "QA – synthetisch", "email": "qa@example.invalid", "plan": "elite",
                "is_admin": FIXTURE.case != "nonadmin"}, "limits": {"plan_name": "QA", "allowed_tabs": None, "max_scans_per_day": 999}})
        if parsed.path == "/api/strategies":
            return self.send_json({"strategies": {"Cup and Handle Breakout": {"display_group": "Structure"}}, "categories": {}})
        if parsed.path == "/api/scan-status":
            return self.send_json(FIXTURE.status())
        if parsed.path == "/api/scan-results":
            if query.get("market_type", ["stocks"])[0] != "stocks":
                return super().do_GET()
            return self.send_json(FIXTURE.result(FIXTURE.stock_owner()))
        if parsed.path == "/api/bi-results":
            owner = "bi_short" if query.get("direction", ["long"])[0].lower() == "short" else "bi_long"
            return self.send_json(FIXTURE.result(owner))
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/scan-control", "/api/scan", "/api/bi-scan"}:
            return super().do_POST()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 65536:
                return self.send_json({"detail": "Invalid synthetic request"}, 400)
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError()
        except (ValueError, TypeError):
            return self.send_json({"detail": "Invalid synthetic request"}, 400)
        if path == "/api/scan-control":
            payload, status = FIXTURE.action(request)
        else:
            owner = FIXTURE.stock_owner() if path == "/api/scan" else (
                "bi_short" if str(request.get("direction", "long")).lower() == "short" else "bi_long")
            payload, status = FIXTURE.start(owner)
        return self.send_json(payload, status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    print(f"Synthetic scan-control UI only: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler).serve_forever()
