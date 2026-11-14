"""Assemble one `guard update` run config for the J02 checks from operator declarations.

The operator declares, per side, the ENV file, the executor implementations,
the shared executor sections and, per check, the executor settings that
differ (`[check_config.<CHECK>]`, RB2-S) and the run-preparation values
(`[run_binding.<CHECK>]`, RB2).  This tool only writes them as guard.toml and
reports, for every J02 check of the MA7 plan, what the existing product code
says about them statically:

- is an executor implementation selected and registered for that side,
- does the per-run binding compute from the declared files and values
  (`run_binding.effective_values`, the same call `guard run` makes),
- does the executor accept the policy values with the merged settings
  (`validate_policy`, the same call static preparation makes).

It never fills a missing value and never turns a report row into a PASS: a
READY_STATIC row only means the declared inputs are well-formed for that
executor.

User decisions RB2-J1~J4 (2026-11-14):
- J1 `--web-values FILE`: the platform web DRAFT values (j02-web-input) become a
  PACK-BASELINE@36 policy input with status DRAFT (official=false).  Keys the
  pack now leaves to the run binding, or no longer declares, are dropped and
  listed; nothing is added.
- J2 `--discover`: per side, `kubectl get` (read-only; no exec, apply or
  delete; the argv is checked) reads the namespace and the run's engine/probe
  Pods by the render record's labels, keeps the JSON snapshots and builds the
  side's Kubernetes target with the existing builder
  (`gpu_a_run_inputs.target`).  The target path is written into every selected
  executor section that takes a `target` unless the spec already sets one.
- J3 the first-scope UNKNOWN checks stay in the J02 config.
- J4 OBS-03 keeps no executor (Kubernetes `log_analysis` does not exist); the
  report records that cause and `guard run` leaves it NOT_RUN -> UNKNOWN.

Spec (YAML):

    pack: packs/PACK-BASELINE            # optional
    policy: policy.yaml                  # guard policy input (values per check)
    store: .guard-j02                    # optional, written to guard.toml
    adapter: vllm                        # optional
    executors: {supply_boot: kubernetes, ...}
    shared: {<executor>: {...}}          # sections common to both sides
    sides:
      baseline:
        env: baseline-env.json
        cluster: {context: ctx, namespace: serving-lab, render: render.json}   # --discover
        executors: {...}                 # optional per-side sections
        checks:
          LIF-05: {executor: {...}, run_binding: {...}}
      candidate: {...}
"""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/dev-1/ma7-session-input-plan.json'
SIDES = ('baseline', 'candidate')
_BARE = re.compile(r'^[A-Za-z0-9_-]+$')
# RB2-J4: recorded cause for checks left without an executor.
RECORDED_UNKNOWN = {'OBS-03': 'KUBERNETES_LOG_ANALYSIS_IMPLEMENTATION_ABSENT'}
RECORDED_EXECUTORS = frozenset({'log_analysis'})
# Kubernetes sections whose settings do not take a single `target` (they use
# per-role, per-generation or identity/profiler targets).
NO_TARGET = frozenset({'lifecycle_transition', 'observation_state', 'supply_boot'})
_K8S_NAME = re.compile(r'^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$')


def j02_checks(plan_file=PLAN):
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
    return [row['check_id'] for row in plan['checks'] if row['session'] == 'J02']


# --- TOML (only str/int/float/bool/list/dict; verified by re-parsing) -------------------------

def _key(key):
    return key if _BARE.match(key) else json.dumps(key, ensure_ascii=False)


