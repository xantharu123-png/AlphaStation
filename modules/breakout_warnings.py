"""Presentation-only warning for an otherwise accepted completed-close breakout."""
from collections.abc import MutableMapping

BREAKOUT_WITHOUT_RETEST_CODE = "breakout_confirmed_without_retest"
BREAKOUT_WITHOUT_RETEST_WARNING = "Ausbruch bestaetigt; Ruecktest noch nicht bestaetigt."


def breakout_warning_fields(confirmed_close: bool, retest_confirmed: bool = False) -> dict:
    """Do not infer confirmation, grant eligibility, or label an existing retest missing."""
    if confirmed_close is not True or retest_confirmed is not False:
        return {}
    return {
        "breakout_confirmation": "confirmed_close",
        "retest_status": "not_confirmed",
        "retest_warning": BREAKOUT_WITHOUT_RETEST_WARNING,
        "warning_codes": [BREAKOUT_WITHOUT_RETEST_CODE],
    }


def apply_breakout_warning(row, confirmed_close: bool, retest_confirmed: bool = False):
    """Idempotently attach/clear only this warning; preserve unrelated warnings.

    Call with freshly validated acceptance evidence, not a cached marker.
    Updates the supplied mapping in place and returns it for ordinary adapters.
    """
    if not isinstance(row, MutableMapping):
        return row
    previous_codes = row.get("warning_codes")
    codes = list(previous_codes) if isinstance(previous_codes, (list, tuple)) else []
    owned = (BREAKOUT_WITHOUT_RETEST_CODE in codes
             or row.get("retest_warning") == BREAKOUT_WITHOUT_RETEST_WARNING
             or (row.get("breakout_confirmation") == "confirmed_close"
                 and row.get("retest_status") == "not_confirmed"))
    if owned:
        for field, value in breakout_warning_fields(True).items():
            if field != "warning_codes" and row.get(field) == value:
                row.pop(field, None)
    codes = [value for value in codes if value != BREAKOUT_WITHOUT_RETEST_CODE]
    if isinstance(previous_codes, (list, tuple)):
        if codes:
            row["warning_codes"] = codes
        elif previous_codes:
            row.pop("warning_codes", None)
    for field in ("warnings", "notes", "risk_flags"):
        values = row.get(field)
        if isinstance(values, list):
            row[field] = [value for value in values if value not in (
                BREAKOUT_WITHOUT_RETEST_CODE, BREAKOUT_WITHOUT_RETEST_WARNING)]
    fields = breakout_warning_fields(confirmed_close, retest_confirmed)
    if fields:
        row.update(fields)
        row["warning_codes"] = [*codes, BREAKOUT_WITHOUT_RETEST_CODE]
    return row
