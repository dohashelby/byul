"""Local coordinator leases and durable release fences; no resource mutation.

A distributed driver can supply another lease manager through its provider.
This implementation coordinates cooperating processes on the same host/account.
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import json
import os
import tempfile
import uuid
from guard.core.execution_recipe import execution_lock
from guard.models import canonical_json, sha256_hex


class ResourceLease:
    def __init__(self, root, identity):
        self.root = Path(root)
        self.key = sha256_hex(canonical_json(identity))
        self.fence = self.root / (self.key + '.fenced.json')
        self.active = self.root / (self.key + '.active.json')
        self.token = None

    def _owner(self):
        try:
            value = json.loads(self.active.read_bytes())
            return value.get('owner') or 'interrupted-coordinator'
        except (OSError, ValueError): return 'interrupted-coordinator'

    def note_owner(self, owner):
        """Persist the exact coordinator-owned label before resource creation."""
        if self.token is None: raise ValueError('RESOURCE_LEASE_NOT_HELD')
        value = json.loads(self.active.read_bytes())
        if value.get('token') != self.token: raise ValueError('RESOURCE_LEASE_OWNER_CHANGED')
        value['owner'] = owner
        tmp = self.active.with_suffix('.' + self.token + '.tmp')
        with tmp.open('xb') as stream:
            stream.write(canonical_json(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, self.active)

    def mark_uncertain(self, owner):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.fence.exists(): return
        with self.fence.open('xb') as stream:
            stream.write(canonical_json({'schema': 'guard-resource-fence/1', 'resource': self.key, 'owner': owner,
                'reason': 'RESOURCE_RELEASE_UNVERIFIED'})); stream.flush(); os.fsync(stream.fileno())

    @contextmanager
    def acquire(self):
        with execution_lock(self.root, 'RESOURCE-' + self.key):
            if self.active.exists(): self.mark_uncertain(self._owner())
            if self.fence.exists(): raise ValueError('RESOURCE_RELEASE_UNVERIFIED_RECOVERY_REQUIRED')
            self.token = uuid.uuid4().hex
            with self.active.open('xb') as stream:
                stream.write(canonical_json({'schema': 'guard-resource-claim/1', 'resource': self.key,
                    'token': self.token, 'pid': os.getpid(), 'owner': None})); stream.flush(); os.fsync(stream.fileno())
            try:
                yield self
            except BaseException:
                self.mark_uncertain(self._owner())
                raise
            else:
                if not self.fence.exists(): self.active.unlink()
            finally: self.token = None

    def recover(self, verify_owned_release):
        """Caller supplies its reviewed, read-only ownership/release verifier."""
        with execution_lock(self.root, 'RESOURCE-' + self.key):
            if self.active.exists() and not self.fence.exists(): self.mark_uncertain(self._owner())
            if not self.fence.exists(): return
            value = json.loads(self.fence.read_bytes())
            if verify_owned_release(value['owner']) is not True:
                raise ValueError('RESOURCE_RELEASE_STILL_UNVERIFIED')
            history = self.root / 'recovered'; history.mkdir(exist_ok=True)
            identifier = self.key + '-' + uuid.uuid4().hex
            # Keep the fence until the orphaned claim has been archived.
            if self.active.exists(): self.active.rename(history / (identifier + '.claim.json'))
            self.fence.rename(history / (identifier + '.json'))


@contextmanager
def provider_leases(environments, prepared, *, root=None):
    from guard.adapters.engine_provider import get_provider
    root = root or Path(tempfile.gettempdir()) / ('guard-resource-leases-' + str(os.getuid() if hasattr(os, 'getuid') else os.getlogin()))
    leases = {}
    for env, options in zip(environments, prepared):
        if not options: continue
        provider = get_provider(env.engine.name)
        for identity in provider.resource_lease_keys(options):
            lease = provider.make_resource_lease(root, identity); leases[lease.key] = lease
    with ExitStack() as stack:
        for key in sorted(leases): stack.enter_context(leases[key].acquire())
        yield tuple(leases.values())
