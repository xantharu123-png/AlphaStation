"""Pure provider-schema normalization and ordinary reference-field terms.

Synthetic authentic-schema fixtures; never requests provider/account resources.
"""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from modules.leaps import contracts
from modules.leaps.models import GreeksObservation, LeapsDataError
from modules.leaps.provider import MassiveLeapsProvider
from tests.fixtures.leaps.provider import (
    NOW, Admission, Transport, capability, chain_raw, contract, listing, raw_contract,
)


SNAPSHOT_DOC = 'https://massive.com/docs/rest/options/snapshots/option-chain-snapshot'
REFERENCE_DOC = 'https://massive.com/docs/rest/options/contracts/all-contracts'
STANDARD_DOC = 'https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications'


def parser():
    admission = Admission()
    return MassiveLeapsProvider(Transport([], admission), lambda: NOW, admission, capability())


def reference_and_listing(kind='CS'):
    reference = raw_contract()
    reference['cfi'] = 'OCASPS'
    return reference, listing(kind=kind)


def derive(raw, stock):
    helper = getattr(contracts, 'derive_massive_standard_terms', None)
    assert callable(helper), 'ordinary Massive reference-field terms producer is missing'
    return helper(raw, stock)


def test_snapshot_values_become_exact_fractions_without_borrowing_a_clock():
    """Catches dropped provider Greeks, percent rescaling, or fabricated model time."""
    raw = chain_raw()
    raw['greeks']['delta'] = D('0.612345678901')
    raw['implied_volatility'] = D('0.412345678901')
    before = deepcopy(raw)
    result = parser().parse_chain_item(raw, contract())

    assert result.greeks.delta == D('0.612345678901')
    assert result.greeks.iv == D('0.412345678901')
    assert result.greeks.raw['delta'] == D('0.612345678901')
    assert result.greeks.raw['implied_volatility'] == D('0.412345678901')
    assert result.greeks.raw['gamma'] == D('0.01')
    assert result.greeks.raw['theta'] == D('-0.005')
    assert result.greeks.raw['vega'] == D('0.3')
    assert result.greeks.units_evidence['delta'] == 'fraction'
    assert result.greeks.units_evidence['iv'] == 'fraction'
    assert SNAPSHOT_DOC in result.greeks.units_evidence['evidence_ids']
    assert result.greeks.model_evidence is None
    assert result.greeks.source_ns is None
    assert result.reference_source_ns is None
    assert raw == before


@pytest.mark.parametrize('delta,iv,want_delta,want_iv', [
    (0, 0, D('0'), D('0')),
    (1, D('1.000001'), D('1'), D('1.000001')),
    (D('0.5'), 35, D('0.5'), D('35')),
])
def test_fraction_boundaries_and_large_iv_are_not_percent_heuristics(delta, iv, want_delta, want_iv):
    """Catches endpoint rejection or dividing a large valid IV by 100."""
    raw = chain_raw()
    raw['greeks']['delta'], raw['implied_volatility'] = delta, iv
    result = parser().parse_chain_item(raw, contract()).greeks
    assert result.delta == want_delta
    assert result.iv == want_iv


@pytest.mark.parametrize('delta,iv', [
    (D('0.6594548069277387'), D('0.2985341234567891')),
    (D('0.123456789012345678901234567890123456'), D('0.000000000000000000000000000000000001')),
])
def test_real_snapshot_precision_is_preserved_without_rounding(delta, iv):
    """Catches applying the 12-place money-input policy to model fractions."""
    raw = chain_raw()
    raw['greeks']['delta'], raw['implied_volatility'] = delta, iv
    result = parser().parse_chain_item(raw, contract()).greeks
    assert result.delta == delta and result.iv == iv
    assert result.raw['delta'] == delta and result.raw['implied_volatility'] == iv
    assert result.delta.as_tuple() == delta.as_tuple()
    assert result.iv.as_tuple() == iv.as_tuple()


@pytest.mark.parametrize('value', [
    D('0.6594548069277387'), D('1e-36'), D('1000000000000'),
])
def test_greek_model_accepts_bounded_exact_provider_precision(value):
    """Catches a model reapplying the money-input cap after wire normalization."""
    result = GreeksObservation(iv=value, units_evidence={'iv': 'fraction'})
    assert result.iv == value and result.iv.as_tuple() == value.as_tuple()


@pytest.mark.parametrize('value', [
    D('1e-37'), D('0e-1000000'), D('0e+1000000'),
    D('1000000000000.000000000000000000000000000000000001'),
    D('0.1234567890123456789012345678901234567'),
    D('1.00000000000000000000000000000000000000000000000000000000000000000'),
    D('NaN'), D('Infinity'), D('-Infinity'), True, '0.4', 0.4,
])
def test_wire_and_model_reject_nonfinite_or_unbounded_greek_numbers(value):
    """Catches malicious Decimal exponent/precision inputs or invalid wire types."""
    raw = chain_raw()
    raw['implied_volatility'] = value
    with pytest.raises(LeapsDataError):
        parser().parse_chain_item(raw, contract())
    with pytest.raises(LeapsDataError):
        GreeksObservation(iv=value, units_evidence={'iv': 'fraction'})


def test_wider_greek_precision_does_not_relax_money_input_precision():
    """Catches widening the shared financial input parser to admit model fields."""
    with pytest.raises(LeapsDataError, match='decimal_bounds'):
        contracts.parse_decimal(D('0.6594548069277387'))


