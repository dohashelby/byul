"""Static inventory regression tests; no SGLang runtime dependency."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine_sglang_inventory as inventory


class EngineSGLangExtractionTests(unittest.TestCase):
    def extract(self, body):
        return inventory.module_inventory("from fastapi import FastAPI\napp = FastAPI()\n" + body, "fixture.py")

    def test_conditional_and_else_are_preserved(self):
        d = self.extract("if flag:\n @app.get('/a')\n async def first(): pass\nelse:\n @app.get('/b')\n async def second(): pass\n")
        self.assertEqual([r['activation_conditions'][0]['branch'] for r in d['routes']], ['if', 'else'])
        self.assertEqual([r['activation_conditions'][0]['expression'] for r in d['routes']], ['flag', 'flag'])

    def test_nested_conditions_are_preserved(self):
        d = self.extract("if outer:\n if inner:\n  @app.post('/x')\n  def f(): pass\n")
        self.assertEqual([x['expression'] for x in d['routes'][0]['activation_conditions']], ['outer', 'inner'])

    def test_dynamic_path_is_not_evaluated(self):
        d = self.extract("@app.post(os.environ.get('PATH', '/default'))\ndef f(): pass\n")
        r = d['routes'][0]['registrations'][0]
        self.assertTrue(r['dynamic_path'])
        self.assertIsNone(r['path_literal'])
        self.assertIn("'/default'", r['path_expression'])

    def test_path_parameter_is_literal_template(self):
        r = self.extract("@app.get('/dump/{method}')\ndef f(): pass\n")['routes'][0]['registrations'][0]
        self.assertFalse(r['dynamic_path'])
        self.assertTrue(r['path_parameters'])

    def test_head_and_multi_method_declarations_are_distinct(self):
        d = self.extract("@app.get('/')\n@app.head('/')\ndef root(): pass\n@app.api_route('/x', methods=['POST', 'PUT'])\ndef change(): pass\n")
        self.assertEqual(len(d['routes']), 2)
        self.assertEqual([x['methods_declared'] for x in d['routes'][0]['registrations']], [['GET'], ['HEAD']])
        self.assertEqual(d['routes'][1]['registrations'][0]['methods_declared'], ['POST', 'PUT'])

    def test_included_router_alias_conditions_prefix_are_preserved(self):
        d = self.extract("if available:\n from sglang.srt.entrypoints.extra import router as extra_router\n app.include_router(extra_router, prefix='/extra')\n")
        inc = d['includes'][0]
        self.assertEqual(inc['import']['module'], 'sglang.srt.entrypoints.extra')
        self.assertEqual(inc['activation_conditions'][0]['expression'], 'available')
        self.assertEqual(inc['import']['conditions'][0]['expression'], 'available')
        self.assertEqual(inc['keyword_expressions']['prefix'], "'/extra'")

    def test_unknown_include_stays_unresolved(self):
        inc = self.extract("app.include_router(factory())\n")['includes'][0]
        self.assertIsNone(inc['import'])

    def test_same_function_name_and_duplicate_path_keep_distinct_identities(self):
        rs = self.extract("@app.get('/x')\ndef f(): pass\n@app.get('/x')\ndef f(): pass\n")['routes']
        self.assertNotEqual(rs[0]['source_function_id'], rs[1]['source_function_id'])
        self.assertEqual(rs[0]['registrations'][0]['path_literal'], rs[1]['registrations'][0]['path_literal'])

    def test_auth_declaration_does_not_become_effective_auth(self):
        r = self.extract("@app.post('/admin')\n@auth_level(AuthLevel.ADMIN_OPTIONAL)\ndef f(): pass\n")['routes'][0]
        self.assertEqual(r['auth_decorator_declarations'], ['auth_level(AuthLevel.ADMIN_OPTIONAL)'])
        self.assertEqual(r['effective_auth_status'], 'unverified')

    def test_unsupported_registration_and_control_flow_are_surfaced(self):
        d = self.extract("app.add_api_route('/x', handler)\nfor p in paths:\n @app.get(p)\n def f(): pass\n")
        self.assertEqual({x['reason'] for x in d['unresolved']}, {'non_decorator_registration_requires_manual_review', 'control_flow_requires_manual_review'})
        self.assertEqual(d['routes'][0]['activation_conditions'][0]['branch'], 'unresolved')

    def test_except_registration_is_surfaced_as_unsupported(self):
        d = self.extract("try:\n pass\nexcept Exception:\n @app.get('/fallback')\n def f(): pass\n")
        self.assertTrue(any(x['reason'] == 'control_flow_requires_manual_review' for x in d['unresolved']))

    def test_source_text_is_not_executed(self):
        d = self.extract("raise RuntimeError('must not execute')\n@app.get('/ok')\ndef f(): pass\n")
        self.assertEqual(len(d['routes']), 1)


class EngineSGLangArtifactTests(unittest.TestCase):
    def setUp(self):
        self.d = json.loads(inventory.DEFAULT_OUTPUT.read_text())

    def test_pinned_inventory_validates(self):
        self.assertEqual(inventory.validate_inventory(self.d)['candidate_instances'], 86)

    def test_included_router_auth_declarations(self):
        named = {r['function']: r for r in self.d['candidates']}
        self.assertEqual(named['get_loads']['auth_decorator_declarations'], [])
        for name in ['scale_elastic_ep', 'is_scaling_elastic_ep']:
            self.assertEqual(named[name]['auth_decorator_declarations'], ['auth_level(AuthLevel.ADMIN_OPTIONAL)'])

    def test_current_basis_delta_matches_current_and_historical_hashes(self):
        import engine_sglang_basis_delta as basis
        delta = json.loads(basis.OUTPUT.read_text())
        current = json.loads((basis.ROOT/'data/checklist.json').read_text())
        profile = json.loads(basis.PROFILE.read_text())
        current_checks = {c['id']: c for g in basis.GROUPS for c in current[g]}
        profile_checks = {c['check_id']: c for c in profile['checks']}
        self.assertEqual(basis.sha((basis.ROOT/'data/checklist.json').read_bytes()), delta['current_checklist_sha256'])
        self.assertEqual(delta['historical_checklist_sha256'], profile['canonical_sha256'])
        self.assertEqual((delta['changed_check_records'], delta['changed_historical_hashes']), (39, 7))
        self.assertEqual(delta['changed_test_branch_conditions'], ['CHK-LIF-004', 'CHK-LIF-012'])
        for row in delta['changes']:
            cid = row['check_id']
            self.assertEqual(row['current_criteria_sha256'], basis.criteria_hash(current_checks[cid]))
            self.assertEqual(row['historical_criteria_sha256'], profile_checks[cid]['criteria_sha256'])

    def test_mutations_do_not_validate(self):
        changes = {
            'drop_conditional': lambda d: d['candidates'].pop(next(i for i,r in enumerate(d['candidates']) if r['function']=='_dumper_control_handler')),
            'drop_include': lambda d: d['include_router_sites'].pop(),
            'duplicate_identity': lambda d: d['candidates'].__setitem__(-1, copy.deepcopy(d['candidates'][0])),
            'flatten_roots': lambda d: next(r for r in d['candidates'] if r['function']=='sglang_root')['activation_conditions'][0].update(branch='if'),
            'literal_dynamic': lambda d: next(r for r in d['candidates'] if r['function']=='ollama_root')['registrations'][0].update(dynamic_path=False),
            'drop_head': lambda d: next(r for r in d['candidates'] if r['function']=='ollama_root')['registrations'].pop(),
            'change_pin': lambda d: d['source_files'][inventory.MAIN].update(sha256='0'*64),
            'change_legacy': lambda d: d['legacy_inventory'].update(sha256='0'*64),
            'lost_legacy_row': lambda d: d['legacy_inventory']['unchanged_rows'].pop(),
            'claim_runtime_count': lambda d: d['counts'].update(actual_active_route_count=86),
            'claim_runtime_auth': lambda d: d['candidates'][0].update(effective_auth_status='verified'),
            'unresolved_include': lambda d: d['unresolved_static_sites'].append({'reason':'unknown'}),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                d = copy.deepcopy(self.d)
                change(d)
                with self.assertRaises((ValueError, KeyError)):
                    inventory.validate_inventory(d)


if __name__ == '__main__':
    unittest.main()
