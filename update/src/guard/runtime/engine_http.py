"""Profile-resolved transport. The /1 transport stays frozen for existing receipts.

Only initialization is release-independent here. Existing ownership, bounded
capture and native readers are inherited; they retain their source validation.
"""
import json
import math
import os
import re
import stat
import threading
import time
from copy import deepcopy
from pathlib import Path
from guard.models import canonical_json, sha256_hex
from . import kubernetes_http as legacy_transport
from .kubernetes_http import (
    KubernetesHTTP as LegacyKubernetesHTTP, KubernetesBindingError,
    PROFILE, REQUEST_BINDING_PROFILE, ROUTE_AUTHENTICATION_PROFILE, OBSERVATION_STATE_PROFILE,
    ENGINE_CALLBACK_PROFILE, ONLINE_INPUT_PROFILE, TERMINATION_PROFILE,
    REQUEST_ATTEMPTS_PROFILE, REQUIRED_EVENT_PROFILE, PROBE_SOURCE_SHA256,
    BINDING_PROBE_SHA256, _BINDING_PROFILES, _validate,
)

# Both releases enforce the same owned Pod transport contract. Historical
# readers remain valid; adopting the new constructor is not a type identity.
OwnedKubernetesTransport = LegacyKubernetesHTTP

NORMAL_CALLBACK_SOURCE_KEY = 'normal_callback_sources'
NORMAL_CALLBACK_SOURCE_SCHEMA = 'guard-mounted-callback-sources/1'
NORMAL_CALLBACK_ATTESTATION_SCHEMA = 'guard-normal-tp2-callback-attestation/1'
NORMAL_CALLBACK_SOURCE_GAP = 'NORMAL_TP2_CALLBACK_SOURCE_UNVERIFIED'
NORMAL_CALLBACK_CUT_SCHEMA = 'guard-normal-tp2-callback-cut/1'
NORMAL_CALLBACK_CUT_GAP = 'NORMAL_TP2_CALLBACK_CUT_UNVERIFIED'
_NO_CALLBACK_CUT = object()
_CALLBACK_FILE = re.compile(r'callbacks-([1-9][0-9]*)(?:-[0-9]{4})?\.jsonl')


def _callback_page_source(*, attest=False, trial=False):
    assets = Path(__file__).parent / 'assets'
    page = (assets / 'callback_page.py.txt').read_text()
    if not attest:
        return page
    reader = (assets / 'normal_callback_sources.py.txt').read_text()
    if trial:
        page += '\n' + (assets / 'normal_callback_cut.py.txt').read_text()
    return reader + '\nattested_page(' + repr(page) + ', sys.argv[1])\n'


