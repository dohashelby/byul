"""Run one owned native fault, then close four independently prepared cases.

The controller prepares a fresh held container/ENV/target/session for each kind.
This tool runs the actor before any passive consumer seals that session. Plan
closure is read-only and delegates every source judgment to the C24 validator.
It returns existing activation observations for manual-import/judge/report.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re

from guard.executors import lab_activation as actor
from guard.models import Environment, Policy, canonical_json
from guard.runtime.bounded import strict_json

KINDS = frozenset({'death', 'delay', 'communication-stall', 'exception'})
CASE_SCHEMA = 'byul-lab-native-fault-case/1'
PLAN_SCHEMA = 'byul-lab-native-fault-plan/1'
PROFILE = 'vllm-native-lifecycle-metadata/24'
FLAGS = ('occurrence_verified', 'request_fault_overlap_verified', 'detection_deadline_verified',
         'upper_notification_deadline_verified', 'affected_request_error_verified', 'clock_verified', 'whole_kind_complete')
SCOPE_FIELDS = frozenset({'source_profile', 'actor_sha256', 'consumer_sha256', 'declared_source_commit',
    'policy_sha256', 'policy_values_sha256', 'image', 'model', 'served_model', 'context',
    'namespace', 'namespace_uid', 'run_id','response_deadline_ms'})


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical_json(value))


def module_sha(module):
    return sha(Path(module.__file__).read_bytes())


def control_module():
    from guard.adapters import vllm_fault_control
    if not callable(getattr(vllm_fault_control, 'validate_fault_case', None)):
        raise ValueError('FAULT_CONTROL_VALIDATOR_UNAVAILABLE')
    return vllm_fault_control


def read_json(path, expected=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('FAULT_CASE_FILE_INVALID')
    with path.open('rb') as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024 or expected is not None and sha(raw) != expected:
        raise ValueError('FAULT_CASE_FILE_HASH_MISMATCH')
    return strict_json(raw, limit=64 * 1024 * 1024)


def _scope(ctx, cfg, policy, values, source_commit):
    if type(source_commit) is not str or re.fullmatch('[0-9a-f]{40}', source_commit) is None:
        raise ValueError('FAULT_CASE_SOURCE_COMMIT_INVALID')
    env = ctx['env']; binding = ctx['binding']; engine = binding['engine']
    model = {k: getattr(env.model,k) for k in ('id','revision','inventory_sha256')}
    if any(type(model.get(k)) is not str or not model[k] for k in ('id', 'revision', 'inventory_sha256')) or re.fullmatch('[0-9a-f]{64}',model['inventory_sha256']) is None:
        raise ValueError('FAULT_CASE_MODEL_SCOPE_REQUIRED')
    return {'source_profile': PROFILE, 'actor_sha256': module_sha(actor), 'consumer_sha256': module_sha(ctx['module']),
        'declared_source_commit': source_commit, 'policy_sha256': policy.ref().sha256,
        'policy_values_sha256': digest(values), 'image': engine['image'], 'model': model,
        'served_model': cfg['model'], 'context': binding['context'], 'namespace': engine['namespace'],
        'namespace_uid': engine['namespace_uid'], 'run_id': binding['run_id'],'response_deadline_ms':cfg['deadline_ms']}


def _scope_valid(scope, module):
    if type(scope) is not dict or set(scope) != SCOPE_FIELDS or scope['source_profile'] != PROFILE:
        raise ValueError('FAULT_CASE_SCOPE_INVALID')
    if scope['actor_sha256'] != module_sha(actor) or scope['consumer_sha256'] != module_sha(module):
        raise ValueError('FAULT_CASE_SOURCE_CHANGED')
    for key in ('actor_sha256', 'consumer_sha256', 'policy_sha256', 'policy_values_sha256'):
        if type(scope[key]) is not str or re.fullmatch('[0-9a-f]{64}', scope[key]) is None:
            raise ValueError('FAULT_CASE_SCOPE_INVALID')
    if type(scope['declared_source_commit']) is not str or re.fullmatch('[0-9a-f]{40}', scope['declared_source_commit']) is None:
        raise ValueError('FAULT_CASE_SCOPE_INVALID')
    if type(scope['model']) is not dict or any(type(scope['model'].get(k)) is not str or not scope['model'][k]
            for k in ('id', 'revision', 'inventory_sha256')):
        raise ValueError('FAULT_CASE_MODEL_SCOPE_REQUIRED')
    if type(scope['response_deadline_ms']) is not int or scope['response_deadline_ms']<=0:
        raise ValueError('FAULT_CASE_RESPONSE_DEADLINE_INVALID')
    if re.fullmatch('[0-9a-f]{64}',scope['model']['inventory_sha256']) is None:
        raise ValueError('FAULT_CASE_MODEL_SCOPE_REQUIRED')
    for key in ('image', 'served_model', 'context', 'namespace', 'namespace_uid', 'run_id'):
        if type(scope[key]) is not str or not scope[key]:
            raise ValueError('FAULT_CASE_SCOPE_INVALID')


def _open_seed(trial):
    seed = trial.get('seed_capture')
    if type(seed) is not dict or seed.get('transport', {}).get('collection_gap') is not False:
        raise ValueError('FAULT_CASE_SEED_UNAVAILABLE')
    packet = seed.get('packet', {})
    if any((packet.get(k) or {}).get('metadata', {}).get('sealed') is not False for k in ('before', 'after')):
        raise ValueError('FAULT_CASE_SESSION_ALREADY_SEALED')


def _validate_case(case, module):
    if type(case) is not dict or set(case) != {'schema', 'scope', 'policy', 'trial', 'activation', 'one_kind_complete'} or case['schema'] != CASE_SCHEMA:
        raise ValueError('FAULT_CASE_INVALID')
    scope = case['scope']; _scope_valid(scope, module)
    policy=Policy.model_validate(case['policy'])
    values=policy.values.get('LIF-08')
    if not policy.hash_ok() or policy.ref().sha256!=scope['policy_sha256'] or digest(values)!=scope['policy_values_sha256']:
        raise ValueError('FAULT_CASE_POLICY_MISMATCH')
    trial = case['trial']; kind = trial.get('fault_kind')
    if trial.get('schema') != 'byul-lab-native-fault-trial/1' or kind not in KINDS:
        raise ValueError('FAULT_CASE_KIND_INVALID')
    expected = trial.get('expected_scope'); binding = trial.get('binding'); control = trial.get('control')
    if any(type(v) is not dict for v in (expected, binding, control)):
        raise ValueError('FAULT_CASE_BOUND_INPUTS_REQUIRED')
    from guard.runtime.kubernetes_binding import validate_capture_target
    env=Environment.model_validate(trial['environment'])
    if validate_capture_target(trial['target'],env)!=binding or digest(trial['target'])!=expected['target_sha256']:
        raise ValueError('FAULT_CASE_TARGET_MISMATCH')
    if ({k:getattr(env.model,k) for k in ('id','revision','inventory_sha256')}!=scope['model']
            or trial['prepared'].get('session')!=control['session'] or trial['prepared'].get('profile')!=PROFILE):
        raise ValueError('FAULT_CASE_PREPARED_SCOPE_MISMATCH')
    engine = binding['engine']
    if (expected != {'source_profile': PROFILE, 'session': control['session'], 'target_sha256': control['target_sha256'],
            'pod_uid': engine['pod_uid'], 'container_id': engine['container_id'],
            'external_request_sha256': control['external_request_sha256'], 'nonce': trial['nonce'],
            'fault_kind': kind, 'rank': control['rank']}
            or any(control[k] != expected[k] for k in ('nonce', 'fault_kind'))
            or any(engine[k] != scope[k] for k in ('namespace', 'namespace_uid', 'image'))
            or binding['context'] != scope['context'] or binding['run_id'] != scope['run_id']
            or trial.get('response_deadline_ms')!=scope['response_deadline_ms']):
        raise ValueError('FAULT_CASE_SCOPE_MISMATCH')
    key=kind.replace('-','_')
    if (type(values) is not dict or trial.get('deadlines')!={'detection_ms':values.get('rank_'+key+'_detection_ms'),
            'notification_ms':values.get('rank_'+key+'_notification_ms'),'response_ms':trial.get('response_deadline_ms')}
            or any(type(trial['deadlines'].get(k)) is not int or trial['deadlines'][k]<0
                for k in ('detection_ms','notification_ms','response_ms'))):
        raise ValueError('FAULT_CASE_POLICY_DEADLINES_MISMATCH')
    _open_seed(trial)
    before = trial.get('pre_signal_capture'); after = trial.get('post_capture'); injection = trial.get('injection')
    if (type(before) is not dict or type(after) is not dict or type(injection) is not dict
            or before.get('transport', {}).get('collection_gap') is not False
            or after.get('transport', {}).get('collection_gap') is not False or after.get('sealed') is not True
            or injection.get('pre_signal_packet') != before.get('packet')):
        raise ValueError('FAULT_CASE_CAPTURE_INCOMPLETE')
    # This is the sole native verdict source, including deadline violations.
    projection = module.validate_fault_case(after['packet'], trial['client'], injection, expected, trial['deadlines'])
    if (type(projection) is not dict or any(type(projection.get(k)) is not bool for k in FLAGS)
            or any(type(projection.get(k)) is not list for k in ('missing', 'violations', 'evidence_refs'))
            or projection != trial.get('projection')):
        raise ValueError('FAULT_CASE_NATIVE_REPLAY_MISMATCH')
    activation = case['activation']
    if type(activation) is not dict or set(activation) != set(actor.OBSERVATIONS) or activation['activation_scope_complete'] is not False:
        raise ValueError('FAULT_CASE_SINGLE_SCOPE_INVALID')
    if any(type(activation[k]) is not list for k in ('activation_steps', 'activation_missing', 'activation_violations')):
        raise ValueError('FAULT_CASE_ACTIVATION_INVALID')
    coherent=(all(projection[k] for k in ('occurrence_verified','request_fault_overlap_verified','clock_verified'))
        and (all(projection[k] for k in FLAGS[:-1]) or bool(projection['violations'])))
    complete = (coherent and projection['whole_kind_complete'] and not projection['missing'] and bool(projection['evidence_refs'])
        and trial.get('issued') is True and trial.get('released') is True and trial.get('restore_confirmed') is True
        and trial.get('error_kind') is None and not activation['activation_missing'])
    if type(case['one_kind_complete']) is not bool or case['one_kind_complete'] != bool(complete):
        raise ValueError('FAULT_CASE_COMPLETENESS_MISMATCH')
    return projection, bool(complete)


def run_one(cfg, policy, source_commit, *, runner=None, clock=None, wait=None):
    """Official one-kind product entry. Every invocation needs fresh inputs.

    cfg.fault_control_kind is explicit; no passive collector runs before actor.
    policy is the existing sealed Policy object. The declared commit is recorded
    alongside actual imported actor/consumer byte hashes, not a Git assertion.
    """
    if type(cfg) is not dict or cfg.get('fault_control_kind') not in KINDS:
        raise ValueError('FAULT_CONTROL_EXPLICIT_KIND_REQUIRED')
    if not isinstance(policy, Policy) or not policy.hash_ok() or type(policy.values.get('LIF-08')) is not dict:
        raise ValueError('FAULT_CASE_POLICY_INVALID')
    values = policy.values['LIF-08']; kind = cfg['fault_control_kind']
    if cfg.get('fault_control_node_image_verification')!='local-containerd-v1':
        raise ValueError('FAULT_CASE_NODE_IMAGE_PROOF_REQUIRED')
    lab = actor.Lab(cfg, runner=runner, clock=clock, wait=wait)
    if not actor.FAULT_CONTROL_KEYS <= set(cfg):
        raise ValueError('FAULT_CASE_NATIVE_CONFIG_REQUIRED')
    ctx = actor._control_context(lab)
    scope = _scope(ctx, cfg, policy, values, source_commit)
    if ctx['module'].PROFILE != PROFILE or not callable(getattr(ctx['module'], 'validate_fault_case', None)):
        raise ValueError('FAULT_CONTROL_VALIDATOR_UNAVAILABLE')
    key = kind.replace('-', '_')
    detection = values.get('rank_' + key + '_detection_ms')
    notification = values.get('rank_' + key + '_notification_ms')
    if type(detection) is not int or detection < 0 or type(notification) is not int or notification < 0:
        raise ValueError('FAULT_CASE_EXPLICIT_DEADLINES_REQUIRED')
    lab.verify_owned()
    number = {'death': 9, 'delay': 19}.get(kind)
    try:
        actor._one_fault(lab, kind, None, detection, number, notification_ms=notification)
    finally:
        actor._final_restore(lab)
    if len(lab.fault_evidence) != 1:
        raise ValueError('FAULT_CASE_SINGLE_ARTIFACT_REQUIRED')
    trial = lab.fault_evidence[0]['document']
    activation = {'activation_steps': lab.steps, 'activation_violations': lab.violations,
        'activation_missing': lab.missing, 'activation_scope_complete': False}
    projection = trial.get('projection') or {}
    complete = (projection.get('whole_kind_complete') is True and not projection.get('missing', [])
        and bool(projection.get('evidence_refs')) and trial.get('issued') is True and trial.get('released') is True
        and trial.get('restore_confirmed') is True and trial.get('error_kind') is None
        and not lab.missing and bool(lab.normal) and all(lab.normal) and not lab.cleanup_errors)
    return {'schema': CASE_SCHEMA, 'scope': scope, 'policy':policy.model_dump(mode='json'),'trial': deepcopy(trial),
        'activation': deepcopy(activation), 'one_kind_complete': bool(complete)}


def close_plan(plan):
    """Read-only exact four-case replay; preserve original FAIL/UNKNOWN facts."""
    if type(plan) is not dict or set(plan) != {'schema', 'cases'} or plan['schema'] != PLAN_SCHEMA:
        raise ValueError('FAULT_PLAN_INVALID')
    refs = plan['cases']
    if type(refs) is not list or len(refs) != 4:
        raise ValueError('FAULT_PLAN_EXACT_FOUR_REQUIRED')
    module = control_module(); cases = []; paths = set()
    for ref in refs:
        if (type(ref) is not dict or set(ref) != {'path', 'sha256'} or type(ref['path']) is not str
                or type(ref['sha256']) is not str or re.fullmatch('[0-9a-f]{64}',ref['sha256']) is None):
            raise ValueError('FAULT_PLAN_REFERENCE_INVALID')
        path = Path(ref['path'])
        if not path.is_absolute() or path.resolve() in paths:
            raise ValueError('FAULT_PLAN_DUPLICATE_FILE')
        paths.add(path.resolve())
        case = read_json(path, ref['sha256']); projection, complete = _validate_case(case, module)
        cases.append((case, projection, complete, deepcopy(ref)))
    if {case['trial']['fault_kind'] for case, *_ in cases} != KINDS:
        raise ValueError('FAULT_PLAN_DUPLICATE_OR_MISSING_KIND')
    if any(case['scope'] != cases[0][0]['scope'] for case, *_ in cases):
        raise ValueError('FAULT_PLAN_LOGICAL_SCOPE_MISMATCH')
    # No session, UID or CID can be recycled across cases, even when Pod names
    # or logical environment/model remain the same after restoration.
    for key in ('session', 'target_sha256', 'pod_uid', 'container_id', 'nonce', 'external_request_sha256'):
        if len({case['trial']['expected_scope'][key] for case, *_ in cases}) != 4:
            raise ValueError('FAULT_PLAN_STALE_CASE_SCOPE:' + key)
    steps = []; violations = []; missing = []
    for case, projection, complete, ref in cases:
        kind = case['trial']['fault_kind']; original = case['activation']
        steps.extend(deepcopy(original['activation_steps']))
        # Native violations are retained directly; actor may also contain an
        # independent operational violation. Avoid duplicating identical rows.
        for violation in [*original['activation_violations'], *projection['violations']]:
            if violation not in violations:
                violations.append(deepcopy(violation))
        missing.extend(deepcopy(original['activation_missing']))
        missing.extend('FAULT_CONTROL:' + kind + ':' + str(v) for v in projection['missing'])
        if not complete:
            missing.append('FAULT_CONTROL_KIND_INCOMPLETE:' + kind)
    return {'activation_steps': steps, 'activation_violations': violations,
        'activation_missing': list(dict.fromkeys(missing)), 'activation_scope_complete': all(row[2] for row in cases),
        'case_refs': [row[3] for row in cases], 'case_outcomes': [
            {'kind': c['trial']['fault_kind'], 'one_kind_complete': complete,
             'native_projection': deepcopy(p)} for c, p, complete, _ in cases]}


def _initialization_inputs(cfg,policy,source_commit):
    from guard.executors import initialization_terminal_case as terminal
    if (type(cfg)is not dict or cfg.get('initialization_failure_kind') not in {'config','model'}
            or not cfg.get('kubectl_context') or type(cfg.get('restore_timeout_ms'))is not int
            or actor.FAULT_CONTROL_KEYS & set(cfg)):
        raise ValueError('INITIALIZATION_EXPLICIT_CONFIG_REQUIRED')
    if (not isinstance(policy,Policy) or not policy.hash_ok() or type(policy.values.get('LIF-09'))is not dict
            or type(source_commit)is not str or re.fullmatch('[0-9a-f]{40}',source_commit)is None
            or not callable(getattr(terminal,'project_terminal_case',None))):
        raise ValueError('INITIALIZATION_POLICY_OR_SOURCE_INVALID')
    for key in ('faulty_manifest','good_manifest'):
        expected=cfg.get(key+'_sha256')
        if (type(expected)is not str or re.fullmatch('[0-9a-f]{64}',expected)is None
                or sha(Path(cfg[key]).read_bytes())!=expected):
            raise ValueError('INITIALIZATION_MANIFEST_HASH_REQUIRED')
    return terminal


_WINDOW_AUTHORITY=object()


class InitializationFailureWindow:
    """Same-process bounded actor handle; no command hooks or persistent service.

    receipt is a copy of the still-failed primary evidence. Gateway collection
    uses the caller's existing reviewed tool. finish always attempts the pinned
    good restore before validating an optional opaque public Gateway file ref.
    """
    def __init__(self,authority,lab,policy,source_commit,terminal):
        if authority is not _WINDOW_AUTHORITY:raise ValueError('INITIALIZATION_WINDOW_INTERNAL_ONLY')
        self._pid=os.getpid();self._lab=lab;self._cfg=deepcopy(lab.cfg)
        self._policy=policy.model_copy(deep=True);self._source_commit=source_commit
        self._terminal=terminal;self._actor_sha=module_sha(actor);self._terminal_sha=module_sha(terminal)
        self._result=None;self._finished=False

    @property
    def receipt(self):return deepcopy(self._lab.initialization_case)

    @property
    def result(self):return deepcopy(self._result)

    @property
    def gateway_probe_budget_seconds(self):
        reserve=self._cfg['restore_timeout_ms']/1000+actor.KUBECTL_CALL_TIMEOUT+actor.REQUEST_CALL_TIMEOUT
        return max(0,min(self._cfg['deadline_ms']/1000,self._lab.deadline-self._lab.clock()-reserve))

    def __enter__(self):return self

    def __exit__(self,kind,error,traceback):
        finish_initialization_restore(self)
        return False


def begin_initialization_failure(cfg,policy,source_commit,*,runner=None,clock=None,wait=None):
    """Prepare normal -> failed boot only; caller must finish in try/finally."""
    terminal=_initialization_inputs(cfg,policy,source_commit)
    lab=actor.Lab(deepcopy(cfg),runner=runner,clock=clock,wait=wait);lab.verify_owned()
    window=InitializationFailureWindow(_WINDOW_AUTHORITY,lab,policy,source_commit,terminal)
    try:
        actor._partial_start(lab,window._policy.values['LIF-09'])
    except Exception as error:
        lab.miss('SCENARIO_INTERRUPTED:'+type(error).__name__)
    except BaseException:
        # A caller cannot receive a handle when initialization itself is aborted.
        actor._final_restore(lab)
        raise
    return window


def finish_initialization_restore(window,*,gateway_evidence_ref=None):
    """Restore first, replay strict Kube primary, retain an opaque Gateway ref.

    A valid hash ref is not a Gateway target/delivery verdict. The existing A
    validator and root integration bind that source to this failed attempt.
    Repeated finish calls return a copy and never repeat a mutation.
    """
    if type(window)is not InitializationFailureWindow or window._pid!=os.getpid():
        raise ValueError('INITIALIZATION_WINDOW_PROCESS_MISMATCH')
    if window._finished:return deepcopy(window._result)
    lab=window._lab
    if lab.cfg!=window._cfg:lab.miss('INITIALIZATION_WINDOW_CONFIG_CHANGED')
    lab.cfg=deepcopy(window._cfg)
    actor._final_restore(lab)
    actor._canonical_initialization(lab)
    ref=None
    if gateway_evidence_ref is not None:
        try:
            if (type(gateway_evidence_ref)is not dict or set(gateway_evidence_ref)!={'path','sha256'}
                    or type(gateway_evidence_ref['path'])is not str or not Path(gateway_evidence_ref['path']).is_absolute()
                    or type(gateway_evidence_ref['sha256'])is not str
                    or re.fullmatch('[0-9a-f]{64}',gateway_evidence_ref['sha256'])is None):
                raise ValueError('INITIALIZATION_GATEWAY_REFERENCE_INVALID')
            read_json(gateway_evidence_ref['path'],gateway_evidence_ref['sha256'])
            ref=deepcopy(gateway_evidence_ref)
        except (OSError,ValueError,TypeError):lab.miss('INITIALIZATION_GATEWAY_REFERENCE_UNVERIFIED')
    try:
        if module_sha(actor)!=window._actor_sha or module_sha(window._terminal)!=window._terminal_sha:
            lab.miss('INITIALIZATION_WINDOW_SOURCE_CHANGED')
    except OSError:lab.miss('INITIALIZATION_WINDOW_SOURCE_UNAVAILABLE')
    facts={'activation_steps':lab.steps,'activation_violations':lab.violations,'activation_missing':lab.missing,
        'activation_scope_complete':not lab.missing and bool(lab.normal) and all(lab.normal)}
    window._result={'schema':'byul-lab-initialization-trial/1','declared_source_commit':window._source_commit,
        'actor_sha256':window._actor_sha,'terminal_consumer_sha256':window._terminal_sha,
        'policy':window._policy.model_dump(mode='json'),'manifest_refs':{k:window._cfg[k+'_sha256'] for k in ('faulty_manifest','good_manifest')},
        'receipt':deepcopy(lab.initialization_case),'activation':deepcopy(facts),'cleanup_errors':list(lab.cleanup_errors),
        'gateway_observation_ref':ref,'gateway_target_delivery_binding_verified':False,
        'native_stage_ack_observed':False,'per_request_gpu_release_verified':False}
    window._finished=True
    return deepcopy(window._result)


def run_initialization(cfg,policy,source_commit,*,runner=None,clock=None,wait=None):
    """Automatic Kubernetes-only window; root uses begin/finish for Gateway D1."""
    window=begin_initialization_failure(cfg,policy,source_commit,runner=runner,clock=clock,wait=wait)
    return finish_initialization_restore(window)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    one = subs.add_parser('run-one')
    one.add_argument('--config', type=Path, required=True)
    one.add_argument('--policy', type=Path, required=True)
    one.add_argument('--source-commit', required=True)
    one.add_argument('--output', type=Path, required=True)
    initial=subs.add_parser('run-initialization')
    initial.add_argument('--config',type=Path,required=True)
    initial.add_argument('--policy',type=Path,required=True)
    initial.add_argument('--source-commit',required=True)
    initial.add_argument('--output',type=Path,required=True)
    plan = subs.add_parser('close-plan')
    plan.add_argument('--plan', type=Path, required=True)
    plan.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('FAULT_OUTPUT_EXISTS')
    if args.command == 'run-one':
        document = run_one(read_json(args.config), Policy.model_validate_json(args.policy.read_bytes()), args.source_commit)
        complete = document['one_kind_complete']
    elif args.command=='run-initialization':
        document=run_initialization(read_json(args.config),Policy.model_validate_json(args.policy.read_bytes()),args.source_commit)
        complete=document['activation']['activation_scope_complete']
    else:
        document = close_plan(read_json(args.plan)); complete = document['activation_scope_complete']
    # No caller-controlled command hooks; output is a new immutable artifact.
    descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(canonical_json(document))
    return 0 if complete else 2


if __name__ == '__main__':
    raise SystemExit(main())
