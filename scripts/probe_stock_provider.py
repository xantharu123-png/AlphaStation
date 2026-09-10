"""Read-only, aggregate-only provider probe; run with /usr/bin/python3 -I -."""
import contextlib
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import PurePosixPath
import signal
import ssl
import stat
import subprocess
from urllib.parse import urlencode


APP = "/home/tradingbot/app"
UNIT = "tradingbot-api.service"
MAX_BODY = 16 * 1024 * 1024
MAX_ROWS = 20000
MAX_CONFIG = 65536
REQUEST_SECONDS = 12
ENDPOINTS = {
    "full_snapshot": "/v2/snapshot/locale/us/markets/stocks/tickers",
    "liquid_control": "/v2/snapshot/locale/us/markets/stocks/tickers/AAPL",
}
FIELDS = {name: name for name in ("lastTrade.p", "lastTrade.t", "lastQuote.t",
          "day.c", "prevDay.c", "min.c", "min.t", "updated")}
# Windows PowerShell JSON objects cannot distinguish keys differing only by case.
FIELDS.update({"lastQuote.bid_price": "lastQuote.p", "lastQuote.ask_price": "lastQuote.P"})
STATES = ("missing", "null", "zero", "negative", "bool", "nonfinite", "string", "other", "positive")


def empty_endpoint(status):
    return {"status": status, "rows": 0,
            "fields": {field: {state: 0 for state in STATES} for field in FIELDS}}


def summarize(payload, endpoint):
    if not isinstance(payload, dict):
        return empty_endpoint("invalid_payload")
    if payload.get("status") not in ("OK", "DELAYED"):
        return empty_endpoint("provider_error")
    if endpoint == "full_snapshot":
        rows = payload.get("tickers")
    else:
        row = payload.get("ticker")
        rows = [row] if isinstance(row, dict) else None
    if not isinstance(rows, list):
        return empty_endpoint("invalid_payload")
    if len(rows) > MAX_ROWS:
        return empty_endpoint("too_many_rows")
    if any(not isinstance(row, dict) for row in rows):
        return empty_endpoint("invalid_payload")
    result = empty_endpoint("delayed" if payload["status"] == "DELAYED" else "ok")
    result["rows"] = len(rows)
    for row in rows:
        for field in FIELDS:
            value, found = row, True
            for part in FIELDS[field].split("."):
                if not isinstance(value, dict) or part not in value:
                    found = False
                    break
                value = value[part]
            if not found:
                state = "missing"
            elif value is None:
                state = "null"
            elif isinstance(value, bool):
                state = "bool"
            elif isinstance(value, (int, float)):
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                state = "nonfinite" if not finite else "zero" if value == 0 else "negative" if value < 0 else "positive"
            else:
                state = "string" if isinstance(value, str) else "other"
            result["fields"][field][state] += 1
    return result


