"""SUP-10 live plugin CPU capture through a dedicated external DRAFT pack.

Uses the existing plugin projection/judge and core; registration is process local.
No observation JSON input, engine startup or installed-target claim is accepted.
"""
from __future__ import annotations

import argparse
import builtins
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
from guard.executors import plugin_loading, registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, load_pack, new_id
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tools import b1_plugin_cpu as collector
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'sup10-source-plugin-cpu-proposal-v1'
SOURCE_PROFILES = {
    'reference5d': '5d35ce3934a7804b298559d2d99eec33792e1eb9',
    'target031': 'db9527a46873454610df6dbedf79a36d6bf1a7f6',
}
GATE_SHA256 = '3245ecaac5b086905fc7fb6b7e37c7e3d355e4e50ab30a9abb2a51cbca961791'
COMPONENT_COMMIT = '42e62a14776893dec8d70d7bd8e49769ce08b4de'
# Reviewed collector snapshot 1839c77; runtime bytes never become approval.
COLLECTOR_SHA256 = '6da3fe88e3c89fa9307b3afcc255146d9bd9461d47ffbf7e8291348d8bd18fa5'
_CASES = {case: (group, configured) for case, group, configured in plugin_loading.CASES}
_STATUSES = {'completed', 'execution_error', 'child_execution_error', 'unsupported', 'partial', 'read_error'}
_VALIDATION_STATUSES = {'verified', 'unavailable', 'mismatch'}
_ERROR_TYPES = {name for name, value in vars(builtins).items()
                if isinstance(value, type) and issubclass(value, Exception)}


def _source_commit(source_profile):
    if source_profile not in SOURCE_PROFILES:
        raise ValueError('SUP10_SOURCE_PROFILE_UNSUPPORTED')
    return SOURCE_PROFILES[source_profile]


def _require_normal_python():
    if sys.flags.optimize or sys.version_info[:2] != (3, 12):
        raise ValueError('SUP10_CPU_REQUIRES_NORMAL_PYTHON312')


