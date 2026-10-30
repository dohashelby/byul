"""SUP-09/11 direct CPU capture: one collection, two evidence objects, one PLAN."""
from __future__ import annotations

import argparse
import builtins
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import time
import yaml

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import dynamic_code_cpu as product, registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, load_pack
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tools import b1_dynamic_code_cpu as collector
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'sup09-11-shared-source-cpu-proposal-v1'
SOURCE_PROFILES = {'reference5d': '5d35ce3934a7804b298559d2d99eec33792e1eb9',
                   'target031': 'db9527a46873454610df6dbedf79a36d6bf1a7f6'}
GATE_SHA256 = 'e7a9efa070b526fed3c331a5e21ea249d6998d50aacb1199bb31fb2f080f3457'
COMPONENT_COMMIT = '4a7d7f6511293d0245ba9678aba7c437642eabd2'
# Reviewed collector snapshot f239748; runtime hashes never become approval.
COLLECTOR_SHA256 = '87b0284285064f06ecb57917841b6837b0adfa8bd2b7d135bbe48076b288df92'
CHECKS = ('SUP-09', 'SUP-11')
_CASES = {case['case_id']: case for case in product.fixtures()}
_MODES = {'normal': 0, 'O': 1, 'OO': 2}
_FIXTURE_FILES = {'model/config.json', 'model/configuration_b1_probe.py', 'builtin/config.json'}
_OUTCOMES = {'accepted', 'rejected_at_trust_gate', 'execution_error', 'unsupported'}
_VALIDATION = {'verified', 'unavailable', 'mismatch'}
_ERROR_TYPES = {name for name, value in vars(builtins).items()
                if isinstance(value, type) and issubclass(value, Exception)}


def _require_normal_python():
    if sys.flags.optimize or sys.version_info[:2] != (3, 12):
        raise ValueError('DYNAMIC_CPU_REQUIRES_NORMAL_PYTHON312')


def _source_commit(profile):
    if not isinstance(profile, str) or profile not in SOURCE_PROFILES:
        raise ValueError('DYNAMIC_CPU_SOURCE_PROFILE_UNSUPPORTED')
    return SOURCE_PROFILES[profile]


