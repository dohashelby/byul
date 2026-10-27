#!/usr/bin/env python3
"""문서·JSON의 CHK 기준과 연결을 비교한다. 파일 수정이나 엔진 실행은 하지 않는다."""
import argparse
import html
import hashlib
import json
import re
from pathlib import Path


def normalize(value):
    text = html.unescape(str(value)).replace('<br>', '\n').replace('<br/>', '\n')
    text = re.sub(r'^\s*[-*] +', '', text, flags=re.M)
    return re.sub(r'\s+', ' ', text).strip()


def compose_method(row):
    """Rebuild the existing display text from its editable structured fields."""
    return (row['base_method_and_evidence'] + '<br>**정상 대조:** ' + row['normal_control']
            + '<br>**순서:** ' + ' → '.join(row['challenge_steps'])
            + '<br>**관측 증거:** ' + '/'.join(row['required_observations'])
            + '<br>**미충족:** ' + row['fail_criteria']
            + '<br>**미확인:** ' + row['unknown_criteria']
            + '<br>**실행 전 준비:** ' + row['remaining_setup'])


def method_matches(row):
    # Existing documents use both '/' and ' / ' between observation items.
    # Compare display whitespace only; every source word and F note remains.
    def display(value):
        return re.sub(r'\s*/\s*', '/', normalize(value))
    return display(row['method_and_evidence']) == display(compose_method(row))


# Reviewed c67 source contract, independent of the catalog being validated.
# Amend this contract only with an explicit framework-definition change and review.
REQUIRED_SUBRESULT_CONTRACT = {'CHK-REQ-005': {'required_subresults': [{'name': '모델별 사용 권한',
                                          'applies_when': '모델을 선택하는 모든 경로',
                                          'satisfied_if': '호출자에게 허용된 최종 모델만 사용한다.'},
                                         {'name': '사용 종료 모델의 실행 중단',
                                          'applies_when': '모델 사용 종료·alias 변경 시',
                                          'satisfied_if': '퇴역 모델은 실행되지 않고 남은 route·alias는 현재 승인 대상과 일치한다.'}],
                 'primary_requirement': 'requirement-1-2',
                 'supporting_requirements': []},
 'CHK-PLT-008': {'required_subresults': [{'name': 'API 전송 보호',
                                          'applies_when': '모든 실제 API 호출 구간; Gateway 사용 여부에 따라 구간 구분',
                                          'satisfied_if': '실제 API 각 구간의 필수 상대 인증·기밀성·무결성 요구와 정상 처리를 확인한다.'},
                                         {'name': '내부 IPC·TP 전송 보호',
                                          'applies_when': '기본 IPC·TP와 선택한 분산 통신',
                                          'satisfied_if': '구간별 허용 peer·보호 요구를 지키고 정상 TP가 완료된다.'}],
                 'primary_requirement': 'requirement-5-5',
                 'supporting_requirements': ['requirement-1-1', 'requirement-1-2']},
 'CHK-LIF-013': {'required_subresults': [{'name': '전체 release 권한',
                                          'applies_when': '별도 weights daemon의 release 경로 사용 시',
                                          'satisfied_if': '허용한 주체·범위·시점에만 전체 release가 수행된다.'},
                                         {'name': '재취득·대체 공급',
                                          'applies_when': '다른 소비자가 다시 취득하거나 fallback할 때',
                                          'satisfied_if': '후속 취득·fallback이 승인한 공급물과 접근 조건을 지킨다.'},
                                         {'name': '기존 참조의 수명',
                                          'applies_when': 'copy/zero-copy 소비 중 참조가 남아 있을 때',
                                          'satisfied_if': 'GPU 작업과 buffer 참조의 수명을 지킨다. 계측 부족은 해당 결과의 미확인으로 '
                                                          '남긴다.'}],
                 'primary_requirement': 'requirement-4-4',
                 'supporting_requirements': ['requirement-1-2',
                                             'requirement-2-2',
                                             'requirement-6-4',
                                             'requirement-7-4']},
 'CHK-OBS-006': {'required_subresults': [{'name': '조회·반출 권한',
                                          'applies_when': '기록·진단 원본과 실제 사본',
                                          'satisfied_if': '허용된 주체만 필요한 자료를 조회·반출한다.'},
                                         {'name': '저장 암호화·복호화 권한',
                                          'applies_when': '자료 등급·저장 위치별 정책에서 암호화를 요구할 때; 요구 미정은 미확인',
                                          'satisfied_if': '필수 저장 암호화와 키·복호화 권한 제한을 증거로 확인한다.'}],
                 'primary_requirement': 'requirement-5-4',
                 'supporting_requirements': ['requirement-1-2']}}
