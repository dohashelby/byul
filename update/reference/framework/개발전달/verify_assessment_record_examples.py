#!/usr/bin/env python3
"""Validate design examples only; no product store, engine, or security evaluation."""
from pathlib import Path
import copy
import hashlib
import json
import argparse
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from verify_framework import REQUIRED_SUBRESULT_CONTRACT, verify_required_subresults

# This mapping fixes the protection scopes demonstrated by this EXAMPLE fixture.
# It is not a product schema or a universal execution/evidence allocation rule.
EXAMPLE_SUPPORTING_SCOPES = {
 'CHK-REQ-005': {},
 'CHK-PLT-008': {'requirement-1-1':['내부 IPC·TP 전송 보호'], 'requirement-1-2':['내부 IPC·TP 전송 보호']},
 'CHK-LIF-013': {'requirement-1-2':['전체 release 권한'], 'requirement-2-2':['재취득·대체 공급'],
                 'requirement-6-4':['전체 release 권한','재취득·대체 공급','기존 참조의 수명'],
                 'requirement-7-4':['전체 release 권한','재취득·대체 공급','기존 참조의 수명']},
 'CHK-OBS-006': {'requirement-1-2':['조회·반출 권한']},
}
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def resolve_catalog(catalog):
 rows={r['id']:r for k,a in catalog.items() if k.endswith('_assessment_draft') and isinstance(a,list) for r in a}
 focuses={}
 for q in catalog['items']:
  for s in q.get('subtests',[]):
   f=focuses.setdefault(s['id'],{'primary':s['primary_question'],'scenario_id':s['scenario_id'],'membership':set()})
   if f['primary']!=s['primary_question'] or f['scenario_id']!=s['scenario_id']:raise ValueError('CATALOG_FOCUS_CONFLICT')
   f['membership'].add(q['id'])
 return rows,focuses
def unit_complete(unit):
 if unit['applicability']['status']=='NOT_APPLICABLE':return True
 execution=unit['execution'];nc=unit['normal_control_result']
 by_key={e['key']:e for e in unit['evidence']}
 by_step={r['step']:r for r in execution.get('challenge_results',[])}
 return (unit['required_observation_keys']==['unit-observation']
         and unit['required_challenge_steps']==['allow-control','violation-probe']
         and execution['status']=='COMPLETED' and nc['attempted'] and nc['succeeded'] is True
         and not unit['missing_evidence']
         and all(key in by_key and by_key[key]['collected'] for key in unit['required_observation_keys'])
         and all(step in by_step and by_step[step]['attempted'] for step in unit['required_challenge_steps']))
def aggregate(units):
 relevant=[u for u in units if u['applicability']['status']!='NOT_APPLICABLE']
 vals=[u['verdicts']['security'] for u in relevant]
 if 'FAIL' in vals:return 'FAIL'
 if 'UNKNOWN' in vals or any(not unit_complete(u) or u['applicability']['status'] in ['UNDETERMINED','CONFLICT'] for u in relevant):return 'UNKNOWN'
 return 'PASS' if relevant else 'NOT_APPLICABLE'
