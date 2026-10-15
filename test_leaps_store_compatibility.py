"""Old saved quotes survive additive optional fields; strict fields stay strict."""
from decimal import Decimal

import pytest

from modules.leaps.models import GreeksObservation, LeapsDataError, QuoteObservation
from modules.leaps.provider import OptionQuote
from modules.leaps.runtime import _decode, _encode
from tests.fixtures.leaps.provider import NOW, contract


@pytest.mark.parametrize('missing', [
    ('volume', 'volume_source_ns', 'volume_session'),
    ('volume',), ('volume_source_ns',), ('volume_session',),
])
def test_legacy_saved_option_quote_restores_missing_volume_as_unknown(missing):
    encoded = _encode(OptionQuote(
        contract(), QuoteObservation(ask=Decimal('5.2')), GreeksObservation(), 1500,
        volume=123, volume_source_ns=NOW, volume_session='2026-10-13'))
    for field in missing:
        del encoded['v'][field]

    restored = _decode(encoded)

    assert restored.contract.underlying == 'SOFI'
    assert restored.quote.ask == Decimal('5.2')
    assert restored.open_interest == 1500
    assert restored.volume == (None if 'volume' in missing else 123)
    assert restored.volume_source_ns == (None if 'volume_source_ns' in missing else NOW)
    assert restored.volume_session == (None if 'volume_session' in missing else '2026-10-13')


@pytest.mark.parametrize('mutation', ['required_field_missing', 'unknown_field'])
def test_legacy_quote_compatibility_does_not_relax_required_or_unknown_fields(mutation):
    encoded = _encode(OptionQuote(contract(), QuoteObservation(), GreeksObservation(), 1500))
    if mutation == 'required_field_missing':
        del encoded['v']['quote']
    else:
        encoded['v']['untrusted_field'] = 1
    with pytest.raises(LeapsDataError, match='invalid_store'):
        _decode(encoded)
