"""Synthetic boundary fixtures; no provider I/O or private market data."""
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, getcontext, ROUND_DOWN
from importlib import import_module
from pathlib import Path
from itertools import product

import pytest


def api():
    # Missing implementation is a deliberate assertion, not a collection error.
    assert Path(__file__).with_name("modules").joinpath("leaps", "contracts.py").is_file(), "LEAPS contract parser feature is missing"
    return import_module("modules.leaps.contracts"), import_module("modules.leaps.models")


def standard():
    return (
        dict(ticker="O:SOFI280121C00015000", underlying_ticker="SOFI", contract_type="call", strike_price="15", expiration_date="2028-01-21", shares_per_contract=100, exercise_style="american", primary_exchange="XNAS"),
        dict(ticker="SOFI", market="stocks", locale="us", type="CS", active=True, currency_name="usd", primary_exchange="XNAS"),
        dict(standard=True, asset_class="equity", premium_multiplier=100, currency="USD", settlement="physical", evidence_ids=["synthetic-terms-1"]),
    )


def test_sofi_contract_identity_decodes_exact_occ_terms():
    c, _ = api()
    contract = c.parse_standard_contract(*standard())
    assert (contract.provider_symbol, contract.underlying, contract.strike, contract.expiry, contract.right) == ("O:SOFI280121C00015000", "SOFI", Decimal("15"), date(2028, 1, 21), "call")
    raw, listing, evidence = standard()
    raw.update(ticker="O:SOFI280121C00016000", strike_price="16")
    assert c.parse_standard_contract(raw, listing, evidence).id != contract.id


@pytest.mark.parametrize("key,value", [("strike_price", "16"), ("expiration_date", "2028-01-22"), ("contract_type", "put"), ("underlying_ticker", "F"), ("strike_price", "0"), ("ticker", "SOFI280121C00015000junk")])
def test_mismatched_or_ineligible_contract_metadata_fails_closed(key, value):
    c, m = api()
    raw, listing, evidence = standard()
    raw[key] = value
    with pytest.raises(m.LeapsDataError) as error:
        c.parse_standard_contract(raw, listing, evidence)
    assert error.value.scope == "contract"


@pytest.mark.parametrize("field", ["ticker", "underlying_ticker", "contract_type", "strike_price", "expiration_date", "shares_per_contract", "exercise_style", "primary_exchange"])
def test_missing_essential_contract_field_rejected(field):
    c, m = api()
    raw, listing, evidence = standard()
    raw.pop(field)
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


@pytest.mark.parametrize("change", ["cash", "150_shares", "multiplier", "unknown", "no_reference", "unknown_settlement", "unknown_currency", "unknown_asset"])
def test_nonstandard_or_unproven_terms_rejected(change):
    c, m = api()
    raw, listing, evidence = standard()
    if change == "cash":
        raw["additional_underlyings"] = [dict(amount="0.01", type="cash", underlying="USD")]
    elif change == "150_shares":
        raw["shares_per_contract"] = 150
    elif change == "multiplier":
        evidence["premium_multiplier"] = 150
    elif change == "unknown":
        evidence["standard"] = None
    elif change == "no_reference":
        evidence["evidence_ids"] = []
    elif change == "unknown_settlement":
        evidence.pop("settlement")
    elif change == "unknown_currency":
        evidence.pop("currency")
    else:
        evidence["asset_class"] = "index"
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


def test_dotted_share_class_root_requires_explicit_alias_evidence():
    c, m = api()
    raw, listing, evidence = standard()
    raw.update(ticker="O:BRKB280121C00015000", underlying_ticker="BRK.B")
    listing["ticker"] = "BRK.B"
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)
    evidence["root_aliases"] = {"BRKB": "BRK.B"}
    assert c.parse_standard_contract(raw, listing, evidence).underlying == "BRK.B"


@pytest.mark.parametrize("value,want", [("0.1", "0.1"), ("1.123456789012", "1.123456789012"), (1000000000000, "1000000000000"), ("1e12", "1E+12"), (Decimal("-0.5"), "-0.5")])
def test_decimal_preserves_exact_bounded_values(value, want):
    c, _ = api()
    assert c.parse_decimal(value) == Decimal(want)


@pytest.mark.parametrize("value", [True, False, 0.1, float("nan"), "NaN", "Infinity", "1,000", "$1", " 1", "1 ", "1_000", "1.1234567890123", "1.1234567890123e1", "1e-1000000", "1e1000000", "1000000000001", "-1000000000001", "0" * 65, None, {}, ""])
def test_decimal_rejects_unsafe_or_unbounded_input(value):
    c, m = api()
    with pytest.raises(m.LeapsDataError):
        c.parse_decimal(value)


