"""Real default physical worker, local HTTP only; exact Decimal wire retained."""
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from decimal import Decimal
import pytest
from modules.leaps.bootstrap import OneAttemptTransport
from modules.leaps.models import LeapsDataError


@pytest.fixture
def peer():
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            calls.append((self.path,self.headers.get('Accept-Encoding')))
            self.send_response(403 if self.path=='/denied' else 200)
            if self.path in ('/gzip','/denied'): self.send_header('Content-Encoding','gzip')
            self.end_headers()
            try:
                if self.path=='/slow':
                    for _ in range(100):
                        self.wfile.write(b' '); self.wfile.flush(); time.sleep(.03)
                elif self.path=='/large': self.wfile.write(b'x'*(8*1024*1024+1))
                elif self.path=='/gzip': self.wfile.write(gzip.compress(b'x'*(8*1024*1024+1)))
                else: self.wfile.write(b'{"price":900719.123456789012,"source_ns":1791898200000000000}')
            except (ConnectionError,OSError): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port),calls
    finally: server.shutdown(); server.server_close(); thread.join(3)


def physical_transport(seconds=2):
    # Trusted local configuration seam only; no browser field controls budgets.
    try:
        return OneAttemptTransport('synthetic-private-token',total_timeout_s=seconds,offline_only=True)
    except TypeError:
        pytest.fail('bounded isolated transport is absent')


def get(transport,url):
    return transport.request('GET',url,headers=transport.market_headers(),timeout=(3,10),allow_redirects=False)


def test_default_worker_original_wire_decimal_and_one_physical_call(peer,monkeypatch):
    import subprocess
    handles=[]
    real=subprocess.Popen
    def spawn(*args,**kwargs):
        assert 'synthetic-private-token' not in repr(args)+repr(kwargs.get('env'))
        result=real(*args,**kwargs); handles.append(result); return result
    monkeypatch.setattr(subprocess,'Popen',spawn)
    response=get(physical_transport(),peer[0]+'/valid')
    parsed=json.loads(response.text,parse_float=Decimal)
    assert parsed=={'price':Decimal('900719.123456789012'),'source_ns':1791898200000000000}
    assert peer[1]==[('/valid','identity')]
    assert len(handles)==1 and handles[0].poll()==0


def test_continuous_body_total_deadline_confirms_exit_before_native_lease_release(peer,tmp_path,monkeypatch):
    import subprocess
    from tests.fixtures.leaps.api import certified
    from modules.leaps.admission import OSLease
    gate=certified(tmp_path)
    handles=[]; original=subprocess.Popen
    def spawn(*args,**kwargs):
        child=original(*args,**kwargs); handles.append(child); return child
    monkeypatch.setattr(subprocess,'Popen',spawn)
    transport=physical_transport(.7)
    assert gate.try_acquire('market').allowed
    started=time.monotonic()
    try:
        with pytest.raises((TimeoutError,LeapsDataError)):
            get(transport,peer[0]+'/slow')
        assert handles and all(p.poll() is not None for p in handles)
        contender=OSLease(gate.root/'request.lock')
        assert not contender.acquire()  # actual physical owner remains held
    finally:
        gate.release('market')
    assert contender.acquire(); contender.release()
    assert time.monotonic()-started<2.5
    assert len(peer[1])==1


@pytest.mark.parametrize('route,reason',[('/large','response_too_large'),('/gzip','unsupported_content_encoding')])
def test_body_budget_rejects_oversize_and_compressed_bomb_before_decode(peer,route,reason):
    with pytest.raises(LeapsDataError,match=reason): get(physical_transport(),peer[0]+route)
    assert len(peer[1])==1


def test_child_external_network_is_denied_without_parent_patch():
    with pytest.raises(LeapsDataError,match='offline_network_denied'):
        get(physical_transport(),'https://example.invalid/forbidden')


@pytest.mark.parametrize('default_output',[False,True])
def test_default_child_inherits_offline_launcher_denial_without_explicit_switch(monkeypatch,default_output):
    monkeypatch.delenv('LEAPS_OFFLINE_NETWORK_DENIED',raising=False)
    if default_output:
        monkeypatch.delenv('ALPHA_QA_OUTPUT_ROOT',raising=False)
        monkeypatch.delenv('ALPHA_QA_SOURCE_ROOT',raising=False)
    assert __import__('os').environ.get('POLYGON_KEY')=='offline-fixture'
    transport=OneAttemptTransport('synthetic')
    assert transport.offline_only, 'QA child must deny before any external attempt'
    with pytest.raises(LeapsDataError,match='offline_network_denied'):
        get(transport, 'https://example.invalid/must-not-resolve')


def test_compressed_authorization_failure_retains_http_authority(peer):
    response=get(physical_transport(),peer[0]+'/denied')
    assert response.status_code==403 and response.text==''


def test_unknown_native_exit_keeps_quiescence_blocked_until_exit_is_proven():
    import subprocess
    class UncertainChild:
        polls=0
        kills=0
        waits=0
        def poll(self):
            self.polls+=1
            if self.polls==1: raise OSError('synthetic query failure')
            return None
        def kill(self):
            self.kills+=1
            if self.kills==1: raise OSError('synthetic termination failure')
        def wait(self,timeout=None):
            self.waits+=1
            if self.waits==1: raise subprocess.TimeoutExpired('private worker',timeout)
            return 0
    child=UncertainChild()
    transport=physical_transport()
    transport._stop_and_confirm(child)
    assert child.waits==2 and child.kills==2 and transport.quiescence_blocked
