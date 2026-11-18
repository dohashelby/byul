"""Prepare the pinned BYUL Gateway for paired, ordinary request observations.

Reads saved inputs and writes run-local manifests. It does not contact AWS,
Kubernetes, the Gateway or the engine, or issue policy/credential material.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
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

from guard.models import Environment, canonical_json, utcnow
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
    output = reserve_output(args.output, protected=[args.preparation.parent, args.environment, args.namespace,
        args.pod, args.configuration, Path(prepared['engine_target']['path'])])
    (output / 'target.json').write_bytes(canonical_json(target))
    (output / 'gateway-source.json').write_bytes(canonical_json({
        'environment': GATEWAY_ENV, 'target_id': prepared['runtime_target_id'],
        'runtime_policy_sha256': prepared['runtime_policy_sha256'],
    }))


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
    for name in ('preparation', 'environment', 'namespace', 'pod', 'configuration', 'output'):
        binding.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        (_prepare if args.command == 'prepare' else _bind)(args)
    except (AttributeError, ImportError, KeyError, TypeError, OSError, ValueError) as error:
        reason = str(error)
        parser.error(reason if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', reason) else 'GATEWAY_PREPARATION_INPUT_INVALID')


if __name__ == '__main__':
    main()
