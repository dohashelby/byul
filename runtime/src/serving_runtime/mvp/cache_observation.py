"""Privilege-separated continuous cache metadata and actual Pod mount observation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
from uuid import uuid4

from ..enforcement.cache import check_cache
from ..enforcement.intent import parse_intent
from .asset_binding import check_execution_binding
from .collectors import CollectionFailure
from .native_receipts import canonical, read_protected, timestamp
from .policy import _object_without_duplicates, _reject_json_constant
from .target import _parse as parse_target, pods_command, instance_id, summarize_pods

SCHEMA = 'runtime-cache-observation/1'
SETTINGS = 'runtime-cache-observer-settings/1'
SOURCE_SCHEMA = 'runtime-cache-observer-source/1'
LIMIT = 1024 * 1024
CACHE_ENV = frozenset({'VLLM_CACHE_ROOT', 'TORCHINDUCTOR_CACHE_DIR', 'TRITON_CACHE_DIR', 'FLASHINFER_WORKSPACE_BASE', 'XDG_CACHE_HOME', 'CUDA_CACHE_PATH'})


def decode(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_object_without_duplicates,
                      parse_constant=_reject_json_constant)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def source_bundle_modules():
    functions = (check_cache, check_execution_binding, parse_intent, parse_target,
                 read_protected, _object_without_duplicates)
    paths = {__name__: Path(__file__)}
    for function in functions:
        paths[function.__module__] = Path(sys.modules[function.__module__].__file__)
    paths['serving_runtime.tools.cache_observation_feed'] = Path(__file__).resolve().parents[3] / 'tools' / 'cache_observation_feed.py'
    result = {}
    for name, path in paths.items():
        with path.open('rb') as source:
            raw = source.read(2 * LIMIT + 1)
        if len(raw) > 2 * LIMIT:
            raise ValueError('CACHE_PRODUCER_SOURCE_BOUND')
        result[name] = hashlib.sha256(raw).hexdigest()
    return result


def source_bundle_sha256():
    return digest({'schema': SOURCE_SCHEMA, 'modules': source_bundle_modules()})


def _pin(path, expected, reader_gid):
    raw = read_protected(path, reader_gid, limit=LIMIT)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('CACHE_INPUT_HASH_MISMATCH')
    return decode(raw)


def _binding(settings):
    return {key: settings[key] for key in ('target_sha256', 'intent_sha256', 'policy_sha256', 'bundle_id',
                                          'model_host_path', 'expected_command', 'expected_args', 'expected_cache_environment')}


def _bounded_query(command, timeout=5):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'})
    payload = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('CACHE_POD_QUERY_TIMEOUT')
                part = os.read(process.stdout.fileno(), min(65536, LIMIT + 1 - len(payload)))
                if not part:
                    break
                payload.extend(part)
                if len(payload) > LIMIT:
                    raise ValueError('CACHE_POD_QUERY_BOUND')
            if process.wait(timeout=max(.1, deadline-time.monotonic())) != 0:
                raise ValueError('CACHE_POD_QUERY_FAILED')
        return decode(bytes(payload))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def atomic_snapshot(path, document, reader_gid):
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = '.' + path.name + '.' + uuid4().hex + '.tmp'
    descriptor = -1
    try:
        parent = os.fstat(directory)
        if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022:
            raise PermissionError('CACHE_SNAPSHOT_ROOT_DIRECTORY_REQUIRED')
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        os.fchown(descriptor, 0, reader_gid)
        os.fchmod(descriptor, 0o640)
        encoded = canonical(document) + b'\n'
        if len(encoded) > LIMIT:
            raise ValueError('CACHE_SNAPSHOT_BOUND')
        with os.fdopen(descriptor, 'wb', closefd=False) as output:
            output.write(encoded); output.flush(); os.fsync(descriptor)
        os.replace(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        finally:
            os.close(directory)


class CacheObservationProducer:
    def __init__(self, settings_path, *, settings_sha256, reader_gid, kubeconfig, kubectl='/usr/local/bin/kubectl', query=_bounded_query):
        if os.geteuid() != 0 or type(reader_gid) is not int or reader_gid < 0:
            raise PermissionError('CACHE_ROOT_PRODUCER_REQUIRED')
        self.settings = _pin(settings_path, settings_sha256, reader_gid)
        required = {'schema', 'target_path', 'target_sha256', 'intent_path', 'intent_sha256', 'policy_sha256',
                    'bundle_id', 'model_host_path', 'expected_command', 'expected_args', 'expected_cache_environment'}
        if type(self.settings) is not dict or set(self.settings) != required or self.settings['schema'] != SETTINGS:
            raise ValueError('CACHE_SETTINGS_INVALID')
        for name in ('target_sha256', 'intent_sha256', 'policy_sha256'):
            if type(self.settings[name]) is not str or re.fullmatch(r'[0-9a-f]{64}', self.settings[name]) is None:
                raise ValueError('CACHE_SETTINGS_PIN_REQUIRED')
        if type(self.settings['bundle_id']) is not str or not self.settings['bundle_id']:
            raise ValueError('CACHE_BUNDLE_REQUIRED')
        for name in ('expected_command', 'expected_args'):
            if type(self.settings[name]) is not list or len(self.settings[name]) > 128 or any(type(v) is not str or len(v) > 4096 for v in self.settings[name]):
                raise ValueError('CACHE_LAUNCH_INVALID')
        environment = self.settings['expected_cache_environment']
        if type(environment) is not dict or not environment.keys() <= CACHE_ENV or any(type(value) is not str or len(value) > 4096 for value in environment.values()):
            raise ValueError('CACHE_APPROVED_ENVIRONMENT_INVALID')
        self.reader_gid, self.kubeconfig, self.kubectl, self.query = reader_gid, str(kubeconfig), str(kubectl), query
        read_protected(self.kubeconfig, reader_gid, limit=LIMIT)
        info = Path(self.kubectl).lstat()
        if not Path(self.kubectl).is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR:
            raise PermissionError('CACHE_PROTECTED_KUBECTL_REQUIRED')
        self.source_modules = source_bundle_modules()
        self.source_sha256 = digest({'schema': SOURCE_SCHEMA, 'modules': self.source_modules})
        self.binding_sha256 = digest(_binding(self.settings))

    def collect(self):
        document = {'schema': SCHEMA, 'producer_sha256': self.source_sha256,
                    'source_schema': SOURCE_SCHEMA, 'source_modules': self.source_modules, 'binding_sha256': self.binding_sha256,
                    'binding': {k: self.settings[k] for k in ('target_sha256', 'intent_sha256', 'policy_sha256', 'bundle_id')},
                    'state': 'unavailable', 'cache_check': None, 'execution_binding': None, 'error_class': None, 'error_code': None}
        try:
            target = _pin(self.settings['target_path'], self.settings['target_sha256'], self.reader_gid)
            registration = parse_target(target)
            selector = registration['kubernetes']
            if selector is None:
                raise ValueError('CACHE_KUBERNETES_TARGET_REQUIRED')
            intent = parse_intent(_pin(self.settings['intent_path'], self.settings['intent_sha256'], self.reader_gid))
            if intent.assets is None:
                raise ValueError('CACHE_ASSETS_REQUIRED')
            command = pods_command(selector)
            command[0:1] = [self.kubectl, '--kubeconfig', self.kubeconfig]
            def binding():
                response = self.query(command)
                items = response.get('items') if isinstance(response, dict) else None
                summary = summarize_pods(response, selector.container)
                current = summary.get('instance')
                if summary['state'] != 'ok' or current is None:
                    raise ValueError('CACHE_UNIQUE_POD_REQUIRED')
                # Match the existing target collector's active selection, then
                # retain the full original Pod for mount/launch verification.
                selected = [item for item in items if isinstance(item, dict)
                    and item.get('metadata', {}).get('uid') == current['pod_uid']
                    and not item.get('metadata', {}).get('deletionTimestamp')
                    and item.get('status', {}).get('phase') == 'Running']
                if len(selected) != 1:
                    raise ValueError('CACHE_RUNNING_POD_REQUIRED')
                pod = selected[0]
                result = check_execution_binding(pod, target, intent, model_host_path=self.settings['model_host_path'],
                    expected_command=self.settings['expected_command'], expected_args=self.settings['expected_args'])
                containers = [value for value in pod.get('spec', {}).get('containers', []) if value.get('name') == selector.container]
                if len(containers) != 1 or containers[0].get('envFrom'):
                    raise ValueError('CACHE_ENVIRONMENT_INDIRECT_UNVERIFIED')
                security = containers[0].get('securityContext') or {}
                pod_security = pod.get('spec', {}).get('securityContext') or {}
                uid = security.get('runAsUser', pod_security.get('runAsUser'))
                gid = security.get('runAsGroup', pod_security.get('runAsGroup'))
                if type(uid) is not int or uid < 0:
                    raise ValueError('CACHE_WRITER_UID_UNDECLARED')
                capabilities = security.get('capabilities') or {}
                def capability_names(key):
                    values = capabilities.get(key, [])
                    return [value for value in values[:64] if type(value) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', value)] if type(values) is list else []
                result['writer_identity'] = {'expected_uid': intent.assets.engine_uid, 'declared_uid': uid,
                    'declared_gid': gid if type(gid) is int and gid >= 0 else None, 'provenance': 'pod_security_context',
                    'privileged': security.get('privileged') if type(security.get('privileged')) is bool else None,
                    'allow_privilege_escalation': security.get('allowPrivilegeEscalation') if type(security.get('allowPrivilegeEscalation')) is bool else None,
                    'capabilities_add': capability_names('add'), 'capabilities_drop': capability_names('drop')}
                if uid != intent.assets.engine_uid:
                    result['issues'].append('cache_writer_uid_mismatch')
                    result['state'] = 'mismatch'
                actual = {}
                for value in containers[0].get('env', []):
                    name = value.get('name')
                    if name not in CACHE_ENV:
                        continue
                    if name in actual or type(value.get('value')) is not str or 'valueFrom' in value:
                        raise ValueError('CACHE_ENVIRONMENT_UNRESOLVED')
                    actual[name] = value['value']
                result['cache_environment_sha256'] = digest(actual)
                if actual != self.settings['expected_cache_environment']:
                    result['issues'].append('cache_environment_mismatch')
                    result['state'] = 'mismatch'
                return result
            before = binding()
            cache = check_cache(intent, max_entries=512)
            after = binding()
            if before != after:
                raise ValueError('CACHE_POD_CHANGED_DURING_OBSERVATION')
            document.update(state='observed', cache_check=cache, execution_binding=after)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, subprocess.SubprocessError) as error:
            document['error_class'] = type(error).__name__
            code = str(error)
            document['error_code'] = code if re.fullmatch(r'CACHE_[A-Z0-9_]{1,96}', code) else 'CACHE_OBSERVER_FAILED'
        document['generated_at'] = datetime.now(timezone.utc).isoformat()
        return document


class CacheObservationReader:
    def __init__(self, path, *, reader_gid, producer_sha256, binding_sha256, current_binding, stale_after_s=15):
        for value in (producer_sha256, binding_sha256):
            if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
                raise ValueError('CACHE_READER_PINS_REQUIRED')
        if type(reader_gid) is not int or reader_gid < 0 or not math.isfinite(stale_after_s) or stale_after_s <= 0:
            raise ValueError('CACHE_READER_CONFIGURATION_INVALID')
        self.path, self.reader_gid, self.producer_sha256 = path, reader_gid, producer_sha256
        self.binding_sha256, self.current_binding, self.stale_after_s = binding_sha256, current_binding, stale_after_s

    def collect(self):
        try:
            document = decode(read_protected(self.path, self.reader_gid, limit=LIMIT))
            age = (datetime.now(timezone.utc)-timestamp(document['generated_at'])).total_seconds()
            if (document.get('schema') != SCHEMA or document.get('producer_sha256') != self.producer_sha256
                    or document.get('binding_sha256') != self.binding_sha256 or not -5 <= age <= self.stale_after_s):
                raise ValueError('CACHE_OBSERVATION_PIN_OR_FRESHNESS_MISMATCH')
            modules = document.get('source_modules')
            if (document.get('source_schema') != SOURCE_SCHEMA or type(modules) is not dict or not 1 <= len(modules) <= 16
                    or any(type(name) is not str or re.fullmatch(r'[A-Za-z0-9_.]{1,128}', name) is None
                           or type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None for name, value in modules.items())
                    or digest({'schema': SOURCE_SCHEMA, 'modules': modules}) != self.producer_sha256):
                raise ValueError('CACHE_PRODUCER_BUNDLE_MISMATCH')
            binding = document['binding']
            current = self.current_binding()
            if any(binding.get(k) != current[k] for k in ('target_sha256', 'policy_sha256', 'bundle_id')):
                raise ValueError('CACHE_CURRENT_TARGET_POLICY_MISMATCH')
            if document.get('state') != 'observed':
                code = document.get('error_code')
                raise ValueError(code if type(code) is str and re.fullmatch(r'CACHE_[A-Z0-9_]{1,96}', code) else 'CACHE_HOST_OBSERVATION_UNAVAILABLE')
            cache, execution = document['cache_check'], document['execution_binding']
            if type(cache) is not dict or type(execution) is not dict:
                raise ValueError('CACHE_OBSERVATION_RESULT_INVALID')
            if current.get('instance_id') is None or instance_id(execution['pod_uid'], execution['container_id']) != current['instance_id']:
                raise ValueError('CACHE_CURRENT_INSTANCE_MISMATCH')
            metadata_age = (datetime.now(timezone.utc)-timestamp(cache['checked_at'])).total_seconds()
            if not -5 <= metadata_age <= self.stale_after_s:
                raise ValueError('CACHE_METADATA_STALE')
            if cache.get('state') == 'incomplete':
                raise ValueError('CACHE_METADATA_COVERAGE_INCOMPLETE')
            metadata_ok = cache.get('state') in {'ok', 'other_generations_present'} and not cache.get('unexpected_entries')
            intact = metadata_ok and execution.get('state') == 'match'
            problems = list(execution.get('issues', []))
            if not metadata_ok:
                problems.append('cache_metadata:' + str(cache.get('state')))
            metadata = {'sources': ['host_filesystem', 'kubernetes_api'], 'binding': binding, 'generation_path': cache.get('current_generation'),
                        'cache_state': cache.get('state'), 'execution_binding': execution,
                        'ancestor_problems': cache.get('ancestor_problems'), 'walk_errors': cache.get('walk_errors'), 'limits': cache.get('limits')}
            result = {'checked_at': document['generated_at'], 'intact': intact, 'total': 1,
                      'manifest_sha256': self.binding_sha256, 'counts': {'match': int(intact), 'changed': int(not intact), 'missing': 0, 'unreadable': 0, 'symlink': 0},
                      'items': [{'path': 'cache:' + str(cache.get('current_generation')), 'state': 'match' if intact else 'changed'}],
                      'extra_file_count': 0, 'extra_symlink_count': 0, 'walk_error_count': len(cache.get('walk_errors', [])),
                      'limit': f"캐시 세대 {cache.get('current_generation')} · 메타데이터 {cache.get('state')} · 실제 Pod 바인딩 {execution.get('state')} · 내용 출처/오염 소비는 별도 확인",
                      'cache_metadata': metadata, 'issues': problems}
            return result, 'local_file'
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            code = str(error)
            reason = code if re.fullmatch(r'CACHE_[A-Z0-9_]{1,96}', code) else type(error).__name__
            raise CollectionFailure('cache observation unavailable ('+reason+')', last_value=None) from error
