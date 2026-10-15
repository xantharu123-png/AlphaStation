"""Explicit configured probe composition. Import/default/dry-run does zero IO.

Factory is trusted local code, not a browser field. It supplies verified real
admission adapters/proofs and secret headers. No allocation is synthesized or
provisioned. HTTP success proves technical access, never redistribution rights.
"""
from dataclasses import dataclass, field
from importlib import import_module
import os
import re
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from .admission import Admission, FileBudget, PriorityGate
from .models import CapabilityEvidence, LeapsDataError
from .provider import MassiveLeapsProvider
from ._http_worker import _safe_retry_after


@dataclass(frozen=True)
class ProviderConfiguration:
    credential: str = field(repr=False)
    admission: Admission
    clock_ns: object
    capability: CapabilityEvidence
    request: object = None  # trusted one-physical-attempt test/composition seam


class OneAttemptTransport:
    def __init__(self, credential, request=None, *, total_timeout_s=20, offline_only=False):
        self._credential = credential
        self._request = request
        if type(total_timeout_s) not in (int,float) or not 0<total_timeout_s<=20:
            raise LeapsDataError('invalid_transport_budget','provider')
        self.total_timeout_s=total_timeout_s
        self.offline_only=(offline_only is True or os.environ.get('LEAPS_OFFLINE_NETWORK_DENIED')=='1'
                           or bool(os.environ.get('ALPHA_QA_OUTPUT_ROOT')) or bool(os.environ.get('ALPHA_QA_SOURCE_ROOT'))
                           or os.environ.get('POLYGON_KEY')=='offline-fixture')
        self.quiescence_blocked=False

    def market_headers(self):
        return {'Authorization':'Bearer '+self._credential}

    def _stop_and_confirm(self, child):
        # No universal OS kill guarantee is claimed. If termination fails,
        # remain in this call WITH native ownership until exit is observed.
        while True:
            try:
                if child.poll() is not None:
                    return
            except OSError:
                self.quiescence_blocked=True
            try:
                child.kill()
            except OSError:
                self.quiescence_blocked=True
            try:
                child.wait(timeout=1)
                return
            except (subprocess.TimeoutExpired,OSError):
                self.quiescence_blocked=True

    def _physical(self,url,headers):
        deadline=time.monotonic()+self.total_timeout_s
        payload=json.dumps({'url':url,'headers':headers,'offline_only':self.offline_only}).encode('utf-8')
        if len(payload)>65536:
            raise LeapsDataError('transport_input_too_large','provider')
        # No inherited app configuration, PYTHONPATH, startup hooks or secrets.
        environment={key:value for key,value in os.environ.items()
                     if key.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH')}
        child=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).with_name('_http_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env=environment,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            try:
                output,_=child.communicate(payload,timeout=max(.001,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                self._stop_and_confirm(child)
                raise TimeoutError('LEAPS transport total deadline') from None
            # communicate observes process exit, not merely EOF from a thread.
            if child.returncode!=0 or len(output)>8*1024*1024+4096:
                raise LeapsDataError('transport_worker_failed','provider')
            line,separator,body=output.partition(b'\n')
            if not separator or len(line)>4096:
                raise LeapsDataError('transport_worker_failed','provider')
            meta=json.loads(line)
            reason=meta.get('error')
            if reason=='connection_failed':
                raise ConnectionError('LEAPS transport unavailable')
            if reason:
                allowed={'response_too_large','unsupported_content_encoding','offline_network_denied','transport_error'}
                raise LeapsDataError(reason if reason in allowed else 'transport_error','provider')
            if type(meta.get('status')) is not int or len(body)>8*1024*1024:
                raise LeapsDataError('transport_worker_failed','provider')
            retry_after=_safe_retry_after(meta.get('retry_after')) if meta['status']==429 else None
            return SimpleNamespace(status_code=meta['status'],text=body.decode('utf-8'),
                                   headers={'Retry-After':retry_after} if retry_after is not None else {})
        finally:
            self._stop_and_confirm(child)
            for stream in (child.stdin,child.stdout):
                stream.close()

    def request(self, method, url, *, headers, timeout, allow_redirects):
        # No retry or rate_limited_get here. Provider owns two attempts, each
        # independently admitted. Redirects and requests adapter retry disabled.
        if method!='GET' or timeout!=(3,10) or allow_redirects is not False:
            raise LeapsDataError('unsafe_transport_request','provider')
        try:
            if self._request is None:
                return self._physical(url,headers)
            return self._request(method,url,headers=headers,timeout=timeout,allow_redirects=False)
        except LeapsDataError:
            raise
        except (ConnectionError,TimeoutError):
            raise
        except Exception as error:
            import requests
            if isinstance(error,(requests.ConnectionError,requests.Timeout)):
                raise ConnectionError('transport unavailable') from None
            raise LeapsDataError('transport_error','provider') from None


def load_configured_provider(*, factory=None):
    """Called ONLY after explicit --execute; never discovers secret files.

    Optional LEAPS_PROVIDER_FACTORY=package.module:function chooses a trusted
    deployment factory. No default factory/credentials/source rights exist.
    The factory owns proof acquisition; this module never fabricates it.
    """
    try:
        if factory is None:
            spec = os.environ.get('LEAPS_PROVIDER_FACTORY','')
            if not spec:
                raise LeapsDataError('configured_factory_required','provider')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*',spec):
                raise LeapsDataError('invalid_configured_factory','provider')
            module, name = spec.split(':')
            factory = getattr(import_module(module),name)
        config = factory()
        if (not isinstance(config,ProviderConfiguration) or not isinstance(config.credential,str)
                or not config.credential.strip() or len(config.credential)>4096
                or any(ord(c)<33 or ord(c)>126 for c in config.credential)
                or not callable(config.clock_ns) or not isinstance(config.capability,CapabilityEvidence)):
            raise LeapsDataError('invalid_provider_configuration','provider')
        admission = config.admission
        if (not isinstance(admission,Admission) or not isinstance(admission.budget,FileBudget)
                or not isinstance(admission.priority,PriorityGate) or admission.writer is None
                or admission.root!=admission.priority.root):
            raise LeapsDataError('verified_budget_priority_required','provider')
        try:
            now = config.clock_ns()
            if type(now) is not int or admission.budget.allocation(now)//10<1:
                raise ValueError()
            with admission.priority.guard():
                if admission.priority.active_locked(now):
                    raise ValueError()
            if not (admission.root/'admission.initialized').is_file() or not (admission.root/'usage.json').is_file():
                raise ValueError()
        except Exception:
            raise LeapsDataError('verified_budget_priority_required','provider') from None
        return MassiveLeapsProvider(OneAttemptTransport(config.credential,config.request),config.clock_ns,admission,config.capability)
    except LeapsDataError:
        raise
    except Exception:
        raise LeapsDataError('configured_factory_failed','provider') from None
