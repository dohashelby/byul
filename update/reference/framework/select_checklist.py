#!/usr/bin/env python3
"""Read-only Q-level applicability assistant; no engine tests or security verdicts."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REFERENCE = {'engine': 'vllm', 'version': '0.31.0',
             'source_commit': 'db9527a46873454610df6dbedf79a36d6bf1a7f6'}


def known_version(env):
    return all(env.get(k) == v for k, v in REFERENCE.items())


def reviewed_absence(env, rule):
    """A reviewed exact-installation claim allows only a provisional exclusion.

    Expected evidence: {feature, status: 'reviewed', reviewer, reviewed_at,
    disabled: true, nonexposed: true, references: [location, ...],
    target: {engine, version, source_commit, image_digest}}.
    The tool validates structure/binding, not the truth of supplied evidence.
    """
    records = env.get('feature_absence_evidence', {})
    evidence = records.get(rule) if isinstance(records, dict) else None
    if not known_version(env):
        return False, '미확인 엔진·판본: 적용 매핑을 검토하기 전에는 제외할 수 없음'
    if not isinstance(evidence, dict):
        return False, '문자열 설명만으로 제외할 수 없음. 대상에 연결된 검수 기록 필요'
    for field in ('reviewer', 'reviewed_at'):
        if not isinstance(evidence.get(field), str) or not evidence[field].strip():
            return False, '검수자·검수 시각 필요'
    refs = evidence.get('references')
    if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x.strip() for x in refs):
        return False, '비사용·비노출 확인 자료 위치 필요'
    if (evidence.get('feature') != rule or evidence.get('status') != 'reviewed'
            or evidence.get('disabled') is not True or evidence.get('nonexposed') is not True):
        return False, '해당 기능의 비사용·비노출 검수 필요'
    target = evidence.get('target')
    keys = ('engine', 'version', 'source_commit', 'image_digest')
    if not isinstance(target, dict) or any(
            not isinstance(env.get(k), str) or not env[k].strip() or target.get(k) != env[k] for k in keys):
        return False, '근거 대상과 현재 엔진·판본·commit·이미지 식별자가 다르거나 누락됨'
    if env.get('profile_status') in ('계획값', 'planned'):
        return False, '계획 환경은 실제 비사용·비노출 확인 전까지 제외할 수 없음'
    return True, '동일 대상의 검수된 비사용·비노출 입력에 따른 후보. 근거의 진위와 제외 범위는 담당자 최종 확인 필요'


def select(catalog, env):
    out = []
    for item in catalog['items']:
        rule = item['applicability_rule']
        if rule == 'always' or item['id'] == 'Q-R05-11':
            state, reason = '적용', '공통 보호 요구. 실제 기능별 하위 시험을 선택해야 함'
            if item['id'] == 'Q-R05-11':
                reason = '실제 소비 파일·형식·대체 경로·파생 cache 확인은 공통 점검. 개별 분기 비사용으로 질문 전체를 제외하지 않음'
        else:
            value = env.get(rule)
            if rule == 'prefix_cache_or_shared_cache' and env.get('prefix_cache') is True:
                if value is False:
                    state, reason = '미정', '입력 모순: prefix_cache=true인데 cache 사용 조건은 false. 환경 입력 정정 필요'
                else:
                    state, reason = '적용', 'prefix_cache=true: cache 보호 점검 필요'
            elif value is True:
                state, reason = '적용', f'{rule}=true'
            elif value is False:
                accepted, reason = reviewed_absence(env, rule)
                state = '비적용 후보' if accepted else '미정'
            else:
                state, reason = '미정', '활성 여부 또는 검수된 비사용·비노출 근거 확인 필요'
        mapping = ('입력 엔진·commit 식별자가 기준과 일치함. 실제 설치·consumer·의존성·활성 기능 매핑은 별도 확인'
                   if known_version(env) else '엔진·판본별 consumer와 증거 위치 매핑 필요')
        out.append({'id': item['id'], 'title': item['title'], 'applicability': state,
                    'reason': reason, 'engine_mapping': mapping,
                    'policy_status': '미입력' if not env.get('policy_revision') else '항목별 채택·값 확인 필요',
                    'execution_status': '미실행', 'security_verdict': '미확인'})
    pending = []
    for key, message in [('r10_features', 'R10 기능 사용 여부 또는 검토 대기'),
                         ('managed_or_external_fallback', 'R14 책임·외부 fallback 검토 대기')]:
        accepted, reason = reviewed_absence(env, key)
        # Even reviewed absence does not claim coverage for unsupported features.
        if env.get(key) is False and accepted:
            pending.append(f'{message}: 비사용 검수 입력 있음. 기능 사용 환경의 평가 지원은 여전히 별도 검토 필요')
        else:
            pending.append(f'{message}: {reason}')
    context = []
    gateway = env.get('gateway')
    if gateway is True:
        context.append('Gateway 경유 입력: 사용자·앱 신원 전달, 직접 접근, 인증 제외 경로 및 관리 접근을 확인해야 함')
    elif gateway is False:
        context.append('Gateway 비사용 입력: 엔진 진입점의 인증·인가와 네트워크 접근 제한을 별도 설계·확인해야 함')
    else:
        context.append('Gateway 경유 여부 미확인')
    for key, label in [('nodes', '노드 수'), ('tp_groups', 'TP 그룹 수')]:
        value = env.get(key)
        if type(value) is not int or value < 1:
            context.append(f'{label} 미확인 또는 잘못된 입력')
        elif value > 1:
            context.append(f'{label}={value}: 단일 노드·TP 그룹 1개 참조를 넘어서는 통신·공유·실패 경로 검토 필요')
        else:
            context.append(f'{label}=1 입력. 실제 배포 확인 및 노드 내부 IPC·TP 점검 필요')
    return {'profile_id': env.get('id'), 'selection_unit': 'legacy_Q',
            'subtest_selection': '미구현: 77행 및 시나리오별 하위 시험을 자동 선택하지 않음',
            'scope_limitations': pending, 'context_checks': context, 'items': out,
            'notice': '입력 기반 질문 선택 보조이며 보안 판정·설치 확인 결과가 아님. 비적용 후보도 담당자가 근거와 제외 범위를 최종 검수해야 함.'}


def run_self_tests():
    """Run bounded selector regressions; no engine access or security verdict."""
    import unittest
    import sys
    m = sys.modules[__name__]
    CAT = {'items': [{'id': i, 'title': i, 'applicability_rule': r} for i, r in [
     ('Q-R05-11', 'alternate_sources_or_formats_or_derived_cache'),
     ('Q-C03', 'prefix_cache_or_shared_cache'), ('Q-C13', 'shared_or_remote_weights'),
     ('Q-C01', 'always')]]}
    
    def env():
        e = dict(m.REFERENCE, image_digest='sha256:example', profile_status='observed',
                 shared_or_remote_weights=False, feature_absence_evidence={})
        e['feature_absence_evidence']['shared_or_remote_weights'] = {
            'feature': 'shared_or_remote_weights', 'status': 'reviewed', 'reviewer': 'reviewer-a',
            'reviewed_at': '2026-10-18T12:00:00+09:00', 'disabled': True, 'nonexposed': True,
            'references': ['evidence/runtime-config.json', 'evidence/listener-inventory.json'],
            'target': {k: e[k] for k in ('engine', 'version', 'source_commit', 'image_digest')}}
        return e
    
    def state(e, ident):
        return next(x for x in m.select(CAT, e)['items'] if x['id'] == ident)['applicability']
    
    class SelectorTests(unittest.TestCase):
        def test_default_file_check_cannot_be_excluded(self):
            e = env(); e.update(alternate_sources_or_formats_or_derived_cache=False)
            e['feature_absence_evidence']['alternate_sources_or_formats_or_derived_cache'] = 'off'
            self.assertEqual(state(e, 'Q-R05-11'), '적용')
        def test_cache_true_activates_without_aggregate(self):
            e = env(); e['prefix_cache'] = True
            self.assertEqual(state(e, 'Q-C03'), '적용')
        def test_cache_conflict_cannot_exclude(self):
            e = env(); e.update(prefix_cache=True, prefix_cache_or_shared_cache=False)
            self.assertEqual(state(e, 'Q-C03'), '미정')
        def test_free_text_cannot_exclude(self):
            e = env(); e['feature_absence_evidence']['shared_or_remote_weights'] = 'anything'
            self.assertEqual(state(e, 'Q-C13'), '미정')
        def test_reviewed_exact_evidence_is_only_provisional(self):
            self.assertEqual(state(env(), 'Q-C13'), '비적용 후보')
        def test_stale_image_and_unreviewed_evidence(self):
            for field, value in [('image_digest', 'different'), ('source_commit', 'different')]:
                e = env(); e[field] = value
                self.assertEqual(state(e, 'Q-C13'), '미정')
            e = env(); e['feature_absence_evidence']['shared_or_remote_weights']['reviewer'] = ''
            self.assertEqual(state(e, 'Q-C13'), '미정')
        def test_unknown_version_even_matching_record_cannot_exclude(self):
            e = env(); e['version'] = '0.32.0'
            e['feature_absence_evidence']['shared_or_remote_weights']['target']['version'] = '0.32.0'
            self.assertEqual(state(e, 'Q-C13'), '미정')
        def test_planned_environment_cannot_exclude(self):
            e = env(); e['profile_status'] = '계획값'
            self.assertEqual(state(e, 'Q-C13'), '미정')
        def test_pending_scope_cannot_be_erased_by_text(self):
            e = env(); e.update(r10_features=False, managed_or_external_fallback=False)
            e['feature_absence_evidence'].update(r10_features='none', managed_or_external_fallback='none')
            self.assertEqual(len(m.select(CAT, e)['scope_limitations']), 2)
        def test_no_pass_or_subtest_selection(self):
            result = m.select(CAT, env())
            self.assertEqual(result['selection_unit'], 'legacy_Q')
            self.assertIn('미구현', result['subtest_selection'])
            self.assertTrue(all(x['security_verdict'] == '미확인' and x['execution_status'] == '미실행' for x in result['items']))
        def test_context_reads_topology_without_verdict(self):
            e = env(); e.update(gateway=False, nodes=2, tp_groups=2)
            msg = ' '.join(m.select(CAT, e)['context_checks'])
            self.assertIn('Gateway 비사용', msg); self.assertIn('노드 수=2', msg); self.assertIn('TP 그룹 수=2', msg)
        def test_malformed_boolean_not_truthy(self):
            e = env(); e['shared_or_remote_weights'] = 'false'
            self.assertEqual(state(e, 'Q-C13'), '미정')
    
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SelectorTests))
    if not result.wasSuccessful():
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('environment', type=Path, nargs='?')
    ap.add_argument('--self-test', action='store_true', help='Run selector regression checks without engine access')
    args = ap.parse_args()
    if args.self_test:
        run_self_tests()
        return
    if args.environment is None:
        ap.error('environment JSON is required unless --self-test is used')
    catalog = json.loads((ROOT / 'data/checklist.json').read_text())
    env = json.loads(args.environment.read_text())
    print(json.dumps(select(catalog, env), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
