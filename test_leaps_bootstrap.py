"""Configured probe is functional with synthetic proof, credentials and IO."""
from importlib import import_module
from pathlib import Path
import json

import pytest

from scripts.probe_leaps_provider import main
from tests.fixtures.leaps.provider import NOW, capability, Response, chain_raw
from tests.fixtures.leaps.api import certified


def bootstrap():
    assert Path('modules/leaps/bootstrap.py').is_file(), 'configured bootstrap missing'
    return import_module('modules.leaps.bootstrap')


def test_cli_dry_run_never_invokes_configured_factory(capsys):
    def forbidden():
        pytest.fail('dry-run loaded credentials/config')
    assert main([], factory=forbidden)==0
    assert json.loads(capsys.readouterr().out)['requests']==0


@pytest.mark.parametrize('retry',[False,True])
def test_cli_configured_synthetic_path_three_total_attempts(tmp_path,capsys,retry):
    mod = bootstrap()
    admission = certified(tmp_path)
    responses = ([Response(status=500),Response(),Response()] if retry else
        [Response(),Response({'status':'OK','results':[chain_raw()]}),Response({'status':'OK','results':{'T':'SOFI','p':20,'P':21,'s':200,'S':300,'y':NOW-1}})])
    calls = []
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        return responses.pop(0)
    def factory():
        return mod.ProviderConfiguration('synthetic-private-key',admission,lambda:NOW,capability(),request)
    assert main(['--execute','--max-requests','3','--underlying','SOFI'],factory=factory)==0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert len(calls)==3 and payload['requests']==3 and payload['display_rights']=='unverified'
    assert all(c[2]['allow_redirects'] is False and c[2]['timeout']==(3,10) for c in calls)
    assert 'synthetic-private-key' not in output and all('synthetic-private-key' not in c[1] for c in calls)
    assert calls[0][2]['headers']['Authorization']=='Bearer synthetic-private-key'
    if not retry:
        assert payload['option_quotes'] is True and payload['stock_quotes'] is True


def test_execute_missing_or_broken_factory_sanitized_zero_transport(monkeypatch,capsys):
    bootstrap()
    monkeypatch.delenv('LEAPS_PROVIDER_FACTORY',raising=False)
    assert main(['--execute','--max-requests','3','--underlying','SOFI'])==2
    assert json.loads(capsys.readouterr().out)['reason']=='configured_factory_required'
    def broken():
        raise RuntimeError('synthetic-private-key')
    assert main(['--execute','--max-requests','3','--underlying','SOFI'],factory=broken)==2
    assert 'synthetic-private-key' not in capsys.readouterr().out


def test_unproven_admission_factory_never_dispatches(tmp_path,capsys):
    mod = bootstrap()
    from modules.leaps.admission import Admission
    def transport(*args,**kwargs):
        pytest.fail('unproven config dispatched')
    config = mod.ProviderConfiguration('synthetic-private-key',Admission(None,None,lambda:NOW),lambda:NOW,capability(),transport)
    assert main(['--execute','--max-requests','3','--underlying','SOFI'],factory=lambda:config)==2
    payload = json.loads(capsys.readouterr().out)
    assert payload['requests']==0 and payload['reason']=='verified_budget_priority_required'