def verify_runtime_components(repo):
    """Reuse the reviewed component gate after verifying both tool identities."""
    gate_file = repo / 'update/tools/lif14_profiler_cpu.py'
    collector_file = repo / 'update/tools/b1_plugin_cpu.py'
    try:
        if (gate_file.is_symlink() or not Path(component_gate.__file__).resolve().samefile(gate_file)
                or not Path(inspect.getsourcefile(component_gate.verify_component)).resolve().samefile(gate_file)
                or hashlib.sha256(gate_file.read_bytes()).hexdigest() != GATE_SHA256
                or component_gate.PRODUCT_COMPONENT_COMMIT != COMPONENT_COMMIT):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('SUP10_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    if not isinstance(COLLECTOR_SHA256, str) or re.fullmatch('[0-9a-f]{64}', COLLECTOR_SHA256) is None:
        raise ValueError('SUP10_COLLECTOR_PIN_UNCONFIGURED')
    try:
        if (collector_file.is_symlink() or not Path(collector.__file__).resolve().samefile(collector_file)
                or not Path(inspect.getsourcefile(collector.capture)).resolve().samefile(collector_file)
                or hashlib.sha256(collector_file.read_bytes()).hexdigest() != COLLECTOR_SHA256):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('SUP10_COLLECTOR_IDENTITY_OR_BYTES_MISMATCH') from None
    component_gate.verify_component(repo)


def _check_environment(check, env, source_profile):
    commit = _source_commit(source_profile)
    if (check.id != 'SUP-10' or not env.hash_ok() or env.engine.name != 'vllm'
            or env.engine.version != 'reference-' + commit):
        raise ValueError('SUP10_CPU_ENVIRONMENT_MISMATCH')
    return commit


def _status(value):
    return value if isinstance(value, str) and value in _STATUSES else 'unknown'


def _events(value):
    try:
        return plugin_loading.sanitize_events(value)
    except (TypeError, ValueError):
        return None


def _safe_error_type(value):
    return value if isinstance(value, str) and value in _ERROR_TYPES else 'Exception'


def _safe_validation(value):
    validation = value if isinstance(value, dict) else {}
    post = validation.get('post') if isinstance(validation.get('post'), dict) else {}
    pre_status, post_status = validation.get('pre'), post.get('status')
    safe = {'pre': pre_status if isinstance(pre_status, str) and pre_status in _VALIDATION_STATUSES else 'unavailable',
            'post': {'status': post_status if isinstance(post_status, str) and post_status in _VALIDATION_STATUSES else 'unavailable'},
            'boundary': 'source_fixture_component' if validation.get('boundary') == 'source_fixture_component' else 'unavailable'}
    if 'error_type' in post:
        safe['post']['error_type'] = _safe_error_type(post['error_type'])
    return safe


def _qualified_rows(rows, source_profile):
    """Attribute events per child before reusing the unchanged product projection."""
    qualified = []
    for row in rows:
        if not isinstance(row, dict):
            qualified.append(row)
            continue
        validation = row.get('source_validation')
        validation = validation if isinstance(validation, dict) else {}
        post = validation.get('post')
        post = post if isinstance(post, dict) else {}
        if (row.get('target_started') is True
                and row.get('source_profile') == source_profile
                and row.get('source_commit') == _source_commit(source_profile)
                and validation.get('pre') == 'verified'
                and validation.get('boundary') == 'source_fixture_component'
                and isinstance(post.get('status'), str)
                and post.get('status') in {'verified', 'unavailable'}):
            qualified.append(row)
        else:
            qualified.append({'case_id': row.get('case_id'), 'status': 'source_attribution_unavailable'})
    return qualified


def _safe_rows(rows):
    """Persist only the existing case/event vocabulary, never arbitrary extras."""
    safe = []
    for row in rows:
        if not isinstance(row, dict) or row.get('case_id') not in _CASES:
            continue
        case = row['case_id']
        group, configured = _CASES[case]
        after = row.get('after') if isinstance(row.get('after'), dict) else {}
        safe.append({'case_id': case, 'group': group, 'configured_names': configured,
            'status': _status(row.get('status')), 'target_started': row.get('target_started') is True,
            'events': _events(row.get('events', [])),
            'event_collection_status': _status(row.get('event_collection_status', 'completed')),
            'source_profile': row.get('source_profile') if isinstance(row.get('source_profile'), str) and row.get('source_profile') in SOURCE_PROFILES else None,
            'source_commit': row.get('source_commit') if isinstance(row.get('source_commit'), str) and row.get('source_commit') in SOURCE_PROFILES.values() else None,
            'source_validation': _safe_validation(row.get('source_validation')),
            'after': {'status': _status(after.get('status')), 'events': _events(after.get('events', []))}})
    return safe


def _validated_record(record, source_profile, producer_sha256):
    commit = _source_commit(source_profile)
    if not isinstance(record, dict):
        raise ValueError('SUP10_CPU_RECORD_INVALID')
    validation = record.get('source_validation')
    hashes = record.get('fixture_file_bytes_sha256')
    if (record.get('source_profile') != source_profile or record.get('source_commit') != commit
            or record.get('own_producer_sha256') != producer_sha256 or record.get('installed_target') is not None
            or record.get('capture_status') not in {'completed', 'incomplete'}
            or not isinstance(record.get('results'), list)
            or not isinstance(validation, dict) or validation.get('pre') != 'verified'
            or validation.get('boundary') != 'source_fixture_component'
            or not isinstance(hashes, dict) or not hashes):
        raise ValueError('SUP10_CPU_RECORD_ATTRIBUTION_MISMATCH')
    for name, digest in hashes.items():
        path = Path(name) if isinstance(name, str) else None
        if (path is None or path.is_absolute() or '..' in path.parts
                or not name.startswith('byul_b_cpu_probe/') or path.suffix != '.py'
                or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
            raise ValueError('SUP10_CPU_FIXTURE_HASH_INVALID')
    post = validation.get('post')
    post_status = post.get('status') if isinstance(post, dict) else None
    if not isinstance(post_status, str) or post_status not in _VALIDATION_STATUSES:
        post_status = 'unavailable'
    safe = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': commit,
        'scope': 'source_cpu_installed_plugin_entry_points', 'installed_target': None,
        'runner_hook_verified': False, 'capture_status': record['capture_status'],
        'source_validation': {'pre': 'verified', 'post': {'status': post_status}, 'boundary': 'source_fixture_component'},
        'own_producer_sha256': producer_sha256, 'fixture_file_bytes_sha256': dict(hashes),
        'results': _safe_rows(record['results'])}
    auxiliary = record.get('auxiliary_write')
    if (isinstance(auxiliary, dict) and isinstance(auxiliary.get('status'), str)
            and auxiliary['status'] in {'completed', 'failed'}):
        safe['auxiliary_write'] = {'status': auxiliary['status']}
        if auxiliary['status'] == 'failed':
            safe['auxiliary_write']['error_type'] = _safe_error_type(auxiliary.get('error_type'))
    return safe


def _normal_control(rows):
    by_case = {row.get('case_id'): row for row in rows if isinstance(row, dict)}
    attempted = any(isinstance(row.get('after'), dict) for row in by_case.values())
    succeeded = set(by_case) == set(_CASES)
    for case in _CASES:
        row = by_case.get(case, {})
        after = row.get('after') if isinstance(row.get('after'), dict) else {}
        events = _events(after.get('events', []))
        succeeded = succeeded and after.get('status') == 'completed' and events is not None and any(
            event == {'plugin': 'byul-approved', 'phase': 'general_hook'} for event in events or [])
    return attempted, bool(succeeded)


def _write_artifact(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True, indent=2)


def _produce_draft(check, env, workdir, *, source_root, source_profile, capture_fn, producer_sha256):
    """Private callable boundary for explicitly EXAMPLE contracts and the live executor."""
    _require_normal_python()
    commit = _check_environment(check, env, source_profile)
    started = datetime.now(timezone.utc)
    origin = DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
    config = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': commit,
              'runner_hook_verified': False, 'installed_target': None}
    try:
        raw = capture_fn(Path(source_root), Path(workdir) / 'owned-plugin-cpu', source_profile=source_profile)
        record = _validated_record(raw, source_profile, producer_sha256)
        qualified = _qualified_rows(raw['results'], source_profile)
        observations = plugin_loading.project(qualified)
    except Exception:
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=origin,
            normal_control={'attempted': False, 'succeeded': None}, config=config,
            abort_reason='SUP10_CPU_CAPTURE_UNAVAILABLE', started_at=started, finished_at=datetime.now(timezone.utc))
    attempted, normal = _normal_control(qualified)
    complete = (record['capture_status'] == 'completed'
                and record['source_validation']['post']['status'] == 'verified'
                and record.get('auxiliary_write', {}).get('status') != 'failed'
                and all(row['source_validation']['post']['status'] == 'verified' for row in record['results'])
                and observations['plugin_cpu_cases_complete']['value'] is True and normal)
    observations['runner_hook_verified'] = {'value': False, 'collected': True}
    draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED, data_origin=origin,
        normal_control={'attempted': attempted, 'succeeded': bool(normal and complete) if attempted else None},
        observations=observations, config=config,
        challenges=[{'step': step, 'attempted': bool(raw['results'])} for step in check.challenge_steps],
        abort_reason=(None if complete else 'SUP10_CPU_CAPTURE_INCOMPLETE'
                      + ('; SUP10_CPU_AUXILIARY_WRITE_FAILED'
                         if record.get('auxiliary_write', {}).get('status') == 'failed' else '')),
        started_at=started, finished_at=datetime.now(timezone.utc))
    artifact = Path(workdir) / 'plugin-source-cpu.json'
    try:
        _write_artifact(artifact, record)
        draft.artifacts = [DraftArtifact(name=artifact.name, path=artifact, collector=PROFILE)]
    except Exception:
        return incomplete_artifact_capture(draft)
    return draft


