"""Independent mutations of canonical definitions, current forms, and design examples."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import verify_framework as framework
spec = importlib.util.spec_from_file_location('assessment_examples', ROOT/'개발전달/verify_assessment_record_examples.py')
examples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(examples)
CATALOG = json.loads((ROOT/'data/checklist.json').read_text())
TEMPLATE_RAW = (ROOT/'data/result_template.json').read_bytes()
TEMPLATE = json.loads(TEMPLATE_RAW)
DICTIONARY = json.loads((ROOT/'개발전달/결과기록_필드사전_초안.json').read_text())
DOC = json.loads((ROOT/'개발전달/examples/결과기록_합성예시_초안.json').read_text())
LEGACY = (ROOT/'평가기록/20261024_REQ013_반환정보_평가기록.json').read_bytes()
CHECKLIST_SHA = hashlib.sha256((ROOT/'data/checklist.json').read_bytes()).hexdigest()
TEMPLATE_SHA = hashlib.sha256(TEMPLATE_RAW).hexdigest()

def rows(catalog):
    return {r['id']:r for k,v in catalog.items() if k.endswith('_assessment_draft') and isinstance(v,list) for r in v}

class CanonicalSubresultTests(unittest.TestCase):
    def test_reviewed_catalog_and_current_form(self):
        self.assertEqual(framework.verify_required_subresults(list(rows(CATALOG).values())), [])
        self.assertEqual(framework.verify_result_template(CATALOG,TEMPLATE,DICTIONARY,CHECKLIST_SHA,TEMPLATE_SHA), [])

    def test_each_definition_field_and_owner_is_required(self):
        # Every one of the nine definitions is independently changed; the expected
        # contract is held by verifier code, never reconstructed from this input.
        for cid, contract in framework.REQUIRED_SUBRESULT_CONTRACT.items():
            for index in range(len(contract['required_subresults'])):
                for field in ('name','applies_when','satisfied_if'):
                    for change in ('delete','blank','different'):
                        with self.subTest(check=cid,index=index,field=field,change=change):
                            data=copy.deepcopy(CATALOG);unit=rows(data)[cid]['required_subresults'][index]
                            if change=='delete':unit.pop(field)
                            else:unit[field]='' if change=='blank' else 'unrelated'
                            self.assertTrue(framework.verify_required_subresults(list(rows(data).values())))
                data=copy.deepcopy(CATALOG);rows(data)[cid]['required_subresults'].pop(index)
                self.assertTrue(framework.verify_required_subresults(list(rows(data).values())))

    def test_inventory_deletion_duplication_and_move(self):
        for mutation in ('delete_all_fields','empty_all','delete_one_field','duplicate','move','wrong_type'):
            with self.subTest(mutation=mutation):
                data=copy.deepcopy(CATALOG);r=rows(data)
                if mutation=='delete_all_fields':
                    for cid in framework.REQUIRED_SUBRESULT_CONTRACT:r[cid].pop('required_subresults')
                elif mutation=='empty_all':
                    for cid in framework.REQUIRED_SUBRESULT_CONTRACT:r[cid]['required_subresults']=[]
                elif mutation=='delete_one_field':r['CHK-PLT-008'].pop('required_subresults')
                elif mutation=='duplicate':r['CHK-REQ-005']['required_subresults'].append(copy.deepcopy(r['CHK-REQ-005']['required_subresults'][0]))
                elif mutation=='move':r['CHK-OBS-004']['required_subresults']=[r['CHK-PLT-008']['required_subresults'].pop()]
                else:r['CHK-PLT-008']['required_subresults']={}
                self.assertTrue(framework.verify_required_subresults(list(r.values())))

    def test_full_verifier_rejects_mutations(self):
        paths = ['data/checklist.json','data/result_template.json',
                 '개발전달/결과기록_필드사전_초안.json','03_체크리스트와_보완가이드.md',
                 '01_프레임워크와_위협통제연결.md','노션_공유용/02_보안_프레임워크.md',
                 '노션_공유용/04_환경별_적용과_체크리스트.md','노션_공유용/05_위협통제_매트릭스.md']
        with tempfile.TemporaryDirectory(prefix='framework-contract-test-') as folder:
            root=Path(folder)
            for name in paths:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((ROOT/name).read_bytes())
            self.assertEqual(framework.verify(root)[0],[])
            for mutation in ('all_deleted','one_deleted','duplicate','moved','wrong_primary_anchor','wrong_contribution_anchor'):
                with self.subTest(mutation=mutation):
                    catalog=copy.deepcopy(CATALOG);r=rows(catalog)
                    if mutation=='all_deleted':
                        for cid in framework.REQUIRED_SUBRESULT_CONTRACT:r[cid].pop('required_subresults')
                    elif mutation=='one_deleted':r['CHK-PLT-008']['required_subresults'].pop()
                    elif mutation=='duplicate':r['CHK-REQ-005']['required_subresults'].append(copy.deepcopy(r['CHK-REQ-005']['required_subresults'][0]))
                    elif mutation=='moved':r['CHK-OBS-004']['required_subresults']=[r['CHK-PLT-008']['required_subresults'].pop()]
                    elif mutation=='wrong_primary_anchor':r['CHK-OBS-006']['purpose_ref']['anchor']='purpose-security-1-2'
                    else:r['CHK-OBS-006']['security_requirement_contributions'][0]['anchor']='purpose-security-5-4'
                    (root/'data/checklist.json').write_text(json.dumps(catalog,ensure_ascii=False))
                    errors,_=framework.verify(root)
                    expected='필수 세부 결과' if mutation not in ('wrong_primary_anchor','wrong_contribution_anchor') else '앵커'
                    self.assertTrue(any(expected in e for e in errors),errors)

    def test_requirement_ownership_cannot_be_reassigned(self):
        for cid in framework.REQUIRED_SUBRESULT_CONTRACT:
            data=copy.deepcopy(CATALOG);rows(data)[cid]['purpose_ref']['id']='requirement-8-1'
            self.assertTrue(framework.verify_required_subresults(list(rows(data).values())))
        data=copy.deepcopy(CATALOG);rows(data)['CHK-OBS-006']['security_requirement_contributions']=[]
        self.assertTrue(framework.verify_required_subresults(list(rows(data).values())))

    def test_document_table_is_checked_as_a_complete_table(self):
        data=rows(CATALOG)
        blocks={cid:'| 결과 | 적용 조건 | 충족 기준 |\n| --- | --- | --- |\n'+''.join('| '+' | '.join(sr[k] for k in ('name','applies_when','satisfied_if'))+' |\n' for sr in contract['required_subresults']) for cid,contract in framework.REQUIRED_SUBRESULT_CONTRACT.items()}
        self.assertEqual(framework.verify_required_subresults(list(data.values()),blocks),[])
        for cid in blocks:
            changed=copy.deepcopy(blocks);changed[cid]=changed[cid].rsplit('\n',2)[0]+'\n'
            self.assertTrue(framework.verify_required_subresults(list(data.values()),changed))

    def test_current_form_relations_and_dictionary(self):
        changes={'checklist_revision':'sha256:'+'0'*64,'protection_requirement_id':'requirement-1-2','test_branch_ref':{'name':'changed','scenario_ids':[],'apply_when':''},'shared_question_ids':[],'execution_status':'완료'}
        for field,value in changes.items():
            with self.subTest(field=field):
                t=copy.deepcopy(TEMPLATE);t[field]=value
                self.assertTrue(framework.verify_result_template(CATALOG,t,DICTIONARY,CHECKLIST_SHA,TEMPLATE_SHA))
        for field in TEMPLATE:
            t=copy.deepcopy(TEMPLATE);t.pop(field)
            self.assertIn('결과 양식: 기존 45필드 누락·변경',framework.verify_result_template(CATALOG,t,DICTIONARY,CHECKLIST_SHA,TEMPLATE_SHA))
        d=copy.deepcopy(DICTIONARY);d['fields'].pop()
        self.assertTrue(framework.verify_result_template(CATALOG,TEMPLATE,d,CHECKLIST_SHA,TEMPLATE_SHA))

class ExampleBoundaryTests(unittest.TestCase):
    def validate(self,doc,catalog=CATALOG):
        return examples.validate(doc,catalog,TEMPLATE,LEGACY,CHECKLIST_SHA)

    def test_examples_and_historical_routing(self):
        self.assertEqual(self.validate(DOC),[])
        original=json.loads(LEGACY)
        self.assertEqual(original.get('record_kind'), 'framework_assessment_review')
        self.assertFalse(DOC['historical_review_routing_example']['product_import_supported'])

    def test_catalog_and_example_co_deletion_does_not_remove_expected_contract(self):
        catalog=copy.deepcopy(CATALOG);doc=copy.deepcopy(DOC)
        for row in rows(catalog).values():row.pop('required_subresults',None)
        for case in doc['cases']:case['proposed_scope_extension']['required_subresults']=[]
        self.assertIn('CANONICAL_SUBRESULT_CONTRACT',self.validate(doc,catalog))

    def test_example_independent_connection_mutations(self):
        def ext(d,i=3):return d['cases'][i]['proposed_scope_extension']
        def swap_scope(d):
            contribution=ext(d)['requirement_contributions'][1]
            contribution['source_unit_refs']=[copy.deepcopy(ext(d)['required_subresults'][1]['unit_ref'])]
        mutations=[
          ('all_cases_removed',lambda d:d.update(cases=[]),'EXAMPLE_CHECK_INVENTORY'),
          ('duplicate_case',lambda d:d['cases'].append(copy.deepcopy(d['cases'][0])),'EXAMPLE_CHECK_INVENTORY'),
          ('all_results_removed',lambda d:ext(d).update(required_subresults=[]),'REQUIRED_SUBRESULT_INVENTORY'),
          ('wrong_unit_owner',lambda d:ext(d)['required_subresults'][0]['unit_ref'].update(assessment_id='CHK-REQ-005'),'UNIT_CHECK_MISMATCH'),
          ('wrong_unit_kind',lambda d:ext(d)['required_subresults'][0]['unit_ref'].update(kind='branch'),'UNIT_KIND'),
          ('blank_source_condition',lambda d:ext(d)['required_subresults'][0]['unit_ref'].update(applies_when=''),'SUBRESULT_SOURCE_DEFINITION'),
          ('wrong_source_repo',lambda d:ext(d)['required_subresults'][0]['unit_ref']['checklist_ref'].update(repository='other/repo'),'UNIT_SOURCE_REF'),
          ('no_contributions',lambda d:ext(d).update(requirement_contributions=[]),'CONTRIBUTION_INVENTORY'),
          ('duplicate_contribution',lambda d:ext(d)['requirement_contributions'].append(copy.deepcopy(ext(d)['requirement_contributions'][0])),'CONTRIBUTION_INVENTORY'),
          ('wrong_role',lambda d:ext(d)['requirement_contributions'][1].update(role='primary'),'CONTRIBUTION_ROLE'),
          ('empty_scope',lambda d:ext(d)['requirement_contributions'][1].update(source_unit_refs=[]),'EXAMPLE_CONTRIBUTION_SCOPE'),
          ('wrong_protection_scope',swap_scope,'EXAMPLE_CONTRIBUTION_SCOPE'),
          ('partial_primary',lambda d:ext(d)['requirement_contributions'][0]['source_unit_refs'].pop(),'EXAMPLE_CONTRIBUTION_SCOPE'),
          ('wrong_projection_requirement',lambda d:d['cases'][3]['framework_case_projection'].update(protection_requirement_id='requirement-1-2'),'PROJECTION_REQUIREMENT'),
        ]
        for name,mutate,expected in mutations:
            with self.subTest(case=name):
                doc=copy.deepcopy(DOC);mutate(doc)
                self.assertIn(expected,self.validate(doc))

if __name__=='__main__':unittest.main()
