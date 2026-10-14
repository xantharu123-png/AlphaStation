"""Explicit configured probe composition. Import/default/dry-run does zero IO.

Factory is trusted local code, not a browser field. It supplies verified real
admission adapters/proofs and secret headers. No allocation is synthesized or
provisioned. HTTP success proves technical access, never redistribution rights.
"""
from dataclasses import dataclass, field
from importlib import import_module
import os
import re

from .admission import Admission, FileBudget, PriorityGate
from .models import CapabilityEvidence, LeapsDataError
from .provider import MassiveLeapsProvider


@dataclass(frozen=True)
class ProviderConfiguration:
    credential: str = field(repr=False)
    admission: Admission
    clock_ns: object
    capability: CapabilityEvidence
    request: object = None  # trusted one-physical-attempt test/composition seam


class OneAttemptTransport:
    def __init__(self, credential, request=None):
        self._credential = credential
        self._request = request
        self._session = None
        if request is None:
            import requests
            from requests.adapters import HTTPAdapter
            self._session = requests.Session()
            self._session.trust_env = False
            self._session.mount('https://',HTTPAdapter(max_retries=0))
            self._request = self._session.request

    def market_headers(self):
        return {'Authorization':'Bearer '+self._credential}

    def request(self, method, url, *, headers, timeout, allow_redirects):
        # No retry or rate_limited_get here. Provider owns two attempts, each
        # independently admitted. Redirects and requests adapter retry disabled.
        if method!='GET' or timeout!=(3,10) or allow_redirects is not False:
            raise LeapsDataError('unsafe_transport_request','provider')
        try:
            return self._request(method,url,headers=headers,timeout=timeout,allow_redirects=False)
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