def _value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return '[' + ', '.join(_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(f'{_key(k)} = {_value(v)}' for k, v in value.items()) + ' }'
    raise ValueError('J02_CONFIG_VALUE_UNSUPPORTED')


def dumps_toml(data, prefix=()):
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    out = []
    if prefix and (scalars or not tables):
        out.append('[' + '.'.join(_key(k) for k in prefix) + ']')
    out += [f'{_key(k)} = {_value(v)}' for k, v in scalars.items()]
    if out:
        out.append('')
    for key, table in tables.items():
        out.append(dumps_toml(table, prefix + (key,)))
    return '\n'.join(part for part in out if part is not None)


def build_config(spec, targets=None):
    """guard.toml content (dict) from the spec; sides carry their own check tables.

    `targets` ({side: path}) from --discover fills `target` of every selected
    executor section that takes one, unless the spec already sets it.
    """
    config = {'store': spec.get('store', '.guard-j02'), 'adapter': {'name': spec.get('adapter', 'vllm')},
              'executors': dict(spec.get('executors') or {}), 'update': {'mode': 'external'}}
    for name, section in (spec.get('shared') or {}).items():
        config[name] = deepcopy(section)
    sides = {}
    for side in SIDES:
        declared = (spec.get('sides') or {}).get(side) or {}
        out = deepcopy(declared.get('executors') or {})
        if (targets or {}).get(side):
            for name in sorted(config['executors']):
                if name in NO_TARGET or name in RECORDED_EXECUTORS:
                    continue
                if 'target' not in (config.get(name) or {}) and 'target' not in (out.get(name) or {}):
                    out.setdefault(name, {})['target'] = str(targets[side])
        for cid, entry in (declared.get('checks') or {}).items():
            if entry.get('executor'):
                out.setdefault('check_config', {})[cid] = deepcopy(entry['executor'])
            if entry.get('run_binding'):
                out.setdefault('run_binding', {})[cid] = deepcopy(entry['run_binding'])
        sides[side] = out
    config['sides'] = sides
    return config


def write_config(config, path):
    text = dumps_toml(config)
    if tomllib.loads(text) != config:
        raise ValueError('J02_CONFIG_TOML_ROUNDTRIP_FAILED')
    Path(path).write_text(text, encoding='utf-8')
    return text


# --- J1: web DRAFT values -> @36 policy input ------------------------------------------------

def policy_from_web_values(values, pack, *, data_origin='DECLARED'):
    """(policy document, dropped keys) for the given pack; status DRAFT, never confirmed."""
    from guard.executors import run_binding
    if type(values) is not dict or any(type(v) is not dict for v in values.values()):
        raise ValueError('J02_WEB_VALUES_INVALID')
    out, dropped = {}, {}
    for cid, row in sorted(values.items()):
        check = pack.checks.get(cid)
        if check is None:
            dropped[cid] = sorted(row)
            continue
        declared = {p.key for p in check.policy_params}
        owned = run_binding.owned_keys(check)
        keep = {k: deepcopy(v) for k, v in row.items() if k in declared and k not in owned}
        gone = sorted(set(row) - set(keep))
        if gone:
            dropped[cid] = gone
        out[cid] = keep
    document = {'data_origin': data_origin, 'status': 'DRAFT',
                'pack': {'id': pack.manifest.pack_id, 'revision': pack.manifest.revision},
                'values': out, 'scope': {'include': [], 'exclude': []}, 'exceptions': [],
                'confirmed_by': None, 'confirmed_at': None}
    return document, dropped


# --- J2: read-only kubectl get ---------------------------------------------------------------

def _kubectl(argv, runner=None):
    """Run one read-only `kubectl ... get ... -o json`; refuse anything else."""
    import subprocess
    if (argv[:1] != ['kubectl'] or 'get' not in argv or argv[-2:] != ['-o', 'json']
            or any(word in argv for word in ('exec', 'apply', 'delete', 'patch', 'edit', 'create', 'replace',
                                              'scale', 'label', 'annotate', 'cp', 'port-forward', '--raw'))):
        raise ValueError('J02_KUBECTL_NOT_READ_ONLY')
    if runner is not None:
        return runner(argv)
    done = subprocess.run(argv, capture_output=True, timeout=60, check=False)
    if done.returncode != 0:
        raise ValueError('J02_KUBECTL_GET_FAILED')
    return json.loads(done.stdout)


def discover_side(cluster, env, render, *, out_dir, runner=None):
    """Snapshot the run's namespace and engine/probe Pods and build that side's target file."""
    from guard.models import canonical_json, sha256_hex
    from tools import gpu_a_run_inputs
    context, namespace = cluster.get('context'), cluster.get('namespace')
    if type(context) is not str or not context or type(namespace) is not str or not _K8S_NAME.match(namespace):
        raise ValueError('J02_CLUSTER_DECLARATION_INVALID')
    selector = f"guard.run={render['run_id']},byul.a/generation={render['generation']}"
    base = ['kubectl', '--context', context]
    namespace_json = _kubectl(base + ['get', 'namespace', namespace, '-o', 'json'], runner)
    pods = _kubectl(base + ['-n', namespace, 'get', 'pods', '-l', selector, '-o', 'json'], runner)
    def one(app):
        found = [p for p in pods.get('items', []) if p['metadata'].get('labels', {}).get('app') == app]
        if len(found) != 1:
            raise ValueError('J02_POD_NOT_UNIQUE:' + app)
        return found[0]
    engine, probe = one('serving-engine'), one('engine-test-probe')
    value = gpu_a_run_inputs.target(env, render, namespace_json, engine, probe, context=context,
                                    api_model=cluster.get('api_model', 'test-model'))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    for name, data in (('namespace.json', namespace_json), ('pods.json', pods), ('target.json', value)):
        (out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out_dir / 'target.json', sha256_hex(canonical_json(value))


# --- static report ---------------------------------------------------------------------------

def _row(pack, check_id, side, cfg, env, values):
    from guard.configuration import for_check
    from guard.executors import registry, run_binding
    check = pack.checks[check_id]
    row = {'check_id': check_id, 'side': side, 'executor': check.executor,
           'implementation': cfg.get('executors', {}).get(check.executor), 'status': None, 'reasons': [],
           'run_binding_entries': sorted(run_binding.CONFIG_KEYS.get(check_id, ())) if 2 in run_binding.stages_for(check) else [],
           'policy_keys_missing': sorted(p.key for p in check.policy_params if p.required and p.key not in values)}
    if row['implementation'] is None:
        if check_id in RECORDED_UNKNOWN:
            row['status'], row['reasons'] = 'UNKNOWN_RECORDED', [RECORDED_UNKNOWN[check_id]]
        else:
            row['status'], row['reasons'] = 'MISSING_INPUT', ['EXECUTOR_IMPLEMENTATION_NOT_SELECTED']
        return row
    factory = registry.resolve_executor(check.executor, row['implementation'], engine=env.engine.name,
                                        version=env.engine.version) if env is not None else None
    if factory is None:
        row['status'], row['reasons'] = 'UNSUPPORTED', ['EXECUTOR_NOT_REGISTERED_FOR_ENGINE_VERSION']
        return row
    if row['policy_keys_missing']:
        row['reasons'].append('POLICY_VALUES_MISSING')
    try:
        merged = for_check(cfg, check.executor, check_id)
        effective = values
        if run_binding.keys_for(check):
            _, effective = run_binding.effective_values(
                check, merged.get(check.executor, {}), env, values, side=side, attempt_id=None,
                issued_at=datetime.now(timezone.utc), run_cfg=run_binding.run_config(merged, check_id))
        if not row['policy_keys_missing']:
            factory(merged).validate_policy(check, effective)
    except ValueError as exc:
        code = str(exc)
        row['reasons'].append(code if re.fullmatch(r'[A-Z0-9_]{3,80}', code) else 'INPUT_INVALID')
    except Exception as exc:  # never copy messages that may carry paths or values
        row['reasons'].append('INPUT_CHECK_FAILED:' + type(exc).__name__)
    row['status'] = 'READY_STATIC' if not row['reasons'] else 'MISSING_INPUT'
    return row


def report(spec, config, *, plan_file=PLAN, base=Path('.'), policy_document=None):
    from guard.collectors.file import load_environment
    from guard.configuration import merge_config
    from guard.models import load_pack
    pack = load_pack(base / spec.get('pack', ROOT / 'packs/PACK-BASELINE'))
    policy_path = spec.get('policy')
    document = (policy_document if policy_document is not None else
                yaml.safe_load((base / policy_path).read_text(encoding='utf-8')) if policy_path else {})
    values = (document or {}).get('values') or {}
    if (document or {}).get('pack', {}).get('revision') not in (None, pack.manifest.revision):
        raise ValueError('J02_POLICY_PACK_REVISION_MISMATCH')
    rows = []
    for side in SIDES:
        env_path = ((spec.get('sides') or {}).get(side) or {}).get('env')
        env = load_environment(base / env_path) if env_path else None
        cfg = merge_config(config, config['sides'][side])
        for check_id in j02_checks(plan_file):
            if env is None:
                rows.append({'check_id': check_id, 'side': side, 'status': 'MISSING_INPUT', 'reasons': ['ENV_NOT_DECLARED']})
                continue
            rows.append(_row(pack, check_id, side, cfg, env, values.get(check_id) or {}))
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'schema': 'guard-j02-run-config-report/1', 'pack': pack.ref().model_dump(mode='json'),
            'checks': len(j02_checks(plan_file)), 'counts': counts, 'rows': rows,
            'policy_status': (document or {}).get('status'), 'official': False,
            'limitations': ['static only: no Docker, engine or GPU execution; kubectl only as read-only get with --discover',
                            'READY_STATIC is not a verdict, approval or preflight pass']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--out', type=Path, required=True, help='new directory for guard.toml and report.json')
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--web-values', type=Path, help='J1: platform web DRAFT verification values (JSON)')
    parser.add_argument('--discover', action='store_true', help='J2: read-only kubectl get per side')
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit('output exists; choose a new path')
    from guard.collectors.file import load_environment
    from guard.models import load_pack
    spec = yaml.safe_load(args.spec.read_text(encoding='utf-8'))
    base = args.spec.parent
    args.out.mkdir(parents=True)
    policy_document, dropped = None, {}
    if args.web_values is not None:
        pack = load_pack(base / spec.get('pack', ROOT / 'packs/PACK-BASELINE'))
        policy_document, dropped = policy_from_web_values(json.loads(args.web_values.read_text(encoding='utf-8')), pack)
        (args.out / 'policy.yaml').write_text(yaml.safe_dump(policy_document, allow_unicode=True, sort_keys=False),
                                              encoding='utf-8')
    targets, discovered = {}, {}
    if args.discover:
        for side in SIDES:
            declared = (spec.get('sides') or {}).get(side) or {}
            cluster = declared.get('cluster')
            if not cluster:
                continue
            render = json.loads((base / cluster['render']).read_text(encoding='utf-8'))
            path, digest = discover_side(cluster, load_environment(base / declared['env']), render,
                                         out_dir=args.out / 'cluster' / side)
            targets[side] = path.resolve()
            discovered[side] = {'target': str(path), 'target_sha256': digest}
    config = build_config(spec, targets)
    result = report(spec, config, plan_file=args.plan, base=base, policy_document=policy_document)
    result['web_values_dropped'] = dropped
    result['discovered'] = discovered
    write_config(config, args.out / 'guard.toml')
    (args.out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
