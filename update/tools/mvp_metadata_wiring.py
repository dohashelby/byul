"""Assemble an offline configuration for paired owned Kubernetes metadata checks."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from guard.core.execution_recipe import validate_policy_time
from guard.core.select import _in_scope
from guard.executors import registry, template_supply_approval as template_supply
from guard.models import Environment, Policy, load_pack
from guard.models.artifacts import model_closure_sha256
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.update_validation import reserve_output
from tools import mvp_live_readiness as readiness
from tools.j02_run_config import write_config
from tools.mvp_a_input_preflight import _pairs, _read
from tools.mvp_versions import versions

CHECKS = ('SUP-04', 'PLT-01', 'PLT-04', 'SUP-03')
NODE_IMAGE_PROFILES = ('local-containerd-v1', 'local-k3s-containerd-v1')


def _source(path):
    path = Path(path).absolute()
    raw, _ = _read(path)
    return json.loads(raw, object_pairs_hook=_pairs), {'path': str(path), 'sha256': sha256(raw).hexdigest()}


def _detached_runner(*args, **kwargs):
    raise AssertionError('Wiring validates saved files only')


def _check_reason(check, env, target, transport, values, *, policy):
    if not _in_scope(check.id, policy):
        return 'MVP_POLICY_SCOPE_MISMATCH'
    if any(key.required and key.key not in values for key in check.policy_params):
        return 'MVP_POLICY_VALUE_MISSING'
    try:
        config = {check.executor: {}}
        executor = registry.build_executor(check.executor, 'kubernetes', config,
                                           engine=env.engine.name, version=env.engine.version)
        executor.validate_policy(check, values)
    except (KeyError, TypeError, ValueError):
        return 'METADATA_EXECUTOR_OR_POLICY_UNAVAILABLE'
    if check.id == 'SUP-03':
        factory = registry.adapter_factory(env.engine.name)
        adapter = factory() if factory is not None else None
        ctx = SimpleNamespace(env=env, target=target, adapter=adapter, cprobe=transport, api_model=transport.api_model)
        command = target['engine'].get('command')
        if (type(command) is not list or not command or any(type(arg) is not str or not arg for arg in command)
                or template_supply._target(ctx, kubernetes=True) is None):
            return 'CONFIGURED_TEMPLATE_TARGET_REQUIRED'
    return None


def assemble(inputs, policy_path, *, pack, store, node_image_verification=None, expected=None):
    """Return product TOML settings, hash-pinned index rows and the paired input decisions."""
    if node_image_verification is not None and node_image_verification not in NODE_IMAGE_PROFILES:
        raise ValueError('METADATA_NODE_IMAGE_PROFILE_INVALID')
    expected = versions() if expected is None else expected
    document, policy_pointer = _source(policy_path)
    policy = Policy.model_validate(document)
    if not policy.hash_ok() or policy.pack != pack.ref() or policy.status != 'CONFIRMED' or policy.data_origin == 'EXAMPLE':
        raise ValueError('METADATA_CONFIRMED_POLICY_REQUIRED')
    validate_policy_time(policy)

    sides = {}
    for side in readiness.SIDES:
        env_doc, env_pointer = _source(inputs[side]['environment'])
        env = Environment.model_validate(env_doc)
        engine = expected[side]
        if (not env.hash_ok() or env.data_origin == 'EXAMPLE' or (env.engine.name, env.engine.version, env.engine.image_digest)
                != (engine['engine'], engine['version'], engine['image'])):
            raise ValueError('METADATA_ENV_IDENTITY_MISMATCH')
        if not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver or not env.model.inventory_sha256:
            raise ValueError('METADATA_GPU_AND_MODEL_FACTS_REQUIRED')
        target, target_pointer = _source(inputs[side]['target'])
        try:
            transport = KubernetesHTTP(target, env, duration=1, runner=_detached_runner, profile=REQUEST_BINDING_PROFILE)
        except KubernetesBindingError:
            raise ValueError('METADATA_TARGET_BINDING_INVALID') from None
        if target['lab'] is not False:
            raise ValueError('METADATA_MEASUREMENT_TARGET_REQUIRED')
        sides[side] = env, target, transport, env_pointer, target_pointer

    base, candidate = (sides[side][0] for side in readiness.SIDES)
    if (base.runtime != candidate.runtime or model_closure_sha256(base.model) != model_closure_sha256(candidate.model)
            or base.model.id != candidate.model.id or base.model.revision != candidate.model.revision):
        raise ValueError('METADATA_COMPARISON_CONDITIONS_MISMATCH')
    config = {'store': str(Path(store).absolute()), 'adapter': {'name': base.engine.name},
              'update': {'mode': 'external'}, 'executors': {}, 'sides': {side: {} for side in sides}}
    candidates = {}
    for check_id in CHECKS:
        check = pack.checks[check_id]
        values = policy.values.get(check_id) or {}
        reasons = {side: _check_reason(check, env, target, transport, values, policy=policy)
                   for side, (env, target, transport, _, _) in sides.items()}
        candidates[check_id] = {'configuration_enabled': not any(reasons.values()), 'sides': reasons}
    container_checks = ('SUP-04', 'PLT-01')
    if any(not candidates[cid]['configuration_enabled'] and _in_scope(cid, policy) for cid in container_checks):
        for cid in container_checks:
            entry = candidates[cid]
            if entry['configuration_enabled']:
                entry['configuration_enabled'] = False
                entry['sides'] = dict.fromkeys(sides, 'SHARED_CONTAINER_CHECK_INPUT_UNAVAILABLE')

    bindings = []
    for check_id, decision in candidates.items():
        if not decision['configuration_enabled']:
            continue
        check = pack.checks[check_id]
        config['executors'][check.executor] = 'kubernetes'
        for side, (_, _, _, env_pointer, target_pointer) in sides.items():
            config['sides'][side][check.executor] = {'target': target_pointer['path']}
            bindings.append({'check_id': check_id, 'side': side, 'environment': env_pointer, 'policy': policy_pointer})
        if check_id == 'SUP-04' and node_image_verification is not None:
            config.setdefault('check_config', {})[check_id] = {'node_image_verification': node_image_verification}
    return config, bindings, candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for side in readiness.SIDES:
        parser.add_argument(f'--{side}-environment', type=Path, required=True)
        parser.add_argument(f'--{side}-target', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node-image-verification', choices=NODE_IMAGE_PROFILES)
    args = parser.parse_args()
    inputs = {side: {role: getattr(args, f'{side}_{role}') for role in ('environment', 'target')}
              for side in readiness.SIDES}
    pack = load_pack(readiness.ROOT / 'packs/PACK-BASELINE')
    config, bindings, checks = assemble(inputs, args.policy, pack=pack, store=args.store,
                                        node_image_verification=args.node_image_verification)
    protected = [args.policy, args.store, *(path for side in inputs.values() for path in side.values())]
    output = reserve_output(args.output, protected=protected)
    config_path = output / 'guard.toml'
    write_config(config, config_path)
    cfg_ref = {'path': str(config_path), 'sha256': sha256(config_path.read_bytes()).hexdigest()}
    index = output / 'input-sources.json'
    index.write_text(json.dumps({**readiness.template(pack),
        'inputs': [{**entry, 'configuration': cfg_ref} for entry in bindings]}, indent=2), encoding='utf-8')
    result = readiness.audit(index, pack=pack)
    result['metadata_wiring'] = checks
    (output / 'readiness.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