def verify_runtime_components(repo):
    gate_file = repo / 'update/tools/lif14_profiler_cpu.py'
    collector_file = repo / 'update/tools/b1_dynamic_code_cpu.py'
    try:
        if (gate_file.is_symlink() or not Path(component_gate.__file__).resolve().samefile(gate_file)
                or not Path(inspect.getsourcefile(component_gate.verify_component)).resolve().samefile(gate_file)
                or hashlib.sha256(gate_file.read_bytes()).hexdigest() != GATE_SHA256
                or component_gate.PRODUCT_COMPONENT_COMMIT != COMPONENT_COMMIT):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('DYNAMIC_CPU_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    if not isinstance(COLLECTOR_SHA256, str) or re.fullmatch('[0-9a-f]{64}', COLLECTOR_SHA256) is None:
        raise ValueError('DYNAMIC_CPU_COLLECTOR_PIN_UNCONFIGURED')
    try:
        if (collector_file.is_symlink() or not Path(collector.__file__).resolve().samefile(collector_file)
                or not Path(inspect.getsourcefile(collector.capture)).resolve().samefile(collector_file)
                or hashlib.sha256(collector_file.read_bytes()).hexdigest() != COLLECTOR_SHA256):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('DYNAMIC_CPU_COLLECTOR_IDENTITY_OR_BYTES_MISMATCH') from None
    component_gate.verify_component(repo)


def _validation(value):
    value = value if isinstance(value, dict) else {}
    post = value.get('post') if isinstance(value.get('post'), dict) else {}
    pre_status, post_status = value.get('pre'), post.get('status')
    safe = {'pre': pre_status if isinstance(pre_status, str) and pre_status in _VALIDATION else 'unavailable',
        'post': {'status': post_status if isinstance(post_status, str) and post_status in _VALIDATION else 'unavailable'},
        'boundary': 'source_fixture_component' if value.get('boundary') == 'source_fixture_component' else 'unavailable'}
    if 'error_type' in post:
        safe['post']['error_type'] = post['error_type'] if isinstance(post['error_type'], str) and post['error_type'] in _ERROR_TYPES else 'Exception'
    return safe


def _phases(value):
    try:
        return product.clean_phases(value)
    except (TypeError, ValueError):
        return None


def _outcome(value):
    return value if isinstance(value, str) and value in _OUTCOMES else 'unknown'


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('DYNAMIC_CPU_CAPTURE_TIME_INVALID')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError('DYNAMIC_CPU_CAPTURE_TIME_INVALID')
    return result


def _safe_rows(rows):
    safe = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('case_id'), str) or row['case_id'] not in _CASES:
            continue
        case = _CASES[row['case_id']]
        validation = _validation(row.get('source_validation'))
        safe.append({**case, 'case_identity': row.get('case_identity') if row.get('case_identity') == product.identity(case) else None,
            'outcome': _outcome(row.get('outcome')), 'events': _phases(row.get('events', [])),
            'event_collection_status': row.get('event_collection_status') if row.get('event_collection_status') in ('completed', 'partial', 'read_error', 'not_collected') else 'unknown',
            'target_started': row.get('target_started') is True,
            'python_optimize': row.get('python_optimize') if type(row.get('python_optimize')) is int and row['python_optimize'] in (0, 1, 2) else None,
            'source_profile': row.get('source_profile') if isinstance(row.get('source_profile'), str) and row['source_profile'] in SOURCE_PROFILES else None,
            'source_commit': row.get('source_commit') if row.get('source_commit') in SOURCE_PROFILES.values() else None,
            'collection_id': row.get('collection_id') if isinstance(row.get('collection_id'), str) and re.fullmatch('dynamic-cpu-[0-9a-f]{32}', row['collection_id']) else None,
            'source_validation': validation,
            'selected_fixture_source_matches': row.get('selected_fixture_source_matches') is True,
            'before': {'outcome': _outcome(row.get('before', {}).get('outcome')) if isinstance(row.get('before'), dict) else 'unknown'},
            'after': {'outcome': _outcome(row.get('after', {}).get('outcome')) if isinstance(row.get('after'), dict) else 'unknown'}})
        if row.get('preparation_status') in ('completed', 'failed'):
            safe[-1]['preparation_status'] = row['preparation_status']
        if type(row.get('child_executed')) is bool:
            safe[-1]['child_executed'] = row['child_executed']
    return safe


def _qualified_rows(rows, profile, collection_id):
    qualified = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get('case_id')
        case = _CASES.get(key) if isinstance(key, str) else None
        validation = _validation(row.get('source_validation'))
        if (case is not None and row.get('target_started') is True
                and row.get('source_profile') == profile and row.get('source_commit') == _source_commit(profile)
                and row.get('collection_id') == collection_id
                and type(row.get('python_optimize')) is int and row['python_optimize'] == _MODES[case['mode']]
                and validation['pre'] == 'verified' and validation['boundary'] == 'source_fixture_component'
                and validation['post']['status'] in {'verified', 'unavailable'}):
            qualified.append(deepcopy(row))
    return qualified


def _project(rows, profile, collection_id):
    qualified = _qualified_rows(rows, profile, collection_id)
    projection = product.project(qualified)
    baseline = {}
    for entry in ('autoconfig', 'vllm-config'):
        expected = _CASES[f'{entry}-False-normal']
        matches = [row for row in qualified if row.get('case_id') == expected['case_id']
                   and row.get('case_identity') == product.identity(expected)
                   and _phases(row.get('events', [])) is not None]
        row = matches[0] if matches else {}
        # Match the product's normal rejection/no-import condition. A separate
        # before/after failure affects completeness, not an independent FAIL.
        baseline[entry] = bool(row and row.get('outcome') == 'rejected_at_trust_gate'
            and row.get('event_collection_status') == 'completed' and _phases(row.get('events', [])) == [])
    return projection, qualified, baseline


