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
- J4 excluded OBS-03 from automatic selection while Kubernetes log analysis
  was unavailable. The bounded normal engine collector now takes an explicit
  Kubernetes selection and reports the remaining policy coverage gaps.

Spec (YAML):

    pack: packs/PACK-BASELINE            # optional
    policy: policy.yaml                  # guard policy input (values per check)
    store: .guard-j02                    # optional, written to guard.toml
    adapter: vllm                        # optional
    approved_supply_identity_sha256: <64 lowercase hex> # approved credential identity metadata, not secret bytes
    executors: {supply_boot: kubernetes, ...}   # optional; omit to select Kubernetes automatically
    shared: {<executor>: {...}}          # sections common to both sides
    sides:
      baseline:
        env: baseline-env.json
        model_snapshot: baseline-model/       # RB3: fixed model version, local files only
        model_inventory: baseline-inventory.json # model_inventory.py --scope all output
        cluster: {context: ctx, namespace: serving-lab, render: render.json}   # --discover
        executors: {...}                 # optional per-side sections
        checks:
          LIF-05: {executor: {...}, run_binding: {...}}
      candidate: {...}

Without `executors`, every executor used by a J02 check whose Kubernetes
implementation is registered for both sides' engine/version is selected as
`kubernetes` (2026-11-16 user decision: Kubernetes is the only deployment).
A declared `executors` table is kept exactly. Selection only names an
implementation; missing targets, settings or policy values stay reported.

RB3 draft inputs require both sides' ENV model id/revision/inventory SHA to
agree. Each declared snapshot must reproduce its declared all-scope inventory
and ENV hash. Missing declarations/files are reported, never downloaded. The
default chat template is the tokenizer_config.json chat_template string (or
chat_template.jinja when that field is absent); run observations never select
it. These decisions fill only absent web DRAFT keys and confer no approval.
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
# RB2-J4: explicit selection for the bounded normal Kubernetes collector.
EXPLICIT_KUBERNETES_EXECUTORS = frozenset({'log_analysis'})
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


def kubernetes_executors(pack, environments, checks):
    """{executor: 'kubernetes'} for J02 executors registered for every given side's engine/version.

    Both sides must be declared: a one-sided selection would give the two
    sides different executor sets. OBS-03 `log_analysis` is opt-in.
    """
    from guard.executors import registry
    if len(environments) != len(SIDES):
        return {}
    names = sorted({pack.checks[cid].executor for cid in checks
                    if cid in pack.checks and pack.checks[cid].executor
                    and pack.checks[cid].executor not in EXPLICIT_KUBERNETES_EXECUTORS})
    return {name: 'kubernetes' for name in names
            if all(registry.resolve_executor(name, 'kubernetes', engine=env.engine.name,
                                             version=env.engine.version) is not None for env in environments)}


def build_config(spec, targets=None, executors=None):
    """guard.toml content (dict) from the spec; sides carry their own check tables.

    `targets` ({side: path}) from --discover fills `target` of every selected
    executor section that takes one, unless the spec already sets it.
    `executors` (automatic selection) is used only when the spec declares none.
    """
    chosen = spec.get('executors') if spec.get('executors') is not None else executors
    config = {'store': spec.get('store', '.guard-j02'), 'adapter': {'name': spec.get('adapter', 'vllm')},
              'executors': dict(chosen or {}), 'update': {'mode': 'external'}}
    for name, section in (spec.get('shared') or {}).items():
        config[name] = deepcopy(section)
    sides = {}
    for side in SIDES:
        declared = (spec.get('sides') or {}).get(side) or {}
        out = deepcopy(declared.get('executors') or {})
        if (targets or {}).get(side):
            for name in sorted(config['executors']):
                if (name in NO_TARGET or name in EXPLICIT_KUBERNETES_EXECUTORS
                        and config['executors'][name] != 'kubernetes'):
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