@pytest.mark.parametrize("value", [0, -1, 1000001, True, "1", Decimal("1.5"), 1.0])
def test_quantity_requires_bounded_native_integer(value):
    c, m = api()
    with pytest.raises(m.LeapsDataError):
        c.parse_quantity(value)


def test_quantity_inclusive_bounds():
    c, _ = api()
    assert (c.parse_quantity(1), c.parse_quantity(1000000)) == (1, 1000000)


def test_local_decimal_context_does_not_change_global_settings():
    c, _ = api()
    before = getcontext().copy()
    try:
        getcontext().prec = 7
        getcontext().rounding = ROUND_DOWN
        with c.decimal_context():
            assert Decimal(1) / Decimal(3) == Decimal("0." + "3" * 50)
            assert getcontext().rounding == "ROUND_HALF_EVEN"
        assert (getcontext().prec, getcontext().rounding) == (7, ROUND_DOWN)
    finally:
        getcontext().prec, getcontext().rounding = before.prec, before.rounding


@pytest.mark.parametrize("as_of,want", [(date(2026, 1, 31), (date(2027, 1, 31), date(2028, 1, 31))), (date(2024, 2, 29), (date(2025, 2, 28), date(2026, 2, 28))), (date(2023, 2, 28), (date(2024, 2, 28), date(2025, 2, 28)))])
def test_expiry_window_uses_inclusive_calendar_months(as_of, want):
    c, _ = api()
    assert c.expiry_window(as_of) == want


@pytest.mark.parametrize("value", [datetime(2026, 10, 13), datetime(2026, 10, 13, tzinfo=timezone(timedelta(hours=1))), "2026-10-13T12:00:00", "broken"])
def test_non_utc_or_naive_time_rejected(value):
    c, m = api()
    with pytest.raises(m.LeapsDataError):
        c.parse_utc_datetime(value)


def test_utc_time_and_nanosecond_epoch_remain_exact():
    c, _ = api()
    assert c.parse_utc_datetime("2026-10-13T12:00:00Z") == datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    assert c.parse_ns_epoch("1791892800000000001") == 1791892800000000001


@pytest.mark.parametrize("value", [1791892800, 1791892800000, 1791892800000000, True, 1791892800000000000.0, "1791892800000000000.5", -1])
def test_epoch_seconds_milliseconds_microseconds_not_treated_as_ns(value):
    c, m = api()
    with pytest.raises(m.LeapsDataError):
        c.parse_ns_epoch(value)


def test_contract_evidence_is_deeply_immutable_and_serializes_decimal_strings():
    c, m = api()
    raw, listing, evidence = standard()
    contract = c.parse_standard_contract(raw, listing, evidence)
    evidence["evidence_ids"].append("mutated")
    assert contract.evidence["evidence_ids"] == ("synthetic-terms-1",)
    with pytest.raises(TypeError):
        contract.evidence["standard"] = False
    with pytest.raises(FrozenInstanceError):
        contract.strike = Decimal("16")
    payload = m.to_json_value(contract)
    assert payload["strike"] == "15"
    assert payload["expiry"] == "2028-01-21"
    assert payload["premium_multiplier"] == "100"


def test_optional_quote_metadata_is_nullable_and_ns_serializes_as_string():
    _, m = api()
    observation = m.QuoteObservation(bid=None, ask=Decimal("2.35"), source_ns=1791892800000000001)
    payload = m.to_json_value(observation)
    assert payload["ask"] == "2.35"
    assert payload["bid"] is None and payload["feed_lag_s"] is None
    assert payload["source_ns"] == "1791892800000000001"


@pytest.mark.parametrize("ask", [Decimal("0"), Decimal("-1"), Decimal("NaN"), "2", 2.0, True])
def test_quote_ask_requires_positive_exact_decimal_when_present(ask):
    _, m = api()
    with pytest.raises(m.LeapsDataError):
        m.QuoteObservation(ask=ask)


@pytest.mark.parametrize("field", ["standard", "asset_class", "premium_multiplier", "currency", "settlement", "evidence_ids"])
def test_missing_each_normative_proof_field_fails_closed(field):
    c, m = api()
    raw, listing, evidence = standard()
    evidence.pop(field)
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


@pytest.mark.parametrize("field,value", [("market", "crypto"), ("locale", "gb"), ("active", False), ("active", 1), ("type", "ETF"), ("currency_name", "eur"), ("ticker", "F")])
def test_listing_must_prove_eligible_us_equity_or_adr(field, value):
    c, m = api()
    raw, listing, evidence = standard()
    listing[field] = value
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


