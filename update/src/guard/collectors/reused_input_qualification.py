"""Separate qualification for nested reused fields and returned token identity."""
from pathlib import Path
import hashlib,json,re
from guard.adapters import vllm_reused_input
from . import qualification_document

ASSETS=Path(vllm_reused_input.__file__).with_name('assets')
REGISTRY=ASSETS/'reused_input_qualifications.json'
SCOPE='selected-reused-fields-current-request-frontend-and-returned-token-identity'


def producer():
    root=Path(vllm_reused_input.__file__).parents[1]
    paths=[Path(__file__),Path(vllm_reused_input.__file__),root/'adapters/vllm.py',
        root/'adapters/vllm_native_input_rejection.py',root/'adapters/assets/native_input_rejection_profiles.json',
        root/'executors/kubernetes_reused_input_matrix.py',root/'reused_matrix_policy.py',
        root/'executors/kubernetes_input_matrix.py',Path(qualification_document.__file__)]
    digest=hashlib.sha256()
    for path in paths:digest.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    return {'name':'guard-vllm-reused-field-matrix','version':'1','source_sha256':digest.hexdigest()}


def status(engine,version,image):
    from . import native_input,native_input_qualification
    result={'registered':False,'producer':producer(),'native_capture_dependency':native_input.producer(),
        'scope':SCOPE,'native_gpu_token_consumption_qualified':False,'whole69_complete':False}
    try:
        data=qualification_document.read(native_input._read(REGISTRY,32768))
        keys={'engine','version','image','producer','native_capture_dependency','scope','evidence_file','evidence_bytes','evidence_sha256','verified_export_sha256'}
        if(type(data)is not dict or set(data)!={'schema','qualifications'}or data['schema']!='guard-reused-field-matrix-qualifications/1'
                or type(data['qualifications'])is not list or len(data['qualifications'])>64
                or any(type(r)is not dict or set(r)!=keys for r in data['qualifications'])):raise ValueError
        rows=[r for r in data['qualifications']if(r['engine'],r['version'],r['image'],r['producer'])==(engine,version,image,result['producer'])]
        if not rows:return result
        if len(rows)!=1:raise ValueError
        row,=rows
        if(row['native_capture_dependency']!=result['native_capture_dependency']or row['scope']!=SCOPE
                or type(row['evidence_file'])is not str or re.fullmatch(r'[A-Za-z0-9_-]+\.json',row['evidence_file'])is None
                or type(row['evidence_bytes'])is not int or not 0<row['evidence_bytes']<=32768
                or not native_input._sha(row['evidence_sha256'])or not native_input._sha(row['verified_export_sha256'])):raise ValueError
        raw=native_input.runtime.shared.sources._bytes(ASSETS/row['evidence_file'],{'bytes':row['evidence_bytes'],'sha256':row['evidence_sha256']})
        proof=qualification_document.read(raw)
        required=('actual_gpu_source_qualified','both_field_endpoint_cases_verified','normal_control_fixture_distinct_verified',
            'current_original_source_and_response_hmac_verified','original_chat_renderer_reuse_and_processing_returns_verified',
            'same_fixture_ambiguity_refused','missing_or_tampered_source_refused')
        if(proof.get('schema')!='byul-reused-field-matrix-actual-qualification/1'or proof.get('producer')!=result['producer']
                or proof.get('native_capture_dependency')!=result['native_capture_dependency']or proof.get('scope')!=SCOPE or proof.get('image')!=image
                or any(proof.get(k)is not True for k in required)
                or proof.get('native_gpu_token_consumption_qualified')is not False or proof.get('whole69_complete')is not False
                or not native_input_qualification.status(engine,version,image)['registered']):raise ValueError
        return {**result,'registered':True,'evidence_sha256':row['evidence_sha256'],'verified_export_sha256':row['verified_export_sha256']}
    except(OSError,KeyError,TypeError,ValueError):raise ValueError('REUSED_INPUT_QUALIFICATION_INVALID')from None
