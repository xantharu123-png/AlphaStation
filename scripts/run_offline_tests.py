"""Local QA only: fake credentials, private disposable state, external I/O denied.

Run from a source checkout; never use this launcher as a production service.
"""
import os
import smtplib
import socket
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(os.environ.get("ALPHA_QA_SOURCE_ROOT", Path(__file__).resolve().parent.parent)).resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    qa_output_root = os.environ.get("ALPHA_QA_OUTPUT_ROOT")
    RUN = (Path(qa_output_root) / ("qa-" + uuid4().hex[:12]) if qa_output_root
           else ROOT / "output" / ("mail-fix-qa-" + uuid4().hex))
    RUN.mkdir(parents=True)
    for key, name in {
        "ALPHA_DATA_DIR": "data", "ALPHA_RUNTIME_TMP_DIR": "runtime",
        "SIGNAL_TRACKER_DB_PATH": "tracker.sqlite",
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH": "acceptance.sqlite",
        "SUPPRESSION_TELEMETRY_DB_PATH": "suppression.sqlite",
        "MAIL_OUTBOX_DB_PATH": "outbox.sqlite", "AUTH_DB_PATH": "auth.sqlite",
        "AUTH_DB_LEGACY_JSON_PATH": "missing-users.json", "EMAIL_DEDUPE_FILE": "dedupe.json",
    }.items():
        os.environ[key] = str(RUN / name)
    (RUN / "runtime").mkdir()
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", POLYGON_KEY="offline-fixture",
        GMAIL_USER="sender@example.invalid", GMAIL_APP_PASSWORD="offline-test-only",
        ALERT_EMAIL="recipient@example.invalid", ALERT_SEND_TO_SUBSCRIBERS="1",
        JWT_SECRET="offline-qa-secret-not-for-production", MAIL_OUTBOX_ENABLED="1")

real_connect = socket.socket.connect


def offline_connect(self, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return real_connect(self, address)
    raise RuntimeError("Offline QA prohibits external network")


socket.socket.connect = offline_connect


def no_smtp(*args, **kwargs):
    raise RuntimeError("Offline QA prohibits SMTP")


smtplib.SMTP = no_smtp
smtplib.SMTP_SSL = no_smtp
real_exists = Path.exists


def no_local_secrets(path):
    if path.name in {"secrets.toml", ".env"}:
        return False
    return real_exists(path)


# Tests can subsequently exercise temporary fixtures and monkeypatched SMTP.
with patch.object(Path, "exists", no_local_secrets):
    import api

if __name__ == "__main__":
    import pytest
    print("Isolated QA directory:", RUN, flush=True)
    raise SystemExit(pytest.main(["--basetemp", str(RUN / "pytest"), "-p", "no:cacheprovider",
                                 "--junitxml", str(RUN / "results.xml"),
                                 "--ignore=output", "--ignore=tmp", *sys.argv[1:]]))