# RB3-D1 (2026-11-15 user decision): "허용 호출자에게 test-model 현재 판본만, 퇴역 판본 없음, 승인 자격 하나".
# The principals are the REQ-05 executor's two test roles; their fingerprints are labels, never a verified identity
# (the executor keeps principal_identity_verified false).
REQ05_PRINCIPALS = {'allowed': 'byul-j02-req05-allowed', 'denied': 'byul-j02-req05-denied'}
REQ05_DECISION = 'RB3-D1 사용자 결정(2026-11-15): 허용 호출자에게 현재 모델 판본만, 퇴역 판본 없음, 승인 자격 하나'


def _sha(text):
    from guard.models import sha256_hex
    return sha256_hex(text.encode())


def decided_values(document, environments):
    """Fill REQ-05 from the RB3-D1 decision where the document has no value; returns what was filled.

    The grant names the model of the given environments, which must agree; web values are never overwritten.
    """
    models = {(env.model.id, env.model.revision) for env in environments}
    if len(models) != 1 or not all(type(v) is str and v for v in next(iter(models))):
        raise ValueError('J02_REQ05_MODEL_NOT_UNIQUE')
    (model, revision), = models
    allowed, denied = (_sha('principal:' + REQ05_PRINCIPALS[r]) for r in ('allowed', 'denied'))
    decided = {'allowed_principal_sha256': allowed, 'denied_principal_sha256': denied,
               'selected_model_grants': [f'{allowed}:{_sha(model)}:{_sha(revision)}'], 'retired_model_revisions': []}
    row = document['values'].setdefault('REQ-05', {})
    filled = sorted(k for k in decided if k not in row)
    row.update({k: decided[k] for k in filled})
    return {'REQ-05': {'keys': filled, 'basis': REQ05_DECISION}} if filled else {}


RB3_TEMPLATE_BASIS = 'RB3-Q1 사용자 결정(2026-11-15): 고정 모델 판본 기본 chat template, J02 lab DRAFT 전용'
RB3_SUPPLY_BASIS = 'RB3-Q2 사용자 결정(2026-11-15): ENV 모델 inventory 파일 SHA-256 목록·별도 선언된 승인 공급 자격 신원 하나, J02 lab DRAFT 전용'


