#!/usr/bin/env python3
"""Read-only Q-level applicability assistant; no engine tests or security verdicts."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
REFERENCE = {'engine': 'vllm', 'version': '0.31.0',
             'source_commit': 'db9527a46873454610df6dbedf79a36d6bf1a7f6'}
OBSERVED_STATUSES = ('observed', '실제확인')
PLANNED_STATUSES = ('planned', '계획값')
ROLES = ('current', 'candidate', 'test')
FLAGS = ('gateway', 'prefix_cache', 'shared_or_remote_weights', 'r10_features',
         'managed_or_external_fallback', 'request_code_revision_selection', 'dynamic_import',
         'alternate_sources_or_formats_or_derived_cache', 'remote_acquisition_or_retry',
         'prefix_cache_or_shared_cache')
_DIGEST = re.compile(r'(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}')
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)')


def canonical_sha256(value):
    """Hash JSON values, not a file. Non-finite numbers are unsupported."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def environment_snapshot_sha256(env):
    """Bind all environment input except the claims themselves; never repair claims."""
    return canonical_sha256({k: v for k, v in env.items() if k != 'feature_absence_evidence'})


def strict_json_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('non-finite JSON number')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    # Also catches exponent overflow (1e999), which parse_constant does not see.
    canonical_sha256(value)
    return value


def known_version(env):
    return all(env.get(k) == v for k, v in REFERENCE.items())


def observed_profile(env):
    return type(env.get('profile_status')) is str and env['profile_status'] in OBSERVED_STATUSES


def valid_timestamp(value):
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None
    except ValueError:
        return False


def flag_issue(env, key):
    value = env.get(key)
    return key in env and value is not None and type(value) is not bool


def reviewed_absence(env, rule):
    """Check a reviewed claim's shape and exact input binding, not its truth.

    target contains engine/version/source_commit/image_digest and the canonical
    environment_snapshot_sha256 (all top-level input except absence claims).
    A legacy claim without this binding remains unresolved.
    """
    if not observed_profile(env):
        return False, '실제확인 상태 필요: observed 또는 실제확인; 계획·미정·누락·오형식은 제외 근거로 사용하지 않음'
    if not known_version(env):
        return False, '미확인 엔진·판본: 적용 매핑을 검토하기 전에는 제외할 수 없음'
    records = env.get('feature_absence_evidence', {})
    evidence = records.get(rule) if isinstance(records, dict) else None
    if not isinstance(evidence, dict):
        return False, '대상에 연결된 검수 기록과 비사용·비노출 자료 위치 필요'
    if type(evidence.get('reviewer')) is not str or not evidence['reviewer'].strip():
        return False, '검수자 필요'
    if not valid_timestamp(evidence.get('reviewed_at')):
        return False, '검수 시각 형식 필요: 시간대가 있는 RFC3339 시각'
    refs = evidence.get('references')
    if not isinstance(refs, list) or not refs or not all(type(x) is str and x.strip() for x in refs):
        return False, '비사용·비노출 확인 자료 위치 필요'
    if (evidence.get('feature') != rule or evidence.get('status') != 'reviewed'
            or evidence.get('disabled') is not True or evidence.get('nonexposed') is not True):
        return False, '해당 기능의 비사용·비노출 검수 필요'
    target = evidence.get('target')
    keys = ('engine', 'version', 'source_commit', 'image_digest')
    if not isinstance(target, dict) or any(
            type(env.get(k)) is not str or not env[k].strip() or target.get(k) != env[k] for k in keys):
        return False, '근거 대상과 현재 엔진·판본·commit·이미지 식별자가 다르거나 누락됨'
    if not _DIGEST.fullmatch(env['image_digest']):
        return False, '이미지 digest 형식 필요: sha256:64자리 소문자 16진수 또는 repository@sha256:digest'
    if target.get('environment_snapshot_sha256') != environment_snapshot_sha256(env):
        return False, '환경 snapshot 연결이 없거나 현재 입력과 다름. 구 claim 원본을 보존하고 재검토 필요'
    return True, '동일 입력 snapshot에 연결된 비사용·비노출 검수의 후보. 외부 근거의 진위·현재 유효성과 제외 범위는 담당자 확인 필요'