def _validated_record(raw, profile, producer_sha256):
    if (not isinstance(raw, dict) or raw.get('source_profile') != profile or raw.get('source_commit') != _source_commit(profile)
            or raw.get('own_producer_sha256') != producer_sha256 or raw.get('installed_target') is not None
            or raw.get('capture_status') not in ('completed', 'incomplete') or not isinstance(raw.get('rows'), list)
            or not isinstance(raw.get('collection_id'), str) or re.fullmatch('dynamic-cpu-[0-9a-f]{32}', raw['collection_id']) is None):
        raise ValueError('DYNAMIC_CPU_RECORD_ATTRIBUTION_MISMATCH')
    validation = _validation(raw.get('source_validation'))
    if validation['pre'] != 'verified' or validation['boundary'] != 'source_fixture_component':
        raise ValueError('DYNAMIC_CPU_RECORD_PRECHECK_UNAVAILABLE')
    hashes = raw.get('fixture_file_bytes_sha256')
    if not isinstance(hashes, dict) or set(hashes) != _FIXTURE_FILES or any(
            not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None for value in hashes.values()):
        raise ValueError('DYNAMIC_CPU_FIXTURE_HASH_INVALID')
    started, finished = _utc(raw.get('capture_started_at')), _utc(raw.get('capture_finished_at'))
    if finished < started:
        raise ValueError('DYNAMIC_CPU_CAPTURE_TIME_INVALID')
    aux = raw.get('auxiliary_write') if isinstance(raw.get('auxiliary_write'), dict) else {}
    auxiliary = {'status': aux.get('status') if aux.get('status') in ('completed', 'failed') else 'failed'}
    if 'error_type' in aux:
        auxiliary['error_type'] = aux['error_type'] if isinstance(aux['error_type'], str) and aux['error_type'] in _ERROR_TYPES else 'Exception'
    return {'profile': PROFILE, 'source_profile': profile, 'source_commit': _source_commit(profile),
        'scope': 'source_cpu_owned_custom_config_loaders', 'installed_target': None,
        'runner_hook_verified': False, 'collection_id': raw['collection_id'],
        'capture_started_at': started.isoformat(), 'capture_finished_at': finished.isoformat(),
        'capture_status': raw['capture_status'], 'source_validation': validation, 'auxiliary_write': auxiliary,
        'own_producer_sha256': producer_sha256, 'fixture_file_bytes_sha256': dict(hashes), 'rows': _safe_rows(raw['rows'])}


def _write_artifact(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True, indent=2)