@pytest.mark.parametrize('which', ['both', 'delta', 'iv', 'null_delta', 'null_iv'])
def test_missing_provider_greeks_remain_missing_independently(which):
    """Catches zero filling and discarding the other independently present field."""
    raw = chain_raw()
    if which in ('both', 'delta'):
        raw.pop('greeks')
    if which in ('both', 'iv'):
        raw.pop('implied_volatility')
    if which == 'null_delta':
        raw['greeks']['delta'] = None
    if which == 'null_iv':
        raw['implied_volatility'] = None
    result = parser().parse_chain_item(raw, contract()).greeks
    assert result.delta == (None if which in ('both', 'delta', 'null_delta') else D('0.6'))
    assert result.iv == (None if which in ('both', 'iv', 'null_iv') else D('0.4'))
    assert result.source_ns is None and result.model_evidence is None


@pytest.mark.parametrize('field,value', [
    ('delta', D('-0.000000000001')), ('delta', D('1.000000000001')),
    ('delta', 35), ('implied_volatility', D('-0.000000000001')),
    ('delta', D('NaN')), ('delta', D('Infinity')), ('implied_volatility', D('-Infinity')),
    ('delta', True), ('delta', '0.6'), ('delta', 0.6),
    ('implied_volatility', False), ('implied_volatility', '0.4'),
    ('gamma', D('NaN')), ('theta', {}), ('vega', []),
])
def test_malformed_nonfinite_or_out_of_domain_greeks_are_not_admitted(field, value):
    """Catches accepting put delta on a call or malformed values as filter evidence."""
    raw = chain_raw()
    if field == 'implied_volatility':
        raw[field] = value
    else:
        raw['greeks'][field] = value
    with pytest.raises(LeapsDataError):
        parser().parse_chain_item(raw, contract())


@pytest.mark.parametrize('field,value', [
    ('ticker', 'O:SOFI280121C00016000'), ('contract_type', 'put'),
    ('strike_price', 16), ('shares_per_contract', True),
    ('exercise_style', 'european'), ('expiration_date', '2028-01-22'),
])
def test_normalized_greeks_never_authorize_a_mismatched_contract(field, value):
    """Catches moving Greek availability ahead of the proven-contract join."""
    raw = chain_raw()
    raw['details'][field] = value
    with pytest.raises(LeapsDataError, match='chain_contract_mismatch'):
        parser().parse_chain_item(raw, contract())


@pytest.mark.parametrize('kind,asset_class', [('CS', 'equity'), ('ADRC', 'adr'), ('ADRP', 'adr')])
def test_ordinary_reference_fields_produce_consumable_standard_100_terms(kind, asset_class):
    """Catches missing ordinary terms or treating ADRs as unknown/index products."""
    reference, stock = reference_and_listing(kind)
    before = deepcopy((reference, stock))
    evidence = derive(reference, stock)
    parsed = contracts.parse_standard_contract(reference, stock, evidence)

    assert parsed.underlying == 'SOFI' and parsed.right == 'call'
    assert parsed.premium_multiplier == D(100) and parsed.shares == 100
    assert parsed.currency == 'USD' and parsed.settlement == 'physical'
    assert parsed.evidence['asset_class'] == asset_class
    assert REFERENCE_DOC in parsed.evidence['evidence_ids']
    assert STANDARD_DOC in parsed.evidence['evidence_ids']
    assert parsed.evidence['premium_multiplier_rule'] == 'standard_us_equity_adr_100'
    assert (reference, stock) == before


@pytest.mark.parametrize('field', ['cfi', 'contract_type', 'exercise_style', 'shares_per_contract',
                                  'underlying_ticker', 'ticker', 'primary_exchange'])
def test_terms_producer_does_not_invent_missing_reference_conditions(field):
    """Catches ordinary-terms defaults for essential missing provider fields."""
    reference, stock = reference_and_listing()
    reference.pop(field)
    assert derive(reference, stock) is None


@pytest.mark.parametrize('field,value', [
    ('cfi', 'OCESPS'), ('cfi', None), ('contract_type', 'put'),
    ('exercise_style', 'european'), ('shares_per_contract', 150),
    ('shares_per_contract', True), ('shares_per_contract', D(100)),
    ('additional_underlyings', [{'amount': D('0.01'), 'type': 'cash', 'underlying': 'USD'}]),
    ('additional_underlyings', None), ('additional_underlyings', {}),
    ('strike_price', 16), ('expiration_date', '2028-01-22'),
    ('ticker', 'O:SOFI1280121C00015000'), ('underlying_ticker', 'OTHER'),
    ('correction', True), ('primary_exchange', ''),
])
def test_nonordinary_or_contradictory_reference_fields_do_not_produce_terms(field, value):
    """Catches CFI-only approval, ignored cash deliverables, or symbol mismatch."""
    reference, stock = reference_and_listing()
    reference[field] = value
    assert derive(reference, stock) is None


@pytest.mark.parametrize('field,value', [
    ('ticker', 'OTHER'), ('market', 'indices'), ('locale', 'global'),
    ('type', 'ETF'), ('active', False), ('active', 1), ('currency_name', 'eur'),
    ('currency_name', None), ('primary_exchange', ''),
])
def test_ineligible_or_mismatched_actual_listing_does_not_produce_terms(field, value):
    """Catches trusting request filters instead of the actual returned listing."""
    reference, stock = reference_and_listing()
    stock[field] = value
    assert derive(reference, stock) is None


def test_explicitly_empty_additional_underlyings_remains_an_ordinary_contract():
    """Catches rejecting the schema's explicit no-extra-deliverable representation."""
    reference, stock = reference_and_listing()
    reference['additional_underlyings'] = []
    evidence = derive(reference, stock)
    assert contracts.parse_standard_contract(reference, stock, evidence).shares == 100
