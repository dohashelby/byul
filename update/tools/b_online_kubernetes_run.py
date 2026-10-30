"""Owned Online Kubernetes orchestration; unavailable A capabilities fail closed.

Consumes the frozen A108 public HTTP, owned seal and Online /9 parser contracts.
No CPU collection, response-only admission inference, or kubectl mutation occurs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import importlib
import json
from pathlib import Path
import re
import sys
import uuid

import yaml

from guard.collectors.lifecycle import load_prepared
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.executors import kubernetes_lifecycle_capture as lifecycle_reader
from guard.executors import lifecycle_session
from guard.executors import lifecycle_capture
from guard.executors.online_input_cpu import judge_params
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, canonical_json, load_pack
from guard.runtime import kubernetes_http
from guard.runtime.bounded import strict_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack

CHECKS = ('REQ-07', 'REQ-08', 'SUP-08')
EXECUTOR = 'online_input_kubernetes'
PROFILE = 'byul-online-input-kubernetes-orchestration/1'
# Frozen from A108 e24dfb5653d66f9d82fc4537a995208f29ad113e; the
# reference checkout 414a7c9 changes only B REQ13 adapter, not these A bytes.
# Runtime discovery and operator manifests never create approved source pins.
A_SOURCE_PINS = {'runtime/kubernetes_http.py': '3da64c61ccceda9899e57d022b3ff7afaee3a85906d118a9309f368dd8f38566',
 'executors/kubernetes_lifecycle_capture.py': '9313ece704ffcab509cdf6ac22e6ff0b3f8395d2effecd77a21633342fa0e0f7',
 'executors/lifecycle_capture.py': '3c593cf8fb7961d452fc16a6e611dd3d406d626ffa846265ba17dac5f3bc684c',
 'executors/lifecycle_session.py': '38526674e8df7de6b25499e924f30c4ed6e94e3346ff944249a5fee00de467e1',
 'adapters/vllm_online_input.py': 'bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1',
 'adapters/vllm_lifecycle_profiles.py': 'ca77c6f19c5bae155061ba40ce9bd316d71626d47c59357f16b640e531cdb4a1',
 'adapters/vllm_lifecycle_sensor.py': '2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd',
 'adapters/vllm_online_sensor.py': '2774c821bcf92f08bcc042d6b5d5e1b16aabaf5832c23fec6a76c6019faee830',
 'adapters/vllm_online_metadata.py': '206d5df86bac7ea0b3a7b882d115314662792128bd9ec6d5dfb959a5225e8860',
 'executors/owned_capture.py': '81d23f82cb4a619d86a30d3bab65adfb305edb9633faa496bdd401924851e950',
 'executors/session_seal.py': '9a7e887a3234e5a361a89161f9c4b60927f6a471b9d0f7bf23d44987123ba1c4',
 'collectors/lifecycle.py': '7780652391c3102883e167b6d03b40231c9bc62b63deb0d967be22bbd4896a82',
 'runtime/kubernetes_binding.py': '25dd7ef74b40645d43f022573c9fa6636de76e714865c5f1d29f0d1cbec84e48',
 'runtime/bounded.py': '25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290'}
ONLINE_PROFILE = 'vllm-native-lifecycle-metadata/9'
ONLINE_CODE_CONTRACT = 'python312-source-relative-code/1'
ONLINE_POINT_CONTRACTS = {'InputProcessor._validate_model_input': {'async': False,
                                          'filename': 'vllm/v1/engine/input_processor.py',
                                          'module': 'vllm.v1.engine.input_processor',
                                          'source_sha256': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
                                          'default_deny': 'decoder_length'},
 'InputProcessor._validate_prompt_len': {'async': False,
                                         'filename': 'vllm/v1/engine/input_processor.py',
                                         'module': 'vllm.v1.engine.input_processor',
                                         'source_sha256': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
                                         'default_deny': 'decoder_length'},
 'InputProcessor.process_inputs': {'async': False,
                                   'filename': 'vllm/v1/engine/input_processor.py',
                                   'module': 'vllm.v1.engine.input_processor',
                                   'source_sha256': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b'},
 'OnlineRenderer.preprocess_chat': {'async': True,
                                    'filename': 'vllm/renderers/online_renderer.py',
                                    'module': 'vllm.renderers.online_renderer',
                                    'source_sha256': '1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e'},
 'OnlineRenderer.render_chat': {'async': True,
                                'filename': 'vllm/renderers/online_renderer.py',
                                'module': 'vllm.renderers.online_renderer',
                                'source_sha256': '1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e'},
 'OnlineRenderer.validate_chat_template': {'async': False,
                                           'filename': 'vllm/renderers/online_renderer.py',
                                           'module': 'vllm.renderers.online_renderer',
                                           'source_sha256': '1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e',
                                           'default_deny': 'template_nonnull'},
 'online_renderer._reused_prompt_token_ids': {'async': False,
                                              'filename': 'vllm/renderers/online_renderer.py',
                                              'module': 'vllm.renderers.online_renderer',
                                              'source_sha256': '1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e'},
 'protocol.validate_request_mm_kwargs': {'async': False,
                                         'filename': 'vllm/entrypoints/generate/base/protocol.py',
                                         'module': 'vllm.entrypoints.generate.base.protocol',
                                         'source_sha256': '67ede411b35a8344ddb87bfaaf1a1abea8acd39a4d23655e34032015505167c0',
                                         'default_deny': 'mm_nonempty'}}
ONLINE_RUNTIME_CONTRACTS = {'cpython-3.12.14': {'callback_code_sha256': '1728a373625ec44d943c9762ea6b31c85571876897a0ed81a1d321e4e5eee2a3',
                     'callback_function_sha256': '70369194951aaf040b401ecb9c5e9d5492285da1c98802860fd09b347b604cf2',
                     'function_codes': {'InputProcessor._validate_model_input': '9e741ab702847ae7123ebbe6224083c3feb3152aa122adb4e692d5f27edb4f0f',
                                        'InputProcessor._validate_prompt_len': '9b5e805454a645106f9ecef9c3a2d6ff5205158f28668c1d676e2a70ca57faae',
                                        'InputProcessor.process_inputs': '8cc4c2a41215abc29a20967c52e17e362a3f6abf63f618ecad9d1f4534d32ae0',
                                        'OnlineRenderer.preprocess_chat': 'c39dd01ebda9161eb33195c08c44a987c42f5c09af68dcde1c49a850d8e178d1',
                                        'OnlineRenderer.render_chat': '12781547cd1bca158c29ea2e7e86eec8510b4569d5f3cf8b0ea7f454c5d5a562',
                                        'OnlineRenderer.validate_chat_template': 'c797417738f55004bd75a709c3e60f22584b64ea77f91323e509d5f2fc25782c',
                                        'online_renderer._reused_prompt_token_ids': '14f7fbe2beb7e1227751a2eccc7cc424b3464a46124f91f552ab3f5991ec0830',
                                        'protocol.validate_request_mm_kwargs': '2df55628e9262c2c8c87bdbf904c02a58eedfe5f41118c378129da6e93b0de44'},
                     'module_codes': {'vllm.entrypoints.generate.base.protocol': '4925c4d0e258e9aad091f229d6747c429c3307337ab894437479b707277211b4',
                                      'vllm.renderers.online_renderer': '561c1cfbf72b5ecc908310596018c3a99254f44156410a81520a893710aca4b4',
                                      'vllm.v1.engine.input_processor': '82734a678f09390c1cf59c4899fcf567c254557508cfdfd0971c5101e40c0f2a'}},
 'cpython-3.12.3': {'callback_code_sha256': 'd30da68b43da5f70cca1a1f0b49291e53496e64ec9e8513acf50b2f285ab075b',
                    'callback_function_sha256': 'c29bc425984626fda69d8af826ed0595097233f4841b55a4f5ca0cacd6e43a6f',
                    'function_codes': {'InputProcessor._validate_model_input': '63b07fe66433d2d4462fc57dbbc53d65a30ca0bc1c347937e052b0ea54ca5fe1',
                                       'InputProcessor._validate_prompt_len': '9b5e805454a645106f9ecef9c3a2d6ff5205158f28668c1d676e2a70ca57faae',
                                       'InputProcessor.process_inputs': '336ac2d4cf2aa135bfe76766ccf05de1e15cf1a78b35710f861dff6cba34c9c1',
                                       'OnlineRenderer.preprocess_chat': 'f2cbf70e51aa44010f0061aee1e340bfb0456af6e142c3b82d430973147aee70',
                                       'OnlineRenderer.render_chat': 'e3dceceaf9209a7cb35a1665adcfc633d7dc3f73d3786dab4a67fcf02b6364f5',
                                       'OnlineRenderer.validate_chat_template': 'c797417738f55004bd75a709c3e60f22584b64ea77f91323e509d5f2fc25782c',
                                       'online_renderer._reused_prompt_token_ids': '14f7fbe2beb7e1227751a2eccc7cc424b3464a46124f91f552ab3f5991ec0830',
                                       'protocol.validate_request_mm_kwargs': '2df55628e9262c2c8c87bdbf904c02a58eedfe5f41118c378129da6e93b0de44'},
                    'module_codes': {'vllm.entrypoints.generate.base.protocol': '9c600ebedf518e29d3a801b9431f629862f3a354510e4cd199c5fed7fbc885d9',
                                     'vllm.renderers.online_renderer': 'ace99d2bbdf4b720bae445f4cee623b607090288e35442e4250e462b195ac958',
                                     'vllm.v1.engine.input_processor': 'e79004d94a39740156c6420b87e077a04ef4840eff62b06b38da6e7191decd39'}}}
_BODY_KEYS = {'model', 'messages', 'max_tokens', 'stream', 'kv_transfer_params', 'echo',
              'chat_template', 'chat_template_kwargs', 'mm_processor_kwargs', 'media_io_kwargs'}
_HASH_KEYS = {'asgi_request_id_sha256', 'native_request_id_sha256', 'call_id_sha256',
              'session_id_sha256', 'process_start_sha256', 'parsed_fields_sha256',
              'input_ids_sha256', 'admitted_ids_sha256', 'domain_sha256', 'config_sha256'}
_BOOL_KEYS = {'native_returned', 'request_ids_in_domain', 'request_ids_within_model_vocab',
              'template_gate_bypass', 'mm_gate_bypass', 'length_source_verified', 'trust_request_chat_template',
              'trust_request_mm_kwargs', 'source_field_supported', 'after_admitted'}


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _read(path, limit=262144):
    with Path(path).open('rb') as stream:
        return strict_json(stream.read(limit + 1), limit=limit)


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def proposal_pack(folder):
    """External DRAFT only, never a permanent registry or shared-pack change."""
    folder = Path(folder)
    (folder / 'checks').mkdir(parents=True, exist_ok=False)
    files = []
    for cid in CHECKS:
        check = CheckDef(id=cid, revision=1, area=cid.split('-')[0], purpose='실제 Online 입력 소비 확인',
            question='귀속된 native 입력 소비가 선정 source/config 경계를 위반했는가?',
            review_order='owned_direct_pod_native', applies_when={'always': True},
            normal_control='각 요청 후 같은 serving generation의 정상 admission',
            challenge_steps=['승인된 manifest의 직접 Pod 요청과 native session join'],
            required_observations=[{'key': 'online_input_confirmed_violations', 'type': 'list[str]'},
                                   {'key': 'online_kubernetes_complete', 'type': 'bool'}],
            satisfied_if='선정 source/config에 귀속된 실제 native 수집',
            fail_criteria='독립 domain 또는 실제 trust/length 경계 위반의 native 소비',
            unknown_criteria='실제 source/필드/세대/요청/native 귀속 미확인',
            executor=EXECUTOR, judge={'plugin': 'rule_table', 'params': judge_params()},
            limitations=['DRAFT B runner. A Online HTTP/native join/seal 미지원 시 ABORTED.'])
        raw = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
        name = 'checks/' + cid + '.yaml'
        (folder / name).write_bytes(raw)
        files.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest()})
    (folder / 'pack.yaml').write_text(yaml.safe_dump({'pack_id': 'PACK-B-ONLINE-KUBERNETES-PROPOSAL',
        'revision': 1, 'engines': ['vllm'], 'checks': list(CHECKS), 'files_sha256': files,
        'review': {'status': 'DRAFT'}}, sort_keys=False))
    return load_pack(folder)


def _safe_text_body(body):
    if len(canonical_json(body)) > 131072:
        raise ValueError('ONLINE_K8S_REQUEST_TOO_LARGE')
    for message in body['messages']:
        if (type(message) is not dict or set(message) != {'role', 'content'}
                or message['role'] not in {'system', 'user', 'assistant'}
                or type(message['content']) is not str
                or re.search(r'(?i)(?:[a-z][a-z0-9+.-]*://|data:|www\.)', message['content'])):
            raise ValueError('ONLINE_K8S_TEXT_ONLY_MESSAGES_REQUIRED')


def validate_inputs(env, policy, pack, target, prepared, manifest, *, duration, binary):
    if (not env.hash_ok() or not policy.hash_ok() or env.engine.name != 'vllm'
            or policy.pack != pack.ref() or set(pack.checks) != set(CHECKS)
            or set(policy.scope.include) != set(CHECKS) or policy.scope.exclude or policy.values or policy.exceptions):
        raise ValueError('ONLINE_K8S_ENV_POLICY_PACK_MISMATCH')
    for check in pack.checks.values():
        if (check.executor != EXECUTOR or check.judge.plugin != 'rule_table'
                or check.judge.params != judge_params() or check.policy_params
                or {o.key: o.type for o in check.required_observations} != {
                    'online_input_confirmed_violations': 'list[str]', 'online_kubernetes_complete': 'bool'}):
            raise ValueError('ONLINE_K8S_PACK_CONTRACT_MISMATCH')
    # Constructor does no cluster I/O. Existing public API performs exact target,
    # ENV, alias, container, digest, address and duration validation.
    transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
        profile=kubernetes_http.REQUEST_BINDING_PROFILE)
    lifecycle_reader.validate_target(_capture_target(env, target), env)
    expected = {'schema', 'environment_ref', 'target_sha256', 'session', 'run_id', 'owner',
                'recovery_owner', 'isolated_run', 'domain_config_sha256', 'cases'}
    if (type(manifest) is not dict or set(manifest) != expected
            or manifest['schema'] != 'byul-online-kubernetes-run-input/1'
            or manifest['environment_ref'] != env.ref().model_dump(mode='json')
            or manifest['target_sha256'] != transport.target_sha256
            or manifest['session'] != prepared['session']
            or manifest['run_id'] != target['kubernetes']['run_id']
            or not _sha(manifest['domain_config_sha256']) or manifest['isolated_run'] is not True
            or any(type(manifest[k]) is not str or not re.fullmatch('[A-Za-z0-9._@-]{1,100}', manifest[k])
                   for k in ('owner', 'recovery_owner'))):
        raise ValueError('ONLINE_K8S_OWNERSHIP_MANIFEST_MISMATCH')
    cases = manifest['cases']
    if type(cases) is not list or not 1 <= len(cases) <= 16:
        raise ValueError('ONLINE_K8S_CASES_INVALID')
    seen, checks = set(), set()
    for case in cases:
        if (type(case) is not dict or set(case) != {'id', 'check_id', 'body', 'after_body'}
                or type(case['id']) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,59}', case['id'])
                or case['id'] in seen or case['check_id'] not in CHECKS):
            raise ValueError('ONLINE_K8S_CASES_INVALID')
        if set(case['after_body']) - {'model', 'messages', 'max_tokens', 'stream'}:
            raise ValueError('ONLINE_K8S_AFTER_MUST_BE_NORMAL')
        for body in (case['body'], case['after_body']):
            if (type(body) is not dict or set(body) - _BODY_KEYS or body.get('model') != transport.api_model
                    or body.get('stream', False) is not False or type(body.get('max_tokens')) is not int or body.get('max_tokens') != 1
                    or type(body.get('messages')) is not list or not body['messages']):
                raise ValueError('ONLINE_K8S_REQUEST_INVALID')
            _safe_text_body(body)
        seen.add(case['id']); checks.add(case['check_id'])
    return transport


def _capture_target(env, target):
    return {'schema': 'guard-kubernetes-lifecycle-target/1',
        'run_id': target['kubernetes']['run_id'], 'environment_ref': env.ref().model_dump(mode='json'),
        'lab': target['lab'], 'kubernetes': {'context': target['kubernetes']['context'],
                                           'engine': deepcopy(target['kubernetes']['engine'])}}


def _approved_capabilities():
    """Require the exact reviewed A108 implementation before any network call."""
    root = Path(__file__).resolve().parents[1] / 'src/guard'
    modules = {}
    for relative, expected in A_SOURCE_PINS.items():
        path = root / relative
        if (not _sha(expected) or path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError('ONLINE_K8S_A_SOURCE_MISMATCH')
        module = importlib.import_module('guard.' + relative[:-3].replace('/', '.'))
        if not Path(module.__file__).resolve().samefile(path):
            raise RuntimeError('ONLINE_K8S_A_SOURCE_MISMATCH')
        modules[relative] = module
    for function, path in ((kubernetes_http.KubernetesHTTP.__call__, 'runtime/kubernetes_http.py'),
                           (lifecycle_session.capture_session, 'executors/lifecycle_session.py'),
                           (lifecycle_reader.KubernetesLifecycleReader.snapshot, 'executors/kubernetes_lifecycle_capture.py'),
                           (lifecycle_reader.KubernetesLifecycleReader.seal_session, 'executors/kubernetes_lifecycle_capture.py'),
                           (modules['adapters/vllm_online_input.py'].snapshot_online_input, 'adapters/vllm_online_input.py')):
        if not Path(inspect.getsourcefile(function)).resolve().samefile(root / path):
            raise RuntimeError('ONLINE_K8S_A_FUNCTION_ORIGIN_MISMATCH')
    if kubernetes_http.ONLINE_INPUT_PROFILE != 'kubernetes-owned-online-input-http-v1':
        raise RuntimeError('ONLINE_K8S_A_PROFILE_UNAVAILABLE')


class _ProductionSession:
    """Connect exact A108 public HTTP, owned seal and parser-checked capture."""
    def __init__(self, env, target, prepared, manifest, *, duration, binary):
        _approved_capabilities()
        if prepared.get('profile') != ONLINE_PROFILE:
            raise ValueError('ONLINE_K8S_EXPLICIT_ONLINE_PROFILE_REQUIRED')
        self.transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
            profile=kubernetes_http.ONLINE_INPUT_PROFILE)
        self.capture_target = _capture_target(env, target)
        self.reader = lifecycle_reader.KubernetesLifecycleReader(self.capture_target, env, prepared,
                                                                duration=duration, binary=binary)
        self.target, self.prepared, self.manifest = target, prepared, manifest
        self.pending = []
        self.before = None
        self.packet = None
        self.sealed = False
        self.request_prefix = 'byul-online-' + uuid.uuid4().hex

    def preflight(self):
        self.before = self.reader.snapshot()
        return True  # snapshot verifies the selected generation or raises.

    def case(self, case, target):
        request_hashes = [hashlib.sha256((self.request_prefix + '-' + case['id'] + '-' + suffix).encode()).hexdigest()
                          for suffix in ('primary', 'after')]
        self.pending.append((deepcopy(case), request_hashes))
        for suffix, key in (('primary', 'body'), ('after', 'after_body')):
            rid = self.request_prefix + '-' + case['id'] + '-' + suffix
            request = {'id': 'online-' + case['id'] + '-' + suffix, 'mode': 'json', 'method': 'POST',
                'url': target['engine']['trusted_url'] + '/v1/chat/completions',
                'headers': {'X-Request-Id': rid}, 'body': deepcopy(case[key]), 'read_delay_ms': 0}
            # Raw responses stay transient and never establish native consumption.
            self.transport('kubernetes-probe', [request], timeout=30)
        return None  # All native files are read once after seal, not per check.

    def seal(self):
        error = None
        try:
            self.sealed = self.reader.seal_session().get('sealed') is True
        except Exception as exc:
            error = exc
        finally:
            engine = self.reader.binding['engine']
            self.packet = lifecycle_session.capture_session(self.reader, self.capture_target,
                container=engine['container'], image=engine['image'], prepared=self.prepared,
                owner_identity=self.reader.owner_identity, read_file=self.reader.read_file,
                image_check=self.reader.image_check)
        coverage = (self.packet.get('recorded_files_covered') is True
                    and self.packet.get('live_census_stable') is True
                    and all((r.get('capture') or {}).get('summary', {}).get('recording_closed') is True
                            for r in self.packet.get('recordings', [])))
        rows = [_join_native(self.packet, case, hashes, target=self.target,
                             prepared=self.prepared, manifest=self.manifest)
                for case, hashes in self.pending]
        try:
            after = self.reader.snapshot()
            generation = after == self.before
        except Exception:
            generation = False
        return {'sealed': self.sealed and error is None and coverage, 'generation_verified': generation,
                'rows': [row for row in rows if row is not None]}

    def close(self):
        # HTTP has a bounded own-process watchdog. No engine/probe Pod is deleted.
        # Retry only sealing the exact owned session when its first seal failed.
        if not self.sealed:
            self.sealed = self.reader.seal_session().get('sealed') is True
        report = self.transport.report()
        return {'owned_probe_reaped': not report.get('cleanup_errors', []),
                'owned_session_closed': self.sealed}


def _native_digest(value):
    # Callback canonical values use the sensor's ASCII JSON convention.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _native_admission(snapshots):
    calls = [r for r in snapshots if r.get('point') == 'InputProcessor.process_inputs' and r.get('phase') == 'CALL']
    returns = [r for r in snapshots if r.get('point') == 'InputProcessor.process_inputs' and r.get('phase') == 'RETURN']
    if len(calls) != 1 or len(returns) != 1:
        return None
    # Reused ASGI IDs, multiple native requests or mismatched call generations
    # cannot be silently folded into one admission.
    first, last = calls[0], returns[0]
    binding = first.get('binding', {})
    later = last.get('binding', {})
    if (any(not _sha(binding.get(k)) or binding[k] != later.get(k) for k in
            ('call_id_sha256', 'native_request_id_sha256', 'asgi_request_id_sha256', 'process_start_sha256'))
            or last.get('status') != 'collected'):
        return None
    meta = last.get('metadata', {})
    if (first.get('metadata', {}).get('processor_input') != meta.get('processor_input')
            or first.get('metadata', {}).get('effective_config') != meta.get('effective_config')
            or meta.get('native_request_id_sha256') != binding['native_request_id_sha256']
            or meta.get('native_request_binding_matches') is not True):
        return None
    for key in ('processor_input', 'admitted'):
        item = meta.get(key, {})
        if (item.get('status') != 'collected' or not _sha(item.get('canonical_sha256'))
                or type(item.get('count')) is not int or item['count'] < 0):
            return None
    return last


def _join_native(packet, case, request_hashes, *, target, prepared, manifest):
    """Join only this run's parser-checked native files, never external facts JSON.

    Online /9 metadata carries A's source-relative module/callback code proof.
    Matching snapshots stay in one actual PID/start and one ASGI request; native
    CALL/RETURN must have the same native request and call identity.
    """
    if not isinstance(ONLINE_POINT_CONTRACTS, dict):
        return None
    domains = {}
    for row in packet.get('recordings', []):
        capture = row.get('capture') or {}
        if (not row.get('session_owner_match') or not row.get('listed_file_identity_match')
                or capture.get('process_context_verified') is not True
                or capture.get('image_digest_verified') is not True):
            continue
        callback = capture.get('online_callback_provenance') or {}
        codes = ONLINE_RUNTIME_CONTRACTS.get(callback.get('code_runtime'), {})
        if (capture.get('online_code_contract') != ONLINE_CODE_CONTRACT or not codes
                or callback.get('module') != 'guard.adapters.vllm_online_input'
                or callback.get('bytes_sha256') != A_SOURCE_PINS['adapters/vllm_online_input.py']
                or callback.get('code_sha256') != codes['callback_code_sha256']):
            continue
        records = capture.get('records', [])
        if not records:
            continue
        context = records[0]['payload']['context']
        modules = {r['payload'].get('module'): r['payload'] for r in records
                   if r.get('event') == 'MODULE' and r['payload'].get('verified') is True}
        for record in records:
            payload = record.get('payload', {})
            snapshot = payload.get('metadata', {}).get('online_input')
            if record.get('event') != 'NATIVE' or not isinstance(snapshot, dict):
                continue
            contract = ONLINE_POINT_CONTRACTS.get(payload.get('point'), {})
            module = modules.get(contract.get('module'), {})
            if (not contract or snapshot.get('point') != payload.get('point')
                    or not _sha(contract.get('source_sha256'))
                    or prepared.get('source_sha256', {}).get(contract['module']) != contract['source_sha256']
                    or payload.get('source_sha256') != contract['source_sha256']
                    or module.get('source_sha256') != contract['source_sha256']
                    or module.get('online_runtime') != callback['code_runtime']
                    or module.get('online_code_sha256') != codes['module_codes'].get(contract['module'])):
                continue
            binding = snapshot.get('binding', {})
            asgi = binding.get('asgi_request_id_sha256')
            if (snapshot.get('schema') != 'byul-online-input-snapshot/1'
                    or binding.get('pid') != context.get('pid')
                    or binding.get('session_id_sha256') != _native_digest(prepared['session'])
                    or binding.get('process_start_sha256') != _native_digest(context.get('start_ticks'))
                    or asgi not in request_hashes):
                continue
            domains.setdefault((context['pid'], context['start_ticks'], callback['code_runtime']), {h: [] for h in request_hashes})[asgi].append(snapshot)
    candidates = []
    for snapshots in domains.values():
        admitted = _native_admission(snapshots[request_hashes[0]])
        if admitted is not None:
            candidates.append((snapshots, admitted))
    if len(candidates) != 1:
        return None
    snapshots, admitted = candidates[0]
    metadata = admitted['metadata']
    primary = snapshots[request_hashes[0]]
    fields = _case_fields(case)
    rendered = _pair(primary, 'OnlineRenderer.render_chat')
    if rendered is None or not _fields_match(rendered[0]['metadata'].get('parsed_fields'), fields, explicit=True):
        return None
    domain = metadata.get('token_id_domain', {})
    config = metadata.get('effective_config', {})
    config_hash = digest({'effective_config': config, 'token_id_domain_sha256': domain.get('canonical_sha256')})
    if config_hash != manifest['domain_config_sha256']:
        return None
    native = {**admitted['binding'], 'parsed_fields_sha256': digest(fields), 'native_returned': True,
        'source_field_supported': True, 'input_ids_sha256': metadata['processor_input']['canonical_sha256'],
        'admitted_ids_sha256': metadata['admitted']['canonical_sha256'],
        'prompt_count': metadata['admitted']['count'], 'config_sha256': config_hash,
        'effective_max_model_len': config.get('max_model_len'),
        'after_admitted': _normal_after(snapshots[request_hashes[1]], config, domain, case['after_body'])}
    if (domain.get('status') == 'collected' and domain.get('request_ids_canonical_sha256') == native['input_ids_sha256']):
        native.update(domain_sha256=domain.get('canonical_sha256'),
            request_ids_in_domain=domain.get('request_ids_in_domain'),
            request_ids_within_model_vocab=domain.get('request_ids_within_model_vocab'))
    native.update(_gate_bypass(primary, fields))
    native['runner_type'] = config.get('runner_type')
    native['prompt_type'] = 'decoder' if _decoder_length(primary, metadata) else 'unavailable'
    native['length_source_verified'] = native['prompt_type'] == 'decoder'
    return {**native, 'case_id': case['id'], 'check_id': case['check_id'], 'target_sha256': digest(target),
        'manifest_sha256': digest(manifest), 'session': prepared['session'],
        'source_verified': True, 'generation_verified': True, 'request_join_verified': True}


def _pair(snapshots, point):
    calls = [s for s in snapshots if s.get('point') == point and s.get('phase') == 'CALL']
    returns = [s for s in snapshots if s.get('point') == point and s.get('phase') == 'RETURN']
    if len(calls) != 1 or len(returns) != 1:
        return None
    if (not _sha(calls[0].get('binding', {}).get('call_id_sha256'))
            or calls[0]['binding']['call_id_sha256'] != returns[0].get('binding', {}).get('call_id_sha256')):
        return None
    return calls[0], returns[0]


def _fields_match(parsed, fields, *, explicit=False):
    return isinstance(parsed, dict) and all(isinstance(parsed.get(k), dict)
        and parsed[k].get('present') is True and parsed[k].get('status') == 'collected'
        and (not explicit or parsed[k].get('explicitly_set') is True)
        and parsed[k].get('canonical_sha256') == _native_digest(v) for k, v in fields.items())


def _gate_bypass(snapshots, fields):
    result = {'template_gate_bypass': False, 'mm_gate_bypass': False}
    if not isinstance(ONLINE_POINT_CONTRACTS, dict):
        return result
    for point, trust, names, contract_name in (
        ('OnlineRenderer.validate_chat_template', 'trust_request_chat_template', ('chat_template', 'chat_template_kwargs'), 'template_nonnull'),
        ('protocol.validate_request_mm_kwargs', 'trust_request_mm_kwargs', ('mm_processor_kwargs', 'media_io_kwargs'), 'mm_nonempty')):
        contract = ONLINE_POINT_CONTRACTS.get(point, {})
        if contract.get('default_deny') != contract_name:
            continue
        selected = {k: fields[k] for k in names if k in fields}
        forbidden = ((selected.get('chat_template') is not None or
            isinstance(selected.get('chat_template_kwargs'), dict) and selected['chat_template_kwargs'].get('chat_template') is not None)
            if contract_name == 'template_nonnull' else any(bool(v) for v in selected.values()))
        pair = _pair(snapshots, point)
        if not forbidden or pair is None:
            continue
        if all(s.get('metadata', {}).get(trust) is False
               and _fields_match(s['metadata'].get('parsed_fields'), selected) for s in pair):
            result['template_gate_bypass' if contract_name == 'template_nonnull' else 'mm_gate_bypass'] = True
    return result


def _decoder_length(snapshots, metadata):
    count = metadata.get('admitted', {}).get('count')
    for snapshot in snapshots:
        point = snapshot.get('point')
        if snapshot.get('phase') != 'CALL' or point not in {'InputProcessor._validate_prompt_len', 'InputProcessor._validate_model_input'}:
            continue
        if ONLINE_POINT_CONTRACTS.get(point, {}).get('default_deny') != 'decoder_length':
            continue
        m = snapshot.get('metadata', {})
        same = (m.get('candidate_prompt_count') == count if point.endswith('_validate_prompt_len') else
                m.get('processor_input', {}).get('canonical_sha256') == metadata.get('processor_input', {}).get('canonical_sha256'))
        if m.get('prompt_type') == 'decoder' and m.get('effective_config') == metadata.get('effective_config') and same:
            return True
    return False


def _normal_after(snapshots, configuration, domain, body):
    admitted = _native_admission(snapshots)
    rendered = _pair(snapshots, 'OnlineRenderer.render_chat')
    if admitted is None or rendered is None or _case_fields({'body': body}):
        return False
    parsed = rendered[0].get('metadata', {}).get('parsed_fields', {})
    for field in ('chat_template', 'chat_template_kwargs', 'mm_processor_kwargs', 'media_io_kwargs', 'kv_transfer_params', 'echo'):
        item = parsed.get(field, {})
        neutral = (False,) if field == 'echo' else (None, {})
        if (item.get('status') != 'collected' or item.get('explicitly_set') is not False
                or item.get('canonical_sha256') not in {_native_digest(v) for v in neutral}):
            return False
    m = admitted['metadata']
    if m.get('effective_config') != configuration:
        return False
    for key, ids_key in (('token_id_domain', 'processor_input'), ('admitted_token_id_domain', 'admitted')):
        proof = m.get(key, {})
        if (proof.get('status') != 'collected' or proof.get('canonical_sha256') != domain.get('canonical_sha256')
                or proof.get('request_ids_in_domain') is not True or proof.get('request_ids_within_model_vocab') is not True
                or proof.get('request_ids_canonical_sha256') != m[ids_key]['canonical_sha256']
                or m[ids_key].get('integer_ids') is not True or m[ids_key].get('count', 0) <= 0):
            return False
    maximum = configuration.get('max_model_len')
    count = m['admitted']['count']
    return (type(maximum) is int and maximum > 0 and count <= maximum
            and not (count == maximum and configuration.get('runner_type') == 'generate')
            and m['processor_input']['canonical_sha256'] == m['admitted']['canonical_sha256'])


def _case_fields(case):
    return {k: v for k, v in case['body'].items() if k not in {'model', 'messages', 'stream', 'max_tokens'}}


def _qualify(raw, case, *, target_sha256, manifest_sha256, session):
    """A-checked join is required; no HTTP status, CPU source or JSON promotion."""
    if type(raw) is not dict:
        return None
    safe = {k: raw[k] for k in _HASH_KEYS if _sha(raw.get(k))}
    safe.update({k: raw[k] for k in _BOOL_KEYS if type(raw.get(k)) is bool})
    for key in ('prompt_count', 'effective_max_model_len'):
        if type(raw.get(key)) is int and raw[key] >= 0:
            safe[key] = raw[key]
    for key, values in (('runner_type', {'generate', 'pooling', 'draft'}), ('prompt_type', {'decoder', 'encoder'})):
        if raw.get(key) in values:
            safe[key] = raw[key]
    qualified = (raw.get('case_id') == case['id'] and raw.get('check_id') == case['check_id']
        and raw.get('target_sha256') == target_sha256 and raw.get('manifest_sha256') == manifest_sha256
        and raw.get('session') == session and raw.get('source_verified') is True
        and raw.get('generation_verified') is True and raw.get('request_join_verified') is True
        and safe.get('source_field_supported') is True
        and all(k in safe for k in ('asgi_request_id_sha256', 'native_request_id_sha256',
                                    'call_id_sha256', 'process_start_sha256', 'parsed_fields_sha256'))
        and safe['parsed_fields_sha256'] == digest(_case_fields(case)))
    violation = False
    if qualified and safe.get('native_returned') is True:
        same_admission = (_sha(safe.get('input_ids_sha256')) and
            safe.get('input_ids_sha256') == safe.get('admitted_ids_sha256'))
        length_violation = (same_admission and safe.get('length_source_verified') is True
            and safe.get('prompt_type') == 'decoder' and type(safe.get('prompt_count')) is int
            and type(safe.get('effective_max_model_len')) is int and safe['effective_max_model_len'] > 0
            and (safe['prompt_count'] > safe['effective_max_model_len'] or
                 safe['prompt_count'] == safe['effective_max_model_len'] and safe.get('runner_type') == 'generate'))
        if case['check_id'] == 'REQ-08':
            violation = bool(same_admission and _sha(safe.get('domain_sha256')) and _sha(safe.get('config_sha256'))
                and (safe.get('request_ids_in_domain') is False or safe.get('request_ids_within_model_vocab') is False)) or length_violation
        elif case['check_id'] == 'REQ-07':
            violation = safe.get('template_gate_bypass') is True or length_violation
        else:
            violation = safe.get('mm_gate_bypass') is True
    return {'case_id': case['id'], 'check_id': case['check_id'], 'qualified': bool(qualified),
            'violation': bool(qualified and violation), 'metadata': safe}


def run_pipeline(env, policy, pack, target, prepared, manifest, output, *, execute=False,
                 duration=300, binary='kubectl', _example_session=None):
    if sys.flags.optimize:
        raise ValueError('ONLINE_K8S_REQUIRES_NORMAL_PYTHON')
    transport = validate_inputs(env, policy, pack, target, prepared, manifest, duration=duration, binary=binary)
    if _example_session is not None and env.data_origin != DataOrigin.EXAMPLE:
        raise ValueError('ONLINE_K8S_INJECTION_REQUIRES_EXAMPLE_ENV')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest_sha = digest(manifest)
    record = {'profile': PROFILE, 'collection_id': 'online-kubernetes-' + uuid.uuid4().hex,
        'target_sha256': transport.target_sha256, 'manifest_sha256': manifest_sha,
        'session_sha256': digest(prepared['session']), 'rows': [], 'errors': [], 'cleanup_errors': [],
        'started_at': None, 'finished_at': None, 'sealed': False, 'generation_verified': False}
    store = Store(output / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    if {item.check_id for item in plan.items if item.selected} != set(CHECKS):
        raise ValueError('ONLINE_K8S_PLAN_SELECTION_MISMATCH')
    store.put(plan)
    session = None
    try:
        if execute:
            session = _example_session or _ProductionSession(env, target, prepared, manifest, duration=duration, binary=binary)
            if session.preflight() is not True:
                raise RuntimeError('PREFLIGHT')
            record['started_at'] = datetime.now(timezone.utc).isoformat()
            for case in manifest['cases']:
                try:
                    raw = session.case(deepcopy(case), deepcopy(target))
                    safe = _qualify(raw, case, target_sha256=transport.target_sha256,
                                    manifest_sha256=manifest_sha, session=prepared['session'])
                    if safe is not None:
                        record['rows'].append(safe)
                except Exception:
                    record['errors'].append('ONLINE_K8S_CASE_UNAVAILABLE')
            try:
                final = session.seal()
                for raw in final.get('rows', []):
                    case = next((c for c in manifest['cases'] if c['id'] == raw.get('case_id')), None)
                    if case is not None and not any(r['case_id'] == case['id'] for r in record['rows']):
                        safe = _qualify(raw, case, target_sha256=transport.target_sha256, manifest_sha256=manifest_sha, session=prepared['session'])
                        if safe is not None:
                            record['rows'].append(safe)
                record['sealed'] = final.get('sealed') is True
                record['generation_verified'] = final.get('generation_verified') is True
            except Exception:
                record['errors'].append('ONLINE_K8S_SEAL_UNAVAILABLE')
    except Exception:
        record['errors'].append('ONLINE_K8S_A_CAPABILITY_OR_PREFLIGHT_UNAVAILABLE')
    finally:
        if record['started_at']:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
        if session is not None:
            try:
                cleanup = session.close()
                if cleanup.get('owned_probe_reaped') is not True or cleanup.get('owned_session_closed') is not True:
                    raise RuntimeError('CLEANUP')
            except Exception:
                record['cleanup_errors'].append('ONLINE_K8S_OWNED_CLEANUP_UNVERIFIED')
    if not execute:
        record['errors'].append('ONLINE_K8S_DRY_RUN_NO_COLLECTION')
    complete = (len(record['rows']) == len(manifest['cases']) and all(r['qualified'] and r['metadata'].get('after_admitted') is True
        for r in record['rows']) and record['sealed'] and record['generation_verified']
        and not record['errors'] and not record['cleanup_errors'])
    class BoundExecutor:
        name, version = EXECUTOR, '0.1.0'
        def run(self, check, environment, values, adapter, workdir):
            if environment.ref() != env.ref() or values:
                raise ValueError('ONLINE_K8S_BOUND_ENV_MISMATCH')
            rows = [r for r in record['rows'] if r['check_id'] == check.id and r['qualified']]
            check_complete = complete and bool(rows)
            draft = EvidenceDraft(status=ExecStatus.COMPLETED if check_complete else ExecStatus.ABORTED,
                data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                normal_control={'attempted': record['started_at'] is not None, 'succeeded': check_complete if record['started_at'] else None},
                observations={'online_input_confirmed_violations': {'value': [r['case_id'] for r in rows if r['violation']] if rows else None,
                    'collected': bool(rows)}, 'online_kubernetes_complete': {'value': bool(check_complete), 'collected': True}},
                challenges=[{'step': step, 'attempted': record['started_at'] is not None} for step in check.challenge_steps],
                config={k: record[k] for k in ('profile', 'collection_id', 'target_sha256', 'manifest_sha256', 'session_sha256')},
                started_at=datetime.fromisoformat(record['started_at']) if record['started_at'] else None,
                finished_at=datetime.fromisoformat(record['finished_at']) if record['finished_at'] else None,
                abort_reason=None if check_complete else 'ONLINE_K8S_CAPTURE_INCOMPLETE', cleanup_errors=record['cleanup_errors'])
            try:
                path = Path(workdir) / 'online-native.json'
                _write(path, {**deepcopy(record), 'check_id': check.id})
                draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
            except Exception:
                draft = incomplete_artifact_capture(draft)
            return draft
    executor = BoundExecutor()
    key = EXECUTOR + ':owned-native'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: executor
    try:
        evidences = run_plan(store, pack, plan, RunConfig(executors={EXECUTOR: 'owned-native'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    now = datetime.now(timezone.utc)
    results = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    summary = {k: record[k] for k in ('profile', 'collection_id', 'started_at', 'finished_at', 'errors', 'cleanup_errors')}
    summary.update({'execution_complete': complete, 'live_support_available': False,
        'origin': 'EXAMPLE' if env.data_origin == DataOrigin.EXAMPLE else 'DECLARED',
        'environment': env.ref().model_dump(mode='json'), 'policy': policy.ref().model_dump(mode='json'),
        'pack': pack.ref().model_dump(mode='json'), 'plan': plan.ref().model_dump(mode='json'),
        'evidence': [e.ref().model_dump(mode='json') for e in evidences],
        'results': [r.ref().model_dump(mode='json') for r in results], 'report': report.ref().model_dump(mode='json'),
        'store_verify_errors': store.verify(), 'exact_ref_rejudgment_unchanged': False, 'restore_verified': False})
    try:
        replay = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
        if [r.ref() for r in replay] != [r.ref() for r in results]:
            raise ValueError('REJUDGE')
        summary['exact_ref_rejudgment_unchanged'] = True
        export_bundle(store.root, output / 'evidence.zip')
        restore_bundle(output / 'evidence.zip', output / 'restored')
        restored = Store(output / 'restored')
        restored_plan = restored.resolve(plan.ref())
        replay = judge_plan(restored, get_pack(restored, plan.inputs.pack), restored_plan, as_of=now)
        if restored.verify() or [r.ref() for r in replay] != [r.ref() for r in results]:
            raise ValueError('RESTORE')
        summary['restore_verified'] = True
    except Exception:
        summary['errors'] = [*summary['errors'], 'ONLINE_K8S_REPLAY_OR_BUNDLE_UNAVAILABLE']
    _write(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare-pack')
    prepare.add_argument('--output', required=True, type=Path)
    run = sub.add_parser('run')
    for key in ('env', 'policy', 'pack', 'target', 'prepared', 'case-manifest', 'output'):
        run.add_argument('--' + key, required=True, type=Path)
    run.add_argument('--execute', action='store_true')
    run.add_argument('--duration', type=float, default=300)
    run.add_argument('--kubectl', default='kubectl')
    args = parser.parse_args()
    if args.command == 'prepare-pack':
        print(json.dumps(proposal_pack(args.output).ref().model_dump(mode='json')))
        return 0
    summary = run_pipeline(Environment.model_validate(_read(args.env)), Policy.model_validate(_read(args.policy)),
        load_pack(args.pack), _read(args.target), load_prepared(args.prepared), _read(args.case_manifest), args.output,
        execute=args.execute, duration=args.duration, binary=args.kubectl)
    print(json.dumps(summary))
    return 0 if summary['execution_complete'] and summary['restore_verified'] and not summary['errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