class _SharedCapture:
    """Private per-run binding; EXAMPLE tests inject only an in-memory callable."""
    name, version = 'dynamic_code_cpu', '0.1.0'

    def __init__(self, env, plan_ref, source_root, source_profile, *, capture_fn, producer_sha256, component_check):
        self.env_ref, self.plan_ref, self.origin = env.ref(), plan_ref, env.data_origin
        self.source_root, self.source_profile = Path(source_root), source_profile
        self.capture_fn, self.producer_sha256, self.component_check = capture_fn, producer_sha256, component_check
        self.loaded, self.record, self.observations = False, None, {}
        self.started, self.finished, self.component_ok = None, None, True
        self.collection_id, self.baseline = None, {}
        self._check_binding(env, plan_ref, source_profile)

    def _check_binding(self, env, plan_ref, profile):
        if (not env.hash_ok() or env.ref() != self.env_ref or plan_ref != self.plan_ref
                or profile != self.source_profile or env.data_origin != self.origin or env.engine.name != 'vllm'
                or env.engine.version != 'reference-' + _source_commit(profile)):
            raise ValueError('DYNAMIC_CPU_SHARED_ENVIRONMENT_OR_PLAN_MISMATCH')

    def _load(self, workdir):
        self.loaded = True
        self.started = datetime.now(timezone.utc)
        try:
            raw = deepcopy(self.capture_fn(self.source_root, Path(workdir) / 'owned-dynamic-cpu', source_profile=self.source_profile))
            record = _validated_record(raw, self.source_profile, self.producer_sha256)
            projection, qualified, baseline = _project(raw['rows'], self.source_profile, record['collection_id'])
            record['baseline_qualification'] = baseline
            self.started, self.finished = _utc(record['capture_started_at']), _utc(record['capture_finished_at'])
            self.collection_id, self.baseline = record['collection_id'], baseline
            self.record, self.observations = record, projection
            self.complete = (record['capture_status'] == 'completed' and record['source_validation']['post']['status'] == 'verified'
                and record['auxiliary_write']['status'] == 'completed' and projection['complete']
                and len(qualified) == len(_CASES)
                and all(_validation(row.get('source_validation'))['post']['status'] == 'verified' for row in qualified))
        except Exception:
            self.complete = False
            self.finished = datetime.now(timezone.utc)

    def draft(self, check, env, policy_values, workdir, *, plan_ref, source_profile):
        _require_normal_python()
        self._check_binding(env, plan_ref, source_profile)
        if check.id not in CHECKS or policy_values:
            raise ValueError('DYNAMIC_CPU_CHECK_OR_POLICY_UNSUPPORTED')
        try:
            self.component_check()
        except Exception:
            if not self.loaded:
                raise
            self.component_ok = False
        if not self.loaded:
            self._load(workdir)
        try:
            self.component_check()
        except Exception:
            self.component_ok = False
        origin = DataOrigin.EXAMPLE if self.origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        config = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': _source_commit(source_profile),
                  'collection_id': self.collection_id, 'installed_target': None, 'runner_hook_verified': False}
        complete = self.record is not None and self.complete and self.component_ok
        observations = deepcopy(self.observations.get(check.id, {}))
        # The product projection uses [] for an uncollected axis; EVD's existing
        # null contract requires None at this boundary, without changing verdicts.
        for observation in observations.values():
            if observation.get('collected') is False:
                observation['value'] = None
        if self.record is not None:
            observations['dynamic_cpu_cases_complete'] = {'value': bool(complete), 'collected': True}
        draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED, data_origin=origin,
            normal_control={'attempted': self.record is not None, 'succeeded': bool(complete) if self.record is not None else None},
            observations=observations, config=config,
            challenges=[{'step': step, 'attempted': self.record is not None} for step in check.challenge_steps],
            started_at=self.started, finished_at=self.finished,
            abort_reason=None if complete else ('DYNAMIC_CPU_CAPTURE_INCOMPLETE' if self.record is not None else 'DYNAMIC_CPU_CAPTURE_UNAVAILABLE'))
        if not self.component_ok:
            draft.abort_reason = (draft.abort_reason + '; ' if draft.abort_reason else '') + 'DYNAMIC_CPU_COMPONENT_POSTCHECK_UNAVAILABLE'
        if self.record is not None:
            artifact = Path(workdir) / ('dynamic-source-' + check.id.lower() + '.json')
            try:
                _write_artifact(artifact, {**deepcopy(self.record), 'check_id': check.id})
                draft.artifacts = [DraftArtifact(name=artifact.name, path=artifact, collector=PROFILE)]
            except Exception:
                draft = incomplete_artifact_capture(draft)
        return draft


