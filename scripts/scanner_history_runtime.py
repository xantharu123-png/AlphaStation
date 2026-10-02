"""Disposable offline application import for causal research, never deployment.

Use in a fresh subprocess: application imports are process-global. Market
adapters may read frozen files, but external sockets and SMTP are prohibited.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import os
from pathlib import Path
import smtplib
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def scanner_source_fingerprints():
    """Freeze production calculations AND the research adapter that runs them."""
    paths = [ROOT / "api.py", ROOT / "bg_service.py",
             *sorted((ROOT / "modules").rglob("*.py")),
             *sorted((ROOT / "scripts").glob("scanner_history_*.py"))]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def require_unchanged_scanner_sources(before):
    if before != scanner_source_fingerprints():
        raise ValueError("scanner_calculation_sources_changed_during_replay")


# Captured when this calculation-free module first loads, before a research
# adapter imports any production producer. Do not replace this with a fresh
# baseline at build_report(): already loaded functions could then be old while
# the filesystem hash describes new code.
_IMPORT_SOURCE_FINGERPRINTS = scanner_source_fingerprints()


def imported_scanner_source_fingerprints():
    """Return the original import baseline, or reject a stale loaded process."""
    require_unchanged_scanner_sources(_IMPORT_SOURCE_FINGERPRINTS)
    return dict(_IMPORT_SOURCE_FINGERPRINTS)


@contextmanager
def isolated_application(directory):
    directory = Path(directory).resolve()
    allowed = [(ROOT / "output").resolve(), (ROOT / "tmp").resolve()]
    if not any(parent in directory.parents for parent in allowed):
        raise ValueError("research_state_directory_outside_private_workspace")
    if "api" in sys.modules:
        raise RuntimeError("offline_application_requires_fresh_process")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "runtime").mkdir()
    state_env = {
        key: str(directory / name) for key, name in {
            "ALPHA_DATA_DIR": "data", "ALPHA_RUNTIME_TMP_DIR": "runtime",
            "SIGNAL_TRACKER_DB_PATH": "tracker.sqlite",
            "SIGNAL_DELIVERY_JOURNAL_DB_PATH": "acceptance.sqlite",
            "SUPPRESSION_TELEMETRY_DB_PATH": "suppression.sqlite",
            "MAIL_OUTBOX_DB_PATH": "outbox.sqlite", "AUTH_DB_PATH": "auth.sqlite",
            "AUTH_DB_LEGACY_JSON_PATH": "missing-users.json", "EMAIL_DEDUPE_FILE": "dedupe.json",
        }.items()
    }
    state_env.update(PYTHONDONTWRITEBYTECODE="1", POLYGON_KEY="offline-research",
        GMAIL_USER="sender@example.invalid", GMAIL_APP_PASSWORD="offline-only",
        ALERT_EMAIL="recipient@example.invalid", ALERT_SEND_TO_SUBSCRIBERS="0",
        JWT_SECRET="offline-research-not-a-deploy-secret", MAIL_OUTBOX_ENABLED="0",
        STOCK_SWING_DATA_MODE="starter_swing")
    original_exists, original_connect = Path.exists, socket.socket.connect

    def no_credentials(path):
        return False if path.name in {"secrets.toml", ".env"} else original_exists(path)

    def forbid_io(*args, **kwargs):
        raise RuntimeError("historical_replay_external_io_forbidden")

    def loopback_only(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(sock, address)
        return forbid_io()

    previous_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, state_env))
            stack.enter_context(patch.object(Path, "exists", no_credentials))
            stack.enter_context(patch.object(socket.socket, "connect", loopback_only))
            stack.enter_context(patch.object(smtplib, "SMTP", forbid_io))
            stack.enter_context(patch.object(smtplib, "SMTP_SSL", forbid_io))
            import api

            stack.enter_context(patch.object(api.req.sessions.Session, "request", forbid_io))
            stack.enter_context(patch.object(api, "_send_email_alert", forbid_io))
            yield api
    finally:
        os.chdir(previous_cwd)