TEMPLATE_FIELDS = ('record_id', 'assessment_id', 'protection_requirement_id', 'checklist_revision', 'test_branch_ref', 'test_case_id', 'test_case_parameters', 'question_id', 'subtest', 'procedure_version', 'environment_id', 'environment_revision', 'policy_revision', 'execution_id', 'applicability', 'execution_status', 'collection_status', 'security_verdict', 'functional_verdict', 'performance_verdict', 'policy_values', 'evidence', 'facts', 'limitations', 'remediation_owner', 'approval', 'environment_evidence_source', 'environment_checked_at', 'policy_source', 'policy_approved_by', 'policy_checked_at', 'normal_control_result', 'stop_criteria_ref', 'recovery_procedure_ref', 'remediation_due_at', 'retest_conditions', 'subtest_inventory_complete', 'engine_adapter_ref', 'fixture_ref', 'collection_adapter_ref', 'started_at', 'finished_at', 'scenario_ids', 'subtest_id', 'shared_question_ids')


def verify_required_subresults(rows, blocks=None):
    """Check the canonical inventory, definitions and owning requirements."""
    errors = []
    rowmap = {r['id']: r for r in rows}
    actual_owners = {r['id'] for r in rows if r.get('required_subresults')}
    if actual_owners != set(REQUIRED_SUBRESULT_CONTRACT):
        errors.append('필수 세부 결과: 4CHK 귀속 목록 불일치')
    for rid, expected in REQUIRED_SUBRESULT_CONTRACT.items():
        row = rowmap.get(rid, {})
        actual = row.get('required_subresults')
        if actual != expected['required_subresults']:
            errors.append(rid + ': 필수 세부 결과 목록·이름·적용 조건·충족 기준 불일치')
        if row.get('purpose_ref', {}).get('id') != expected['primary_requirement']:
            errors.append(rid + ': 필수 세부 결과 주 요구 귀속 불일치')
        supporting = row.get('security_requirement_contributions', [])
        if [c.get('requirement_id') for c in supporting] != expected['supporting_requirements']:
            errors.append(rid + ': 필수 세부 결과 보조 요구 귀속 불일치')
        if blocks is not None:
            block = blocks.get(rid, '')
            match = re.search(r'^\| 결과 \| 적용 조건 \| 충족 기준 \|\n\| --- \| --- \| --- \|\n((?:\|[^\n]+\|\n)+)', block, re.M)
            table = re.findall(r'^\| (.*?) \| (.*?) \| (.*?) \|$', match[1], re.M) if match else []
            expected_table = [tuple(sr[k] for k in ('name', 'applies_when', 'satisfied_if')) for sr in expected['required_subresults']]
            if [tuple(normalize(v) for v in r) for r in table] != expected_table:
                errors.append(rid + ': 03 필수 세부 결과 표 누락·중복·원문 불일치')
    for row in rows:
        if 'required_subresults' in row and not isinstance(row['required_subresults'], list):
            errors.append(row['id'] + ': required_subresults 배열 필요')
    return errors


