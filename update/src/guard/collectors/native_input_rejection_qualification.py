"""Separate exact-source qualification for the native rejection projection."""
from pathlib import Path
import hashlib,json,re
from guard.adapters import vllm_native_input_rejection as projection
from . import native_input,native_input_qualification

ASSETS=projection.PROFILES.parent
REGISTRY=ASSETS/'native_input_rejection_qualifications.json'
SCOPE='selected-completion-token-text-and-chat-text-original-rejections'


def producer():
    root=Path(native_input.__file__).parents[1]
    paths=[Path(__file__),Path(projection.__file__),projection.PROFILES,
        root/'executors/kubernetes_observed_input.py',root/'adapters/vllm.py',Path(native_input_qualification.__file__),
        root/'adapters/vllm_input_matrix.py',root/'executors/kubernetes_input_matrix.py',root/'runtime/kubernetes_input_matrix.py',
        root/'input_matrix_policy.py']
    digest=hashlib.sha256()
    for path in paths:digest.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    return {'name':'guard-vllm-original-input-rejection','version':'1','source_sha256':digest.hexdigest()}


def status(engine,version,image):
    expected=producer();result={'registered':False,'producer':expected,'scope':SCOPE,
        'native_capture_dependency':native_input.producer(),'whole69_complete':False,'full_check_scope_qualified':False}
    data=json.loads(native_input._read(REGISTRY,32768),object_pairs_hook=native_input.runtime.shared._pairs)
    if(type(data)is not dict or set(data)!={'schema','qualifications'}or data['schema']!='guard-original-input-rejection-qualifications/1'
            or type(data['qualifications'])is not list or len(data['qualifications'])>64):raise ValueError('NATIVE_REJECTION_QUALIFICATION_INVALID')
    keys={'engine','version','image','producer','native_capture_dependency','scope','evidence_file','evidence_bytes','evidence_sha256','verified_export_sha256'}
    if any(type(r)is not dict or set(r)!=keys for r in data['qualifications']):raise ValueError('NATIVE_REJECTION_QUALIFICATION_INVALID')
    matches=[r for r in data['qualifications']if(r['engine'],r['version'],r['image'],r['producer'])==(engine,version,image,expected)]
    if not matches:return result
    if len(matches)!=1:raise ValueError('NATIVE_REJECTION_QUALIFICATION_INVALID')
    row,=matches
    if(set(row)!={'engine','version','image','producer','native_capture_dependency','scope','evidence_file','evidence_bytes','evidence_sha256','verified_export_sha256'}
            or row['native_capture_dependency']!=result['native_capture_dependency']or row['scope']!=SCOPE
            or type(row['evidence_file'])is not str or re.fullmatch(r'[A-Za-z0-9_-]+\.json',row['evidence_file'])is None
            or type(row['evidence_bytes'])is not int or not 0<row['evidence_bytes']<=32768
            or not native_input._sha(row['evidence_sha256'])or not native_input._sha(row['verified_export_sha256'])):raise ValueError('NATIVE_REJECTION_QUALIFICATION_INVALID')
    raw=native_input.runtime.shared.sources._bytes(ASSETS/row['evidence_file'],{'bytes':row['evidence_bytes'],'sha256':row['evidence_sha256']})
    proof=json.loads(raw,object_pairs_hook=native_input.runtime.shared._pairs)
    required=('actual_gpu_source_qualified','source_and_raise_instructions_matched','current_request_fixture_links_verified',
        'normal_and_two_original_rejections_verified','all_explicit_pairs_verified','tampered_source_rejected','missing_or_other_site_rejected')
    if(proof.get('schema')!='byul-original-input-rejection-actual-qualification/1'or any(proof.get(k)is not True for k in required)
            or proof.get('producer')!=expected or proof.get('native_capture_dependency')!=row['native_capture_dependency']
            or proof.get('scope')!=SCOPE or proof.get('image')!=image
            or proof.get('whole69_complete')is not False or proof.get('full_check_scope_qualified')is not False
            or not native_input_qualification.status(engine,version,image)['registered']):raise ValueError('NATIVE_REJECTION_QUALIFICATION_INVALID')
    return {**result,'registered':True,'evidence_sha256':row['evidence_sha256'],'verified_export_sha256':row['verified_export_sha256']}
