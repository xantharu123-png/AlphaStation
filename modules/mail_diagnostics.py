"""Request-local reuse for read-only mail diagnostics, never for sending.

This is not a transaction across scanner caches. It only avoids repeatedly
filtering the same reference universe during one synchronous admin request.
There is no process-wide cache or reuse between requests.
"""
from contextvars import ContextVar
from functools import wraps


_reference_reads = ContextVar("mail_diagnostic_reference_reads", default=None)

TRANSPORT_DIAGNOSTIC_LABELS = {
    "smtp_authentication_failed": "Mailserver lehnt die Anmeldung ab",
    "smtp_recipients_rejected": "Mailserver hat die Empfaenger abgelehnt",
    "smtp_sender_rejected": "Mailserver hat den Absender abgelehnt",
    "smtp_message_rejected": "Mailserver hat die Nachricht abgelehnt",
    "smtp_delivery_failed": "Versand fehlgeschlagen; keine Mailserver-Annahme",
    "smtp_delivery_outcome_unknown": "Mailserver-Annahme unklar; kein automatischer Neuversand",
}


def transport_diagnostic_code(raw_reason):
    """Decode only the sender's exact type:outcome format; never echo text.

    Unknown DATA outcomes take precedence over exception names: an unexpected
    SMTPDataError can mean a malformed reply, not an explicit rejection.
    """
    if not isinstance(raw_reason, str) or len(raw_reason) > 120:
        return None
    error_type, separator, outcome = raw_reason.partition(":")
    if not separator or not error_type.isascii() or not error_type.isidentifier():
        return None
    if outcome == "outcome_unknown":
        return "smtp_delivery_outcome_unknown"
    if outcome != "not_delivered":
        return None
    return {
        "SMTPAuthenticationError": "smtp_authentication_failed",
        "SMTPRecipientsRefused": "smtp_recipients_rejected",
        "SMTPSenderRefused": "smtp_sender_rejected",
        "SMTPDataError": "smtp_message_rejected",
    }.get(error_type, "smtp_delivery_failed")


def mail_diagnostic_snapshot(function):
    """Give each invocation a fresh scope and release it even on failure."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _reference_reads.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _reference_reads.reset(token)
    return wrapped


def read_diagnostic_reference(key, reader):
    """Reuse a successful read only inside the explicitly opted-in request."""
    reads = _reference_reads.get()
    if reads is None:
        return reader()
    if key not in reads:
        reads[key] = reader()
    return reads[key]
