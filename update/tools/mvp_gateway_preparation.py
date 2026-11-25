"""Prepare the pinned BYUL Gateway for paired, ordinary request observations.

Reads saved inputs and writes run-local manifests. It does not contact AWS,
Kubernetes, the Gateway or the engine, or issue policy/credential material.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from tempfile import TemporaryDirectory

import yaml

from guard.executors.kubernetes_gateway_observation import MAX_BYTES, final_records, snapshot_record
from guard.models import DataOrigin, Environment, canonical_json, new_id, utcnow
from guard.models.objects import FactValue
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import verify_owned_pod
from guard.update_validation import _plain_path, reserve_output
from tools.gpu_a_run_inputs import _binding
from tools.mvp_metadata_wiring import _source, _transport
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions

ARCHIVE_SHA256 = '5c0a9bd82dfffdf3334392a6c84f011537135193e98d57d0db836c720dab7953'
GATEWAY_IMAGE = 'docker.io/library/byul-gateway-codex@sha256:0dc282b701783a4aab3fc5dbcca5e094490921d930cf6adb3f1b86a9f3c9f754'
IMAGE_CONFIG_SHA256 = '05111efac75be41ed3eb3e384dcc3d6ce895556f69408832091e41311b895f22'
RUNTIME_SOURCE_TREE = 'b58c87dd6f23999745f12bc36fd2e1944eab0809'
PRODUCER_SHA256 = '444a827fa471387affa69d35893e816bd14b951b3d31b3fae6c0c19ab9aa473d'
PRODUCER_LAYER = 'blobs/sha256/4fd663fc668659bba3622dc23b4406a72fd9e8742879df0e5dbceb06c0f6b0be'
PRODUCER_MEMBER = 'opt/serving-runtime/serving_runtime/mvp/request_records.py'
GATEWAY_ENV = 'byul-update-normal-observation'
GATEWAY_TEMPLATE = (Path(__file__).resolve().parents[2] / 'runtime/deploy/gpu-lab/gateway.yaml')
GATEWAY_ROLE = 'serving-gateway'
INPUT_ANNOTATION = 'byul.lab/gateway-inputs-sha256'
RUNTIME_ROLES = {'gateway_policy', 'policy_approvals', 'target'}
INPUT_FIELDS = {'environment', 'target', 'bundle', 'bundle_sha256', 'auth_secret'}
FILE_LIMIT = 256 * 1024
_ORDINARY_ROUTE = 'POST /v1/chat/completions'
_OBSERVATION_FILES = {'response_receipt', 'audit_receipt', 'journal'}
_OWNER_PIN_FIELDS = {'engine_owner_sha256', 'gateway_owner_sha256', 'environment_ref', 'request_sha256'}
_RESPONSE_FIELDS = _OWNER_PIN_FIELDS | {'body_sha256', 'body_bytes', 'status', 'request_id', 'route', 'run_sha256',
                                        'observed_started_ns', 'observed_finished_ns'}
_AUDIT_FIELDS = _OWNER_PIN_FIELDS | {'event', 'gateway_target_sha256', 'owner_before_after_verified',
    'producer_sha256', 'runtime_policy_sha256', 'journal_bytes', 'journal_sha256', 'selected_row_sha256',
    'recorded_at', 'actor_sha256', 'session', 'independent_denied_event_observed'}
_OBSERVATION_INVALID = 'GATEWAY_ORDINARY_OBSERVATION_INVALID'


def reviewed_producer(archive):
    """Verify the exact retained OCI archive, then read its one reviewed source file."""
    path = _plain_path(Path(archive))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 64 * 1024**2:
            raise ValueError('GATEWAY_REVIEWED_ARCHIVE_REQUIRED')
        raw = stream.read(64 * 1024**2 + 1)
    if sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('GATEWAY_REVIEWED_ARCHIVE_REQUIRED')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as layout:
        with layout.extractfile(PRODUCER_LAYER) as source_layer:
            layer = source_layer.read()
    with tarfile.open(fileobj=io.BytesIO(layer), mode='r:*') as layout:
        with layout.extractfile(PRODUCER_MEMBER) as source:
            producer = source.read(FILE_LIMIT + 1)
    if sha256(producer).hexdigest() != PRODUCER_SHA256:
        raise ValueError('GATEWAY_REVIEWED_PRODUCER_REQUIRED')
    return producer


def _pinned_file(path, expected):
    path = _plain_path(Path(path))
    raw, _ = _read(path)
    if len(raw) > FILE_LIMIT or sha256(raw).hexdigest() != expected:
        raise ValueError('GATEWAY_PUBLISHED_FILE_IDENTITY_MISMATCH')
    return raw


def _published_bundle(path, expected, revocations, issued_at):
    """Use Runtime's existing loaders on the same exact bytes we will mount."""
    from serving_runtime.policy.approval import HashListApprovalVerifier
    from serving_runtime.policy.policy import load_policy
    from serving_runtime.policy.policy_bundle import verify_policy_bundle
    from serving_runtime.policy.policy_state import evaluate_policy, load_revocations
    from serving_runtime.target.target import load_target

    root = _plain_path(Path(path))
    manifest = _pinned_file(root / 'bundle.json', expected)
    document = json.loads(manifest)
    if type(document) is not dict:
        raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
    entries = document.get('files')
    if not isinstance(entries, list) or len(entries) != len(RUNTIME_ROLES):
        raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
    paths = {'bundle.json': manifest}
    for entry in entries:
        if type(entry) is not dict or type(entry.get('path')) is not str:
            raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
        relative = PurePosixPath(entry['path'])
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                or relative.as_posix() != entry['path'] or relative.as_posix() in paths):
            raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
        paths[relative.as_posix()] = _pinned_file(root / relative, entry['sha256'])
    if sum(map(len, paths.values())) > FILE_LIMIT:
        raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
    with TemporaryDirectory(prefix='byul-runtime-gateway-preflight-') as directory:
        snapshot = Path(directory)
        bundle_root = snapshot / 'bundle'
        for name, raw in paths.items():
            member = bundle_root / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(raw)
        revoked = snapshot / 'revocations.json'
        revoked.write_bytes(revocations)
        verified = verify_policy_bundle(bundle_root, now=issued_at)
        roles = verified.role_paths()
        if set(roles) != RUNTIME_ROLES:
            raise ValueError('GATEWAY_PUBLISHED_BUNDLE_SCOPE_INVALID')
        policy_state = load_policy(roles['gateway_policy'])
        policy = policy_state.policy
        revocation = load_revocations(revoked)
        if (not policy_state.ok or policy is None or policy.profile != 'default'
                or revocation.state != 'ok' or policy_state.sha256 != verified.policy_ref['sha256']):
            raise ValueError('GATEWAY_PUBLISHED_POLICY_REQUIRED')
        approval = HashListApprovalVerifier(roles['policy_approvals']).verify(
            Path(roles['gateway_policy']).read_bytes(), policy.policy_id, policy.policy_version,
            policy.approval_ref, now=issued_at)
        if approval.state != 'verified' or not evaluate_policy(
                policy_state, revocation, approval=approval, now=issued_at)['gateway_active']:
            raise ValueError('GATEWAY_PUBLISHED_POLICY_NOT_ACTIVE')
        registration = load_target(roles['target'])
    return paths, policy, registration, policy_state.sha256