def _verify_normal_source_observation(value, normal):
    observed = value.get(NORMAL_CALLBACK_SOURCE_KEY)
    if (type(observed) is not dict or observed.keys() != {'schema', 'files_sha256', 'filesystem_sha256',
                                                           'file_count', 'uid', 'gid'}
            or observed['schema'] != NORMAL_CALLBACK_SOURCE_SCHEMA
            or observed['files_sha256'] != normal['files_sha256']
            or type(observed['filesystem_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', observed['filesystem_sha256'])
            or type(observed['file_count']) is not int or observed['file_count'] != len(normal['files'])
            or type(observed['uid']) is not int or observed['uid'] != 2000
            or type(observed['gid']) is not int or observed['gid'] != 0
            or normal['observation'] is not None and observed != normal['observation']):
        raise KubernetesBindingError()
    normal['observation'] = deepcopy(observed)
    normal['observed_pages'] += 1


def _verify_trial_page(page, requested_cursor, seek_end, held):
    try:
        scope = page['normal_callback_writers']
        writers = scope['writers']
        snapshot, before, cursor = (page[key] for key in ('snapshot', 'start_cursor', 'cursor'))
        if (type(scope) is not dict or scope.keys() != {'session_directory_sha256', 'writers'}
                or not re.fullmatch('[0-9a-f]{64}', scope['session_directory_sha256'])
                or type(writers) is not dict or not 0 < len(writers) <= 256
                or any(not re.fullmatch('[1-9][0-9]*', pid) or type(ticks) is not int or ticks <= 0
                       for pid, ticks in writers.items())
                or type(snapshot) is not dict or len(snapshot) > 256
                or type(before) is not dict or before != (requested_cursor or {})
                or any(type(offset) is not int for offset in before.values())
                or type(cursor) is not dict or type(page['rows']) is not list
                or type(page['gaps']) is not list or type(page['done']) is not bool):
            raise ValueError
        for name, file in snapshot.items():
            match = _CALLBACK_FILE.fullmatch(name)
            if (match is None or type(file) is not dict
                    or file.keys() != {'device', 'inode', 'pid', 'size', 'start_ticks'}
                    or any(type(value) is not int or value < 0 for value in file.values())
                    or file['inode'] == 0 or file['pid'] != int(match[1])
                    or file['start_ticks'] != writers.get(match[1])):
                raise ValueError
        if any(name not in snapshot or type(offset) is not int or not 0 <= offset <= snapshot[name]['size']
               for name, offset in cursor.items()):
            raise ValueError
        if seek_end:
            if (not page['done'] or page['rows'] or page['gaps']
                    or cursor != {name: file['size'] for name, file in snapshot.items()}):
                raise ValueError
        else:
            if (scope != held['writer_scope'] or page[NORMAL_CALLBACK_SOURCE_KEY] != held['source_observation']):
                raise ValueError
            for name, first in held['snapshot'].items():
                current = snapshot[name]
                if (current['size'] < first['size']
                        or any(current[key] != first[key] for key in ('device', 'inode', 'pid', 'start_ticks'))):
                    raise ValueError
    except (KeyError, TypeError, ValueError):
        raise KubernetesBindingError() from None


def _cut_archive_matches(held):
    path, size = held['archive_path'], held['archive_bytes']
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return False
            raw = stream.read(size + 1)
            after = os.fstat(stream.fileno())
        def identity(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

        return (len(raw) == size and sha256_hex(raw) == held['receipt']['start_page']['sha256']
                and identity(before) == identity(after) == identity(path.lstat()))
    except OSError:
        return False


class KubernetesHTTP(LegacyKubernetesHTTP):
    def __init__(self, target, env, *, duration, binary="kubectl", runner=None, profile=PROFILE, clock=None):
        if profile not in {PROFILE, *_BINDING_PROFILES}:
            raise KubernetesBindingError()
        from guard.adapters.engine_profiles import require_transport
        try:
            self.support_profile = require_transport(env.engine.name, env.engine.version, profile)
        except ValueError:
            raise KubernetesBindingError() from None
        self.page_archive = None
        self.page_capture = 0
        self.callback_cursors = {}
        self.callback_trial_gaps = set()
        self._normal_callback_cut = None
        self._retain_gateway_response = False
        self.profile = profile
        if profile==ROUTE_AUTHENTICATION_PROFILE and target.get('lab') is not True:
            raise KubernetesBindingError()
        self.lab = target.get('lab')
        self._normal_callback_plan = deepcopy(target.get('normal_tp2_callbacks'))
        self.engine_name = env.engine.name
        self.engine_version = env.engine.version
        if profile==OBSERVATION_STATE_PROFILE and target.get('lab') is not True:raise KubernetesBindingError()
        if profile in _BINDING_PROFILES:
            try:
                mapping=target["model_mapping"]
                if type(mapping) is dict and mapping.get("schema") in {"guard.model-request-mapping/1", "guard.model-request-mapping/2"}:
                    from guard.collectors.model_mapping import validate_model_mapping
                    validate_model_mapping(env,mapping)
                    self.api_model=mapping["served_model_name"]
                else:
                    if (type(mapping) is not dict or set(mapping)!={"repository_id","revision","api_model"}
                            or mapping["repository_id"]!=env.model.id or mapping["revision"]!=env.model.revision
                            or type(mapping["api_model"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}",mapping["api_model"])):
                        raise ValueError
                    self.api_model=mapping["api_model"]
                if (target.get("schema")!="guard-kubernetes-request-binding-target/1" or not env.hash_ok()
                        or target.get("environment_ref")!=env.ref().model_dump(mode="json")):
                    raise ValueError
                self.model_mapping=deepcopy(mapping)
            except (KeyError,TypeError,ValueError,AttributeError):
                raise KubernetesBindingError() from None
        self.binding, self.endpoint = _validate(target, env)
        self.target_sha256=sha256_hex(canonical_json(target))
        self.environment_ref=(deepcopy(target["environment_ref"]) if profile in _BINDING_PROFILES else None)
        # LIF-13 lab은 실행 안에서 소비자 엔진을 두 번 기동한다. 그 실행기 상한(900초)은 소유 lab 콜백 전송에서만 받는다.
        limit = 900 if profile == ENGINE_CALLBACK_PROFILE and target.get("lab") is True else 300
        if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= limit
                or type(binary) is not str or not binary.strip()):
            raise KubernetesBindingError()
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + duration
        self.binary = binary
        # Retain the published injection seam for existing transport callers.
        self.runner = runner or legacy_transport.capture_pipes
        self.injected = runner is not None
        self.process_observation_origin = ('EXAMPLE' if self.injected or target.get('synthetic') is True
            or env.data_origin.value == 'EXAMPLE' else 'MEASURED')
        if profile in {ONLINE_INPUT_PROFILE, TERMINATION_PROFILE, REQUEST_ATTEMPTS_PROFILE, REQUIRED_EVENT_PROFILE} and self.process_observation_origin != 'EXAMPLE':
            self.process_observation_origin = env.data_origin.value
        # This profile admits only its pinned HTTP Pod endpoint below. Preserve
        # its published source/fixture bytes when the general probe evolves.
        probe_source = (Path(__file__).parent / "assets/http_probe_v1.py.txt").read_text(encoding="utf-8")
        if sha256_hex(probe_source.encode("utf-8")) != PROBE_SOURCE_SHA256:
            raise KubernetesBindingError()
        # The parent owns kubectl, not the remote exec process. A daemon timer
        # bounds this probe itself, including stdin and a slowly dripping body.
        # It terminates only this Python process, never a Pod or another PID.
        self.source = ("import math,os,sys,threading\n"
                       "limit=float(sys.argv[1])\n"
                       "if not math.isfinite(limit) or not 0 < limit <= 30: sys.exit(124)\n"
                       "watchdog=threading.Timer(limit,lambda:os._exit(124))\n"
                       "watchdog.daemon=True\nwatchdog.start()\n"
                       + "exec(compile(" + repr(probe_source) + ",'guard-http-probe','exec'),{'__name__':'__main__'})\n")
        self.source_sha256 = sha256_hex(self.source.encode("utf-8"))
        if profile in _BINDING_PROFILES:
            probe_source=(Path(__file__).parents[1]/"executors/stream_probe.py").read_text(encoding="utf-8")
            if sha256_hex(probe_source.encode())!=BINDING_PROBE_SHA256:
                raise KubernetesBindingError()
            self.source=("import math,os,sys,threading\nlimit=float(sys.argv[1])\n"
                "if not math.isfinite(limit) or not 0 < limit <= 120: sys.exit(124)\n"
                "watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n"
                + "exec(compile("+repr(probe_source)+",'guard-stream-probe','exec'),{'__name__':'__main__'})\n")
            self.source_sha256=sha256_hex(self.source.encode())
        self.model_snapshots = []  # Transient allowlisted pair for the existing A-2 consumer.
        self.snapshot_reads = []
        self.rows = []
        self.failed = False
        self.cleanup_errors = []
        self.lock = threading.Lock()
        self._validation_error_token = None  # transient; never part of report/evidence
        self._validation_error_cases = set()
        self._model_authorization_token = None
        self._model_authorization_cases = set()
        self._model_normal_credential = None
        self._required_event_token = None
        self._required_event_cases = set()
        self._required_event_credential = None
        self._online_dispatch = threading.Lock()
        self._online_token = None
        self._online_cases = set()
        self._online_pending = None
        self._termination_token = None
        self._termination_cases = set()
        self._attempt_token = None
        self._attempt_cases = []
        self._state_token = None
        self._state_cases = set()
        self._callback_lab = None
        self._callback_switches = 0
        self._input_contract = None
        self._gateway_netloc = None

    def _normal_callback_scope(self, session, assets, check_pod, *, trial=False):
        from guard.collectors import engine_callbacks

        plan = getattr(self, '_normal_callback_plan', None)
        if (self.profile != ENGINE_CALLBACK_PROFILE or self.lab is not False or type(plan) is not dict
                or not callable(check_pod) or type(assets) is not dict
                or assets.keys() != {'target_file_sha256', 'plan_sha256', 'files'}
                or any(type(assets[key]) is not str or not re.fullmatch('[0-9a-f]{64}', assets[key])
                       for key in ('target_file_sha256', 'plan_sha256'))
                or assets['plan_sha256'] != sha256_hex(canonical_json(plan))
                or type(session) is not str or not re.fullmatch('[0-9a-f]{32}', session)
                or sha256_hex(session.encode()) != plan.get('session_sha256')):
            raise KubernetesBindingError()
        files = deepcopy(assets['files'])
        shipped = {name: {'bytes': len(raw), 'sha256': sha256_hex(raw)}
                   for name, raw in engine_callbacks._sources().items()}
        if (type(files) is not dict or files.keys() != {*shipped, 'manifest.json', 'settings.json'}
                or any(files[name] != pin for name, pin in shipped.items())
                or sha256_hex(canonical_json({name: pin['sha256'] for name, pin in shipped.items()}))
                != plan.get('assets_sha256')):
            raise KubernetesBindingError()
        for name, bound in (('manifest', 262144), ('settings', 4096)):
            pin = files[name + '.json']
            if (type(pin) is not dict or pin.keys() != {'bytes', 'sha256'}
                    or type(pin['bytes']) is not int or not 0 < pin['bytes'] <= bound
                    or type(pin['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', pin['sha256'])
                    or pin['sha256'] != plan.get(name + '_sha256')):
                raise KubernetesBindingError()
        if trial and plan.get('mode') not in {'scoped-runner-consumption', 'worker-shards-and-scoped-runner-consumption'}:
            raise KubernetesBindingError()
        return {'source': _callback_page_source(attest=True, trial=trial), 'files': files,
                'files_sha256': sha256_hex(canonical_json(files)), 'check_pod': check_pod,
                'observation': None, 'observed_pages': 0, 'trial': trial}

    def iter_callback_pages(self, session, *, seek_end=False, _normal=None):
        """Bound each transport call and verify the same owned Pod around it."""
        if (self.profile != ENGINE_CALLBACK_PROFILE or not re.fullmatch('[0-9a-f]{32}', session)
                or (getattr(self, '_normal_callback_plan', None) is not None) != (_normal is not None)):
            raise KubernetesBindingError()
        source = _normal['source'] if _normal is not None else _callback_page_source()
        import uuid
        snapshot, cursor = None, self.callback_cursors.get(session)
        trial = _normal is not None and _normal['trial']
        held = _normal.get('held_cut') if trial else None
        terminal_epochs = _normal.get('worker_terminal_epochs') if _normal is not None else None
        if trial:
            cursor = held['cursor'] if held is not None else None
        elif terminal_epochs is not None:
            cursor = None
        capture_id = uuid.uuid4().hex
        for _ in range(256):
            pods = self._verify()
            engine = self.binding['engine']
            spec = {'session': session, 'snapshot': snapshot, 'cursor': cursor,
                    'capture_id': capture_id, 'seek_end': seek_end}
            if _normal is not None:
                _normal['check_pod'](pods['engine'])
                spec['normal_callback_assets'] = _normal['files']
                if terminal_epochs is not None:
                    spec['normal_worker_terminal_epochs'] = terminal_epochs
            raw_spec = json.dumps(spec, separators=(',', ':'))
            page = self._call([f"--namespace={engine['namespace']}", 'exec', engine['pod'],
                f"--container={engine['container']}", '--', 'python3', '-I', '-S', '-c', source, raw_spec], cap=1024**2, budget=30)
            pods = self._verify()
            if _normal is not None:
                _normal['check_pod'](pods['engine'])
            if (type(page) is not dict or page.get('schema') != 'guard-callback-page/2'
                    or page.get('capture_id') != capture_id):
                raise KubernetesBindingError()
            if trial:
                _verify_trial_page(page, cursor, seek_end, held)
            if _normal is not None:
                _verify_normal_source_observation(page, _normal)
            yield page
            if page.get('done') is True:
                if not trial and terminal_epochs is None:
                    self.callback_cursors[session] = dict(page['cursor'])
                return
            if page.get('snapshot') == snapshot and page.get('cursor') == cursor:
                raise KubernetesBindingError()
            snapshot, cursor = page['snapshot'], page['cursor']
        raise KubernetesBindingError()

    def _normal_trial_identity(self, session, normal, assets):
        return {'data_origin': self.process_observation_origin, 'environment_ref': deepcopy(self.environment_ref),
                'target_sha256': self.target_sha256, 'target_file_sha256': assets['target_file_sha256'],
                'plan_sha256': assets['plan_sha256'], 'source_sha256': sha256_hex(normal['source'].encode()),
                'owned_generation_sha256': sha256_hex(canonical_json(self.binding)),
                'session_sha256': sha256_hex(session.encode())}

    def begin_callback_trial(self, session, *, normal_callback_assets=None, normal_pod_check=None, archive=None):
        """Fix a start cut before dispatch; old history consumes no trial budget."""
        if (getattr(self, '_normal_callback_plan', None) is not None or normal_callback_assets is not None
                or normal_pod_check is not None or archive is not None):
            with self.lock:
                self._normal_callback_cut = None
            normal = self._normal_callback_scope(session, normal_callback_assets, normal_pod_check, trial=True)
            archive = self.page_archive if archive is None else archive
            if archive is None:
                raise KubernetesBindingError()
            identity = self._normal_trial_identity(session, normal, normal_callback_assets)
            page, = self.iter_callback_pages(session, seek_end=True, _normal=normal)
            raw = canonical_json(page)
            path = Path(archive) / ('start-' + page['capture_id'] + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
            receipt = {'schema': NORMAL_CALLBACK_CUT_SCHEMA, **identity, 'capture_id': page['capture_id'],
                       'start_page': {'path': path.name, 'sha256': sha256_hex(raw)},
                       'cursor_sha256': sha256_hex(canonical_json(page['cursor'])),
                       'writer_scope_sha256': sha256_hex(canonical_json(page['normal_callback_writers'])),
                       'source_observation_sha256': sha256_hex(canonical_json(normal['observation']))}
            with self.lock:
                self._normal_callback_cut = {
                    'receipt': receipt, 'cursor': page['cursor'], 'snapshot': page['snapshot'],
                    'writer_scope': page['normal_callback_writers'], 'source_observation': normal['observation'],
                    'check_pod': normal_pod_check, 'archive_path': path, 'archive_bytes': len(raw),
                }
            return deepcopy(receipt)
        import json
        self.callback_trial_gaps.clear()
        for page in self.iter_callback_pages(session, seek_end=True):
            self.callback_trial_gaps.update(page.get('gaps', []))
            if self.page_archive is not None:
                path = Path(self.page_archive) / ('start-' + page['capture_id'] + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x') as stream: json.dump(page, stream, sort_keys=True)

    def _checked_normal_callback_cut(self, session, cut, held, assets, check_pod):
        if (type(cut) is not dict or held is None or cut != held['receipt']
                or check_pod is not held['check_pod'] or not _cut_archive_matches(held)):
            raise KubernetesBindingError()
        normal = self._normal_callback_scope(session, assets, check_pod, trial=True)
        identity = self._normal_trial_identity(session, normal, assets)
        if any(cut[key] != value for key, value in identity.items()):
            raise KubernetesBindingError()
        return normal

    def verify_normal_callback_cut(self, session, cut, *, normal_callback_assets, normal_pod_check):
        """Recheck the held cut and owned mount declaration just before the ordinary operation."""
        with self.lock:
            held = self._normal_callback_cut
        normal = self._checked_normal_callback_cut(session, cut, held, normal_callback_assets, normal_pod_check)
        normal['check_pod'](self._verify()['engine'])
        with self.lock:
            if held is not self._normal_callback_cut or not _cut_archive_matches(held):
                raise KubernetesBindingError()

    def engine_attempt_events(self, session, *, archive=None, normal_callback_assets=None, normal_pod_check=None,
                              normal_callback_cut=_NO_CALLBACK_CUT, normal_worker_terminal_epochs=None):
        from .callback_pages import assemble
        if archive is None and self.page_archive is not None:
            archive = Path(self.page_archive) / f'capture-{self.page_capture:04d}'
            self.page_capture += 1
        held = normal = None
        if normal_callback_cut is not _NO_CALLBACK_CUT or getattr(self, '_normal_callback_cut', None) is not None:
            with self.lock:
                held = self._normal_callback_cut
                self._normal_callback_cut = None
            if archive is None:
                raise KubernetesBindingError()
            normal = self._checked_normal_callback_cut(
                session, normal_callback_cut, held, normal_callback_assets, normal_pod_check)
            normal['held_cut'] = held
        elif (getattr(self, '_normal_callback_plan', None) is not None or normal_callback_assets is not None
              or normal_pod_check is not None):
            normal = self._normal_callback_scope(session, normal_callback_assets, normal_pod_check)
        if normal_worker_terminal_epochs is not None:
            epochs = normal_worker_terminal_epochs
            if (normal is None or held is not None or archive is None
                    or self._normal_callback_plan.get('mode') != 'worker-shards'
                    or type(epochs) is not dict or not 1 <= len(epochs) <= 8
                    or any(type(pid) is not int or not 0 < pid < 10**10 or type(ticks) is not int or ticks <= 0
                           for pid, ticks in epochs.items())):
                raise KubernetesBindingError()
            normal['worker_terminal_epochs'] = {str(pid): ticks for pid, ticks in epochs.items()}
        pages = self.iter_callback_pages(session) if normal is None else self.iter_callback_pages(session, _normal=normal)
        value = assemble(pages, archive=archive)
        diagnostics = set(value['diagnostic_codes']) | self.callback_trial_gaps
        if normal is not None and 'CALLBACK_PAGE_READ_FAILED' in diagnostics:
            diagnostics.add(NORMAL_CALLBACK_SOURCE_GAP)
        if held is not None:
            value['normal_callback_cut'] = deepcopy(held['receipt'])
            verified = not diagnostics and _cut_archive_matches(held)
            value['normal_callback_cut_verified'] = verified
            if not verified:
                diagnostics.add(NORMAL_CALLBACK_CUT_GAP)
        value['diagnostic_codes'] = sorted(diagnostics)
        value['truncated'] = bool(diagnostics)
        if normal is not None:
            value['mounted_source_attestation'] = {
                'schema': NORMAL_CALLBACK_ATTESTATION_SCHEMA, 'data_origin': self.process_observation_origin,
                'environment_ref': deepcopy(self.environment_ref), 'target_sha256': self.target_sha256,
                'target_file_sha256': normal_callback_assets['target_file_sha256'],
                'plan_sha256': normal_callback_assets['plan_sha256'],
                'source_sha256': sha256_hex(normal['source'].encode()),
                'owned_generation_sha256': sha256_hex(canonical_json(self.binding)),
                'observed_pages': normal['observed_pages'], 'observation': normal['observation'],
                'collection_truncated': value['truncated'],
            }
        return value

    def engine_callback_events(self, session):
        value = self.engine_attempt_events(session)
        return {**value, 'events': [row for process in value['processes'] for row in process['events']]}
