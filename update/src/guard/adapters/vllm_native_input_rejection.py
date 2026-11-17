"""Exact original raise sites; no input contents, consumer or whole-scope proof."""
from pathlib import Path
import json,sys

PROFILES=Path(__file__).with_name('assets')/'native_input_rejection_profiles.json'


def profile(engine,version,image):
    value=json.loads(PROFILES.read_bytes())
    if set(value)!={'schema','profiles'}or value['schema']!='guard-native-input-original-rejection-sites/1':raise ValueError('NATIVE_REJECTION_PROFILE_INVALID')
    runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    rows=[p for p in value['profiles']if(p['engine'],p['version'],p['image'],p['runtime'])==(engine,version,image,runtime)]
    if len(rows)!=1:raise ValueError('NATIVE_REJECTION_PROFILE_UNAVAILABLE')
    return rows[0]


def project(review,selected):
    """Called only after original HMAC records were independently revalidated."""
    result={'schema':'guard-original-input-rejection-facts/1','cases':{},'diagnostic_codes':[],
        'consumer_nonreach_verified':False,'full_check_scope_verified':False,'whole69_complete':False}
    if(review.get('schema')!='guard-native-input-capture-review/1'or review.get('image')!=selected['image']
            or type(review.get('gaps'))is not list or review['gaps']):
        result['diagnostic_codes'].append('NATIVE_REJECTION_CAPTURE_INCOMPLETE');return result
    requests=review.get('requests')
    if type(requests)is not dict:return {**result,'diagnostic_codes':['NATIVE_REJECTION_REQUESTS_UNAVAILABLE']}
    for case,spec in selected['cases'].items():
        rows=requests.get(case,[]);facts={'original_rejection_observed':False,'normal_returns_observed':False,
            'source_refs':[],'diagnostic_codes':[]}
        result['cases'][case]=facts
        # A unique direct raise at this exact instruction identifies the source
        # cause. A propagated validation error or another raise is insufficient.
        matches=[r for r in rows if all(r.get(k)==spec[k]for k in('source_module','point','source_sha256','body_code_sha256'))
            and r.get('event')=='RAISE'and r.get('exception_kind')=='native-validation-error'
            and r.get('original_invoked')is True and r.get('exception_site')=={
                'source_line':spec['source_line'],'code_offset':spec['code_offset'],'direct_raise':True}]
        if len(matches)!=1:facts['diagnostic_codes'].append('NATIVE_REJECTION_UNIQUE_ORIGINAL_RAISE_UNAVAILABLE');continue
        row,=matches
        calls=[r for r in rows if r.get('event')=='CALL'and r.get('call_hmac_sha256')==row['call_hmac_sha256']]
        if len(calls)!=1:facts['diagnostic_codes'].append('NATIVE_REJECTION_ORIGINAL_CALL_UNAVAILABLE');continue
        call,=calls
        stable=('context','point','source_module','source_sha256','body_code_sha256','object_hmac_sha256',
            'request_hmac_sha256','root_call_hmac_sha256','parent_root_call_hmac_sha256','operation_started_ns','path','method')
        if(any(call.get(k)!=row.get(k)for k in stable)or call.get('original_invoked')is not False
                or not row['operation_started_ns']<=call['observation_ns']<=row['observation_ns']):
            facts['diagnostic_codes'].append('NATIVE_REJECTION_CALL_BINDING_UNVERIFIED');continue
        # Verify a complete handler root and its interval, using the same
        # current request/context rather than a different request's success.
        root=[r for r in rows if r.get('call_hmac_sha256')==row['root_call_hmac_sha256']]
        starts=[r for r in root if r['event']=='CALL'];ends=[r for r in root if r['event']=='RETURN']
        if(len(root)!=2 or len(starts)!=1 or len(ends)!=1
                or any(r['context']!=row['context']or r['request_hmac_sha256']!=row['request_hmac_sha256']for r in root)
                or not starts[0]['observation_ns']<=row['operation_started_ns']<=row['observation_ns']<=ends[0]['observation_ns']):
            facts['diagnostic_codes'].append('NATIVE_REJECTION_HANDLER_INTERVAL_UNVERIFIED');continue
        normal=True
        for label in('before','after_'+case):
            control=requests.get(label,[])
            returned=[r for r in control if r.get('point')==spec['point']and r.get('source_module')==spec['source_module']
                and r.get('source_sha256')==spec['source_sha256']and r.get('body_code_sha256')==spec['body_code_sha256']
                and r.get('context')==row['context']and r.get('event')=='RETURN'and r.get('original_invoked')is True]
            normal=normal and len(returned)==1
        facts.update(original_rejection_observed=True,normal_returns_observed=normal,
            source_refs=[{k:row[k]for k in('source_module','point','source_sha256','body_code_sha256',
                'call_hmac_sha256','root_call_hmac_sha256','request_hmac_sha256','operation_started_ns','observation_ns','exception_site')}])
        if not normal:facts['diagnostic_codes'].append('NATIVE_REJECTION_NORMAL_SOURCE_RETURN_UNAVAILABLE')
    return result