def feature_state(env, rule):
    if flag_issue(env, rule):
        return '미정', rule + ': true/false/null만 허용'
    if rule == 'prefix_cache_or_shared_cache':
        # The aggregate covers prefix cache OR any other shared cache. The only
        # separately supported component field is prefix_cache. A null component
        # can be covered by a reviewed claim about the whole aggregate.
        if flag_issue(env, 'prefix_cache'):
            return '미정', 'prefix_cache: true/false/null만 허용; OR 구성값 정정 필요'
        if env.get('prefix_cache') is True:
            if env.get(rule) is False:
                return '미정', '입력 모순: prefix_cache=true인데 cache 사용 조건은 false. 환경 입력 정정 필요'
            value = True
        else:
            value = env.get(rule)
    else:
        value = env.get(rule)
    if not observed_profile(env):
        return '미정', '계획·미정·누락·오형식의 환경값. 실제확인 상태와 대상 근거 확인 필요'
    if value is True:
        return '적용', ('prefix_cache=true: cache 보호 점검 필요' if rule == 'prefix_cache_or_shared_cache'
                          and env.get('prefix_cache') is True else f'{rule}=true')
    if value is False:
        accepted, reason = reviewed_absence(env, rule)
        return ('비적용 후보' if accepted else '미정'), reason
    return '미정', '활성 여부 또는 검수된 비사용·비노출 근거 확인 필요'


