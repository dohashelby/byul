"""Q input/claim/selection metadata regressions. Synthetic only; no engine I/O."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('q_selector', ROOT / 'select_checklist.py')
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)
CATALOG = json.loads((ROOT / 'data/checklist.json').read_text())
LEGACY = json.loads((Path(__file__).parent / 'fixtures/q_selector_legacy_23.json').read_text())['cases']


def bind(env, feature='shared_or_remote_weights'):
    """Create a new synthetic reviewed claim; never used on original legacy cases."""
    env.setdefault('feature_absence_evidence', {})[feature] = {
        'feature': feature, 'status': 'reviewed', 'reviewer': 'EXAMPLE-reviewer',
        'reviewed_at': '2026-10-26T10:00:00+09:00', 'disabled': True, 'nonexposed': True,
        'references': ['EXAMPLE-config-and-all-feature-paths.json'],
        'target': {**{k: env[k] for k in ('engine', 'version', 'source_commit', 'image_digest')},
                   'environment_snapshot_sha256': selector.environment_snapshot_sha256(env)}}
    return env


def sample():
    return bind(copy.deepcopy(LEGACY[0]['input']))


def state(env, query='Q-C13'):
    return next(x for x in selector.select(CATALOG, env)['items'] if x['id'] == query)['applicability']


class SelectorInputTests(unittest.TestCase):
    def test_original_23_inputs_preserved_as_unbound_legacy(self):
        self.assertEqual(len(LEGACY), 23)
        for case in LEGACY:
            with self.subTest(case=case['case']):
                before = copy.deepcopy(case['input'])
                self.assertEqual(state(case['input'], case['inspected_q']), '미정')
                self.assertEqual(case['input'], before)

    def test_r14_followup_three_states(self):
        for value, phrase in [(True, '기능 사용 입력'), (None, '사용 여부 미정'), (False, '비사용 근거 검토 필요')]:
            with self.subTest(value=value):
                env = copy.deepcopy(LEGACY[0]['input']);env['managed_or_external_fallback'] = value
                note = selector.select(CATALOG, env)['scope_limitations'][1]
                self.assertIn(phrase, note)
                if value is not False:
                    self.assertNotIn('비사용 근거', note)

    def test_actual_profile_allowlist(self):
        for status in ['observed', '실제확인']:
            env = sample();env['profile_status'] = status;bind(env)
            self.assertEqual(state(env), '비적용 후보')
        for status in [None, 'unknown', 'PLANNED', '계획 값', True, 1, 'planned', '계획값', {}, []]:
            env = sample();env['profile_status'] = status;bind(env)
            self.assertEqual(state(env), '미정')
        env = sample();del env['profile_status'];bind(env)
        self.assertEqual(state(env), '미정')

    def test_plan_true_is_not_actual_feature_evidence(self):
        env = sample();env.update(profile_status='planned', shared_or_remote_weights=True)
        self.assertEqual(state(env), '미정')
        self.assertEqual(state(env, 'Q-C01'), '적용')

    def test_boolean_and_or_component_types(self):
        for key in ['shared_or_remote_weights', 'prefix_cache', 'prefix_cache_or_shared_cache']:
            for value in [0, 1, 'true', 'false', [], {}]:
                env = sample();env[key] = value;bind(env, 'prefix_cache_or_shared_cache')
                query = 'Q-C13' if key == 'shared_or_remote_weights' else 'Q-C03'
                self.assertEqual(state(env, query), '미정')

    def test_or_true_conflict_and_unknown(self):
        env = sample();env.update(prefix_cache=True, prefix_cache_or_shared_cache=False);bind(env,'prefix_cache_or_shared_cache')
        self.assertEqual(state(env,'Q-C03'), '미정')
        env = sample();env.pop('prefix_cache_or_shared_cache');self.assertEqual(state(env,'Q-C03'),'적용')
        env = sample();env.update(prefix_cache=False,prefix_cache_or_shared_cache=None)
        self.assertEqual(state(env,'Q-C03'),'미정')

    def test_whole_or_review_can_cover_unknown_component(self):
        env = sample();env.update(prefix_cache=None,prefix_cache_or_shared_cache=False);bind(env,'prefix_cache_or_shared_cache')
        self.assertEqual(state(env,'Q-C03'),'비적용 후보')
        env['feature_absence_evidence']['prefix_cache_or_shared_cache']['feature']='prefix_cache'
        self.assertEqual(state(env,'Q-C03'),'미정')

    def test_snapshot_binding_and_excluded_claims(self):
        env = sample();snap=selector.environment_snapshot_sha256(env)
        env['feature_absence_evidence']['shared_or_remote_weights']['reviewer']='another-reviewer'
        self.assertEqual(selector.environment_snapshot_sha256(env),snap)
        for key,value in [('id','new-profile'),('nodes',2),('model','new-model'),('runtime_evidence',['new.json']),('dependencies',{'sdk':'new'}),('policy_revision','new-pol')]:
            changed=copy.deepcopy(env);changed[key]=value
            self.assertNotEqual(selector.environment_snapshot_sha256(changed),snap)
            self.assertEqual(state(changed),'미정')

    def test_missing_binding_is_not_repaired(self):
        env=sample();target=env['feature_absence_evidence']['shared_or_remote_weights']['target'];del target['environment_snapshot_sha256']
        self.assertEqual(state(env),'미정');self.assertNotIn('environment_snapshot_sha256',target)

    def test_time_format_only(self):
        for stamp in ['2026-10-26T01:00:00Z','2026-10-26T10:00:00.123456+09:00','2099-01-01T00:00:00Z','2026-10-26T10:00:00-05:30']:
            env=sample();env['feature_absence_evidence']['shared_or_remote_weights']['reviewed_at']=stamp
            self.assertEqual(state(env),'비적용 후보')
        for stamp in ['not-a-time','2026-10-26','2026-10-26T10:00:00','2026-02-30T00:00:00Z','2026-10-26T25:00:00Z','2026-10-26T10:00:00+09:99','2026-10-26T10:00:00+00:60','2026-10-26T10:00:00+24:00',True]:
            env=sample();env['feature_absence_evidence']['shared_or_remote_weights']['reviewed_at']=stamp
            self.assertEqual(state(env),'미정')

    def test_digest_formats_and_binding(self):
        for digest in ['sha256:'+'a'*64,'registry.example:5000/team/image@sha256:'+'b'*64]:
            env=sample();env['image_digest']=digest;bind(env);self.assertEqual(state(env),'비적용 후보')
        for digest in ['not-a-digest','sha256:example','sha256:'+'A'*64,'sha256:'+'a'*63,'image:latest']:
            env=sample();env['image_digest']=digest;bind(env);self.assertEqual(state(env),'미정')

    def test_r10_r14_reviewed_absence_only_when_false(self):
        for key,index in [('r10_features',0),('managed_or_external_fallback',1)]:
            env=sample();env[key]=False;bind(env,key)
            self.assertIn('비사용 검수 입력 있음',selector.select(CATALOG,env)['scope_limitations'][index])
            env[key]=True
            self.assertIn('기능 사용 입력',selector.select(CATALOG,env)['scope_limitations'][index])
            env[key]=1
            self.assertIn('형식 오류',selector.select(CATALOG,env)['scope_limitations'][index])

    def test_selection_context_is_bound_and_role_explicit(self):
        env=sample();result=selector.select(CATALOG,env);ctx=result['selection_context']
        self.assertEqual(ctx,{'environment_snapshot_sha256':selector.environment_snapshot_sha256(env),'environment_file_bytes_sha256':None,'catalog_canonical_sha256':selector.canonical_sha256(CATALOG),'environment_role':None})
        self.assertEqual(selector.select(CATALOG,env,environment_role='candidate')['selection_context']['environment_role'],'candidate')
        with self.assertRaises(ValueError):selector.select(CATALOG,env,environment_role='production')
        self.assertTrue(all(x['security_verdict']=='미확인' and x['execution_status']=='미실행' for x in result['items']))
        self.assertEqual(result['selection_unit'],'legacy_Q')

    def test_cli_preserves_original_bytes_hash(self):
        env=sample();raw=json.dumps(env,ensure_ascii=False,indent=4).encode()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.json';path.write_bytes(raw)
            run=subprocess.run([sys.executable,str(ROOT/'select_checklist.py'),str(path),'--environment-role','test'],capture_output=True,check=True)
        ctx=json.loads(run.stdout)['selection_context'];self.assertEqual(ctx['environment_file_bytes_sha256'],hashlib.sha256(raw).hexdigest());self.assertEqual(ctx['environment_role'],'test')

    def test_strict_json_input(self):
        for raw in ['{"x":1,"x":2}','{"x":NaN}','{"x":1e999}']:
            with self.assertRaises(ValueError):selector.strict_json_loads(raw)
        for env in [None,[],1]:
            with self.assertRaises(ValueError):selector.select(CATALOG,env)

    def test_topology_missing_unknown_and_planned(self):
        env=sample();env.pop('tp_groups');self.assertIn('TP 그룹 수 미제공',' '.join(selector.select(CATALOG,env)['context_checks']))
        env['tp_groups']=None;self.assertIn('TP 그룹 수 미확인',' '.join(selector.select(CATALOG,env)['context_checks']))
        env['profile_status']='planned';env['tp_groups']=1;self.assertIn('계획값을 실제 배포값으로 복사하지 않음',' '.join(selector.select(CATALOG,env)['context_checks']))


if __name__ == '__main__':
    unittest.main()