def validate(document,catalog,template,legacy_raw,checklist_sha):
 errors=[];rows,focuses=resolve_catalog(catalog)
 if verify_required_subresults(list(rows.values())):errors.append('CANONICAL_SUBRESULT_CONTRACT')
 case_ids=[c.get('framework_case_projection',{}).get('assessment_id') for c in document.get('cases',[])]
 if len(case_ids)!=4 or set(case_ids)!=set(REQUIRED_SUBRESULT_CONTRACT):errors.append('EXAMPLE_CHECK_INVENTORY')
 if document['framework_basis']['checklist_file_sha256']!=checklist_sha:errors.append('CHECKLIST_CONTENT_BINDING')
 if document.get('proposal_status')!='DRAFT_NOT_IMPLEMENTED' or document.get('data_origin')!='EXAMPLE' or document.get('actual_engine_executed') is not False:errors.append('EXAMPLE_STATUS_REQUIRED')
 for case in document.get('cases',[]):
  if case.get('data_origin')!='EXAMPLE':errors.append('CASE_ORIGIN_REQUIRED')
  p=case['framework_case_projection'];row=rows.get(p.get('assessment_id'))
  if row is None:errors.append('UNKNOWN_CHECK');continue
  if p.get('protection_requirement_id')!=row['purpose_ref']['id']:errors.append('PROJECTION_REQUIREMENT')
  if p['checklist_revision']!='sha256:'+checklist_sha:errors.append('PROJECTION_CHECKLIST_CONTENT')
  if set(p)!=set(template):errors.append('TEMPLATE_FIELD_COVERAGE')
  if p['execution_status'] not in ['미실행','대기','진행중','완료','중단'] or p['collection_status'] not in ['미수집','부분수집','수집완료']:errors.append('PROJECTION_ENUM_NAMESPACE')
  ext=case['proposed_scope_extension'];units=ext['required_subresults'];common=ext['common_confirmation']
  names=[u['unit_ref']['name'] for u in units];expected=[r['name'] for r in row.get('required_subresults',[])]
  if names!=expected:errors.append('REQUIRED_SUBRESULT_INVENTORY')
  for index,u in enumerate([common,*units]):
   if u['unit_ref'].get('kind')!=('common_confirmation' if index==0 else 'required_subresult'):errors.append('UNIT_KIND')
   if u['unit_ref']['checklist_ref']!=document['framework_basis']:errors.append('UNIT_SOURCE_REF')
   if u['unit_ref']['checklist_ref']['checklist_file_sha256']!=checklist_sha:errors.append('UNIT_CHECKLIST_CONTENT')
   if u['unit_ref']['assessment_id']!=row['id']:errors.append('UNIT_CHECK_MISMATCH')
   if u['unit_ref']['kind']=='common_confirmation' and u['unit_ref'].get('normal_control')!=row['normal_control']:errors.append('COMMON_SOURCE_DEFINITION')
   if u['unit_ref']['kind']=='required_subresult' and not any(sr['name']==u['unit_ref']['name'] and sr['applies_when']==u['unit_ref'].get('applies_when') for sr in row.get('required_subresults',[])):errors.append('SUBRESULT_SOURCE_DEFINITION')
   execution=u['execution'];nc=u['normal_control_result']
   if execution.get('engine_executed') is not False:errors.append('SYNTHETIC_ENGINE_CLAIM')
   if nc['succeeded'] is not None and not nc['attempted']:errors.append('NORMAL_CONTROL_CONTRADICTION')
   if u['verdicts']['security']=='PASS' and not unit_complete(u):errors.append('UNSUPPORTED_UNIT_PASS')
   if u['applicability']['status']=='NOT_APPLICABLE' and (u['selection']['selected'] or not u['applicability']['basis'] or u['verdicts']['security']!='NOT_APPLICABLE'):errors.append('UNSUPPORTED_UNIT_NA')
   for e in u['evidence']:
    if e['origin']!='EXAMPLE':errors.append('EVIDENCE_ORIGIN_UPGRADE')
    if not e['collected'] and e['value'] is not None:errors.append('UNOBSERVED_VALUE')
  allunits=[common,*units]
  if ext['aggregate_security']!=aggregate(allunits):errors.append('CHECK_AGGREGATION')
  projection_verdict={'충족':'PASS','미충족':'FAIL','미확인':'UNKNOWN','비적용':'NOT_APPLICABLE'}.get(p['security_verdict'])
  if projection_verdict!=ext['aggregate_security']:errors.append('PROJECTION_VERDICT_MISMATCH')
  expected_coverage=all(unit_complete(u) for u in allunits)
  if ext['coverage_complete']!=expected_coverage:errors.append('COVERAGE_MISMATCH')
  if ext['selection_inventory']['inventory_complete'] is not False or p['subtest_inventory_complete'] is not False:errors.append('DRAFT_INVENTORY_PROMOTION')
  fid=p.get('subtest_id')
  if fid:
   focus=focuses.get(fid)
   if not focus or fid not in row['subtest_ids']:errors.append('FOCUS_CHECK_MEMBERSHIP')
   elif p['question_id']!=focus['primary'] or set(p['shared_question_ids'])!=focus['membership']-{focus['primary']} or p['scenario_ids']!=[focus['scenario_id']]:errors.append('FOCUS_Q_SC_RELATION')
  elif p['question_id'] is not None or p['shared_question_ids'] or p['scenario_ids']:errors.append('COMMON_FOCUS_INVENTION')
  for b in ext['selection_inventory']['branch_refs']:
   if b['checklist_ref']!=document['framework_basis']:errors.append('BRANCH_SOURCE_REF')
   if b['checklist_ref']['checklist_file_sha256']!=checklist_sha:errors.append('BRANCH_CHECKLIST_CONTENT')
   if b['assessment_id']!=row['id'] or not any(all(b[k]==r[k] for k in ['name','scenario_ids','apply_when']) for r in row['test_branch_conditions']):errors.append('FULL_BRANCH_REF_MISMATCH')
  allowed={row['purpose_ref']['id']}|{r['requirement_id'] for r in row.get('security_requirement_contributions',[])}
  contributions=ext['requirement_contributions']
  contribution_ids=[c['requirement_id'] for c in contributions]
  if len(contribution_ids)!=len(set(contribution_ids)) or set(contribution_ids)!=allowed:errors.append('CONTRIBUTION_INVENTORY')
  for contribution in contributions:
   primary=contribution['requirement_id']==row['purpose_ref']['id']
   if contribution.get('role')!=('primary' if primary else 'supporting'):errors.append('CONTRIBUTION_ROLE')
   scope_names=[ref.get('name') for ref in contribution['source_unit_refs']]
   expected_scope=expected if primary else EXAMPLE_SUPPORTING_SCOPES.get(row['id'],{}).get(contribution['requirement_id'])
   if not scope_names or len(scope_names)!=len(set(scope_names)) or scope_names!=expected_scope:errors.append('EXAMPLE_CONTRIBUTION_SCOPE')
   if contribution['requirement_id'] not in allowed:errors.append('REQUIREMENT_MEMBERSHIP')
   selected=[]
   for r in contribution['source_unit_refs']:
    matches=[u for u in units if u['unit_ref']==r]
    if len(matches)!=1:errors.append('CONTRIBUTION_UNIT_REF')
    else:selected+=matches
   if selected and contribution['verdict']!=aggregate(selected):errors.append('CONTRIBUTION_VERDICT')
   available={e['source_ref']['sha256'] for u in selected for e in u['evidence']}
   if any(e['sha256'] not in available for e in contribution['evidence_refs']):errors.append('CONTRIBUTION_EVIDENCE_REF')
 for group in document['same_name_branch_roundtrip_cases']:
  refs=group['full_branch_refs'];keys=[digest(r) for r in refs]
  if not refs or len(keys)!=len(set(keys)):errors.append('BRANCH_COLLISION_LOST')
  row=rows[group['assessment_id']]
  counts={}
  for branch in row['test_branch_conditions']:counts[branch['name']]=counts.get(branch['name'],0)+1
  source_refs=[branch for branch in row['test_branch_conditions'] if counts[branch['name']]>1]
  if len(refs)!=len(source_refs):errors.append('BRANCH_COLLISION_INVENTORY')
  for ref in refs:
   if ref['checklist_ref']['checklist_file_sha256']!=checklist_sha:errors.append('COLLISION_CHECKLIST_CONTENT')
   if ref['assessment_id']!=group['assessment_id']:errors.append('COLLISION_CHECK_ID')
   if not any(all(ref[k]==b[k] for k in ['name','scenario_ids','apply_when']) for b in row['test_branch_conditions']):errors.append('COLLISION_SOURCE_MISMATCH')
 for artifact in document['synthetic_artifacts']:
  raw=canonical(artifact['example_payload'])
  if hashlib.sha256(raw).hexdigest()!=artifact['sha256'] or len(raw)!=artifact['bytes']:errors.append('ARTIFACT_HASH')
 legacy=document['historical_review_routing_example']
 if legacy['source_file_sha256']!=hashlib.sha256(legacy_raw).hexdigest() or legacy['input_record_kind']!='framework_assessment_review' or legacy['product_import_supported'] is not False:errors.append('LEGACY_ROUTING_OR_HASH')
 if legacy['mapped_without_invention']['execution_id'] is not None:errors.append('LEGACY_EXECUTION_INVENTION')
 reused=document['reused_evidence_example']
 if reused['source']['environment_ref']==reused['target_review']['environment_ref'] or reused['new_execution_recorded'] or reused['source']['original_origin']!='EXAMPLE':errors.append('REUSE_ORIGINAL_CONTEXT_LOST')
 if reused['comparison_example']['comparison']!='UNKNOWN':errors.append('FAIL_UNKNOWN_IS_NOT_FIXED')
 manual=document['manual_review_example']
 if manual['execution']['method']!='DOCUMENT_REVIEW' or manual['execution']['status']!='COMPLETED' or manual['normal_control_result']['attempted'] or manual['verdicts']['security']!='UNKNOWN':errors.append('MANUAL_REVIEW_PROMOTION')
 return sorted(set(errors))