def select(catalog, env, *, environment_role=None, environment_file_bytes_sha256=None):
    if not isinstance(catalog, dict) or not isinstance(env, dict):
        raise ValueError('catalog and environment must be JSON objects')
    if environment_role is not None and environment_role not in ROLES:
        raise ValueError('unsupported environment role')
    if environment_file_bytes_sha256 is not None and (type(environment_file_bytes_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', environment_file_bytes_sha256) is None):
        raise ValueError('invalid environment file bytes hash')
    snapshot = environment_snapshot_sha256(env)
    out = []
    for item in catalog['items']:
        rule = item['applicability_rule']
        if rule == 'always' or item['id'] == 'Q-R05-11':
            state, reason = '적용', '공통 보호 요구. 실제 기능별 하위 시험을 선택해야 함'
            if item['id'] == 'Q-R05-11':
                reason = '실제 소비 파일·형식·대체 경로·파생 cache 확인은 공통 점검. 개별 분기 비사용으로 질문 전체를 제외하지 않음'
        else:
            state, reason = feature_state(env, rule)
        mapping = ('입력 엔진·commit 식별자가 기준과 일치함. 실제 설치·consumer·의존성·활성 기능 매핑은 별도 확인'
                   if known_version(env) else '엔진·판본별 consumer와 증거 위치 매핑 필요')
        out.append({'id': item['id'], 'title': item['title'], 'applicability': state,
                    'reason': reason, 'engine_mapping': mapping,
                    'policy_status': '미입력' if not env.get('policy_revision') else '항목별 채택·값 확인 필요',
                    'execution_status': '미실행', 'security_verdict': '미확인'})
    pending = []
    for key, label in [('r10_features', 'R10'), ('managed_or_external_fallback', 'R14')]:
        if flag_issue(env, key):
            pending.append(f'{label}: 사용 여부 입력 형식 오류. true/false/null로 확인 필요')
        elif not observed_profile(env):
            pending.append(f'{label}: 계획 또는 실제확인 상태 미정. 사용 여부와 추가 검토 범위 확인 필요')
        elif env.get(key) is True:
            pending.append(f'{label}: 기능 사용 입력. 책임·경계·지원 범위의 추가 검토 필요')
        elif env.get(key) is False:
            accepted, reason = reviewed_absence(env, key)
            pending.append(f'{label}: ' + ('비사용 검수 입력 있음. 기능 사용 환경의 평가 지원은 별도 검토 필요'
                                           if accepted else '비사용 근거 검토 필요: ' + reason))
        else:
            pending.append(f'{label}: 사용 여부 미정. 실제 사용·노출 경로 확인 필요')
    context = []
    if not observed_profile(env):
        context.append('환경은 계획값 또는 실제확인 상태 미정. 조건부 입력을 실제 확인값으로 채택하지 않음')
    for key in FLAGS:
        if flag_issue(env, key):
            context.append(f'{key}: true/false/null만 허용')
    gateway = env.get('gateway')
    if not observed_profile(env):
        context.append('Gateway 실제 사용 여부 확인 필요')
    elif gateway is True:
        context.append('Gateway 경유 입력: 사용자·앱 신원 전달, 직접 접근, 인증 제외 경로 및 관리 접근을 확인해야 함')
    elif gateway is False:
        context.append('Gateway 비사용 입력: 엔진 진입점의 인증·인가와 네트워크 접근 제한을 별도 설계·확인해야 함')
    else:
        context.append('Gateway 경유 여부 미확인')
    for key, label in [('nodes', '노드 수'), ('tp_groups', 'TP 그룹 수')]:
        value = env.get(key)
        if not observed_profile(env):
            context.append(f'{label}: 계획값을 실제 배포값으로 복사하지 않음')
        elif key not in env:
            context.append(f'{label} 미제공. 해당 엔진의 역할·지원 형식 확인 필요')
        elif value is None:
            context.append(f'{label} 미확인. 역할 부재는 해당 엔진의 배포 근거로 별도 확인')
        elif type(value) is not int or value < 1:
            context.append(f'{label} 잘못된 입력. 지원 형식은 양의 정수 또는 null')
        elif value > 1:
            context.append(f'{label}={value}: 단일 노드·TP 그룹 1개 참조를 넘어서는 통신·공유·실패 경로 검토 필요')
        else:
            context.append(f'{label}=1 입력. 실제 배포 확인 및 노드 내부 IPC·TP 점검 필요')
    return {'profile_id': env.get('id'), 'selection_unit': 'legacy_Q',
            'selection_context': {'environment_snapshot_sha256': snapshot,
                'environment_file_bytes_sha256': environment_file_bytes_sha256,
                'catalog_canonical_sha256': canonical_sha256(catalog), 'environment_role': environment_role},
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
        e = dict(m.REFERENCE, image_digest='sha256:' + 'a' * 64, profile_status='observed',
                 shared_or_remote_weights=False, feature_absence_evidence={})
        e['feature_absence_evidence']['shared_or_remote_weights'] = {
            'feature': 'shared_or_remote_weights', 'status': 'reviewed', 'reviewer': 'reviewer-a',
            'reviewed_at': '2026-10-18T12:00:00+09:00', 'disabled': True, 'nonexposed': True,
            'references': ['evidence/runtime-config.json', 'evidence/listener-inventory.json'],
            'target': {k: e[k] for k in ('engine', 'version', 'source_commit', 'image_digest')}}
        e['feature_absence_evidence']['shared_or_remote_weights']['target']['environment_snapshot_sha256'] = m.environment_snapshot_sha256(e)
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
    ap.add_argument('--environment-role', choices=ROLES, help='Input role, only when explicitly supplied')
    ap.add_argument('--self-test', action='store_true', help='Run selector regression checks without engine access')
    args = ap.parse_args()
    if args.self_test:
        run_self_tests()
        return
    if args.environment is None:
        ap.error('environment JSON is required unless --self-test is used')
    try:
        catalog = strict_json_loads((ROOT / 'data/checklist.json').read_bytes())
        raw = args.environment.read_bytes()
        env = strict_json_loads(raw)
        result = select(catalog, env, environment_role=args.environment_role,
                        environment_file_bytes_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        ap.error('unsupported environment/catalog JSON; check object shape, encoding and finite values')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
