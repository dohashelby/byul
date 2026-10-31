"""Read-only LIF12 declarations using the existing owned OBS01 file reader."""
from pathlib import Path
import os
import stat

from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUIRED_EVENT_PROFILE
from .http_common import Ctx, HttpCheckExecutor
from .kubernetes_observation import RequiredEventReader
from .owned_capture import MAX_BYTES
from . import retry_record_contract as contract


def load_scope(path, expected_hash, **binding):
    selected = os.lstat(path)
    if not stat.S_ISREG(selected.st_mode):
        raise ValueError('RETRY_SCOPE_FILE_UNAVAILABLE')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                 | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise ValueError('RETRY_SCOPE_FILE_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        after, current = os.fstat(fd), os.lstat(path)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)
        if (len(raw) != before.st_size or len({identity(v) for v in (selected, before, after, current)}) != 1
                or before.st_ctime_ns != after.st_ctime_ns or selected.st_ctime_ns != current.st_ctime_ns
                or sha256_hex(raw) != expected_hash):
            raise ValueError('RETRY_SCOPE_FILE_UNAVAILABLE')
        return contract.validate_scope(strict_json(raw, limit=MAX_BYTES), **binding)
    finally:
        os.close(fd)


class KubernetesRetryRecordExecutor(HttpCheckExecutor):
    name = 'request_termination'
    version = '0.2.0'
    requires_roles = False
    requires_route_scope = False
    functional_via = 'engine'

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self.runner, self.expected_target = runner, expected_target_sha256

    def validate_policy(self, check, values):
        if check.id != 'LIF-12' or getattr(check, 'collection_profile', None) != contract.PROFILE:
            raise ValueError('RETRY_PROFILE_UNSUPPORTED')
        contract.normalize_policy(values)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        try:
            if set(self.cfg) - {'target', 'retry_scope', 'prepared', 'kubectl_binary', 'max_duration_seconds'} or adapter is None:
                raise ValueError
            if 'prepared' in self.cfg and (type(self.cfg['prepared']) is not str or not self.cfg['prepared']):
                raise ValueError
            if env.engine.name != 'vllm' or env.engine.version != '0.31.0':
                raise ValueError
            start, end = contract.normalize_policy(policy_values)
            if env.ref().sha256 != policy_values['retry_environment_sha256'] or not start <= utcnow() <= end:
                raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_hash = sha256_hex(canonical_json(target))
            if (target.get('lab') is not True or target_hash != policy_values['retry_target_sha256']
                    or self.expected_target is not None and target_hash != self.expected_target):
                raise ValueError
            image = target['kubernetes']['engine']['image']
            if type(image) is not str or '@sha256:' not in image:
                raise ValueError
            scope = load_scope(self.cfg['retry_scope'], policy_values['retry_scope_sha256'],
                policy_sha256=policy_values['retry_policy_sha256'], source_sha256=policy_values['retry_record_source_sha256'],
                image_digest=image.rsplit('@', 1)[1], model_revision=env.model.revision,
                model_inventory_sha256=env.model.inventory_sha256)
            if scope['run_id'] != target['kubernetes']['run_id']:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise ValueError('RETRY_CONFIGURATION_INVALID') from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get('max_duration_seconds', 120),
            binary=self.cfg.get('kubectl_binary', 'kubectl'), runner=self.runner, profile=REQUIRED_EVENT_PROFILE)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
            timeout=10, allow_mutating=False, keys={}, secrets=[], cprobe=transport, workdir=workdir, started=utcnow())
        ctx._retry_scope, ctx._retry_gap, ctx._retry_cleanup = scope, True, []
        return self._finish(ctx)

    def collect(self, ctx):
        values = ctx.policy_values
        start, end = contract.normalize_policy(values)
        reader, snapshots, collection, diagnostics, cleanup = None, [], [], [], []

        def collect_stage(stage):
            row = {'stage': stage, 'collected': False, 'file_bytes_sha256': None,
                   'file_bytes': None, 'owner_binding_verified': False, 'collected_at': utcnow().isoformat()}
            try:
                if not start <= utcnow() <= end:
                    raise ValueError
                role = reader.binding['engine']
                raw, provenance = reader.read_file(reader, ctx.target, container=role['container'], image=role['image'],
                    path='/guard-observation/data/' + values['retry_session'] + '/required-events.json')
                packet = contract.validate_record(strict_json(raw, limit=MAX_BYTES),
                    environment_ref=ctx.env.ref().model_dump(mode='json'), target_sha256=values['retry_target_sha256'],
                    values=values, scope=ctx._retry_scope)
                if provenance.get('owner_binding_verified') is not True or not start <= utcnow() <= end:
                    raise ValueError
                row.update(collected=True, file_bytes_sha256=sha256_hex(raw), file_bytes=len(raw), owner_binding_verified=True)
                snapshots.append({'stage': stage, 'packet': packet})
            except Exception:
                diagnostics.append('RETRY_RECORD_UNAVAILABLE')
            collection.append(row)

        try:
            reader = RequiredEventReader(ctx.cprobe, ctx.target, values['retry_session'],
                binary=self.cfg.get('kubectl_binary', 'kubectl'), duration=self.cfg.get('max_duration_seconds', 120),
                runner=self.runner or capture_pipes)
            reader.snapshot()
            collect_stage('before')
            reader.snapshot()
            collect_stage('after')
            reader.snapshot()
        except Exception:
            diagnostics.append('RETRY_PREFLIGHT_OR_GENERATION_UNAVAILABLE')
        projected = contract.project(ctx._retry_scope, snapshots, values)
        transport, report = None, None
        try:
            transport = ctx.cprobe.report()
            cleanup.extend(transport.get('cleanup_errors', []))
        except Exception:
            diagnostics.append('RETRY_TRANSPORT_REPORT_UNAVAILABLE')
        if reader is not None:
            try:
                report = reader.report()
                if reader.gap:
                    diagnostics.append('RETRY_FILE_COLLECTION_GAP')
                if reader._parent_cleanup_failures:
                    cleanup.append('RETRY_RECORD_PARENT_CLEANUP_UNVERIFIED')
            except Exception:
                diagnostics.append('RETRY_READER_REPORT_UNAVAILABLE')
        ctx._retry_gap = bool(diagnostics or projected['missing'] or projected['conflicts'] or len(snapshots) != 2)
        ctx._retry_cleanup = cleanup
        summary = {'profile': contract.PROFILE, 'scope': ctx._retry_scope, 'snapshots': snapshots,
            'collection': collection, 'projection': projected, 'reader': report, 'transport': transport,
            'diagnostic_codes': sorted(set(diagnostics)), 'fixture': {'profile': contract.PROFILE},
            'producer_authenticated': False, 'automatic_retry_verified': False, 'whole_scope_complete': False}
        observations = {'retry_record_snapshots': snapshots, 'retry_event_declarations': projected['events'],
            'retry_attempt_candidates': projected['attempt_candidates'], 'retry_family_candidates': projected['family_candidates'],
            'retry_cost_candidates': projected['cost_candidates'], 'retry_missing': projected['missing'],
            'retry_conflicts': projected['conflicts'], 'retry_collection': collection, 'retry_unverified': projected['unverified'],
            'retry_records_collected': bool(snapshots), 'retry_producer_authenticated': False, 'automatic_retry_verified': False,
            'retry_nonoccurrence_verified': False, 'retry_previous_work_termination_verified': False,
            'retry_cost_enforcement_verified': False, 'retry_scope_complete': False}
        normal = {'attempted': False, 'succeeded': None,
            'detail': 'Read-only declarations; actual retry/normal control/trigger/previous work/cost enforcement are independently unverified.'}
        challenges = [{'step': step, 'attempted': False, 'observations': {'retry_scope_complete': False}}
                      for step in ctx.check.challenge_steps]
        return {key: {'value': value, 'collected': True} for key, value in observations.items()}, normal, challenges, summary

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        ceiling = DataOrigin.EXAMPLE if self.runner is not None or ctx.target['lab'] or ctx.env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin = min(draft.data_origin, ceiling, key=lambda value: ORIGIN_STRENGTH[value])
        if ctx._retry_gap or ctx._retry_cleanup:
            draft.status = ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._retry_cleanup)
        return draft