def verify_result_template(data, template, dictionary, checklist_sha, template_sha):
    """Validate the current blank framework form; historical records keep their own basis."""
    errors = []
    if set(template) != set(TEMPLATE_FIELDS):
        errors.append('결과 양식: 기존 45필드 누락·변경')
    fields = [f.get('field') for f in dictionary.get('fields', [])]
    if len(fields) != len(set(fields)) or set(fields) != set(TEMPLATE_FIELDS):
        errors.append('필드사전: 45필드 대응 누락·중복')
    if dictionary.get('template_path') != 'data/result_template.json' or dictionary.get('template_bytes_sha256') != template_sha:
        errors.append('필드사전: 결과 양식 경로·bytes SHA 불일치')
    rows = {r['id']: r for k, a in data.items() if k.endswith('_assessment_draft') and isinstance(a, list) for r in a}
    row = rows.get(template.get('assessment_id'))
    if row is None:
        return errors + ['결과 양식: 존재하지 않는 CHK']
    if template.get('checklist_revision') != 'sha256:' + checklist_sha:
        errors.append('결과 양식: 현재 checklist bytes SHA 불일치')
    if template.get('protection_requirement_id') != row['purpose_ref']['id']:
        errors.append('결과 양식: 주 요구 귀속 불일치')
    ref = template.get('test_branch_ref')
    if ref is not None and not any(ref == {k: b[k] for k in ('name', 'scenario_ids', 'apply_when')} for b in row['test_branch_conditions']):
        errors.append('결과 양식: full branch 원문 참조 불일치')
    fid = template.get('subtest_id')
    memberships = [(q['id'], focus) for q in data['items'] for focus in q.get('subtests', []) if focus['id'] == fid]
    if fid:
        if not memberships or fid not in row['subtest_ids']:
            errors.append('결과 양식: CHK/focus 귀속 불일치')
        else:
            focus = memberships[0][1]
            if (template.get('question_id') != focus['primary_question'] or
                set(template.get('shared_question_ids', [])) != {qid for qid, _ in memberships} - {focus['primary_question']} or
                template.get('scenario_ids') != [focus['scenario_id']]):
                errors.append('결과 양식: canonical Q/focus/SC 관계 불일치')
    elif template.get('question_id') is not None or template.get('shared_question_ids') or template.get('scenario_ids'):
        errors.append('결과 양식: 공통 확인에 focus 관계 임의 생성')
    if any(template.get(k) != v for k, v in {'execution_status':'미실행','collection_status':'미수집','security_verdict':'미확인','functional_verdict':'미확인','performance_verdict':'미확인','execution_id':None,'subtest_inventory_complete':False}.items()):
        errors.append('결과 양식: 빈 양식 초기 상태 불일치')
    return errors


