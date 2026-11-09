"""Profile-resolved transport. The /1 transport stays frozen for existing receipts.

Only initialization is release-independent here. Existing ownership, bounded
capture and native readers are inherited; they retain their source validation.
"""
import math
import re
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
        self.profile = profile
        if profile==ROUTE_AUTHENTICATION_PROFILE and target.get('lab') is not True:
            raise KubernetesBindingError()
        self.lab = target.get('lab')
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

    def iter_callback_pages(self, session, *, seek_end=False):
        """Bound each transport call and verify the same owned Pod around it."""
        import json
        if self.profile != ENGINE_CALLBACK_PROFILE or not re.fullmatch('[0-9a-f]{32}', session):
            raise KubernetesBindingError()
        source = (Path(__file__).parent / 'assets/callback_page.py.txt').read_text()
        import uuid
        snapshot, cursor = None, self.callback_cursors.get(session)
        capture_id = uuid.uuid4().hex
        for _ in range(256):
            self._verify()
            engine = self.binding['engine']
            spec = json.dumps({'session': session, 'snapshot': snapshot, 'cursor': cursor,
                'capture_id': capture_id, 'seek_end': seek_end}, separators=(',', ':'))
            page = self._call([f"--namespace={engine['namespace']}", 'exec', engine['pod'],
                f"--container={engine['container']}", '--', 'python3', '-I', '-S', '-c', source, spec], cap=1024**2, budget=30)
            self._verify()
            if type(page) is not dict or page.get('schema') != 'guard-callback-page/2':
                raise KubernetesBindingError()
            yield page
            if page.get('done') is True:
                self.callback_cursors[session] = dict(page['cursor'])
                return
            if page.get('snapshot') == snapshot and page.get('cursor') == cursor:
                raise KubernetesBindingError()
            snapshot, cursor = page['snapshot'], page['cursor']
        raise KubernetesBindingError()

    def begin_callback_trial(self, session):
        """Fix a start cut before dispatch; old history consumes no trial budget."""
        import json
        self.callback_trial_gaps.clear()
        for page in self.iter_callback_pages(session, seek_end=True):
            self.callback_trial_gaps.update(page.get('gaps', []))
            if self.page_archive is not None:
                path = Path(self.page_archive) / ('start-' + page['capture_id'] + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x') as stream: json.dump(page, stream, sort_keys=True)

    def engine_attempt_events(self, session, *, archive=None):
        from .callback_pages import assemble
        if archive is None and self.page_archive is not None:
            archive = Path(self.page_archive) / f'capture-{self.page_capture:04d}'
            self.page_capture += 1
        value = assemble(self.iter_callback_pages(session), archive=archive)
        value['diagnostic_codes'] = sorted(set(value['diagnostic_codes']) | self.callback_trial_gaps)
        value['truncated'] = bool(value['diagnostic_codes'])
        return value

    def engine_callback_events(self, session):
        value = self.engine_attempt_events(session)
        return {**value, 'events': [row for process in value['processes'] for row in process['events']]}