def _deployment(template, binding, model, auth_secret, paths, revoked):
    run_id = binding['run_id']
    name, config_name = f'{GATEWAY_ROLE}-{run_id}', f'gateway-inputs-{run_id}'
    if len(name) > 63:
        raise ValueError('GATEWAY_POD_NAME_TOO_LONG')
    namespace = binding['engine']['namespace']
    labels = {'app': GATEWAY_ROLE, 'guard.run': run_id}
    engine = binding['engine']['pod_ip']
    backend = canonical_json({'schema_version': 'runtime-backend/1.0', 'kind': 'vllm',
                              'base_url': f'http://{engine}:8000', 'model_map': {model: model}})
    files = {'backend.json': backend, 'revocations.json': revoked,
             **{f'bundle/{name}': value for name, value in paths.items()}}
    config_data = {f'input-{i}': files[path].decode('utf-8') for i, path in enumerate(sorted(files))}
    configmap = {'apiVersion': 'v1', 'kind': 'ConfigMap',
                 'metadata': {'name': config_name, 'namespace': namespace, 'labels': labels},
                 'immutable': True, 'data': config_data}
    digest = sha256(canonical_json(config_data)).hexdigest()

    source_container, = template['containers']
    mount = '/etc/serving/gateway-inputs'
    container = {
        'name': 'gateway', 'image': GATEWAY_IMAGE, 'imagePullPolicy': 'Never',
        'args': ['mvp', '--backend', 'vllm', '--backend-config', f'{mount}/backend.json',
                 '--policy-bundle', f'{mount}/bundle', '--revocations', f'{mount}/revocations.json',
                 '--auth-config', '/etc/serving/auth/auth.json', '--gateway-port', '8080',
                 '--data-dir', '/srv/serving/runtime/data', '--environment', GATEWAY_ENV, '--run-id', run_id],
        'securityContext': deepcopy(source_container['securityContext']),
        'resources': deepcopy(source_container['resources']),
        'readinessProbe': deepcopy(source_container['readinessProbe']),
        'env': [{'name': 'SERVING_RUNTIME_CACHE_SALT_KEY',
                 'valueFrom': {'secretKeyRef': {'name': auth_secret, 'key': 'cache-salt-key'}}}],
        'volumeMounts': [{'name': 'inputs', 'mountPath': mount, 'readOnly': True},
                         {'name': 'auth', 'mountPath': '/etc/serving/auth', 'readOnly': True},
                         {'name': 'journal', 'mountPath': '/srv/serving/runtime'},
                         {'name': 'tmp', 'mountPath': '/tmp'}],
    }
    security = deepcopy(template['securityContext'])
    security.pop('supplementalGroups', None)
    security['fsGroup'] = security['runAsGroup']
    volumes = [{'name': 'inputs', 'configMap': {'name': config_name,
                'items': [{'key': key, 'path': path} for key, path in zip(config_data, sorted(files))]}},
               {'name': 'auth', 'secret': {'secretName': auth_secret,
                'items': [{'key': 'auth.json', 'path': 'auth.json'}]}},
               {'name': 'journal', 'emptyDir': {'sizeLimit': '128Mi'}},
               {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': namespace, 'labels': labels,
           'annotations': {INPUT_ANNOTATION: digest}}, 'spec': {'restartPolicy': 'Never',
           'automountServiceAccountToken': False, 'enableServiceLinks': False, 'securityContext': security,
           'containers': [container], 'volumes': volumes}}
    isolation = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                 'metadata': {'name': name, 'namespace': namespace, 'labels': labels},
                 'spec': {'podSelector': {'matchLabels': labels}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [],
                          'egress': [{'to': [{'podSelector': {'matchLabels': {'app': 'serving-engine',
                                     'guard.run': run_id}}}], 'ports': [{'port': 8000, 'protocol': 'TCP'}]}]}}
    return {'pod': pod, 'configuration': configmap, 'isolation': isolation}