class PluginCPUProposalExecutor:
    name, version = 'plugin_loading_cpu', '0.1.0'

    def __init__(self, cfg):
        if type(cfg) is not dict or set(cfg) != {'source_root', 'source_profile'}:
            raise ValueError('SUP10_CPU_CONFIGURATION_INVALID')
        _source_commit(cfg['source_profile'])
        self.cfg = dict(cfg)

    def run(self, check, env, policy_values, adapter, workdir):
        if policy_values:
            raise ValueError('SUP10_CPU_ENTERPRISE_POLICY_UNSET')
        repo = Path(__file__).resolve().parents[2]
        verify_runtime_components(repo)
        draft = _produce_draft(check, env, workdir, **self.cfg,
                               capture_fn=collector.capture, producer_sha256=COLLECTOR_SHA256)
        try:
            verify_runtime_components(repo)
        except Exception:
            draft.status = ExecStatus.ABORTED
            code = 'SUP10_COMPONENT_POSTCHECK_UNAVAILABLE'
            draft.abort_reason = f'{draft.abort_reason}; {code}' if draft.abort_reason else code
            if draft.normal_control.attempted:
                draft.normal_control.succeeded = False
        return draft


def proposal_pack(folder, source_profile):
    _source_commit(source_profile)
    check = CheckDef(id='SUP-10', revision=1, area='SUP', purpose='공급물 실행 경계',
        question='선정 source CPU의 설치 plugin loader가 고정 group/configuration 경계를 지키는가?',
        review_order='source_cpu_reference_first', applies_when={'always': True},
        normal_control='각 10개 사례 직후 승인된 general hook의 실제 호출',
        challenge_steps=['기존 고정 10개 사례의 plugin import/factory/hook 관측'],
        required_observations=[{'key': key, 'type': kind} for key, kind in (
            ('plugin_cpu_phases', 'dict[str, phases]'), ('plugin_loading_confirmed_violations', 'list[str]'),
            ('plugin_cpu_cases_complete', 'bool'), ('runner_hook_verified', 'bool'))],
        satisfied_if='선정 source의 제한된 관측; 기업 plugin 허용 정책은 별도 입력 필요',
        fail_criteria='기존 source filter의 독립적인 허용 경계 위반',
        unknown_criteria='미수집·source 귀속 미확인 또는 실제 runner/기업 정책 미확정',
        executor=PluginCPUProposalExecutor.name, judge={'plugin': 'rule_table', 'params': plugin_loading.judge_params()},
        limitations=['설치 local fixture의 CPU 직접 호출. 배포·runner hook·기업 승인 plugin 정책 미검증.'])
    (folder / 'checks').mkdir(parents=True)
    body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
    (folder / 'checks/SUP-10.yaml').write_bytes(body)
    manifest = {'pack_id': 'PACK-B-SUP10-PLUGIN-CPU-' + source_profile.upper(), 'revision': 1,
        'engines': ['vllm'], 'checks': ['SUP-10'],
        'files_sha256': [{'path': 'checks/SUP-10.yaml', 'sha256': hashlib.sha256(body).hexdigest()}],
        'review': {'status': 'DRAFT'}}
    (folder / 'pack.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
    return load_pack(folder)


def run_pipeline(source_root, output_root, *, source_profile='reference5d', origin=DataOrigin.DECLARED):
    _require_normal_python()
    commit = _source_commit(source_profile)
    verify_runtime_components(Path(__file__).resolve().parents[2])
    started = time.perf_counter()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    pack = proposal_pack(output_root / 'proposal-pack', source_profile)
    env = Environment(id=new_id('ENV'), created_at=now, data_origin=origin,
        engine={'name': 'vllm', 'version': 'reference-' + commit}).seal()
    policy = Policy(id=new_id('POL'), created_at=now, data_origin=origin,
        status='DRAFT', pack=pack.ref(), scope={'include': ['SUP-10']}).seal()
    store = Store(output_root / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    key = PluginCPUProposalExecutor.name + ':source-cpu-proposal'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda raw: PluginCPUProposalExecutor(raw)
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={PluginCPUProposalExecutor.name: 'source-cpu-proposal'},
            raw={'source_root': str(Path(source_root).resolve()), 'source_profile': source_profile}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    res, = judge_plan(store, pack, plan, as_of=now)
    replay, = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
    if replay.ref() != res.ref() or replay.evidence != evd.ref():
        raise ValueError('SUP10_EXACT_REF_REJUDGMENT_MISMATCH')
    rpt = build_report(store, pack, plan, as_of=now)
    store.put(rpt)
    for obj in (env, policy, plan, evd, res, rpt):
        with (output_root / (obj.kind.value + '.json')).open('x') as stream:
            json.dump(obj.to_dict(), stream, ensure_ascii=False, indent=2)
    summary = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': commit,
        'product_component_commit': COMPONENT_COMMIT, 'installed_target': None, 'runner_hook_verified': False,
        'permanent_registration_verified': False, 'execution_status': evd.execution.status.value,
        'data_origin': evd.data_origin.value, 'pack': pack.ref().model_dump(mode='json'),
        'evidence': evd.ref().model_dump(mode='json'), 'result': res.ref().model_dump(mode='json'),
        'report': rpt.ref().model_dump(mode='json'), 'verdicts': res.verdicts.model_dump(mode='json'),
        'observation_count': len(evd.observations), 'artifact_count': len(evd.artifacts),
        'exact_ref_rejudgment_unchanged': True, 'store_verify_errors': store.verify(),
        'parent_wall_seconds': time.perf_counter() - started}
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
    return 0 if (summary['execution_status'] == 'COMPLETED' and summary['artifact_count']
                 and not summary['store_verify_errors']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