def _fixed_inventory(snapshot, inventory_file, env):
    """Verify declared all-scope inventory against the exact local snapshot and ENV.

    Return (file rows, template digest, gap). A gap is a source-input gap, not a
    policy verdict. File reads never use an observed run's selected template.
    """
    import hashlib
    from guard.adapters.model_inventory import inventory
    from guard.models import canonical_json, sha256_hex
    if not snapshot or not inventory_file:
        return None, None, 'RB3_MODEL_SOURCE_NOT_DECLARED'
    snapshot, inventory_file = Path(snapshot), Path(inventory_file)
    if not snapshot.is_dir() or not inventory_file.is_file():
        return None, None, 'RB3_MODEL_SOURCE_MISSING'
    try:
        declared = json.loads(inventory_file.read_text(encoding='utf-8'))
        if type(declared) is not dict or type(declared.get('files')) is not list:
            return None, None, 'RB3_MODEL_SOURCE_INVALID'
        if any(type(row) is not dict or type(row.get('path')) is not str
               or Path(row['path']).is_absolute() or '..' in Path(row['path']).parts
               for row in declared['files']):
            return None, None, 'RB3_MODEL_SOURCE_INVALID'
        if any(not (snapshot / row['path']).is_file() for row in declared['files']):
            return None, None, 'RB3_MODEL_INVENTORY_FILE_MISSING'
        actual = inventory(snapshot, 'all')
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return None, None, 'RB3_MODEL_SOURCE_INVALID'
    if (type(declared) is not dict or declared.get('scope') != 'all' or declared != actual
            or actual['inventory_sha256'] != env.model.inventory_sha256
            or actual['inventory_sha256'] != sha256_hex(canonical_json(actual['files']))):
        return None, None, 'RB3_MODEL_INVENTORY_MISMATCH'
    rows = actual['files']
    names = {row['path'] for row in rows}
    hashes = {row['path']: row['sha256'] for row in rows}
    try:
        if 'tokenizer_config.json' in names:
            raw = (snapshot / 'tokenizer_config.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() != hashes['tokenizer_config.json']:
                return None, None, 'RB3_MODEL_SOURCE_CHANGED'
            config = json.loads(raw)
            template = config.get('chat_template') if type(config) is dict else None
        else:
            template = None
        if template is None and 'chat_template.jinja' in names:
            raw = (snapshot / 'chat_template.jinja').read_bytes()
            if hashlib.sha256(raw).hexdigest() != hashes['chat_template.jinja']:
                return None, None, 'RB3_MODEL_SOURCE_CHANGED'
            template = raw.decode('utf-8')
    except (OSError, ValueError, UnicodeError):
        return rows, None, 'RB3_DEFAULT_TEMPLATE_INVALID'
    if type(template) is not str or not template:
        return rows, None, 'RB3_DEFAULT_TEMPLATE_MISSING'
    return rows, sha256_hex(template.encode('utf-8')), None


def rb3_draft_values(document, environments, side_sources, *, base=Path('.'), approved_supply_identity_sha256=None):
    """Fill absent RB3-Q1/Q2 keys in web DRAFT; return (filled, source gaps).

    `side_sources` maps baseline/candidate to `model_snapshot` and
    `model_inventory` paths declared under `sides` in the J02 spec. No
    observation, selected template, or generated credential is an input.
    `approved_supply_identity_sha256` is one explicitly declared nonsecret
    fingerprint of an approved credential/identity, never credential bytes or
    the REQ-05 synthetic caller role label. It does not authenticate approval.
    """
    if (document.get('status') != 'DRAFT' or document.get('confirmed_by') is not None
            or document.get('confirmed_at') is not None):
        raise ValueError('RB3_DRAFT_POLICY_REQUIRED')
    models = [(env.model.id, env.model.revision, env.model.inventory_sha256) for env in environments]
    if len(models) != len(SIDES) or len(set(models)) != 1 or any(type(v) is not str or not v for v in models[0]):
        return {}, {'both': ['RB3_ENV_MODEL_NOT_UNIQUE']}
    sources, gaps = {}, {}
    for side, env in zip(SIDES, environments):
        declared = side_sources.get(side) or {}
        snapshot = declared.get('model_snapshot')
        inventory_file = declared.get('model_inventory')
        rows, template, gap = _fixed_inventory(base / snapshot if snapshot else None,
                                               base / inventory_file if inventory_file else None, env)
        sources[side] = (rows, template)
        if gap:
            gaps[side] = [gap]
    filled = {}
    # Q2 needs a separate operator declaration: the REQ-05 caller role label
    # does not identify a supply credential. Never infer it from run output.
    approved = approved_supply_identity_sha256
    if type(approved) is str and re.fullmatch(r'[0-9a-f]{64}', approved):
        row = document['values'].setdefault('PLT-05', {})
        if 'supply_allowed_identities' not in row:
            row['supply_allowed_identities'] = [approved]
            filled['PLT-05'] = {'keys': ['supply_allowed_identities'], 'basis': RB3_SUPPLY_BASIS}
    else:
        gaps.setdefault('both', []).append('RB3_APPROVED_SUPPLY_IDENTITY_NOT_DECLARED' if approved is None
                                           else 'RB3_APPROVED_SUPPLY_IDENTITY_INVALID')
    if any(rows is None for rows, _ in sources.values()):
        return filled, gaps
    baseline_rows, baseline_template = sources['baseline']
    candidate_rows, candidate_template = sources['candidate']
    if baseline_rows != candidate_rows:
        gaps.setdefault('both', []).append('RB3_MODEL_INVENTORIES_DIFFER')
        return filled, gaps
    row = document['values'].setdefault('PLT-03', {})
    if 'supply_allowed_objects' not in row:
        row['supply_allowed_objects'] = sorted({entry['sha256'] for entry in baseline_rows})
        filled['PLT-03'] = {'keys': ['supply_allowed_objects'], 'basis': RB3_SUPPLY_BASIS}
    if baseline_template is None or candidate_template is None:
        return filled, gaps
    if baseline_template != candidate_template:
        gaps.setdefault('both', []).append('RB3_DEFAULT_TEMPLATES_DIFFER')
        return filled, gaps
    for cid in ('REQ-07', 'SUP-03'):
        row = document['values'].setdefault(cid, {})
        if 'allowed_template_sha256' not in row:
            row['allowed_template_sha256'] = [baseline_template]
            filled[cid] = {'keys': ['allowed_template_sha256'], 'basis': RB3_TEMPLATE_BASIS}
    return filled, gaps


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

def _version_gap(executor, env):
    """Recorded cause (release data) for an engine-bound executor the release does not admit."""
    from guard.adapters.engine_provider import PROVIDERS, load_installed_providers
    from guard.executors import registry
    if env is None:
        return None
    load_installed_providers()
    provider = PROVIDERS.get(env.engine.name)
    if provider is None or registry.resolve_executor(executor, 'kubernetes', engine=env.engine.name,
                                                     version=env.engine.version) is not None:
        return None
    return provider.executor_gap(env.engine.version, executor)


def _row(pack, check_id, side, cfg, env, values, *, policy_ref=None):
    from guard.configuration import for_check
    from guard.executors import registry, run_binding
    check = pack.checks[check_id]
    row = {'check_id': check_id, 'side': side, 'executor': check.executor,
           'implementation': cfg.get('executors', {}).get(check.executor), 'status': None, 'reasons': [],
           'run_binding_entries': sorted(run_binding.config_keys(check)),
           'policy_keys_missing': sorted(p.key for p in check.policy_params if p.required and p.key not in values)}
    gap = _version_gap(check.executor, env)
    if row['implementation'] is None:
        if check.executor in EXPLICIT_KUBERNETES_EXECUTORS:
            registered = env is not None and registry.resolve_executor(
                check.executor, 'kubernetes', engine=env.engine.name, version=env.engine.version) is not None
            row['kubernetes_registered'] = registered
            if registered:
                row['status'], row['reasons'] = 'MISSING_INPUT', ['EXECUTOR_EXPLICIT_OPT_IN_REQUIRED']
            else:
                row['status'], row['reasons'] = 'UNKNOWN_RECORDED', ['KUBERNETES_LOG_ANALYSIS_IMPLEMENTATION_ABSENT']
        elif gap is not None:
            # The release does not admit this executor; selecting one would not run it.
            row['status'], row['reasons'], row['version_gap'] = 'UNSUPPORTED', ['EXECUTOR_NOT_REGISTERED_FOR_ENGINE_VERSION'], gap
        else:
            row['status'], row['reasons'] = 'MISSING_INPUT', ['EXECUTOR_IMPLEMENTATION_NOT_SELECTED']
            row['kubernetes_registered'] = registry.resolve_executor(
                check.executor, 'kubernetes', engine=env.engine.name, version=env.engine.version) is not None
        return row
    factory = registry.resolve_executor(check.executor, row['implementation'], engine=env.engine.name,
                                        version=env.engine.version) if env is not None else None
    if factory is None:
        row['status'], row['reasons'] = 'UNSUPPORTED', ['EXECUTOR_NOT_REGISTERED_FOR_ENGINE_VERSION']
        if gap is not None:
            row['version_gap'] = gap
        return row
    if row['policy_keys_missing']:
        row['reasons'].append('POLICY_VALUES_MISSING')
    try:
        merged = for_check(cfg, check.executor, check_id)
        effective = values
        if run_binding.keys_for(check):
            _, effective = run_binding.effective_values(
                check, merged.get(check.executor, {}), env, values, side=side, attempt_id=None,
                issued_at=datetime.now(timezone.utc), run_cfg=run_binding.run_config(merged, check_id),
                policy_ref=policy_ref)
        if not row['policy_keys_missing']:
            executor = registry.build_executor(check.executor, row['implementation'], merged, factory=factory)
            executor.validate_policy(check, effective)
    except ValueError as exc:
        code = str(exc)
        row['reasons'].append(code if re.fullmatch(r'[A-Z0-9_]{3,80}', code) else 'INPUT_INVALID')
    except Exception as exc:  # never copy messages that may carry paths or values
        row['reasons'].append('INPUT_CHECK_FAILED:' + type(exc).__name__)
    row['status'] = 'READY_STATIC' if not row['reasons'] else 'MISSING_INPUT'
    return row


def report(spec, config, *, plan_file=PLAN, base=Path('.'), policy_document=None):
    from guard.collectors.file import load_environment, load_policy
    from guard.configuration import merge_config
    from guard.models import load_pack
    pack = load_pack(base / spec.get('pack', ROOT / 'packs/PACK-BASELINE'))
    policy_path = spec.get('policy')
    document = (policy_document if policy_document is not None else
                yaml.safe_load((base / policy_path).read_text(encoding='utf-8')) if policy_path else {})
    values = (document or {}).get('values') or {}
    if (document or {}).get('pack', {}).get('revision') not in (None, pack.manifest.revision):
        raise ValueError('J02_POLICY_PACK_REVISION_MISMATCH')
    # A shorthand DRAFT policy gets a fresh random POL id on each load. It has
    # no stable current Ref to bind here; only an already sealed POL does.
    policy_ref = (load_policy(base / policy_path, pack).ref()
                  if policy_path and policy_document is None and (document or {}).get('kind') == 'POL' else None)
    rows = []
    for side in SIDES:
        env_path = ((spec.get('sides') or {}).get(side) or {}).get('env')
        env = load_environment(base / env_path) if env_path else None
        cfg = merge_config(config, config['sides'][side])
        for check_id in j02_checks(plan_file):
            if env is None:
                rows.append({'check_id': check_id, 'side': side, 'status': 'MISSING_INPUT', 'reasons': ['ENV_NOT_DECLARED']})
                continue
            rows.append(_row(pack, check_id, side, cfg, env, values.get(check_id) or {}, policy_ref=policy_ref))
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
    policy_document, dropped, decided, rb3_gaps = None, {}, {}, {}
    if args.web_values is not None:
        pack = load_pack(base / spec.get('pack', ROOT / 'packs/PACK-BASELINE'))
        policy_document, dropped = policy_from_web_values(json.loads(args.web_values.read_text(encoding='utf-8')), pack)
        if 'REQ-05' in pack.checks and {'selected_model_grants'} <= {q.key for q in pack.checks['REQ-05'].policy_params}:
            environments = [load_environment(base / ((spec.get('sides') or {}).get(side) or {})['env'])
                            for side in SIDES if ((spec.get('sides') or {}).get(side) or {}).get('env')]
            decided = decided_values(policy_document, environments)
            rb3_filled, rb3_gaps = rb3_draft_values(
                policy_document, environments, spec.get('sides') or {}, base=base,
                approved_supply_identity_sha256=spec.get('approved_supply_identity_sha256'))
            decided.update(rb3_filled)
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
    auto = None
    if spec.get('executors') is None:
        pack = load_pack(base / spec.get('pack', ROOT / 'packs/PACK-BASELINE'))
        environments = [load_environment(base / ((spec.get('sides') or {}).get(side) or {})['env'])
                        for side in SIDES if ((spec.get('sides') or {}).get(side) or {}).get('env')]
        auto = kubernetes_executors(pack, environments, j02_checks(args.plan))
    config = build_config(spec, targets, auto)
    result = report(spec, config, plan_file=args.plan, base=base, policy_document=policy_document)
    result['executors_selected'] = 'declared' if auto is None else 'kubernetes_auto'
    result['web_values_dropped'] = dropped
    result['decided_values'] = decided
    result['rb3_source_gaps'] = rb3_gaps
    result['discovered'] = discovered
    write_config(config, args.out / 'guard.toml')
    (args.out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