def proposal_pack(folder, profile):
    _source_commit(profile)
    (folder / 'checks').mkdir(parents=True)
    files = []
    for cid in CHECKS:
        key = 'dynamic_code_confirmed_violations' if cid == 'SUP-09' else 'dynamic_mode_confirmed_violations'
        check = CheckDef(id=cid, revision=1, area='SUP', purpose='공급물 실행 경계',
            question='선정 source CPU의 custom-code trust/mode 경계는 실제 관측과 일치하는가?',
            review_order='source_cpu_reference_first', applies_when={'always': True},
            normal_control='기존 12개 사례 전후 builtin config의 직접 정상 수용',
            challenge_steps=['기존 trust false/true 및 정상/O/OO loader 12개 관측'],
            required_observations=[{'key': key, 'type': 'list[str]'}, {'key': 'dynamic_cpu_cases_complete', 'type': 'bool'}],
            satisfied_if='제한된 source CPU 관측; 기업 승인 설치·최종 소비는 별도 확인',
            fail_criteria='기존 source trust/mode 경계의 확인된 위반', unknown_criteria='미수집·귀속 또는 정상 baseline 미확인',
            executor=_SharedCapture.name, judge={'plugin': 'rule_table', 'params': product.judge_params(cid)},
            limitations=['소유 local config CPU 직접 호출. 모델 weights·배포·최종 model-code 소비 미검증.'])
        body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
        relative = 'checks/' + cid + '.yaml'
        (folder / relative).write_bytes(body)
        files.append({'path': relative, 'sha256': hashlib.sha256(body).hexdigest()})
    manifest = {'pack_id': 'PACK-B-SUP09-11-DYNAMIC-CPU-' + profile.upper(), 'revision': 1, 'engines': ['vllm'],
                'checks': list(CHECKS), 'files_sha256': files, 'review': {'status': 'DRAFT'}}
    (folder / 'pack.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
    return load_pack(folder)


def run_pipeline(source_root, output_root, *, source_profile='reference5d', origin=DataOrigin.DECLARED):
    _require_normal_python()
    commit = _source_commit(source_profile)
    repo = Path(__file__).resolve().parents[2]
    verify_runtime_components(repo)
    began = time.perf_counter()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    pack = proposal_pack(output_root / 'proposal-pack', source_profile)
    env = Environment(id='ENV-B-DYNAMIC-CPU-' + source_profile.upper(), created_at=now, data_origin=origin,
                      engine={'name': 'vllm', 'version': 'reference-' + commit}).seal()
    policy = Policy(id='POL-B-DYNAMIC-CPU-' + source_profile.upper(), created_at=now, data_origin=origin,
                    status='DRAFT', pack=pack.ref(), scope={'include': list(CHECKS)}).seal()
    store = Store(output_root / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    shared = _SharedCapture(env, plan.ref(), source_root, source_profile,
        capture_fn=collector.capture, producer_sha256=COLLECTOR_SHA256, component_check=lambda: verify_runtime_components(repo))
    class BoundExecutor:
        name, version = _SharedCapture.name, _SharedCapture.version
        def run(self, check, environment, values, adapter, workdir):
            return shared.draft(check, environment, values, workdir, plan_ref=plan.ref(), source_profile=source_profile)
    executor = BoundExecutor()
    key = _SharedCapture.name + ':source-cpu-proposal'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda raw: executor
    try:
        evidences = run_plan(store, pack, plan, RunConfig(executors={_SharedCapture.name: 'source-cpu-proposal'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    results = judge_plan(store, pack, plan, as_of=now)
    replay = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
    if [result.ref() for result in replay] != [result.ref() for result in results]:
        raise ValueError('DYNAMIC_CPU_EXACT_REF_REJUDGMENT_MISMATCH')
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    for obj in (env, policy, plan, *evidences, *results, report):
        path = output_root / (obj.kind.value + ('-' + obj.check_id if hasattr(obj, 'check_id') else '') + '.json')
        with path.open('x') as stream:
            json.dump(obj.to_dict(), stream, ensure_ascii=False, indent=2)
    summary = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': commit,
        'product_component_commit': COMPONENT_COMMIT, 'collection_id': shared.collection_id,
        'capture_started_at': shared.started.isoformat() if shared.started else None,
        'capture_finished_at': shared.finished.isoformat() if shared.finished else None,
        'capture_invocations': int(shared.loaded), 'installed_target': None, 'runner_hook_verified': False,
        'permanent_registration_verified': False, 'pack': pack.ref().model_dump(mode='json'),
        'evidence': [evd.ref().model_dump(mode='json') for evd in evidences],
        'results': [result.ref().model_dump(mode='json') for result in results],
        'report': report.ref().model_dump(mode='json'),
        'checks': {result.check_id: {'execution_status': next(e.execution.status.value for e in evidences if e.check_id == result.check_id),
            'data_origin': next(e.data_origin.value for e in evidences if e.check_id == result.check_id),
            'verdicts': result.verdicts.model_dump(mode='json')} for result in results},
        'exact_ref_rejudgment_unchanged': True, 'store_verify_errors': store.verify(),
        'parent_wall_seconds': time.perf_counter() - began}
    with (output_root / 'summary.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
    return summary


def main():
    _require_normal_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', choices=tuple(SOURCE_PROFILES), default='reference5d')
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    summary = run_pipeline(args.source_root, args.output_root, source_profile=args.source_profile)
    print(json.dumps(summary))
    return 0 if all(check['execution_status'] == 'COMPLETED' for check in summary['checks'].values()) and not summary['store_verify_errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
