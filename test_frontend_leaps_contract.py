"""Execute LEAPS presentation contracts; rendered integration lives in browser flow."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent


def evaluate(expression):
    source = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
    start, end = '// LEAPS pure presentation contract', '// LEAPS React flow'
    block = source.split(start, 1)[1].split(end, 1)[0] if start in source else ''
    script = block + '\ntry { console.log(JSON.stringify(' + expression + ')); } catch(e) { console.log(JSON.stringify({missing:e.message})); }'
    result = subprocess.run([shutil.which('node')], input=script, encoding='utf-8', capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


POLICY = dict(enabled=True, results_allowed=True, display_rights_verified=True,
              admin=True, endpoint_capabilities={key: True for key in ['results', 'status', 'scenarios', 'scan', 'control']},
              capabilities={key: True for key in ['reference', 'option_quotes', 'standard_terms', 'sessions', 'budget']})


def test_null_legacy_tabs_do_not_grant_leaps_read():
    assert evaluate('leapsReadAccess(null)') is False
    assert evaluate('leapsReadAccess({enabled:true,results_allowed:true})') is False
    assert evaluate(f'leapsReadAccess({json.dumps(POLICY)})') is True


@pytest.mark.parametrize('key', ['enabled', 'results_allowed', 'display_rights_verified'])
def test_each_read_gate_is_explicit(key):
    policy = {**POLICY, key: False}
    assert evaluate(f'leapsReadAccess({json.dumps(policy)})') is False


@pytest.mark.parametrize('value,want', [(None, '–'), ('0', '0.00'), ('10.55', '10.55'), ('bad', '–'), (False, '–')])
def test_unknown_money_is_not_zero(value, want):
    assert evaluate(f'leapsFormat({json.dumps(value)})') == want


def test_expiry_uses_original_nanoseconds_and_never_receipt():
    row = {'assessment': {'comparison_eligible': True, 'expiry_ns': '1000000000000000001'},
           'observation': {'quote': {'source_ns': '999999999999999999', 'received_ns': '2000000000000000000'}}}
    assert evaluate(f'leapsQuoteCurrent({json.dumps(row)}, 1000000000000)') is True
    assert evaluate(f'leapsQuoteCurrent({json.dumps(row)}, 1000000000001)') is False
    row['assessment']['comparison_eligible'] = False
    assert evaluate(f'leapsQuoteCurrent({json.dumps(row)}, 999999999999)') is False


@pytest.mark.parametrize('value,want', [('99999999999999999999.995', '100000000000000000000.00'), ('9007199254740993.12', '9007199254740993.12'), ('1E+20', '100000000000000000000.00')])
def test_large_server_cash_has_no_binary_float_loss(value, want):
    assert evaluate(f'leapsFormat({json.dumps(value)})') == want


def test_default_quantity_request_does_not_round_trip_backend_prices():
    selection = {'run_id': 'run-A', 'snapshot_id': 'a'*64, 'row': {'contract': {'id': 'call-A'}}}
    assert evaluate(f'leapsScenarioRequest({json.dumps(selection)}, 2)') == {
        'run_id': 'run-A', 'snapshot_id': 'a'*64, 'contract_id': 'call-A', 'quantity': 2}


def test_selection_identity_changes_on_run_snapshot_contract_and_quantity():
    selection = {'run_id': 'A', 'snapshot_id': 'S', 'row': {'contract': {'id': 'C'}}}
    first = evaluate(f'leapsSelectionKey({json.dumps(selection)}, 1)')
    for key in ['run_id', 'snapshot_id']:
        changed = {**selection, key: 'new'}
        assert evaluate(f'leapsSelectionKey({json.dumps(changed)}, 1)') != first
    changed = {**selection, 'row': {'contract': {'id': 'new'}}}
    assert evaluate(f'leapsSelectionKey({json.dumps(changed)}, 1)') != first
    assert evaluate(f'leapsSelectionKey({json.dumps(selection)}, 2)') != first


def test_customer_and_unproven_capability_cannot_control():
    customer = {**POLICY, 'admin': False}
    assert evaluate(f'leapsControlReason({json.dumps(customer)}, "scan", null)') == 'Nur Admin'
    unproven = {**POLICY, 'capabilities': {**POLICY['capabilities'], 'budget': False}}
    assert evaluate(f'leapsControlReason({json.dumps(unproven)}, "scan", null)') == 'Daten-/Budgetnachweis fehlt'
    assert evaluate(f'leapsControlReason({json.dumps(POLICY)}, "pause", null)') == 'Kein aktiver Lauf'
    assert evaluate(f'leapsControlReason({json.dumps(POLICY)}, "scan", null)') == ''
