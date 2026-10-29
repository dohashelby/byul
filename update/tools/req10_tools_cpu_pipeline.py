"""Reference CPU integration proposal through existing EVD/RES/RPT core.

Creates a dedicated DRAFT pack outside product packs and registers the existing
executor protocol only in this process. No engine/API/model is started.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import yaml

from guard.core.select import select
from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import registry
from guard.executors.structured_tools_cpu import PROFILE, StructuredToolsCPUExecutor
from guard.executors.structured_tools import SOURCE_COMMIT, judge_params
from guard.models import CheckDef, Environment, Policy, load_pack
from guard.store import Store
from guard.store.packs import put_pack
from tools.b1_integration_cpu import verify_tool_checkout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    verify_tool_checkout(Path(__file__).resolve().parents[2])
    args.output_root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    now = datetime.now(timezone.utc)
    # Explicit proposal definition: its Ref does not impersonate PACK-BASELINE.
    specs = {'structured_tools_validator_violation': 'bool', 'structured_tools_controls': 'dict[str, outcome]',
        'structured_tools_controls_planned': 'list[str]', 'structured_tools_cpu_rows': 'list[dict]',
        'runner_consumption_verified': 'bool'}
    check = CheckDef(id='REQ-10', revision=1, area='REQ', purpose='최종 입력 검증',
        question='고정 참조 CPU에서 schema/tool 조합의 요청 검증·sampling·xgrammar 변환 경로는?',
        review_order='reference_baseline_first', applies_when={'always': True},
        normal_control='고정 4개 정상 조합의 요청·sampling·backend 경계 수용',
        challenge_steps=['named-json 금지 조합의 request validator 경계 검증'],
        required_observations=[{'key': k, 'type': v} for k, v in specs.items()],
        satisfied_if='참조 CPU 경계 관측; 전체 보안 PASS 규칙 없음',
        fail_criteria='named-json 금지 조합의 실제 요청 클래스 수용',
        unknown_criteria='미수집·미지원 또는 실제 배포·runner 경계 미확인',
        executor='structured_tools_cpu', judge={'plugin': 'rule_table', 'params': judge_params()},
        limitations=['reference CPU 직접 호출. 실제 서버 전처리·배포·runner·서비스 생존 미측정.'])
    pack_dir = args.output_root / 'proposal-pack'
    (pack_dir / 'checks').mkdir(parents=True)
    check_bytes = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
    (pack_dir / 'checks/REQ-10.yaml').write_bytes(check_bytes)
    manifest = {'pack_id': 'PACK-B-REQ10-CPU-PROPOSAL', 'revision': 1, 'engines': ['vllm'], 'checks': ['REQ-10'],
        'files_sha256': [{'path': 'checks/REQ-10.yaml', 'sha256': hashlib.sha256(check_bytes).hexdigest()}],
        'review': {'status': 'DRAFT'}}
    (pack_dir / 'pack.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
    pack = load_pack(pack_dir)
    env = Environment(id='ENV-B-REQ10-CPU-REFERENCE', created_at=now, data_origin='DECLARED',
        engine={'name': 'vllm', 'version': f'reference-{SOURCE_COMMIT}'}, model={'id': 'fixture-model'}).seal()
    policy = Policy(id='POL-B-REQ10-CPU-PROPOSAL', created_at=now, data_origin='DECLARED',
        status='DRAFT', pack=pack.ref(), scope={'include': ['REQ-10']}).seal()
    store = Store(args.output_root / 'store')
    put_pack(store, pack_dir)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    key = 'structured_tools_cpu:reference-cpu-proposal'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: StructuredToolsCPUExecutor(cfg)
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={'structured_tools_cpu': 'reference-cpu-proposal'},
            raw={'source_root': str(args.source_root.resolve()), 'model': 'fixture-model'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    res, = judge_plan(store, pack, plan, as_of=now)
    rpt = build_report(store, pack, plan, as_of=now)
    store.put(rpt)
    for obj in (env, policy, plan, evd, res, rpt):
        with (args.output_root / f'{obj.kind.value}.json').open('x') as stream:
            json.dump(obj.to_dict(), stream, ensure_ascii=False, indent=2)
    summary = {'profile': PROFILE, 'scope': 'reference_cpu_core_integration_proposal',
        'permanent_registration_verified': False, 'pack': pack.ref().model_dump(mode='json'),
        'evidence': evd.ref().model_dump(mode='json'), 'result': res.ref().model_dump(mode='json'),
        'report': rpt.ref().model_dump(mode='json'), 'execution_status': evd.execution.status.value,
        'data_origin': evd.data_origin.value, 'observation_count': len(evd.observations),
        'collected_observations': [k for k, v in evd.observations.items() if v.collected],
        'artifact_count': len(evd.artifacts), 'parent_wall_seconds': time.perf_counter() - started}
    with (args.output_root / 'summary.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps(summary))
    return 0 if evd.execution.status.value == 'COMPLETED' and evd.artifacts else 2


if __name__ == '__main__':
    raise SystemExit(main())