def verify(root):
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)

    data = json.loads((root / 'data/checklist.json').read_text())
    md = (root / '03_체크리스트와_보완가이드.md').read_text()
    areas = ['supply', 'request_state', 'platform', 'lifecycle', 'observability']
    rows = [row for area in areas for row in data[area + '_assessment_draft']]
    ids = [row['id'] for row in rows]
    qmap = {q['id']: q for q in data['items']}
    scmap = {sc['id']: sc for sc in data['scenarios']}
    purpose_ids = {q['anchor'] for q in data['common_protection_structure']['questions']}
    check(len(ids) == len(set(ids)) == 77, 'CHK 수·중복: 77개 고유 ID 필요')
    anchors = re.findall(r'<a id="(CHK-[A-Z]+-\d+)"', md)
    check(anchors == data['assessment_registry']['display_order'], '03 CHK 앵커 순서·목록 불일치')
    links = sum(len(q['scenario_ids']) for q in qmap.values())
    counts = data['counts']
    for key, value in [('questions',len(qmap)),('scenarios',len(scmap)),('question_subtest_links',links)]:
        check(counts[key] == value, f'counts.{key} 불일치')
    expected_coverage = dict(assessment_rows=len(rows), legacy_questions=len(qmap),
                             scenarios=len(scmap), question_scenario_links=links)
    check(data['risk_context_legend']['coverage'] == expected_coverage, 'risk_context_legend.coverage 불일치')

    positions = list(re.finditer(r'<a id="(CHK-[A-Z]+-\d+)"></a>', md))
    blocks = {match[1]:md[match.end():positions[i+1].start() if i+1<len(positions) else len(md)]
              for i,match in enumerate(positions)}
    errors.extend(verify_required_subresults(rows, blocks))
    template_raw = (root / 'data/result_template.json').read_bytes()
    dictionary = json.loads((root / '개발전달/결과기록_필드사전_초안.json').read_text())
    errors.extend(verify_result_template(data, json.loads(template_raw), dictionary,
                  hashlib.sha256((root / 'data/checklist.json').read_bytes()).hexdigest(),
                  hashlib.sha256(template_raw).hexdigest()))
    structure = data['common_protection_structure']
    functions = structure['functions']
    requirements = structure['questions']
    reqmap = {r['id']:r for r in requirements}
    check(len(functions) == 8, '보안 기능 수 불일치')
    check(len(requirements) == len(reqmap) == structure['question_count'] == 36, '보호 요구 수·ID 불일치')
    primary_ids = [rid for q in requirements for rid in q['assessment_ids']]
    check(len(primary_ids) == len(set(primary_ids)) == 77 and set(primary_ids) == set(ids), '36개 요구의 CHK 주 배치 누락·중복')
    check(len(data['legacy_protection_structure']['questions']) == 28, '기존 28개 목적 정책 색인 누락')
    check(len(data['framework_model']['components']) == 7 and len(data['framework_model']['lifecycle']) == 7, '전체 관리체계·생명주기 누락')
    check(bool(data['framework_model']['governance']['activities']) and bool(data['framework_model']['risk_management']['activities']), '거버넌스·위험 관리 활동 누락')
    lifecycle = data['framework_model']['lifecycle']
    check(len({s.get('id') for s in lifecycle}) == 7, '생명주기 고정 ID 중복·누락')
    lifecycle_checks = set()
    for stage in lifecycle:
        for field in ['trigger','performer_roles','review_role','approval_role','candidate_assessment_ids','assessment_action','required_evidence','decision_required_when','decision_type','decision_conditions','recheck_triggers','status']:
            check(bool(stage.get(field)), stage['name']+': 생명주기 필드 누락 '+field)
        candidate_ids = stage.get('candidate_assessment_ids', [])
        check(len(candidate_ids) == len(set(candidate_ids)), stage['name']+': 점검 후보 중복')
        check(set(candidate_ids) <= set(ids), stage['name']+': 존재하지 않는 점검 후보')
        if stage.get('id') != 'LC-DESIGN':
            lifecycle_checks.update(candidate_ids)
    check(lifecycle_checks == set(ids), '설계 이후 생명주기에서 점검 후보 누락')
    for fn in ['01_프레임워크와_위협통제연결.md','노션_공유용/02_보안_프레임워크.md']:
        lifecycle_doc = (root / fn).read_text()
        for stage in lifecycle:
            for text in stage['required_evidence'] + stage['decision_conditions'] + [stage['decision_required_when']]:
                check(text in lifecycle_doc, fn+': 생명주기 상세 불일치 '+stage['name'])
    for f in functions:
        fq = [q for q in requirements if q['function_id'] == f['id']]
        check(f['requirement_ids'] == [q['id'] for q in fq], f['id']+': 보호 요구 역참조 불일치')
        check(f['assessment_ids'] == [rid for q in fq for rid in q['assessment_ids']], f['id']+': CHK 역참조 불일치')
    for q in requirements:
        for rid in q['assessment_ids']:
            r = next(r for r in rows if r['id'] == rid)
            check(r['purpose_ref']['id'] == q['id'] and r['parent_purpose'] == q['purpose'], rid+': 주 보호 요구 불일치')
            check(r['purpose_ref'] == {'id':q['id'], 'area':q['area'], 'name':q['purpose'],
                  'anchor':q['anchor'], 'document':'03_체크리스트와_보완가이드.md'},
                  rid+': 주 보호 요구 이름·영역·앵커·문서 불일치')
    for r in rows:
        for c in r.get('security_requirement_contributions', []):
            check(c['requirement_id'] in reqmap and r['id'] in reqmap[c['requirement_id']]['related_assessment_ids'], r['id']+': 보조 요구 역참조 불일치')
            check(c['name'] in blocks[r['id']], r['id']+': 03 보조 요구 설명 누락')
            requirement = reqmap.get(c['requirement_id'], {})
            check(c.get('name') == requirement.get('purpose') and c.get('anchor') == requirement.get('anchor') and
                  c.get('function_id') == requirement.get('function_id') and c.get('role') == 'supporting',
                  r['id']+': 보조 요구 이름·앵커·기능·역할 불일치')
    for q in requirements:
        expected = {r['id'] for r in rows for c in r.get('security_requirement_contributions',[]) if c['requirement_id'] == q['id']}
        check(expected == set(q['related_assessment_ids']), q['id']+': 보조 CHK 역참조 불일치')
    legacy_ids = {q['anchor'] for q in data['legacy_protection_structure']['questions']}
    check({q['legacy_purpose_ref']['anchor'] for q in rows} <= legacy_ids, '이전 정책 앵커 누락')
    total_branches = 0
    for row in rows:
        rid = row['id']; block = blocks.get(rid, '')
        check(method_matches(row), rid + ': 구조 필드와 합성 확인 방법 불일치')
        def same(actual, expected, field):
            check(normalize(actual) == normalize(expected), f'{rid}: {field} 불일치')
        heading = re.search(r'^#### (.+)$', block, re.M)
        same(heading[1] if heading else '', row['display_number']+' '+row['question'], '질문')
        table = dict(re.findall(r'^\| ([^|]+) \| (.*?) \|$', block.split('<details>',1)[0], re.M))
        for label, field in [('충족','satisfied_if'),('미충족','fail_criteria'),('미확인','unknown_criteria')]:
            same(table.get(label,''), row[field], field)
        same(table.get('필요한 증거',''), '\n'.join(row['required_observations']), '필요한 증거')
        condition = table.get('적용 조건','').split('<br>')[0]
        same(condition, '**'+row['applicability_criteria']['reference_scope']+'** — '+row['applies_when'], '적용 조건')
        branches = row['test_branch_conditions']; total_branches += len(branches)
        check(table.get('시험 분기','').startswith('**'+str(len(branches))+'개**'),rid+': 분기 수 표시 불일치')
        branch_match = re.search(r'<summary>환경별 적용·시험 분기 확인</summary>(.*?)</details>',block,re.S)
        branch_text = branch_match[1] if branch_match else ''
        actual = re.findall(r'^\| (.*?) \| (.*?) \| (.*?) \|$',branch_text,re.M)[2:]
        expected = [(b['name'],b['apply_when'],' · '.join(b['scenario_ids'])) for b in branches]
        check(len(actual)==len(expected),rid+': 분기 표 행 수 불일치')
        for index,(a,b) in enumerate(zip(actual,expected)):
            # 시나리오 사이 구분 기호는 표시 형식이며 ID·순서는 그대로 비교한다.
            same(a[0],b[0],f'분기 {index+1} 이름');same(a[1],b[1],f'분기 {index+1} 조건')
            check(re.findall(r'SC-\d+-\d+',a[2])==branches[index]['scenario_ids'],rid+': 분기 시나리오 불일치')
        for label, field in [('정상 대조','normal_control'),('준비·남은 확인','remaining_setup')]:
            value = re.search(r'\*\*'+label+r':\*\* ([^\n]*)',block)
            same(value[1] if value else '', row[field], label)
        steps = re.search(r'\*\*시험 순서:\*\* ([^\n]*)',block)
        same(steps[1] if steps else '', ' → '.join(row['challenge_steps']), '시험 순서')
        check(row['applicability_criteria']['required_environment_facts_summary'] in branch_text,rid+': 필요 환경 정보 누락')
        remediation = re.search(r'\*\*보완 방법\*\*\s*\n(.*?)\n\*\*준비·남은 확인:',block,re.S)
        same(remediation[1] if remediation else '',row['remediation'],'보완 방법')
        check(row['purpose_ref']['anchor'] in purpose_ids,rid+': 보호 목적 없음')
        check(row['execution_status']=='not_run',rid+': 실행 상태 변경 — 실제 시험 근거 별도 검토 필요')
        for qid in row['legacy_q_ids']:
            check(qid in qmap and rid in qmap[qid]['assessment_ids'],rid+': Q 역참조 불일치 '+qid)
        for sid in row['scenario_ids']:
            check(sid in scmap and rid in scmap[sid]['assessment_ids'],rid+': SC 역참조 불일치 '+sid)
        branch_scs = {sid for b in branches for sid in b['scenario_ids']}
        check(branch_scs==set(row['scenario_ids']),rid+': 분기/점검 시나리오 범위 불일치')
    for records, field in [(qmap,'legacy_q_ids'),(scmap,'scenario_ids')]:
        for key, record in records.items():
            expected = {r['id'] for r in rows if key in r[field]}
            check(set(record['assessment_ids'])==expected,key+': 역참조 목록 불일치')
    # Canonical display names and representative remedies must agree across shared pages.
    for row in rows:
        rid = row['id']; block = blocks[rid]
        roles = row.get('evidence_roles', {})
        check(bool(roles.get('providers')) and bool(roles.get('assignment_status')), rid+': 자료 제공 역할 누락')
        for provider in roles.get('providers', []):
            check(provider in block, rid+': 원문 자료 제공 역할 불일치')
        # The independent contract above also detects absent or moved inventories.

    notion4 = (root / '노션_공유용/04_환경별_적용과_체크리스트.md').read_text()
    shared_rows = re.findall(r'^\| [0-9.]+ \| \[(CHK-[A-Z]+-\d+)\]\([^\n]+?\) \| ([^|\n]+) \|', notion4, re.M)
    check(len(re.findall(r'<summary>[0-9.]+ [^\n]+ — CHK-[A-Z]+-\d+</summary>', notion4)) == 77, '노션 04 항목별 해설 수 불일치')
    check(len(shared_rows) == 77, '노션 04 점검 목록 수 불일치')
    rowmap = {r['id']: r for r in rows}
    check(len({r['display_number'] for r in rows}) == 77, '읽기 번호 중복')
    for r in rows:
        check(re.search(r'^\| '+re.escape(r['display_number'])+r' \| \['+re.escape(r['id'])+r'\]', notion4, re.M) is not None, r['id']+': 요약표 번호 불일치')
    check(notion4.count('□ 충족　□ 미충족　□ 미확인　□ 해당 없음') == 154, '빈 체크칸 수 불일치')

    for rid, question in shared_rows:
        check(question.strip() == rowmap[rid]['question'], rid+': 노션 질문 이름 불일치')
    notion5 = (root / '노션_공유용/05_위협통제_매트릭스.md').read_text()
    named_scenarios = re.findall(r'\[(SC-\d+-\d+) ([^\]\n]+)\]', notion5)
    check(len(named_scenarios) == 83, '노션 05 시나리오 이름 수 불일치')
    for sid, title in named_scenarios:
        check(title == scmap[sid]['title'], sid+': 노션 시나리오 이름 불일치')
    original1 = (root / '01_프레임워크와_위협통제연결.md').read_text()
    for sid, sc in scmap.items():
        check('<summary>'+sid+' '+sc['title']+'</summary>' in original1, sid+': 원문 이름 불일치')
    guide = data.get('threat_classification_guide', {})
    types = {v['id'] for v in guide.get('types', [])}
    check(types == set('STRIDE'), 'STRIDE 유형 정의 누락')
    source = (root / '01_프레임워크와_위협통제연결.md').read_text()
    notion = (root / '노션_공유용/05_위협통제_매트릭스.md').read_text()
    classified = 0; failures = 0; contexts = []
    for sid, sc in scmap.items():
        c = sc.get('threat_classification', {})
        codes = c.get('types', [])
        check(len(codes) == len(set(codes)) and set(codes) <= types, sid+': 유형 오류')
        check(bool(c.get('reason')) and bool(c.get('classification_condition')),sid+': 분류 이유·조건 누락')
        if sc['kind'] == '실패·오설정 검증':
            failures += 1
            check(not codes and c.get('status') == 'failure_misconfiguration', sid+': 비악의 실패의 공격 유형 오분류')
        elif codes:
            classified += 1
            check(c.get('status') == 'threat_hypothesis',sid+': 위협 가설 상태 불일치')
        else:
            contexts.append(sid)
            check(c.get('status') == 'scope_context',sid+': 유형 보류 사유 상태 누락')
        match = re.search(r'<a id="'+sid+r'"></a>(.*?)</details>',source,re.S)
        block = match[1] if match else ''
        check(c.get('reason','!') in block and c.get('classification_condition','!') in block,sid+': 01 분류/JSON 불일치')
        check(set(re.findall(r'CHK-[A-Z]+-\d+',block))==set(sc['assessment_ids']),sid+': 01 연결 CHK 불일치')
        line = next((line for line in notion.splitlines() if line.startswith('| ['+sid+' ')), '')
        check(bool(line),sid+': 노션 유형 목록 누락')
        for code in codes:
            check(code+' '+next(x['label'] for x in guide['types'] if x['id']==code) in block,sid+': 01 유형 누락')
            check(code+' '+next(x['label'] for x in guide['types'] if x['id']==code) in line,sid+': 노션 유형 누락')
        check(set(re.findall(r'CHK-[A-Z]+-\d+',line))==set(sc['assessment_ids']),sid+': 노션 연결 CHK 누락')
    check(classified+failures+len(contexts)==83,'분류 대상 합계 불일치')
    check(classified == data['counts']['threat_scenarios'] == 73, '위협 후보 합계 불일치')
    check(len(contexts) == data['counts']['scope_context_scenarios'] == 1, '공유 신뢰 범위 합계 불일치')
    check(scmap['SC-08-02']['kind'] == '공유 신뢰 범위', 'SC-08-02 구분 불일치')
    check(failures==guide.get('failure_misconfiguration')==9,'실패·오설정 수 불일치')
    check(contexts==guide.get('scope_context'),'유형 보류 목록 불일치')
    for code in types:
        check(guide.get('counts',{}).get(code)==sum(code in s['threat_classification']['types'] for s in scmap.values()),code+': 유형별 수 불일치')
    for mapping in guide.get('external_mappings',[]):
        check(mapping.get('status')=='conditional_reference' and bool(mapping.get('condition')),mapping['id']+': 외부 분류 조건 누락')
        check(set(mapping['scenarios'])<=set(scmap),mapping['id']+': 외부 분류 SC 없음')
        check(all(scmap[sid]['kind']=='위협 후보' for sid in mapping['scenarios']),mapping['id']+': 일반 실패에 공격 기법 부여')
        check(set(mapping['assessment_ids'])=={a for sid in mapping['scenarios'] for a in scmap[sid]['assessment_ids']},mapping['id']+': 외부 분류 CHK 역참조 불일치')
    tax=data['threat_mechanism_taxonomy']; primary=[sid for t in tax['types'] for sid in t['scenario_ids']]
    check(len(tax['types'])==17 and len(primary)==len(set(primary))==82,'원리별 주 분류 수·중복 오류')
    check(set(primary)|set(tax['context_ids'])==set(scmap),'시나리오 분류 누락')
    for t in tax['types']:
        for sid in t['scenario_ids']:check(scmap[sid]['mechanism_classification']['primary']==t['id'],sid+': 원리 역참조 오류')
    check(source.count('**공격·실패 원리:**')==83,'원문 원리 설명 누락')
    return errors, dict(security_functions=len(functions), protection_requirements=len(requirements), checks=len(rows), branches=total_branches, q_scenario_links=links, required_subresults=sum(len(r.get('required_subresults', [])) for r in rows if isinstance(r.get('required_subresults', []), list)), template_fields=len(json.loads(template_raw)), classified_scenarios=classified, failure_scenarios=failures, scope_context_scenarios=len(contexts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    errors, counts = verify(args.root)
    print(json.dumps({'ok':not errors,**counts,'errors':errors},ensure_ascii=False,indent=2))
    raise SystemExit(bool(errors))

if __name__ == '__main__':
    main()
