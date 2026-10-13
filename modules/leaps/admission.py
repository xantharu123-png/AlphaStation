"""Fail-closed LEAPS admission primitives; no legacy limiter or live bootstrap.

Injected certificates are a trusted deployment boundary, NOT browser input.
Successful reservation is logical dispatch; an already admitted request may
finish after a priority owner starts. Only the request lease spans transport.
"""
from pathlib import Path
from dataclasses import dataclass
from decimal import Decimal
import json
import os
import threading

from .models import AdmissionDecision


def _private_permissions(path, *, directory=False):
    """Apply permissions only to freshly created LEAPS-owned paths."""
    if os.name == 'posix':
        os.chmod(path, 0o700 if directory else 0o600)
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        adv = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        convert.restype = wintypes.BOOL
        get_dacl = adv.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        get_dacl.restype = wintypes.BOOL
        set_info = adv.SetNamedSecurityInfoW
        set_info.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_info.restype = wintypes.DWORD
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        flags = 'OICI' if directory else ''
        descriptor = ctypes.c_void_p()
        if not convert(f'D:P(A;{flags};FA;;;OW)(A;{flags};FA;;;SY)', 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
            if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present:
                raise OSError('private ACL unavailable')
            result = set_info(str(path), 1, 0x80000004, None, None, dacl, None)
            if result:
                raise ctypes.WinError(result)
        finally:
            kernel.LocalFree(descriptor)
    else:
        raise OSError('private permissions unsupported')


def private_namespace(root):
    """root is ALPHA_DATA_DIR, never a public/browser-supplied path."""
    root = Path(root).resolve(strict=True)
    path = root / 'leaps'
    if path.is_symlink() or (path.exists() and path.resolve() != path):
        raise OSError('unsafe LEAPS namespace')
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise OSError('invalid LEAPS namespace')
        if not (path / '.private-ready').is_file() or (path / '.private-ready').is_symlink():
            raise OSError('unverified private namespace')
    else:
        _private_permissions(path, directory=True)
        marker = path / '.private-ready'
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        _private_permissions(marker)
    return path


class OSLease:
    """Native advisory lock, no TTL. Lock files must never be replaced/deleted.

    ``private=False`` opens an EXISTING certified external shared lock without
    changing its permissions. All LEAPS-owned locks use private=True.
    """
    def __init__(self, path, *, private=True):
        self.path, self.private = Path(path), private
        self._fd = None
        self._owner = None

    def acquire(self):
        if self._fd is not None:
            return False
        if self.path.is_symlink():
            raise OSError('unsafe lock path')
        if self.private:
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(self.path, os.O_RDWR)
            else:
                try:
                    _private_permissions(self.path)
                except BaseException:
                    os.close(fd)
                    raise
        else:
            fd = os.open(self.path, os.O_RDWR)
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            elif os.name == 'posix':
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise OSError('OS locks unsupported')
        except OSError:
            os.close(fd)
            return False
        self._fd, self._owner = fd, (os.getpid(), threading.get_ident())
        return True

    def release(self):
        if self._fd is None or self._owner != (os.getpid(), threading.get_ident()):
            raise RuntimeError('lease release by non-owner')
        fd = self._fd
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        self._fd = self._owner = None

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError('LEAPS lease busy')
        return self

    def __exit__(self, *exc):
        self.release()


def lock_identity(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino)


def _read_json(path):
    if Path(path).is_symlink():
        raise ValueError('unsafe state path')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def _fresh(proof, now):
    return (type(now) is int and type(proof.valid_from_ns) is int
            and type(proof.valid_until_ns) is int
            and 0 < proof.valid_from_ns <= now <= proof.valid_until_ns
            and isinstance(proof.evidence_ids, tuple) and bool(proof.evidence_ids)
            and all(isinstance(item, str) and item.strip() for item in proof.evidence_ids))


@dataclass(frozen=True)
class BudgetProof:
    """Certified allocation and exact shared lock inode, verified out of band.

    Certification also covers every consumer using this lock/state, the epoch
    window format, and the transport's one-attempt/no-hidden-retry contract.
    No environment default or capability boolean is such certification.
    """
    allocation: int
    valid_from_ns: int
    valid_until_ns: int
    evidence_ids: tuple[str, ...]
    lock_identity: tuple[int, int]
    state_path: str


class FileBudget:
    """Strict adapter for certified existing {window,count} shared state.

    Never provisions/resets missing or corrupt shared state. The shared lock
    is held only around validation/read/increment/fsync; no user callbacks,
    sleeps, priority waits, atomic-writer hooks or transport run under it.
    """
    def __init__(self, root, lock_path, state_path, proof):
        self.root = private_namespace(root)
        self.lock_path, self.state_path = Path(lock_path), Path(state_path)
        self.proof = proof

    def allocation(self, now):
        proof = self.proof
        if (not isinstance(proof, BudgetProof) or not _fresh(proof, now)
                or type(proof.allocation) is not int or proof.allocation <= 0
                or self.lock_path.is_symlink() or self.state_path.is_symlink()
                or str(self.state_path.resolve(strict=True)) != proof.state_path
                or lock_identity(self.lock_path) != proof.lock_identity):
            raise ValueError('unverified budget')
        return proof.allocation

    def reserve_market(self, now):
        with OSLease(self.lock_path, private=False):
            allocation = self.allocation(now)
            state = _read_json(self.state_path)
            window = now // (60 * 10**9)
            if (type(state) is not dict or set(state) != {'window', 'count'}
                    or type(state['window']) is not int or not 0 <= state['window'] <= window
                    or type(state['count']) is not int or state['count'] < 0):
                raise ValueError('invalid shared budget')
            count = state['count'] if state['window'] == window else 0
            if count >= allocation:
                return False
            # Same existing inode semantics as certified peers; uncertain write
            # is a denied dispatch, never a reset or a borrowed fail-open token.
            with self.state_path.open('w', encoding='utf-8') as handle:
                json.dump({'window': window, 'count': count + 1}, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            return True


@dataclass(frozen=True)
class PriorityProof:
    """Complete all-process stock/BI/alert roster and common reservation guard.

    All priority STARTS must use this same guard before publishing active.
    owner_keys contains the exact complete roster, not a sampled API view.
    Evidence IDs certify coverage of paused/finishing owners as active too.
    """
    valid_from_ns: int
    valid_until_ns: int
    evidence_ids: tuple[str, ...]
    owner_keys: tuple[str, ...]
    lock_identity: tuple[int, int]


class PriorityGate:
    """No inactive default. Only a certified, fresh full roster can dispatch."""
    def __init__(self, root, proof):
        self.root = private_namespace(root)
        self.proof = proof
        self.lock_path, self.state_path = self.root / 'priority.lock', self.root / 'priority.json'

    def guard(self):
        return OSLease(self.lock_path, private=False)

    def active_locked(self, now):
        p = self.proof
        if (not isinstance(p, PriorityProof) or not _fresh(p, now)
                or not isinstance(p.owner_keys, tuple) or not p.owner_keys
                or len(set(p.owner_keys)) != len(p.owner_keys)
                or not all(isinstance(k, str) and k for k in p.owner_keys)
                or lock_identity(self.lock_path) != p.lock_identity):
            raise ValueError('unverified priority')
        state = _read_json(self.state_path)
        if (type(state) is not dict or set(state) != {'owners', 'observed_ns'}
                or type(state['observed_ns']) is not int
                or not p.valid_from_ns <= state['observed_ns'] <= now
                or now - state['observed_ns'] > 5 * 10**9
                or type(state['owners']) is not dict or set(state['owners']) != set(p.owner_keys)
                or any(type(v) is not bool for v in state['owners'].values())):
            raise ValueError('unknown priority state')
        return any(state['owners'].values())

    def publish_owners(self, owners, now):
        """Publish a newly observed COMPLETE roster under the reservation guard.

        Trusted certified publisher only, never client JSON. A one-owner change
        cannot renew stale evidence for other workers. All priority start paths
        must publish their complete current roster with this guard held.
        """
        with self.guard():
            if (not isinstance(self.proof, PriorityProof) or not _fresh(self.proof, now)
                    or lock_identity(self.lock_path) != self.proof.lock_identity
                    or type(owners) is not dict or set(owners) != set(self.proof.owner_keys)
                    or any(type(value) is not bool for value in owners.values())):
                raise ValueError('incomplete owner proof')
            state = {'owners': owners, 'observed_ns': now}
            with self.state_path.open('w', encoding='utf-8') as handle:
                json.dump(state, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())


class Admission:
    def __init__(self, shared_budget, priority_active, clock_ns, *, atomic_writer=None):
        self.budget, self.priority, self.clock_ns = shared_budget, priority_active, clock_ns
        self.root = shared_budget.root if isinstance(shared_budget, FileBudget) else None
        self.writer = atomic_writer
        self._lease, self._scope = None, None
        self._mutex = threading.Lock()

    @staticmethod
    def provision(root, atomic_writer):
        """Explicit once-only local allocation initialization, not scan startup.

        Marker persists if publication fails. Missing usage after provisioning
        always denies; never reinitialize a used/lost bucket automatically.
        """
        root = private_namespace(root)
        with OSLease(root / 'provision.lock'):
            marker = root / 'admission.initialized'
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            _private_permissions(marker)
            if (root / 'usage.json').exists():
                raise FileExistsError('existing admission usage')
            atomic_writer(root / 'usage.json', {'version': 1, 'last_ns': 0, 'requests': []})

    def try_acquire(self, provider_scope):
        def deny(reason, retry=1):
            return AdmissionDecision(False, reason, Decimal(retry))
        with self._mutex:
            if provider_scope not in ('market', 'sec'):
                return deny('unknown_scope')
            if self._lease is not None:
                return deny('request_busy')
            if self.root is None or not isinstance(self.priority, PriorityGate) or self.priority.root != self.root or self.writer is None:
                return deny('unverified_admission')
            lease = OSLease(self.root / 'request.lock')
            try:
                now = self.clock_ns()
                allocation = self.budget.allocation(now)
                cap = min(20, allocation // 10)
                if not cap:
                    return deny('no_allocation')
                if not lease.acquire():
                    return deny('request_busy')
                with self.priority.guard():
                    now = self.clock_ns()
                    cap = min(20, self.budget.allocation(now) // 10)
                    if self.priority.active_locked(now):
                        return deny('priority_active')
                    if not (self.root / 'admission.initialized').is_file():
                        raise ValueError('unprovisioned admission')
                    state = _read_json(self.root / 'usage.json')
                    if (type(state) is not dict or set(state) != {'version', 'last_ns', 'requests'}
                            or type(state['version']) is not int or state['version'] != 1
                            or type(state['last_ns']) is not int or not 0 <= state['last_ns'] <= now
                            or type(state['requests']) is not list
                            or any(type(n) is not int or not 0 < n <= state['last_ns'] for n in state['requests'])
                            or state['requests'] != sorted(state['requests']) or len(state['requests']) > 20):
                        raise ValueError('invalid local usage')
                    recent = [n for n in state['requests'] if n > now - 60 * 10**9]
                    if len(recent) >= cap:
                        return deny('local_budget_exhausted', 60)
                    if provider_scope == 'market' and not self.budget.reserve_market(now):
                        return deny('shared_budget_exhausted', 60)
                    # Lost token on failure is conservative. No IO is authorized
                    # until BOTH publications succeeded while guard held.
                    self.writer(self.root / 'usage.json', {'version': 1, 'last_ns': now, 'requests': recent + [now]})
                self._lease, self._scope = lease, provider_scope
                return AdmissionDecision(True, 'allowed', None)
            except Exception:
                # An injected proof/publisher failure never becomes authority.
                return deny('admission_unavailable')
            finally:
                if lease._fd is not None and self._lease is not lease:
                    lease.release()

    def release(self, provider_scope):
        with self._mutex:
            if self._lease is None or provider_scope != self._scope:
                raise RuntimeError('request release by non-owner')
            self._lease.release()
            self._lease = self._scope = None