def prepare_pair(inputs, revocations, revocations_sha256, *, issued_at=None):
    """Validate both target sides and render the existing Gateway in isolated Pods."""
    releases = versions()
    if type(inputs) is not dict or set(inputs) != set(releases):
        raise ValueError('GATEWAY_PAIRED_INPUTS_REQUIRED')
    issued_at = issued_at or utcnow()
    revoked = _pinned_file(revocations, revocations_sha256)
    template_bytes, _ = _read(GATEWAY_TEMPLATE)
    template = yaml.safe_load(template_bytes)['spec']['template']['spec']
    layouts = {}
    models = {}
    generations = {}
    for side, expected in releases.items():
        settings = inputs[side]
        if (type(settings) is not dict or set(settings) != INPUT_FIELDS
                or any(type(value) is not str or not value for value in settings.values())
                or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', settings['auth_secret']) is None):
            raise ValueError('GATEWAY_SIDE_INPUTS_INVALID')
        document_paths = (settings['environment'], settings['target'])
        (document, _), (target, target_ref) = (_source(_plain_path(Path(item))) for item in document_paths)
        env = Environment.model_validate(document)
        transport = _transport(target, env)
        if (not env.hash_ok() or env.data_origin == 'EXAMPLE' or not env.runtime.gpu_count
                or (env.engine.name, env.engine.version, env.engine.image_digest)
                != (expected['engine'], expected['version'], expected['image'])
                or target.get('observed_version') != env.engine.version or transport.endpoint.port != 8000
                or 'kubernetes_gateway' in target
                or any(transport.binding['engine'][key] != transport.binding['probe'][key]
                       for key in ('namespace', 'namespace_uid'))):
            raise ValueError('GATEWAY_SIDE_ENGINE_TARGET_MISMATCH')
        paths, policy, registered, digest = _published_bundle(settings['bundle'], settings['bundle_sha256'],
                                                            revoked, issued_at)
        model = transport.api_model
        if (registered.state != 'ok' or registered.target_id != policy.target_id
                or (registered.engine_kind, registered.engine_version, registered.image_digest,
                    registered.model_id, registered.model_revision)
                != (env.engine.name, env.engine.version, env.engine.image_digest.rsplit('@', 1)[1],
                    model, env.model.revision)
                or list(policy.allowed_models) != [model]):
            raise ValueError('GATEWAY_PUBLISHED_TARGET_MISMATCH')
        models[side] = (model, env.model, digest)
        generations[side] = (transport.binding['context'], transport.binding['run_id'])
        deployments = _deployment(template, transport.binding, model, settings['auth_secret'], paths, revoked)
        layouts[side] = {'environment_ref': env.ref().model_dump(mode='json'), 'engine_target': target_ref,
                         'runtime_policy_sha256': digest, 'runtime_target_id': registered.target_id,
                         'bundle_sha256': settings['bundle_sha256'], 'revocations_sha256': revocations_sha256,
                         'template_sha256': sha256(template_bytes).hexdigest(), 'deployment': deployments}
    if models['baseline'] != models['candidate']:
        raise ValueError('GATEWAY_PAIRED_POLICY_AND_MODEL_REQUIRED')
    if generations['baseline'] == generations['candidate']:
        raise ValueError('GATEWAY_DISTINCT_RUN_LABELS_REQUIRED')
    return layouts


def bind_gateway(target_path, env, prepared, *, namespace, pod, configuration):
    """Attach only the current, separately observed Gateway to the engine target."""
    target, pointer = _source(_plain_path(Path(target_path)))
    if (prepared['environment_ref'] != env.ref().model_dump(mode='json')
            or prepared['engine_target'] != pointer):
        raise ValueError('GATEWAY_PREPARED_ENGINE_TARGET_MISMATCH')
    transport = _transport(target, env)
    expected = prepared['deployment']['pod']
    expected_config = prepared['deployment']['configuration']
    spec, observed = expected['spec'], pod['spec']
    if (namespace['metadata'].get('labels', {}).get('byul.lab/owned') != 'true'
            or namespace['metadata'].get('name') != expected['metadata']['namespace']
            or {x: configuration.get(x) for x in ('kind', 'immutable', 'data')}
            != {x: expected_config[x] for x in ('kind', 'immutable', 'data')}
            or any(configuration['metadata'].get(key) != expected_config['metadata'][key]
                   for key in ('name', 'namespace'))
            or pod['metadata'].get('name') != expected['metadata']['name']
            or pod['metadata'].get('annotations', {}).get(INPUT_ANNOTATION)
            != sha256(canonical_json(expected_config['data'])).hexdigest()
            or any(observed.get(key) != spec[key] for key in ('automountServiceAccountToken', 'enableServiceLinks', 'restartPolicy'))
            or observed.get('initContainers') or observed.get('ephemeralContainers') or observed.get('shareProcessNamespace')):
        raise ValueError('GATEWAY_OBSERVED_CONFIGURATION_MISMATCH')
    actual_container, = observed['containers']
    expected_container, = spec['containers']
    runtime_keys = ('args', 'env', 'image', 'imagePullPolicy', 'securityContext', 'volumeMounts')
    if (actual_container.get('command') or actual_container.get('envFrom')
            or actual_container.get('lifecycle') or actual_container.get('livenessProbe')
            or actual_container.get('startupProbe')
            or any(actual_container.get(key) != expected_container[key] for key in runtime_keys)
            or any(observed['securityContext'].get(key) != value for key, value in spec['securityContext'].items())):
        raise ValueError('GATEWAY_OBSERVED_CONFIGURATION_MISMATCH')
    volumes = {v['name']: v for v in observed['volumes']}
    if len(volumes) != len(spec['volumes']) or any(
            set(volumes.get(v['name'], {})) != set(v)
            or any(any(volumes[v['name']][kind].get(key) != value for key, value in props.items())
                   for kind, props in v.items() if kind != 'name') for v in spec['volumes']):
        raise ValueError('GATEWAY_OBSERVED_CONFIGURATION_MISMATCH')
    binding = _binding(pod, namespace, 'gateway', GATEWAY_ROLE)
    verify_owned_pod(pod, namespace['metadata'], binding, transport.binding['run_id'])
    engine = transport.binding['engine']
    if (binding['namespace'] != engine['namespace'] or binding['namespace_uid'] != engine['namespace_uid']
            or binding['image'] != GATEWAY_IMAGE or binding['restart_count'] != 0
            or binding['image_id'] not in {GATEWAY_IMAGE, f'docker-pullable://{GATEWAY_IMAGE}',
                                           f'sha256:{IMAGE_CONFIG_SHA256}'}
            or any(binding[key] == transport.binding[role][key] for role in ('engine', 'probe')
                   for key in ('pod_uid', 'container_id', 'pod_ip'))):
        raise ValueError('GATEWAY_OBSERVED_OWNER_MISMATCH')
    return {**target, 'kubernetes_gateway': binding}


def _owner_sha256(binding):
    """Match the existing owned capture's verify_owned_pod projection."""
    fields = ('namespace_uid', 'pod_uid', 'container', 'container_id', 'node', 'restart_count', 'started_at')
    owner = {key: binding[key] for key in fields}
    owner.update(transport='kubernetes', image_reference=binding['image'], reported_image_id=binding['image_id'])
    return sha256(canonical_json(owner)).hexdigest()


def _check_observation_times(response, recorded_at, target, prepared):
    def epoch_ns(value):
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            raise ValueError(_OBSERVATION_INVALID)
        delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000

    config = prepared['deployment']['configuration']['data']
    policy_raw, = (raw for raw in config.values() if sha256(raw.encode()).hexdigest() == prepared['runtime_policy_sha256'])
    policy = strict_json(policy_raw.encode(), limit=FILE_LIMIT)
    start, finish = (response[key] for key in ('observed_started_ns', 'observed_finished_ns'))
    valid_from, valid_until = (epoch_ns(policy[key]) for key in ('valid_from', 'valid_until'))
    owner_started = max(epoch_ns(role['started_at'])
                        for role in (target['kubernetes']['engine'], target['kubernetes_gateway']))
    # The Runtime final can be appended after the client receives the response.
    if (type(start) is not int or type(finish) is not int or not 0 < start < finish < 2**63
            or not valid_from <= start < finish < valid_until or owner_started > start
            or not start <= epoch_ns(recorded_at) < valid_until):
        raise ValueError(_OBSERVATION_INVALID)


def _observation_environment(env, target, prepared, observation, expected_sha256):
    """Join existing owned receipts without reading response content or sending requests."""
    if env.data_origin == DataOrigin.EXAMPLE or target.get('synthetic', False) is not False:
        raise ValueError(_OBSERVATION_INVALID)
    selection = strict_json(_pinned_file(observation, expected_sha256), limit=FILE_LIMIT)
    if (type(selection) is not dict or selection.keys() != _OBSERVATION_FILES or any(
            type(row) is not dict or row.keys() != {'path', 'sha256'}
            or type(row['path']) is not str or not row['path']
            or type(row['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
            for row in selection.values())):
        raise ValueError(_OBSERVATION_INVALID)
    inputs = {}
    for key, row in selection.items():
        raw, _ = _read(_plain_path(Path(row['path'])), max_bytes=MAX_BYTES)
        if sha256(raw).hexdigest() != row['sha256']:
            raise ValueError(_OBSERVATION_INVALID)
        inputs[key] = raw
    response, audit = (strict_json(inputs[key], limit=MAX_BYTES) for key in ('response_receipt', 'audit_receipt'))
    journal = inputs['journal']
    gateway_fact = env.facts.get('gateway')
    if gateway_fact is not None and (gateway_fact.value is not None or gateway_fact.conflict):
        raise ValueError('GATEWAY_ENVIRONMENT_FACT_ALREADY_BOUND')
    if (type(response) is not dict or response.keys() != _RESPONSE_FIELDS or type(audit) is not dict
            or audit.keys() != _AUDIT_FIELDS or response['route'] != _ORDINARY_ROUTE or response['status'] != 200
            or type(response['status']) is not int or not isinstance(response['request_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', response['request_id'])
            or type(response['body_bytes']) is not int or not 0 < response['body_bytes'] <= MAX_BYTES
            or not isinstance(response['body_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', response['body_sha256'])
            or audit['owner_before_after_verified'] is not True or audit['event'] != 'ACCESS_ALLOWED'
            or audit['independent_denied_event_observed'] is not False or type(audit['journal_bytes']) is not int
            or audit['journal_bytes'] != len(journal) or audit['journal_sha256'] != selection['journal']['sha256']
            or audit['producer_sha256'] != PRODUCER_SHA256 or not journal.endswith(b'\n')
            or not isinstance(audit['session'], str) or not re.fullmatch('[0-9a-f]{32}', audit['session'])):
        raise ValueError(_OBSERVATION_INVALID)

    run = target['kubernetes']['run_id']
    old_ref = env.ref().model_dump(mode='json')
    target_sha256 = sha256(canonical_json(target)).hexdigest()
    common = {'environment_ref': old_ref, 'request_sha256': sha256(response['request_id'].encode()).hexdigest(),
              'engine_owner_sha256': _owner_sha256(target['kubernetes']['engine']),
              'gateway_owner_sha256': _owner_sha256(target['kubernetes_gateway'])}
    if (any(response[key] != value or audit[key] != value for key, value in common.items())
            or response['run_sha256'] != sha256(run.encode()).hexdigest() or audit['gateway_target_sha256'] != target_sha256
            or audit['runtime_policy_sha256'] != prepared['runtime_policy_sha256']):
        raise ValueError(_OBSERVATION_INVALID)
    matches = [row for row in final_records(journal.decode('utf-8').split('\n'), run_id=run, session=audit['session'])
               if row.get('request_id') == response['request_id']]
    if len(matches) != 1 or matches[0].get('route') != _ORDINARY_ROUTE:
        raise ValueError(_OBSERVATION_INVALID)
    source = {'environment': GATEWAY_ENV, 'target_id': prepared['runtime_target_id'],
              'runtime_policy_sha256': prepared['runtime_policy_sha256'], 'request_sha256': common['request_sha256']}
    _, selected_sha256, recorded_at = snapshot_record(canonical_json(matches[0]) + b'\n', source=source, run_id=run,
        values={'observation_session': audit['session'], 'observation_allowed_actor_sha256': audit['actor_sha256']})
    if (selected_sha256, recorded_at) != (audit['selected_row_sha256'], audit['recorded_at']):
        raise ValueError(_OBSERVATION_INVALID)
    _check_observation_times(response, recorded_at, target, prepared)

    provenance = {**common, 'gateway_target_sha256': target_sha256,
        'runtime_policy_sha256': source['runtime_policy_sha256'], 'route': _ORDINARY_ROUTE, 'recorded_at': recorded_at,
        'observation_sha256': expected_sha256, 'input_sha256': {key: row['sha256'] for key, row in selection.items()}}
    fact = FactValue(value=True, origin=DataOrigin.DECLARED,
                     source='gateway-observation.json#sha256=' + sha256(canonical_json(provenance)).hexdigest())
    result = env.model_copy(update={'id': new_id('ENV'), 'revision': 1, 'created_at': utcnow(),
                                    'facts': {**env.facts, 'gateway': fact}}).seal()
    ref = result.ref().model_dump(mode='json')
    revised = {**target, 'environment_ref': ref, 'model_mapping': {**target['model_mapping'], 'environment_ref': ref}}
    _transport({key: value for key, value in revised.items() if key != 'kubernetes_gateway'}, result)
    return result, revised, provenance, [Path(observation), *(Path(row['path']) for row in selection.values())]


def _prepare(args):
    producer = reviewed_producer(args.archive)
    inputs, _ = _source(_plain_path(args.inputs))
    result = prepare_pair(inputs, args.revocations, args.revocations_sha256)
    protected = [args.inputs, args.archive, args.revocations,
                 *(Path(row[key]) for row in inputs.values() for key in ('environment', 'target', 'bundle'))]
    output = reserve_output(args.output, protected=protected)
    (output / 'request_records.py').write_bytes(producer)
    for side, prepared in result.items():
        (output / f'{side}-gateway.yaml').write_text(yaml.safe_dump_all(prepared['deployment'].values()), encoding='utf-8')
    document = {'image': GATEWAY_IMAGE, 'image_config_sha256': IMAGE_CONFIG_SHA256,
                'archive_sha256': ARCHIVE_SHA256, 'runtime_source_tree': RUNTIME_SOURCE_TREE,
                'producer_sha256': PRODUCER_SHA256, 'gateway_environment': GATEWAY_ENV,
                'live_target_verified': False, 'gateway_records_observed': False, 'sides': result}
    (output / 'preparation.json').write_bytes(canonical_json(document))


def _bind(args):
    receipt, reference = _source(_plain_path(args.preparation))
    if (reference['sha256'] != args.preparation_sha256 or receipt['image'] != GATEWAY_IMAGE
            or receipt['producer_sha256'] != PRODUCER_SHA256 or receipt['gateway_environment'] != GATEWAY_ENV):
        raise ValueError('GATEWAY_PREPARATION_RECEIPT_MISMATCH')
    prepared = receipt['sides'][args.side]
    env = Environment.model_validate(_source(_plain_path(args.environment))[0])
    sources = {key: _source(_plain_path(getattr(args, key)))[0] for key in ('namespace', 'pod', 'configuration')}
    target = bind_gateway(prepared['engine_target']['path'], env, prepared, **sources)
    protected = [args.preparation.parent, args.environment, args.namespace, args.pod, args.configuration,
                 Path(prepared['engine_target']['path'])]
    source = {'environment': GATEWAY_ENV, 'target_id': prepared['runtime_target_id'],
              'runtime_policy_sha256': prepared['runtime_policy_sha256']}
    if args.observation is not None:
        env, target, provenance, observation_files = _observation_environment(
            env, target, prepared, args.observation, args.observation_sha256)
        protected.extend(observation_files)
        source['request_sha256'] = provenance['request_sha256']
    output = reserve_output(args.output, protected=protected)
    if args.observation is not None:
        (output / 'gateway-observation.json').write_bytes(canonical_json(provenance))
        (output / 'environment.json').write_bytes(canonical_json(env.to_dict()))
    (output / 'target.json').write_bytes(canonical_json(target))
    (output / 'gateway-source.json').write_bytes(canonical_json(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare')
    for name in ('inputs', 'archive', 'revocations', 'output'):
        preparation.add_argument('--' + name, type=Path, required=True)
    preparation.add_argument('--revocations-sha256', required=True)
    binding = commands.add_parser('bind')
    binding.add_argument('--side', choices=('baseline', 'candidate'), required=True)
    binding.add_argument('--preparation-sha256', required=True)
    binding.add_argument('--observation', type=Path, help='Pinned response receipt, audit receipt and journal pointers')
    binding.add_argument('--observation-sha256', help='Independent raw SHA-256 of the observation pointers')
    for name in ('preparation', 'environment', 'namespace', 'pod', 'configuration', 'output'):
        binding.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'bind' and (args.observation is None) != (args.observation_sha256 is None):
        parser.error('--observation and --observation-sha256 are required together')
    try:
        (_prepare if args.command == 'prepare' else _bind)(args)
    except (AttributeError, ImportError, KeyError, TypeError, OSError, ValueError) as error:
        reason = str(error)
        parser.error(reason if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', reason) else 'GATEWAY_PREPARATION_INPUT_INVALID')


if __name__ == '__main__':
    main()