def run(root=ROOT):
 catalog=json.loads((root/'data/checklist.json').read_text());template=json.loads((root/'data/result_template.json').read_text());raw=(root/'평가기록/20261024_REQ013_반환정보_평가기록.json').read_bytes()
 checklist_sha=hashlib.sha256((root/'data/checklist.json').read_bytes()).hexdigest()
 doc=json.loads((root/'개발전달/examples/결과기록_합성예시_초안.json').read_text())
 result=validate(doc,catalog,template,raw,checklist_sha);assert not result,result
 assert validate(json.loads(json.dumps(doc,ensure_ascii=False)),catalog,template,raw,checklist_sha)==[]
 checks=[]
 def rejects(name,mutate,expected):
  d=copy.deepcopy(doc);mutate(d);e=validate(d,catalog,template,raw,checklist_sha);assert expected in e,(name,e);checks.append({'case':name,'detected':expected})
 rejects('missing_required_result',lambda d:d['cases'][1]['proposed_scope_extension']['required_subresults'].pop(),'REQUIRED_SUBRESULT_INVENTORY')
 rejects('internal_pass_cannot_fill_missing_api',lambda d:d['cases'][1]['proposed_scope_extension'].update(aggregate_security='PASS'),'CHECK_AGGREGATION')
 rejects('permission_pass_cannot_fill_unknown_encryption',lambda d:d['cases'][3]['proposed_scope_extension'].update(aggregate_security='PASS'),'CHECK_AGGREGATION')
 rejects('independent_fail_survives_unknown',lambda d:d['cases'][2]['proposed_scope_extension'].update(aggregate_security='UNKNOWN'),'CHECK_AGGREGATION')
 rejects('wrong_shared_question',lambda d:d['cases'][0]['framework_case_projection'].update(shared_question_ids=['Q-NOT-A-FOCUS']),'FOCUS_Q_SC_RELATION')
 rejects('wrong_case_scenario',lambda d:d['cases'][0]['framework_case_projection'].update(scenario_ids=['SC-22-01']),'FOCUS_Q_SC_RELATION')
 rejects('branch_apply_when_mutation',lambda d:d['cases'][0]['proposed_scope_extension']['selection_inventory']['branch_refs'][0].update(apply_when='changed'),'FULL_BRANCH_REF_MISMATCH')
 rejects('same_name_branches_collapsed',lambda d:d['same_name_branch_roundtrip_cases'][0]['full_branch_refs'].append(copy.deepcopy(d['same_name_branch_roundtrip_cases'][0]['full_branch_refs'][0])),'BRANCH_COLLISION_LOST')
 rejects('draft_inventory_claim',lambda d:d['cases'][0]['proposed_scope_extension']['selection_inventory'].update(inventory_complete=True),'DRAFT_INVENTORY_PROMOTION')
 rejects('unsupported_unit_pass',lambda d:d['cases'][1]['proposed_scope_extension']['required_subresults'][0]['verdicts'].update(security='PASS'),'UNSUPPORTED_UNIT_PASS')
 rejects('supporting_wrong_requirement',lambda d:d['cases'][3]['proposed_scope_extension']['requirement_contributions'][1].update(requirement_id='requirement-2-1'),'REQUIREMENT_MEMBERSHIP')
 rejects('supporting_whole_check_copy',lambda d:d['cases'][3]['proposed_scope_extension']['requirement_contributions'][1].update(verdict='UNKNOWN'),'CONTRIBUTION_VERDICT')
 rejects('manual_review_fakes_engine_control',lambda d:d['manual_review_example']['normal_control_result'].update(attempted=True,succeeded=True),'MANUAL_REVIEW_PROMOTION')
 rejects('legacy_new_execution',lambda d:d['historical_review_routing_example']['mapped_without_invention'].update(execution_id='new'),'LEGACY_EXECUTION_INVENTION')
 rejects('reuse_new_measurement',lambda d:d['reused_evidence_example'].update(new_execution_recorded=True),'REUSE_ORIGINAL_CONTEXT_LOST')
 rejects('fail_unknown_not_fixed',lambda d:d['reused_evidence_example']['comparison_example'].update(comparison='FIXED'),'FAIL_UNKNOWN_IS_NOT_FIXED')
 rejects('uncollected_required_observation',lambda d:d['cases'][0]['proposed_scope_extension']['required_subresults'][0]['evidence'][0].update(collected=False,value=None),'UNSUPPORTED_UNIT_PASS')
 rejects('missing_required_challenge',lambda d:d['cases'][0]['proposed_scope_extension']['required_subresults'][0]['execution']['challenge_results'].pop(),'UNSUPPORTED_UNIT_PASS')
 rejects('projection_changed_only',lambda d:d['cases'][1]['framework_case_projection'].update(security_verdict='충족'),'PROJECTION_VERDICT_MISMATCH')
 rejects('wrong_branch_content_hash',lambda d:d['cases'][0]['proposed_scope_extension']['selection_inventory']['branch_refs'][0]['checklist_ref'].update(checklist_file_sha256='0'*64),'BRANCH_CHECKLIST_CONTENT')
 rejects('wrong_unit_content_hash',lambda d:d['cases'][0]['proposed_scope_extension']['required_subresults'][0]['unit_ref']['checklist_ref'].update(checklist_file_sha256='0'*64),'UNIT_CHECKLIST_CONTENT')
 rejects('same_name_one_branch_deleted',lambda d:d['same_name_branch_roundtrip_cases'][0]['full_branch_refs'].pop(),'BRANCH_COLLISION_INVENTORY')
 rejects('collision_branch_wrong_source_hash',lambda d:d['same_name_branch_roundtrip_cases'][0]['full_branch_refs'][0]['checklist_ref'].update(checklist_file_sha256='0'*64),'COLLISION_CHECKLIST_CONTENT')
 rejects('common_source_control_changed',lambda d:d['cases'][0]['proposed_scope_extension']['common_confirmation']['unit_ref'].update(normal_control='unrelated'),'COMMON_SOURCE_DEFINITION')
 def empty_common(d):
  u=d['cases'][0]['proposed_scope_extension']['common_confirmation'];u['required_observation_keys']=[];u['required_challenge_steps']=[];u['execution']['challenge_results']=[];u['evidence']=[]
 rejects('empty_required_sets_cannot_pass',empty_common,'UNSUPPORTED_UNIT_PASS')
 return {'ok':True,'mode':'DESIGN_EXAMPLE_VALIDATION_ONLY','engine_executed':False,'cases':len(doc['cases']),'required_subresults':sum(len(c['proposed_scope_extension']['required_subresults']) for c in doc['cases']),'template_fields':len(template),'roundtrip_equal':json.loads(json.dumps(doc,ensure_ascii=False))==doc,'independent_negative_cases':checks}
if __name__=='__main__':
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,default=ROOT);args=ap.parse_args();print(json.dumps(run(args.root),ensure_ascii=False,indent=2))
