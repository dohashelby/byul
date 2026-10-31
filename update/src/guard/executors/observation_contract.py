"""OBS01 closed declared record contract; no producer authentication proof.

OBS01 requires independently observed events and their actual audit records.
Source/HTTP/collector agreement alone cannot prove actor or approval identity.
"""
from datetime import datetime
import re
from guard.models import canonical_json,sha256_hex

PROFILE='kubernetes-required-event-record-observation-v1'
POLICY_TYPES={
    'observation_environment_sha256':'str','observation_target_sha256':'str',
    'observation_validity_start':'str','observation_validity_end':'str',
    'observation_max_delay_ms':'int','observation_required_events':'list[str]',
    'observation_required_fields':'list[str]',
    'observation_session':'str','observation_expected_source_sha256':'str',
    'observation_event_policy_sha256':'str','observation_allowed_actor_sha256':'str',
    'observation_denied_actor_sha256':'str',
}
EVENTS=frozenset({'ACCESS_ALLOWED','ACCESS_DENIED','ADMIN_ACTION','APPROVAL','ARTIFACT_CONSUMED','DEPLOYMENT'})
FIELDS=frozenset({'actor_sha256','target_sha256','run_sha256','request_sha256','policy_sha256',
    'approval_sha256','result_sha256','source_sha256'})
MANDATORY_FIELDS=frozenset({'actor_sha256','target_sha256','run_sha256','policy_sha256'})
SCHEMA='guard-required-event-records/1'
OBSERVATIONS={'required_event_trials':'list','required_event_records':'list','required_event_matches':'list',
    'required_event_missing':'list','required_event_conflict_candidates':'list','required_event_collection':'list',
    'required_event_records_collected':'bool','required_event_producer_authenticated':'bool',
    'required_event_independent_trigger_verified':'bool','required_event_scope_complete':'bool'}


def fingerprint(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def _time(value):
    if type(value) is not str or len(value)>40:raise ValueError
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError
    return stamp


def _closed_list(value,allowed):
    return (type(value) is list and 0<len(value)<=len(allowed)
        and all(type(item) is str and item in allowed for item in value)
        and len(set(value))==len(value))


def normalize_policy(values):
    """Explicit caller inputs; no policy, actor, or source approval is invented."""
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        if any(not fingerprint(values[k]) for k in POLICY_TYPES if k.endswith('_sha256')):raise ValueError
        if (type(values['observation_session']) is not str or re.fullmatch(r'[0-9a-f]{32}',values['observation_session']) is None
                or values['observation_allowed_actor_sha256']==values['observation_denied_actor_sha256']):raise ValueError
        start,end=(_time(values[k]) for k in ('observation_validity_start','observation_validity_end'))
        delay=values['observation_max_delay_ms'];events=values['observation_required_events'];fields=values['observation_required_fields']
        if (not 0<(end-start).total_seconds()<=600 or type(delay) is not int or not 0<=delay<=300000
                or not _closed_list(events,EVENTS) or not _closed_list(fields,FIELDS)
                or not MANDATORY_FIELDS.issubset(fields)
                or 'APPROVAL' in events and 'approval_sha256' not in fields):raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError,OverflowError):
        raise ValueError('OBSERVATION_POLICY_INVALID') from None


