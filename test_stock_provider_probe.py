"""Offline behavioral coverage of the standalone, non-trading provider probe."""
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import shutil
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).parent / "scripts" / "probe_stock_provider.py"


@pytest.fixture
def probe():
    if not SOURCE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("standalone_stock_probe", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_field_states_never_count_invalid_prices_as_usable(probe):
    assert probe is not None, "Standalone aggregate provider probe is not implemented"
    values = [None, 0, -1, True, float("inf"), float("nan"), "12", {}, 12.5]
    rows = [{"lastTrade": {"p": value}} for value in values] + [{}]
    result = probe.summarize({"status": "OK", "tickers": rows}, "full_snapshot")
    assert result["status"] == "ok"
    assert result["rows"] == 10
    assert result["fields"]["lastTrade.p"] == {
        "missing": 1, "null": 1, "zero": 1, "negative": 1, "bool": 1,
        "nonfinite": 2, "string": 1, "other": 1, "positive": 1,
    }
    assert "12.5" not in json.dumps(result)


def test_control_snapshot_counts_fields_without_echoing_symbol_or_price(probe):
    payload = {"status": "DELAYED", "ticker": {"ticker": "PRIVATE_SYMBOL",
               "lastTrade": {"p": 12345.67, "t": 123456789}, "day": {"c": 98765.43}}}
    result = probe.summarize(payload, "liquid_control")
    assert result["status"] == "delayed"
    assert result["rows"] == 1
    assert result["fields"]["day.c"]["positive"] == 1
    assert result["fields"]["lastTrade.t"]["positive"] == 1
    assert all(secret not in json.dumps(result) for secret in ("PRIVATE_SYMBOL", "12345.67", "98765.43"))


def test_quote_fields_keep_bid_and_ask_distinct_in_case_insensitive_powershell(probe):
    result = probe.summarize({"status": "OK", "ticker": {"lastQuote": {"p": 0, "P": 2}}}, "liquid_control")
    assert "lastQuote.bid_price" in result["fields"]
    assert result["fields"]["lastQuote.bid_price"]["zero"] == 1
    assert result["fields"]["lastQuote.ask_price"]["positive"] == 1


@pytest.mark.parametrize("payload,kind,want", [
    ({"status": "ERROR", "error": "secret"}, "full_snapshot", "provider_error"),
    ({"status": "OK", "tickers": None}, "full_snapshot", "invalid_payload"),
    ({"status": "OK", "ticker": []}, "liquid_control", "invalid_payload"),
    ({"status": "OK", "tickers": [None]}, "full_snapshot", "invalid_payload"),
    ({"status": "OK", "tickers": [{}] * 20001}, "full_snapshot", "too_many_rows"),
])
def test_malformed_provider_payload_cannot_be_successful_empty(probe, payload, kind, want):
    result = probe.summarize(payload, kind)
    assert result["status"] == want
    assert result["rows"] == 0
    assert "secret" not in json.dumps(result)


def config_reader(files, seen):
    def read(path, limit):
        seen.append(str(path))
        value = files.get(str(path), FileNotFoundError())
        if isinstance(value, Exception):
            raise value
        return value.encode()
    return read


def test_startup_environment_wins_without_reading_any_secret_file(probe):
    def forbidden(*args):
        pytest.fail("file credentials must not be read for a nonempty startup key")
    key, info = probe.load_key({"POLYGON_KEY": "startup"}, "/app", "/service", forbidden)
    assert key == "startup"
    assert info == {"status": "available", "source": "startup_environment", "binding": "uncertain"}


def test_config_uses_api_line_parser_and_last_key_not_dotenv_semantics(probe):
    files = {
        "/service/.streamlit/secrets.toml": "POLYGON_KEY='home'",
        "/app/.streamlit/secrets.toml": "POLYGON_KEY='app'",
        "/app/.env": "# comment\nexport POLYGON_KEY=ignored\nPOLYGON_KEY=first\nPOLYGON_KEY=\"last # literal\"\n",
    }
    key, info = probe.load_key({}, "/app", "/service", config_reader(files, []))
    assert key == "last # literal"
    assert info["source"] == "app_env"
    assert info["binding"] == "uncertain"


def test_empty_higher_key_removes_lower_key(probe):
    files = {"/app/.env": "POLYGON_KEY=''", "/app/.streamlit/secrets.toml": "POLYGON_KEY=lower"}
    key, info = probe.load_key({}, "/app", "/service", config_reader(files, []))
    assert key is None
    assert info["status"] == "missing"
    assert info["source"] == "app_env"


def test_unreadable_higher_file_is_unknown_never_lower_fallback(probe):
    files = {"/app/.env": PermissionError("secret"), "/app/.streamlit/secrets.toml": "POLYGON_KEY=lower"}
    key, info = probe.load_key({}, "/app", "/service", config_reader(files, []))
    assert key is None
    assert info["status"] == "unavailable"
    assert info["source"] == "app_env"
    assert "secret" not in json.dumps(info)


def test_missing_files_can_fall_back_to_service_home_but_not_root_home(probe):
    seen = []
    key, info = probe.load_key({}, "/app", "/service", config_reader({
        "/service/.streamlit/secrets.toml": "POLYGON_KEY=service",
        "/root/.streamlit/secrets.toml": "POLYGON_KEY=root",
    }, seen))
    assert key == "service"
    assert info["source"] == "service_home_secrets"
    assert not any("/root/" in path for path in seen)


def proc_stat(pid=7, ticks=123):
    # starttime is field 22, immediately after 19 fields beginning with state.
    return f"{pid} (python worker) S " + " ".join(["0"] * 18 + [str(ticks)])


def test_process_identity_pins_pid_start_cwd_and_unmixed_uid_gid(probe):
    status = "Uid:\t1000\t1000\t1000\t1000\nGid:\t1001\t1001\t1001\t1001\n"
    result = probe.parse_identity(7, status, proc_stat(), "/app", "/app", 1000, 1001)
    assert result == {"pid": 7, "start_ticks": 123, "cwd": "/app", "uid": 1000, "gid": 1001}
    for bad in (status.replace("1000\t1000", "0\t1000", 1), status.replace("1001", "0")):
        with pytest.raises(ValueError):
            probe.parse_identity(7, bad, proc_stat(), "/app", "/app", 1000, 1001)
    with pytest.raises(ValueError):
        probe.parse_identity(7, status, proc_stat(), "/wrong", "/app", 1000, 1001)


def test_service_home_comes_from_verified_proc_or_service_pwd(probe):
    assert probe.service_home({"HOME": "/service-home"}, "/pwd-home") == "/service-home"
    assert probe.service_home({}, "/pwd-home") == "/pwd-home"
    for bad in ("relative", "/x/../root", "/root\x00suffix"):
        with pytest.raises(ValueError):
            probe.service_home({"HOME": bad}, "/pwd-home")


class FakeFilesystem:
    O_RDONLY, O_NOFOLLOW, O_DIRECTORY, O_NONBLOCK, O_CLOEXEC = 0, 1, 2, 4, 8

    def __init__(self, body=b"POLYGON_KEY=fixture", unsafe=None, changed=False):
        self.body, self.unsafe, self.changed = body, unsafe, changed
        self.paths, self.closed, self.calls, self.fstats = {}, [], [], {}
        self.stream = io.BytesIO(body)

    def open(self, name, flags, dir_fd=None):
        path = name if dir_fd is None else self.paths[dir_fd].rstrip("/") + "/" + name
        self.calls.append((path, flags))
        assert flags & self.O_NOFOLLOW
        if self.unsafe == path:
            raise OSError("symlink or unreadable path")
        fd = len(self.paths) + 10
        self.paths[fd] = path
        return fd

    def fstat(self, fd):
        path = self.paths[fd]
        self.fstats[fd] = self.fstats.get(fd, 0) + 1
        mode = stat.S_IFDIR if path != "/app/.env" else stat.S_IFREG
        if self.unsafe == "fifo" and path == "/app/.env":
            mode = stat.S_IFIFO
        changed = self.changed and self.fstats[fd] > 1 and path == "/app/.env"
        return SimpleNamespace(st_mode=mode, st_dev=1, st_ino=fd, st_size=len(self.body),
                               st_mtime_ns=int(changed), st_ctime_ns=0)

    def read(self, fd, size):
        return self.stream.read(size)

    def close(self, fd):
        self.closed.append(fd)


def test_credential_read_anchors_every_directory_and_checks_file_before_and_after(probe):
    kernel = FakeFilesystem()
    assert probe.secure_read("/app/.env", 100, kernel) == b"POLYGON_KEY=fixture"
    assert [path for path, flags in kernel.calls] == ["/", "/app", "/app/.env"]
    assert len(kernel.closed) == 3


@pytest.mark.parametrize("unsafe,changed,limit", [
    ("/app", False, 100), ("/app/.env", False, 100), ("fifo", False, 100),
    (None, True, 100), (None, False, 3),
])
def test_credential_symlink_fifo_oversize_or_mutation_is_rejected(probe, unsafe, changed, limit):
    kernel = FakeFilesystem(unsafe=unsafe, changed=changed)
    with pytest.raises((ValueError, OSError)):
        probe.secure_read("/app/.env", limit, kernel)
    assert len(kernel.closed) == len(kernel.paths)


def test_privilege_drop_irreversibly_sets_all_ids_and_removes_groups(probe):
    events = []
    identity = {"uids": (0, 0, 0), "gids": (0, 0, 0), "groups": [0]}
    def set_value(key, value):
        events.append(key)
        identity[key] = value
    kernel = SimpleNamespace(geteuid=lambda: identity["uids"][1],
        setgroups=lambda value: set_value("groups", value),
        setresgid=lambda *value: set_value("gids", value),
        setresuid=lambda *value: set_value("uids", value),
        getresuid=lambda: identity["uids"], getresgid=lambda: identity["gids"],
        getgroups=lambda: identity["groups"])
    probe.drop_privileges(1000, 1001, kernel)
    assert events == ["groups", "gids", "uids"]
    assert identity == {"uids": (1000, 1000, 1000), "gids": (1001, 1001, 1001), "groups": []}
    with pytest.raises(ValueError):
        probe.drop_privileges(0, 0, kernel)


class FakeConnection:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls, self.closed = response, error, [], False

    def request(self, method, path, headers):
        self.calls.append((method, path, headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def network_result(probe, monkeypatch, status=200, body=b'{"status":"OK","tickers":[]}', error=None):
    response = io.BytesIO(body)
    response.status = status
    connection = FakeConnection(response, error)
    destinations = []
    def factory(host, timeout, context):
        destinations.append((host, timeout, context))
        return connection
    monkeypatch.setattr(probe.http.client, "HTTPSConnection", factory)
    result = probe.request_snapshot("full_snapshot", "PRIVATE_KEY")
    return result, connection, destinations


def test_request_has_fixed_verified_https_destination_and_no_redirect_retry(probe, monkeypatch):
    result, connection, destinations = network_result(probe, monkeypatch, status=302)
    assert result["status"] == "redirect"
    assert len(destinations) == 1
    host, timeout, context = destinations[0]
    assert host == "api.polygon.io" and timeout <= 15
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert len(connection.calls) == 1 and connection.calls[0][0] == "GET"
    assert connection.calls[0][1].startswith("/v2/snapshot/locale/us/markets/stocks/tickers?")
    assert connection.closed
    assert "PRIVATE_KEY" not in json.dumps(result)


@pytest.mark.parametrize("status,want", [(401, "unauthorized"), (403, "forbidden"),
    (429, "rate_limited"), (500, "server_error"), (404, "client_error")])
def test_http_failure_is_fixed_code_and_never_reads_provider_error_body(probe, monkeypatch, status, want):
    result, connection, _ = network_result(probe, monkeypatch, status, b"SECRET RESPONSE")
    assert result["status"] == want
    assert connection.response.tell() == 0
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("error,want", [(TimeoutError("key secret"), "timeout"),
    (ssl.SSLError("provider body"), "tls_error"), (OSError("https://secret"), "connection_error")])
def test_transport_errors_never_leak_raw_exception_or_retry(probe, monkeypatch, error, want):
    result, connection, destinations = network_result(probe, monkeypatch, error=error)
    assert result["status"] == want
    assert len(destinations) == 1 and len(connection.calls) == 1
    assert "secret" not in json.dumps(result) and "provider body" not in json.dumps(result)


def test_response_size_and_malformed_json_are_fail_closed(probe, monkeypatch):
    monkeypatch.setattr(probe, "MAX_BODY", 16)
    result, connection, _ = network_result(probe, monkeypatch, body=b"x" * 100)
    assert result["status"] == "too_large"
    assert connection.response.tell() <= 17
    result, _, _ = network_result(probe, monkeypatch, body=b"not-json")
    assert result["status"] == "invalid_json"


def test_unknown_endpoint_cannot_control_destination(probe):
    with pytest.raises(ValueError):
        probe.request_snapshot("https://attacker.example/", "PRIVATE_KEY")


def runtime():
    return {"pid": 7, "start_ticks": 123, "cwd": "/app", "uid": 1000, "gid": 1001,
            "home": "/service", "environment": {"POLYGON_KEY": "startup"}}


def test_collect_drops_before_config_network_and_performs_exactly_two_gets(probe):
    events = []
    def drop(uid, gid):
        events.append("drop")
    def requester(kind, key):
        assert events[0] == "drop"
        assert key == "startup"
        events.append(kind)
        return probe.empty_endpoint("ok")
    report = probe.collect(discover=runtime, verify=lambda item: True, drop=drop, requester=requester)
    assert events == ["drop", "full_snapshot", "liquid_control"]
    assert report["service_identity"] == "verified"
    assert report["config"]["binding"] == "uncertain"
    assert "startup" not in json.dumps(report).replace("startup_environment", "")


def test_changed_process_prevents_any_network_and_discards_results(probe):
    def forbidden(*args):
        pytest.fail("changed process must not cause provider access")
    result = probe.collect(discover=runtime, verify=lambda item: False, drop=lambda *args: None, requester=forbidden)
    assert result["service_identity"] == "changed"
    assert result["endpoints"]["full_snapshot"]["status"] == "not_requested"


def test_failure_after_first_get_discards_unbound_results_and_stops(probe):
    checks = iter([True, True, False])
    calls = []
    def request(kind, key):
        calls.append(kind)
        return probe.empty_endpoint("ok")
    result = probe.collect(discover=runtime, verify=lambda item: next(checks), drop=lambda *args: None, requester=request)
    assert calls == ["full_snapshot"]
    assert result["service_identity"] == "changed"
    assert result["endpoints"]["full_snapshot"]["status"] == "not_requested"


def test_ascii_standalone_cli_failure_is_safe_and_never_imports_app(probe):
    # No active Linux service on this test host; the CLI must still emit safe JSON.
    source = SOURCE.read_text(encoding="ascii")
    result = subprocess.run([os.sys.executable, "-I", "-B", "-"], input=source, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["kind"] == "stock_provider_probe"
    assert report["service_identity"] == "unavailable"
    assert result.stderr == ""


def test_file_configuration_is_never_read_before_privilege_drop(probe):
    events = []
    def discover():
        value = runtime()
        value["environment"] = {}
        return value
    def reader(path, limit):
        assert events == ["drop"]
        return b"POLYGON_KEY=filekey"
    report = probe.collect(discover=discover, verify=lambda value: True,
        drop=lambda *args: events.append("drop"), reader=reader,
        requester=lambda *args: probe.empty_endpoint("ok"))
    assert report["config"]["source"] == "app_env"
    assert report["service_identity"] == "verified"


def test_privilege_failure_never_reads_files_or_network(probe):
    def fail_drop(*args):
        raise PermissionError("sensitive")
    def forbidden(*args):
        pytest.fail("credentials/network after failed privilege drop")
    result = probe.collect(discover=runtime, drop=fail_drop, reader=forbidden, requester=forbidden)
    assert result["service_identity"] == "unavailable"
    assert "sensitive" not in json.dumps(result)


def test_credential_growing_past_limit_is_rejected_without_reading_entire_file(probe):
    kernel = FakeFilesystem(body=b"a" * 100)
    actual_stat = kernel.fstat
    def stat_small(fd):
        value = actual_stat(fd)
        value.st_size = 1
        return value
    kernel.fstat = stat_small
    with pytest.raises(ValueError):
        probe.secure_read("/app/.env", 8, kernel)
    assert kernel.stream.tell() == 9


def wrapper_payload():
    fields = ("lastTrade.p", "lastTrade.t", "lastQuote.bid_price", "lastQuote.ask_price", "lastQuote.t",
              "day.c", "prevDay.c", "min.c", "min.t", "updated")
    states = ("missing", "null", "zero", "negative", "bool", "nonfinite", "string", "other", "positive")
    endpoint = {"status": "ok", "rows": 0, "fields": {name: dict.fromkeys(states, 0) for name in fields}}
    return {"schema_version": 1, "kind": "stock_provider_probe", "read_only": True,
            "captured_at": "2026-09-10T12:00:00+00:00", "service_identity": "verified",
            "config": {"status": "available", "source": "startup_environment", "binding": "uncertain"},
            "endpoints": {"full_snapshot": endpoint, "liquid_control": endpoint}}


def invoke_wrapper(tmp_path, payload):
    wrapper = SOURCE.with_name("probe_hetzner_provider.ps1")
    assert wrapper.is_file(), "PowerShell provider probe wrapper is not implemented"
    shell = shutil.which("powershell")
    if not shell:
        pytest.skip("Windows PowerShell unavailable")
    payload_path = tmp_path / "response.json"
    payload_path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="ascii")
    runner = tmp_path / "runner.ps1"
    calls = tmp_path / "ssh-call.json"
    output = tmp_path / "private"
    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"
    # The external SSH boundary is the only double; the real wrapper validates/writes.
    runner.write_text("""
$ErrorActionPreference = 'Stop'
function global:Get-Date { [DateTime]::Parse('2026-09-10T12:34:56Z').ToUniversalTime() }
function global:ssh {
  begin { $CapturedArgs = @($args) }
  process { }
  end {
    ConvertTo-Json -InputObject $CapturedArgs -Compress | Set-Content -LiteralPath CALLPATH
    $global:LASTEXITCODE = 0
    Get-Content -Raw -LiteralPath RESPONSEPATH
  }
}
& WRAPPERPATH -OutputDirectory OUTPUTPATH
""".replace("CALLPATH", quoted(calls)).replace("RESPONSEPATH", quoted(payload_path))
        .replace("WRAPPERPATH", quoted(wrapper)).replace("OUTPUTPATH", quoted(output)), encoding="ascii")
    result = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                            text=True, capture_output=True, timeout=15)
    return result, output, calls


def test_powershell_wrapper_validates_and_privately_saves_new_aggregate_file(tmp_path):
    payload = wrapper_payload()
    result, output, calls = invoke_wrapper(tmp_path, payload)
    assert result.returncode == 0, result.stderr
    files = list(output.glob("provider-probe-*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == payload
    args = json.loads(calls.read_text(encoding="utf-8-sig"))
    assert "StrictHostKeyChecking=yes" in args
    assert "/usr/bin/python3 -I -" in args
    assert "root@178.104.69.209" in args


@pytest.mark.parametrize("mutation", ["extra_secret", "negative_count", "wrong_kind", "string_count", "bad_sum"])
def test_powershell_wrapper_rejects_unexpected_or_leaking_schema_before_write(tmp_path, mutation):
    payload = wrapper_payload()
    if mutation == "extra_secret":
        payload["key"] = "PRIVATE_KEY"
    elif mutation == "wrong_kind":
        payload["kind"] = "other"
    elif mutation == "negative_count":
        payload["endpoints"]["full_snapshot"]["fields"]["day.c"]["zero"] = -1
    elif mutation == "string_count":
        payload["endpoints"]["full_snapshot"]["fields"]["day.c"]["zero"] = "0"
    else:
        payload["endpoints"]["full_snapshot"]["fields"]["day.c"]["positive"] = 1
    result, output, _ = invoke_wrapper(tmp_path, payload)
    assert result.returncode != 0
    assert not list(output.glob("*.json"))
    assert "PRIVATE_KEY" not in result.stdout + result.stderr


def test_powershell_wrapper_does_not_save_unvalidated_duplicate_json_text(tmp_path):
    text = json.dumps(wrapper_payload()).replace('"kind": "stock_provider_probe"',
        '"kind": "PRIVATE_KEY", "kind": "stock_provider_probe"')
    result, output, _ = invoke_wrapper(tmp_path, text)
    assert result.returncode == 0, result.stderr
    files = list(output.glob("provider-probe-*.json"))
    assert len(files) == 1
    assert "PRIVATE_KEY" not in files[0].read_text(encoding="utf-8")


def test_powershell_wrapper_never_overwrites_existing_private_evidence(tmp_path):
    result, output, _ = invoke_wrapper(tmp_path, wrapper_payload())
    assert result.returncode == 0, result.stderr
    target, = output.glob("provider-probe-*.json")
    first = target.read_bytes()
    replacement = wrapper_payload()
    replacement["config"]["source"] = "app_env"
    second, _, _ = invoke_wrapper(tmp_path, replacement)
    assert second.returncode != 0
    assert target.read_bytes() == first


def test_linux_alarm_covers_entire_request_and_restores_handler(probe, monkeypatch):
    calls = []
    state = {"handler": "original"}
    def change_handler(sig, handler):
        previous = state["handler"]
        state["handler"] = handler
        calls.append(("handler", handler))
        return previous
    def timer(which, seconds):
        calls.append(("timer", seconds))
        return (0.0, 0.0)
    fake = SimpleNamespace(SIGALRM=14, ITIMER_REAL=0, signal=change_handler, setitimer=timer)
    monkeypatch.setattr(probe, "signal", fake)
    with pytest.raises(TimeoutError):
        with probe.request_deadline():
            state["handler"](14, None)
    assert calls[1] == ("timer", 12)
    assert calls[-2:] == [("timer", 0), ("handler", "original")]


@pytest.mark.parametrize("field,value", [("pid", 8), ("start_ticks", 124), ("cwd", "/other"), ("uid", 0), ("gid", 0)])
def test_identity_verification_rejects_each_restart_or_privilege_change(probe, monkeypatch, field, value):
    current = {name: runtime()[name] for name in ("pid", "start_ticks", "cwd", "uid", "gid")}
    current[field] = value
    monkeypatch.setattr(probe, "current_identity", lambda: current)
    assert probe.verify_identity(runtime()) is False
