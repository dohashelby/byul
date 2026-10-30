"""Pinned component CPU profiler proposal through existing EVD/RES/RPT core."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import time
import yaml

from guard.core.select import select
from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import registry
from guard.executors.profiler_lifecycle_cpu import SOURCE_COMMIT, PROFILE, ProfilerLifecycleCPUExecutor, judge_params
from guard.models import CheckDef, Environment, Policy, load_pack
from guard.store import Store
from guard.store.packs import put_pack
from tools import b1_integration_cpu as snapshot_helper

BASE_SHA = 'c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
PINNED_PATHS = ('update/src/guard', 'update/packs', 'update/uv.lock',
                'update/pyproject.toml', 'update/reference/framework')
HELPER_SHA256 = '858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'

# Filled with the reviewed frozen module commit by the coordinating session.
# An unconfigured pin refuses execution; HEAD is never used as the expected pin.
PRODUCT_COMPONENT_COMMIT = '033972019f117d78c1742a3f958fe8d7910087ce'


def verify_component(repo):
    if not isinstance(PRODUCT_COMPONENT_COMMIT, str) or re.fullmatch('[0-9a-f]{40}', PRODUCT_COMPONENT_COMMIT) is None:
        raise ValueError('LIF14_PRODUCT_COMPONENT_PIN_UNCONFIGURED')
    helper_file = repo / 'update/tools/b1_integration_cpu.py'
    try:
        imported = Path(snapshot_helper.__file__).resolve()
        function_file = Path(inspect.getsourcefile(snapshot_helper.verify_snapshot)).resolve()
        if (helper_file.is_symlink() or not imported.samefile(helper_file) or not function_file.samefile(helper_file)
                or hashlib.sha256(helper_file.read_bytes()).hexdigest() != HELPER_SHA256):
            raise ValueError
        if snapshot_helper.BASE_SHA != BASE_SHA or snapshot_helper.PINNED_PATHS != PINNED_PATHS:
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('LIF14_SNAPSHOT_HELPER_IDENTITY_OR_SCOPE_MISMATCH') from None
    expected = repo / 'update/src/guard'
    for name, module in tuple(sys.modules.items()):
        if name != 'guard' and not name.startswith('guard.'):
            continue
        filename = getattr(module, '__file__', None)
        if filename is None or not any(parent.is_dir() and parent.samefile(expected) for parent in Path(filename).resolve().parents):
            raise ValueError('LIF14_OWN_PRODUCT_IMPORT_MISMATCH')
    snapshot_helper.verify_snapshot(repo, PRODUCT_COMPONENT_COMMIT, PINNED_PATHS)


def main():
    if sys.flags.optimize:
        raise ValueError('LIF14_CPU_REQUIRES_ASSERTIONS')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    verify_component(repo)
    started = time.perf_counter()
    args.output_root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    check = CheckDef(id='LIF-14', revision=1, area='LIF', purpose='종결·자원 회수',
        question='선정 CPU profiler의 종료와 실제 native 수집·trace 산출·후속 시작은 일치하는가?',
        review_order='conditional_when_selected', applies_when={'always': True},
        normal_control='실제 CPU tag 수집과 정상 trace 산출 완료',
        challenge_steps=['정상 CPU profiler 종료 관측', '별도 소유 경로의 trace writer 오류와 같은 wrapper 복구 관측'],
        required_observations=[{'key': key, 'type': type_} for key, type_ in (
            ('profiler_cpu_stop_mismatch', 'bool'), ('profiler_cpu_trials', 'list[dict]'), ('profiler_cpu_deadline_policy', 'str'))],
        satisfied_if='선정 source CPU 경계 관측; 기업 기한 정책 확정 필요',
        fail_criteria='wrapper 종료 표시와 확인한 native 수집 상태의 불일치',
        unknown_criteria='native 미수집 또는 기업 기한 정책 미설정', executor='profiler_lifecycle_cpu',
        judge={'plugin': 'rule_table', 'params': judge_params()},
        limitations=['CPU reference source 직접 호출. HTTP·worker·GPU·전체 engine 생존과 기업 기한 정책은 미검증.'])
    pack_dir = args.output_root / 'proposal-pack'
    (pack_dir / 'checks').mkdir(parents=True)
    body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
    (pack_dir / 'checks/LIF-14.yaml').write_bytes(body)
    manifest = {'pack_id': 'PACK-B-LIF14-CPU-PROPOSAL', 'revision': 1, 'engines': ['vllm'], 'checks': ['LIF-14'],
        'files_sha256': [{'path': 'checks/LIF-14.yaml', 'sha256': hashlib.sha256(body).hexdigest()}], 'review': {'status': 'DRAFT'}}
    (pack_dir / 'pack.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
    pack = load_pack(pack_dir)
    env = Environment(id='ENV-B-LIF14-CPU-REFERENCE', created_at=now, data_origin='DECLARED',
        engine={'name': 'vllm', 'version': 'reference-' + SOURCE_COMMIT}).seal()
    policy = Policy(id='POL-B-LIF14-CPU-PROPOSAL', created_at=now, data_origin='DECLARED', status='DRAFT', pack=pack.ref()).seal()
    store = Store(args.output_root / 'store')
    put_pack(store, pack_dir)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    key = 'profiler_lifecycle_cpu:component-cpu-proposal'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: ProfilerLifecycleCPUExecutor(cfg)
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={'profiler_lifecycle_cpu': 'component-cpu-proposal'},
            raw={'source_root': str(args.source_root.resolve()), 'owned_parent': str(args.output_root / 'owned-cpu')}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    res, = judge_plan(store, pack, plan, as_of=now)
    rpt = build_report(store, pack, plan, as_of=now)
    store.put(rpt)
    verify_component(repo)
    for obj in (env, policy, plan, evd, res, rpt):
        with (args.output_root / (obj.kind.value + '.json')).open('x') as stream:
            json.dump(obj.to_dict(), stream, ensure_ascii=False, indent=2)
    summary = {'profile': PROFILE, 'source_commit': SOURCE_COMMIT, 'product_component_commit': PRODUCT_COMPONENT_COMMIT,
        'common_base_reference_commit': BASE_SHA, 'permanent_registration_verified': False,
        'execution_status': evd.execution.status.value, 'data_origin': evd.data_origin.value,
        'pack': pack.ref().model_dump(mode='json'), 'evidence': evd.ref().model_dump(mode='json'),
        'result': res.ref().model_dump(mode='json'), 'report': rpt.ref().model_dump(mode='json'),
        'verdicts': res.verdicts.model_dump(mode='json'), 'observation_count': len(evd.observations),
        'artifact_count': len(evd.artifacts), 'store_verify_errors': store.verify(),
        'parent_wall_seconds': time.perf_counter() - started}
    with (args.output_root / 'summary.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps(summary))
    return 0 if evd.execution.status.value == 'COMPLETED' and evd.artifacts else 2


if __name__ == '__main__':
    raise SystemExit(main())