def validate_record(value,*,environment_ref,target_sha256,session,expected_source_sha256,forbidden_values=()):
    keys={'schema','session','environment_ref','target_sha256','producer_source_sha256','producer_authenticated',
        'source_ended','dropped_events','events'}
    if (type(value) is not dict or set(value)!=keys or value['schema']!=SCHEMA or value['session']!=session
            or value['environment_ref']!=environment_ref or value['target_sha256']!=target_sha256
            or value['producer_source_sha256']!=expected_source_sha256 or value['producer_authenticated'] is not False
            or type(value['source_ended']) is not bool or type(value['dropped_events']) is not int
            or not 0<=value['dropped_events']<=2**31-1 or type(value['events']) is not list or len(value['events'])>64):
        raise ValueError('OBSERVATION_RECORD_BINDING_INVALID')
    for index,row in enumerate(value['events']):
        if (type(row) is not dict or set(row)!={'seq','event_id_sha256','event',*FIELDS}
                or type(row['seq']) is not int or row['seq']!=index or not fingerprint(row['event_id_sha256'])
                or type(row['event']) is not str or row['event'] not in EVENTS
                or any(row[k] is not None and not fingerprint(row[k]) for k in FIELDS)):
            raise ValueError('OBSERVATION_EVENT_INVALID')
    def forbidden(item):
        if type(item) is str:return any(secret and secret in item for secret in forbidden_values)
        if type(item) is dict:return any(forbidden(v) for v in item.values())
        if type(item) is list:return any(forbidden(v) for v in item)
        return False
    if forbidden(value):raise ValueError('OBSERVATION_SENSITIVE_RECORD_REFUSED')
    # Matching an explicit expected source claim is not authentication of code,
    # actor, approval, clock, record completeness, or an independent event source.
    return value


def correlate(snapshots,trials,values,*,run_sha256):
    records=[];seen=set();matches=[];missing=[];conflicts=[]
    for snapshot in snapshots:
        for row in snapshot['packet']['events']:
            key=sha256_hex(canonical_json(row))
            if key not in seen:records.append(row);seen.add(key)
    for trial in trials:
        if not trial['attempted']:continue
        expected_event='ACCESS_DENIED' if trial['role']=='denied' else 'ACCESS_ALLOWED'
        relevant=[r for r in records if r['request_sha256']==trial['request_id_sha256'] and r['event']==expected_event]
        fields=values['observation_required_fields'];candidate=relevant[0] if len(relevant)==1 else None
        expected={'actor_sha256':values['observation_'+trial['role']+'_actor_sha256'],
            'target_sha256':values['observation_target_sha256'],'run_sha256':run_sha256,
            'policy_sha256':values['observation_event_policy_sha256'],
            'source_sha256':values['observation_expected_source_sha256'],
            'result_sha256':sha256_hex(canonical_json({'outcome':trial['outcome']}))}
        mismatches=[k for k,v in expected.items() if candidate is not None and candidate[k] is not None and candidate[k]!=v]
        absent=[k for k in fields if candidate is None or candidate[k] is None]
        arrival=[s['collected_monotonic_ns'] for s in snapshots if candidate is not None and candidate in s['packet']['events']
            and s['collected_monotonic_ns']>=trial['finished_monotonic_ns']]
        delay=(min(arrival)-trial['finished_monotonic_ns'])//1_000_000 if arrival else None
        matched=(candidate is not None and not mismatches and not absent and trial['expected_response_observed']
            and delay is not None and delay<=values['observation_max_delay_ms'])
        matches.append({'case_id':trial['case_id'],'event':expected_event,'candidate_count':len(relevant),
            'event_id_sha256':candidate['event_id_sha256'] if candidate else None,'missing_fields':absent,
            'mismatched_fields':mismatches,'collection_arrival_upper_bound_ms':delay,'declared_record_match_observed':bool(matched),
            'actor_identity_verified':False,'independent_event_source_verified':False,'producer_authenticated':False})
        if not matched:missing.append({'case_id':trial['case_id'],'event':expected_event,'reason':'required_record_or_binding_or_deadline_unverified'})
        if mismatches or len(relevant)>1:conflicts.append({'case_id':trial['case_id'],'event':expected_event,
            'mismatched_fields':mismatches,'duplicate_request_records':len(relevant)>1,'confirmed_violation':False})
    observed={m['event'] for m in matches}
    for event in values['observation_required_events']:
        if event not in observed:missing.append({'case_id':None,'event':event,
            'reason':'selected_event_occurrence_unverified' if event in {'ACCESS_ALLOWED','ACCESS_DENIED'} else 'independent_trigger_and_source_not_implemented'})
    return {'records':records,'matches':matches,'missing':missing,'conflict_candidates':conflicts,
        'producer_authenticated':False,'independent_trigger_verified':False,'scope_complete':False}
