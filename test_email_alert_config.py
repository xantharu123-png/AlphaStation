import api


def test_email_config_can_be_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL", "one@example.com, two@example.com")

    secrets = api._load_secrets()

    assert secrets["GMAIL_USER"] == "sender@example.com"
    assert secrets["GMAIL_APP_PASSWORD"] == "app-password"
    assert secrets["ALERT_EMAIL"] == "one@example.com, two@example.com"


def test_email_status_never_exposes_credentials(monkeypatch):
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.com",
        "GMAIL_APP_PASSWORD": "secret-app-password",
        "ALERT_EMAIL": "one@example.com,two@example.com",
    })

    status = api._email_alert_status()

    assert status["configured"] is True
    assert status["recipient_count"] == 2
    assert "secret-app-password" not in str(status)
    assert "sender@example.com" not in str(status)
