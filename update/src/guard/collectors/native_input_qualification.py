"""Exact-source qualification support data; never a full-check verdict or auth."""
from pathlib import Path
import json
import re

from . import native_input

ASSETS=Path(native_input.runtime.__file__).with_name('assets')
QUALIFICATIONS=ASSETS/'native_input_qualifications.json'
FIELDS=frozenset({'engine','version','image','producer','scope','evidence_file','evidence_bytes',
    'evidence_sha256','verified_export_sha256'})
REQUIRED=('actual_gpu_source_qualified','current_product_request_link_verified',
    'normal_and_rejection_source_cases_verified','source_bytes_matched',
    'changed_observer_source_rejected','missing_source_record_incomplete')


def status(engine,version,image):
    expected=native_input.producer()
    result={'registered':False,'producer':expected,'qualified_scope':None,
        'producer_authenticated':False,'full_check_scope_qualified':False,
        'fresh_runtime_bindings_verified':False,'whole69_complete':False}
    try:
        data=json.loads(native_input._read(QUALIFICATIONS,32768),object_pairs_hook=native_input.runtime.shared._pairs)
        if(type(data)is not dict or set(data)!={'schema','qualifications'}
                or data['schema']!='guard-native-input-qualifications/1'or type(data['qualifications'])is not list):raise ValueError
        matches=[]
        for row in data['qualifications']:
            if type(row)is not dict or set(row)!=FIELDS:raise ValueError
            if (row['engine'],row['version'],row['image'],row['producer'])==(engine,version,image,expected):matches.append(row)
        if len(matches)!=1:return {**result,'reason':'EXACT_SOURCE_QUALIFICATION_UNAVAILABLE'}
        row,=matches
        if(type(row['evidence_file'])is not str or not re.fullmatch(r'[A-Za-z0-9_-]+\.json',row['evidence_file'])
                or type(row['evidence_bytes'])is not int or not 0<row['evidence_bytes']<=32768
                or not native_input._sha(row['evidence_sha256'])or not native_input._sha(row['verified_export_sha256'])):raise ValueError
        raw=native_input.runtime.shared.sources._bytes(ASSETS/row['evidence_file'],
            {'bytes':row['evidence_bytes'],'sha256':row['evidence_sha256']})
        evidence=json.loads(raw,object_pairs_hook=native_input.runtime.shared._pairs)
        if(evidence.get('schema')!='byul-native-input-actual-source-qualification/1'
                or any(evidence.get(k)is not True for k in REQUIRED)
                or evidence.get('producer')!=expected or evidence.get('image')!=image or evidence.get('scope')!=row['scope']
                or evidence.get('whole69_complete')is not False
                or evidence.get('full_req08_or_req09_scope_complete')is not False):raise ValueError
        return {**result,'registered':True,'qualified_scope':row['scope'],
            'evidence_sha256':row['evidence_sha256'],'verified_export_sha256':row['verified_export_sha256'],
            'qualification_source_manifest_sha256':evidence['source_manifest_sha256'],
            'reason':'SELECTED_SOURCE_AND_CURRENT_REQUEST_LINK_GPU_QUALIFIED'}
    except(OSError,ValueError,TypeError,KeyError):
        raise ValueError('NATIVE_INPUT_QUALIFICATION_DATA_INVALID')from None