def test_optional_correction_changes_provenance_not_standard_admission():
    c, _ = api()
    raw, listing, evidence = standard()
    original = c.parse_standard_contract(raw, listing, evidence)
    raw["correction"] = 1
    corrected = c.parse_standard_contract(raw, listing, evidence)
    assert corrected.id == original.id
    assert corrected.terms_fingerprint != original.terms_fingerprint


def test_adr_listing_with_standard_normative_proof_is_eligible():
    c, _ = api()
    raw, listing, evidence = standard()
    listing["type"] = "ADRC"
    evidence["asset_class"] = "adr"
    assert c.parse_standard_contract(raw, listing, evidence).shares == 100


def test_proven_fraction_greeks_remain_fraction_and_provenance_nullable():
    _, m = api()
    greeks = m.GreeksObservation(delta=Decimal("0.35"), iv=Decimal("0.4"), units_evidence={"delta": "fraction", "iv": "fraction"})
    assert m.to_json_value(greeks)["delta"] == "0.35"
    assert greeks.source_ns is None and greeks.model_evidence is None


def test_unproven_greek_units_never_become_normalized_values():
    _, m = api()
    greeks = m.GreeksObservation(delta=Decimal("0.35"), iv=Decimal("35"), raw={"iv": "35"})
    assert greeks.delta is None and greeks.iv is None
    assert m.to_json_value(greeks)["raw"] == {"iv": "35"}


def test_shared_snapshot_serialization_preserves_ratios_and_freezes_nested_data():
    _, m = api()
    result = m.ExpiryResult(Decimal("235"), Decimal("500"), Decimal("265"), Decimal("0.35"), Decimal("17.35"), Decimal("1500"), Decimal("500"), Decimal("0.2"), None)
    run = m.RunSpec("synthetic-run", date(2026, 10, 13), {"minimum": Decimal("0.35")}, "u1", "1", "1")
    data = {"results": [result]}
    snapshot = m.LeapsSnapshot(run, "complete", data, {}, [], None, [])
    data["results"].clear()
    payload = m.to_json_value(snapshot)
    assert payload["data"]["results"][0]["premium_roi"] == "0.35"
    assert payload["run"]["filters"]["minimum"] == "0.35"


def test_data_errors_expose_only_sanitized_code_and_scope():
    _, m = api()
    error = m.LeapsDataError("invalid_decimal", "contract")
    assert (error.code, error.scope, str(error)) == ("invalid_decimal", "contract", "contract:invalid_decimal")
    with pytest.raises(ValueError):
        m.LeapsDataError("https://private/key=secret", "transport")


@pytest.mark.parametrize("field,value", [("exercise_style", []), ("primary_exchange", None), ("expiration_date", {}), ("additional_underlyings", None)])
def test_malformed_optional_and_essential_wire_types_raise_safe_error(field, value):
    c, m = api()
    raw, listing, evidence = standard()
    raw[field] = value
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


def test_malformed_listing_type_raises_safe_error():
    c, m = api()
    raw, listing, evidence = standard()
    listing["type"] = []
    with pytest.raises(m.LeapsDataError):
        c.parse_standard_contract(raw, listing, evidence)


def test_quote_constructor_rejects_wrong_source_epoch_unit():
    _, m = api()
    with pytest.raises(m.LeapsDataError):
        m.QuoteObservation(ask=Decimal("2.35"), source_ns=1791892800000)


def test_local_context_ignores_global_exponent_resource_limits():
    c, _ = api()
    before = getcontext().copy()
    try:
        getcontext().Emax = 2
        getcontext().Emin = -2
        with c.decimal_context():
            assert Decimal("1000000").normalize() == Decimal("1e6")
    finally:
        getcontext().Emax, getcontext().Emin = before.Emax, before.Emin


def test_equivalent_strike_lexemes_share_semantic_terms_fingerprint():
    c, _ = api()
    raw, listing, evidence = standard()
    before = c.parse_standard_contract(raw, listing, evidence)
    raw["strike_price"] = "15.000"
    after = c.parse_standard_contract(raw, listing, evidence)
    assert before.terms_fingerprint == after.terms_fingerprint


@pytest.mark.parametrize("present", list(product((False, True), repeat=8)))
def test_quote_optional_metadata_combinations_remain_nullable(present):
    _, m = api()
    possible = {"bid_size": 1, "ask_size": 2, "source_ns": 1791892800000000000, "received_ns": 1791892800000000001, "size_unit": "contracts", "feed_mode": "realtime", "feed_lag_s": Decimal("0"), "source": "synthetic"}
    kwargs = {name: value for (name, value), enabled in zip(possible.items(), present) if enabled}
    payload = m.to_json_value(m.QuoteObservation(ask=Decimal("2.35"), **kwargs))
    for name, enabled in zip(possible, present):
        assert (payload[name] is not None) == enabled