def secure_read(path, limit, os_api=os):
    """Pin every parent with no-follow dir FDs; never open FIFO/block devices."""
    target = PurePosixPath(path)
    if not target.is_absolute() or ".." in target.parts or "\x00" in str(path):
        raise ValueError("unsafe_path")
    descriptors = []
    try:
        flags = os_api.O_RDONLY | os_api.O_NOFOLLOW | os_api.O_NONBLOCK | os_api.O_CLOEXEC
        parent = os_api.open("/", flags | os_api.O_DIRECTORY)
        descriptors.append(parent)
        for part in target.parts[1:-1]:
            parent = os_api.open(part, flags | os_api.O_DIRECTORY, dir_fd=parent)
            descriptors.append(parent)
            if not stat.S_ISDIR(os_api.fstat(parent).st_mode):
                raise ValueError("unsafe_parent")
        fd = os_api.open(target.name, flags, dir_fd=parent)
        descriptors.append(fd)
        before = os_api.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("unsafe_file")
        chunks, length = [], 0
        while length <= limit:
            chunk = os_api.read(fd, min(16384, limit + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        after = os_api.fstat(fd)
        identity = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if length > limit or any(getattr(before, key) != getattr(after, key) for key in identity):
            raise ValueError("changed_or_oversize_file")
        return b"".join(chunks)
    finally:
        for fd in reversed(descriptors):
            os_api.close(fd)


def load_key(environment, app, home, reader=secure_read):
    """Project API precedence, not an assertion about its cached in-memory key."""
    source, key = "startup_environment", environment.get("POLYGON_KEY")
    if not key:
        source = "none"
        paths = (("app_env", PurePosixPath(app) / ".env"),
                 ("app_secrets", PurePosixPath(app) / ".streamlit/secrets.toml"),
                 ("service_home_secrets", PurePosixPath(home) / ".streamlit/secrets.toml"))
        for source, path in paths:
            try:
                text = reader(str(path), MAX_CONFIG).decode("utf-8")
            except FileNotFoundError:
                continue
            except Exception:
                return None, {"status": "unavailable", "source": source, "binding": "uncertain"}
            values = {}
            for raw in text.splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    name, value = line.split("=", 1)
                    values[name.strip()] = value.strip().strip('"').strip("'")
            if "POLYGON_KEY" in values:
                key = values["POLYGON_KEY"]
                break
        else:
            source = "none"
    if not key:
        return None, {"status": "missing", "source": source, "binding": "uncertain"}
    if not isinstance(key, str) or len(key) > 512 or any(ord(char) < 32 or ord(char) > 126 for char in key):
        return None, {"status": "unavailable", "source": source, "binding": "uncertain"}
    return key, {"status": "available", "source": source, "binding": "uncertain"}


def parse_identity(pid, status_text, stat_text, cwd, app, uid, gid):
    if type(pid) is not int or pid <= 0 or uid <= 0 or gid <= 0 or cwd != app:
        raise ValueError("invalid_process_identity")
    ids = {}
    for line in status_text.splitlines():
        name, separator, value = line.partition(":")
        if separator and name in ("Uid", "Gid"):
            numbers = [int(item) for item in value.split()]
            if len(numbers) != 4 or len(set(numbers)) != 1:
                raise ValueError("mixed_identity")
            ids[name] = numbers[0]
    if ids != {"Uid": uid, "Gid": gid} or not stat_text.startswith(str(pid) + " ("):
        raise ValueError("unexpected_service_identity")
    _, separator, fields = stat_text.rpartition(") ")
    if not separator:
        raise ValueError("invalid_process_stat")
    ticks = int(fields.split()[19])
    if ticks <= 0:
        raise ValueError("invalid_start_ticks")
    return {"pid": pid, "start_ticks": ticks, "cwd": cwd, "uid": uid, "gid": gid}


def service_home(environment, pwd_home):
    home = environment.get("HOME") or pwd_home
    path = PurePosixPath(home)
    if not path.is_absolute() or ".." in path.parts or "\x00" in home:
        raise ValueError("invalid_service_home")
    return str(path)


def proc_bytes(pid, name, limit):
    if name not in ("stat", "status", "environ"):
        raise ValueError("invalid_proc_field")
    with open("/proc/{}/{}".format(pid, name), "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversize_proc_field")
    return data


def current_identity():
    import pwd
    account = pwd.getpwnam("tradingbot")
    result = subprocess.run(["/usr/bin/systemctl", "show", UNIT, "--property=MainPID", "--value"],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 32:
        raise ValueError("invalid_service_pid")
    pid = int(result.stdout.strip())
    cwd = os.readlink("/proc/{}/cwd".format(pid))
    return parse_identity(pid, proc_bytes(pid, "status", 65536).decode("ascii"),
                          proc_bytes(pid, "stat", 8192).decode("ascii"), cwd, APP,
                          account.pw_uid, account.pw_gid)


def discover_service():
    import pwd
    runtime = current_identity()
    environment = {}
    for entry in proc_bytes(runtime["pid"], "environ", 262144).split(b"\0"):
        name, separator, value = entry.partition(b"=")
        if separator and name in (b"HOME", b"POLYGON_KEY"):
            environment[name.decode("ascii")] = value.decode("utf-8")
    if current_identity() != runtime:
        raise ValueError("service_changed")
    home = service_home(environment, pwd.getpwuid(runtime["uid"]).pw_dir)
    return dict(runtime, home=home, environment=environment)


def verify_identity(runtime):
    try:
        return current_identity() == {key: runtime[key] for key in ("pid", "start_ticks", "cwd", "uid", "gid")}
    except Exception:
        return False


def drop_privileges(uid, gid, os_api=os):
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise ValueError("invalid_reader_identity")
    if os_api.geteuid() == 0:
        os_api.setgroups([])
        os_api.setresgid(gid, gid, gid)
        os_api.setresuid(uid, uid, uid)
    if (os_api.getresuid() != (uid, uid, uid) or os_api.getresgid() != (gid, gid, gid)
            or os_api.getgroups()):
        raise ValueError("privilege_drop_failed")


@contextlib.contextmanager
def request_deadline():
    # The executable collector runs only on Linux, in the main interpreter thread.
    # SIGALRM bounds DNS, TLS, response headers and slow trickle bodies together.
    if not hasattr(signal, "setitimer"):
        yield
        return
    def expired(signum, frame):
        raise TimeoutError()
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, REQUEST_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *old_timer)


def request_snapshot(endpoint, key):
    if endpoint not in ENDPOINTS:
        raise ValueError("invalid_endpoint")
    connection = None
    try:
        with request_deadline():
            # http.client neither honors proxy environment variables nor follows redirects.
            connection = http.client.HTTPSConnection("api.polygon.io", timeout=REQUEST_SECONDS,
                                                      context=ssl.create_default_context())
            connection.request("GET", ENDPOINTS[endpoint] + "?" + urlencode({"apiKey": key}),
                               headers={"Accept": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            status = response.status
            if status != 200:
                code = ("redirect" if 300 <= status < 400 else "unauthorized" if status == 401
                        else "forbidden" if status == 403 else "rate_limited" if status == 429
                        else "server_error" if 500 <= status < 600 else "client_error")
                return empty_endpoint(code)
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                return empty_endpoint("too_large")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                return empty_endpoint("invalid_json")
            return summarize(payload, endpoint)
    except ssl.SSLError:
        return empty_endpoint("tls_error")
    except TimeoutError:
        return empty_endpoint("timeout")
    except (OSError, http.client.HTTPException):
        return empty_endpoint("connection_error")
    except Exception:
        return empty_endpoint("unexpected_error")
    finally:
        if connection is not None:
            connection.close()


def collect(discover=discover_service, verify=verify_identity, drop=drop_privileges,
            requester=request_snapshot, reader=secure_read):
    report = {"schema_version": 1, "kind": "stock_provider_probe", "read_only": True,
              "captured_at": datetime.now(timezone.utc).isoformat(), "service_identity": "unavailable",
              "config": {"status": "unavailable", "source": "none", "binding": "uncertain"},
              "endpoints": {name: empty_endpoint("not_requested") for name in ENDPOINTS}}
    try:
        runtime = discover()
        drop(runtime["uid"], runtime["gid"])
        if not verify(runtime):
            report["service_identity"] = "changed"
            return report
        report["service_identity"] = "verified"
        key, report["config"] = load_key(runtime["environment"], runtime["cwd"], runtime["home"], reader)
        if key is None:
            return report
        if not verify(runtime):
            report["service_identity"] = "changed"
            return report
        results = {}
        for endpoint in ENDPOINTS:
            results[endpoint] = requester(endpoint, key)
            if not verify(runtime):
                report["service_identity"] = "changed"
                return report
        report["endpoints"] = results
    except Exception:
        report["service_identity"] = "unavailable"
    return report


def main():
    print(json.dumps(collect(), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
