"""REQ02 physical native auth/handler observations; no principal/policy oracle."""
from copy import deepcopy
from .kubernetes_request_termination import _time
import hashlib
import re

PROFILE='kubernetes-native-route-observation-v1'
POLICY_TYPES={'route_native_environment_sha256':'str','route_native_target_sha256':'str','route_native_prepared_sha256':'str',
    'route_native_validity_start':'str','route_native_validity_end':'str'}
OBSERVATIONS={'native_route_trials':'list','native_route_events':'list','native_route_associations':'list',
    'native_route_missing':'list','native_route_authorization_verified':'bool','native_route_scope_complete':'bool'}

def normalize_policy(p):
    try:
        if type(p) is not dict or set(p)!=set(POLICY_TYPES):raise ValueError
        for k in ('route_native_environment_sha256','route_native_target_sha256','route_native_prepared_sha256'):
            if type(p[k]) is not str or re.fullmatch('[0-9a-f]{64}',p[k]) is None:raise ValueError
        start,end=(_time(p[k]) for k in ('route_native_validity_start','route_native_validity_end'))
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        return start,end
    except Exception:raise ValueError('NATIVE_ROUTE_POLICY_INVALID') from None

def project(packet,trials):
    out={'schema':'guard-native-route-associations/1','events':[],'associations':[],'missing':[],
        'producer_authenticated':False,'principal_identity_verified':False,'authorization_verified':False,
        'gateway_rewrite_verified':False,'registered_handler_verified':False,'worker_identity_verified':False,
        'node_runtime_image_verified':False,'whole_scope_complete':False}
    if packet is None:out['missing'].append('NATIVE_ROUTE_PACKET_UNAVAILABLE');return out
    index=packet['route_handler_index'];recordings=packet['recordings']
    selected={t['request_id_sha256']:t for t in trials if t['attempted']}
    ambiguous=set(index['ambiguous_asgi_request_sha256'])
    for entry in index['invocations'].values():
        request=entry['asgi_request_sha256']
        if request not in selected:continue
        references={tuple(r) for r in entry['events']};events=[v for v in index['events'] if tuple(v['event']) in references]
        if len(out['events'])+len(events)>256:out['missing'].append('NATIVE_ROUTE_EVENT_BOUND');break
        out['events'].extend(deepcopy(events));row=deepcopy(entry);row['case_id']=selected[request]['case_id'];row['recordings']=[]
        for n in sorted({r[0] for r in entry['events']}):
            recording=recordings[n];capture=recording['capture']
            if capture is None:continue
            header=capture['records'][0]['payload']
            row['recordings'].append({'recording_index':n,'pid':recording['pid'],'start_ticks':recording['start_ticks'],
                'capture_sha256':capture['provenance']['capture_sha256'],'profile':header['profile'],
                'session_sha256':hashlib.sha256(header['session'].encode()).hexdigest(),
                'context':deepcopy(header['context']),
                'module_provenance':[{'event_seq':r['seq'],**deepcopy(r['payload'])} for r in capture['records'] if r['event']=='MODULE']})
        row['request_hash_ambiguous']=request in ambiguous
        row['http_entry_metadata_matches']=(entry['entry_method_sha256']==selected[request]['method_sha256']
            and entry['entry_path_sha256']==selected[request]['path_sha256']
            and entry['entry_root_path_sha256']==hashlib.sha256(b'').hexdigest())
        row['same_recording_association_observed']=bool(entry['metadata_consistent'] and entry['recording_closed']
            and entry['source_modules_observed'] and len(row['recordings'])==1 and request not in ambiguous
            and row['http_entry_metadata_matches'] and selected[request]['response_binding_verified'])
        row['authorization_verified']=False;row['security']='UNKNOWN';out['associations'].append(row)
    if not index['stored_index_complete']:out['missing'].append('NATIVE_ROUTE_INDEX_INCOMPLETE')
    for request,trial in selected.items():
        if not any(r['asgi_request_sha256']==request for r in out['associations']):out['missing'].append('NATIVE_ROUTE_SELECTED_REQUEST_NOT_OBSERVED:'+trial['case_id'])
    if not packet['recorded_files_covered']:out['missing'].append('NATIVE_ROUTE_RECORDING_COVERAGE_UNVERIFIED')
    return out
