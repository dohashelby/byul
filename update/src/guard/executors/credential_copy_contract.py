"""Closed PLT12 nonsecret copy declarations, never physical disposal proof.

An owned collected record is not an authenticated inventory/treatment/reuse
producer. IDs are opaque nonsecret UUID-like labels, never credential values
or their hashes. Actual membership and enforcement require separate evidence.
"""
from datetime import datetime
import hashlib,re
from guard.models import canonical_json

PROFILE='kubernetes-credential-copy-record-observation-v1'
INVENTORY_SCHEMA='guard-credential-copy-inventory/1';RECORD_SCHEMA='guard-credential-copy-records/1'
MAX_COPIES=64;MAX_EVENTS=256
POLICY_TYPES={'copy_environment_sha256':'str','copy_target_sha256':'str','copy_inventory_sha256':'str',
 'copy_policy_sha256':'str','copy_record_source_sha256':'str','copy_record_session':'str',
 'copy_validity_start':'str','copy_validity_end':'str','copy_max_record_delay_ms':'int'}
OBSERVATIONS={'copy_inventory_declarations':'list','copy_record_snapshots':'list','copy_event_declarations':'list',
 'copy_treatment_candidates':'list','copy_reuse_candidates':'list','copy_issuer_declarations':'list',
 'copy_missing':'list','copy_conflicts':'list','copy_collection':'list','copy_records_collected':'bool',
 'copy_producer_authenticated':'bool','copy_membership_verified':'bool','copy_treatment_verified':'bool',
 'copy_reuse_enforcer_verified':'bool','copy_issuer_verified':'bool','copy_scope_complete':'bool'}
INVENTORY_FIELDS=frozenset({'schema','credential_family_id','policy_sha256','copies'})
COPY_FIELDS=frozenset({'copy_id','generation','location_id','location_kind','recipient_id','required_action','deadline_at','retention_exception'})
EXCEPTION_FIELDS=frozenset({'exception_id','expires_at','restrictions_required'})
LOCATION_KINDS=frozenset({'FILE','ENV','MOUNT','SECRET_OBJECT','IMAGE','BACKUP','RECIPIENT','OTHER'})
ACTIONS=frozenset({'replace','recall','delete','restrict'})
RESTRICTIONS=frozenset({'access-denied','read-only','audited','not-exported'})
EVENT_TYPES=frozenset({'COPY_INVENTORY_CLAIM','TREATMENT_CLAIM','REUSE_CLAIM','ISSUER_CLAIM'})
RECORD_FIELDS=frozenset({'schema','environment_ref','target_sha256','session','source_sha256','inventory_sha256','policy_sha256',
 'producer_id','snapshot_generation','recorded_at','declared_complete','events'})
EVENT_FIELDS=frozenset({'event_id','seq','type','copy_id','copy_generation','location_id','recipient_id','actor_id','at',
 'method','outcome','restriction_claims','request_id_sha256','enforcer_id'})
OUTCOMES={'COPY_INVENTORY_CLAIM':frozenset({'present','absent','unavailable'}),
 'TREATMENT_CLAIM':frozenset({'completed','retained','failed','unavailable'}),
 'REUSE_CLAIM':frozenset({'allowed','denied','unavailable'}),'ISSUER_CLAIM':frozenset({'revoked','expired','active','unavailable'})}


def digest(raw):return hashlib.sha256(raw).hexdigest()
def fingerprint(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None
def opaque(v):return type(v) is str and re.fullmatch('[0-9a-f]{32}',v) is not None
def uint(v):return type(v) is int and 0<=v<2**31
def stamp(value):
    if type(value) is not str or not 1<=len(value)<=40:raise ValueError('COPY_TIME_INVALID')
    try:result=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('COPY_TIME_INVALID') from None
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('COPY_TIME_INVALID')
    return result


def restrictions(value):
    return type(value) is list and len(value)<=len(RESTRICTIONS) and all(type(v) is str and v in RESTRICTIONS for v in value) and len(value)==len(set(value))


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        for key in ('copy_environment_sha256','copy_target_sha256','copy_inventory_sha256','copy_policy_sha256','copy_record_source_sha256'):
            if not fingerprint(values[key]):raise ValueError
        if not opaque(values['copy_record_session']):raise ValueError
        start,end=stamp(values['copy_validity_start']),stamp(values['copy_validity_end'])
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        delay=values['copy_max_record_delay_ms']
        if type(delay) is not int or not 0<=delay<=600000:raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError('COPY_POLICY_INVALID') from None


def validate_inventory(value,*,expected_policy_sha256):
    try:
        if (type(value) is not dict or set(value)!=INVENTORY_FIELDS or value['schema']!=INVENTORY_SCHEMA
                or not opaque(value['credential_family_id']) or value['policy_sha256']!=expected_policy_sha256
                or type(value['copies']) is not list or len(value['copies'])>MAX_COPIES):raise ValueError
        seen=set()
        for row in value['copies']:
            if type(row) is not dict or set(row)!=COPY_FIELDS:raise ValueError
            if any(not opaque(row[k]) for k in ('copy_id','location_id','recipient_id')) or not uint(row['generation']):raise ValueError
            if row['copy_id'] in seen or row['location_kind'] not in LOCATION_KINDS or row['required_action'] not in ACTIONS:raise ValueError
            seen.add(row['copy_id']);stamp(row['deadline_at']);exception=row['retention_exception']
            if row['required_action']=='restrict' and exception is None:raise ValueError
            if exception is not None:
                if (type(exception) is not dict or set(exception)!=EXCEPTION_FIELDS or not opaque(exception['exception_id'])
                        or not restrictions(exception['restrictions_required']) or not exception['restrictions_required']):raise ValueError
                stamp(exception['expires_at'])
        return value
    except (KeyError,TypeError,ValueError):raise ValueError('COPY_INVENTORY_INVALID') from None


def validate_record(value,*,environment_ref,target_sha256,session,source_sha256,inventory_sha256,policy_sha256):
    try:
        if (type(value) is not dict or set(value)!=RECORD_FIELDS or value['schema']!=RECORD_SCHEMA
                or value['environment_ref']!=environment_ref or value['target_sha256']!=target_sha256
                or value['session']!=session or value['source_sha256']!=source_sha256
                or value['inventory_sha256']!=inventory_sha256 or value['policy_sha256']!=policy_sha256
                or not opaque(value['producer_id']) or not opaque(value['snapshot_generation'])
                or type(value['declared_complete']) is not bool or type(value['events']) is not list or len(value['events'])>MAX_EVENTS):raise ValueError
        ref=value['environment_ref']
        if (type(ref) is not dict or set(ref)!={'kind','id','revision','sha256'} or ref['kind']!='ENV'
                or type(ref['id']) is not str or not 1<=len(ref['id'])<=256
                or type(ref['revision']) is not int or not 1<=ref['revision']<2**31 or not fingerprint(ref['sha256'])):raise ValueError
        stamp(value['recorded_at'])
        for row in value['events']:
            if type(row) is not dict or set(row)!=EVENT_FIELDS or row['type'] not in EVENT_TYPES:raise ValueError
            if any(not opaque(row[k]) for k in ('event_id','copy_id','location_id','recipient_id','actor_id')):raise ValueError
            if not uint(row['seq']) or not uint(row['copy_generation']) or row['outcome'] not in OUTCOMES[row['type']]:raise ValueError
            stamp(row['at'])
            if not restrictions(row['restriction_claims']):raise ValueError
            if row['request_id_sha256'] is not None and not fingerprint(row['request_id_sha256']):raise ValueError
            if row['enforcer_id'] is not None and not opaque(row['enforcer_id']):raise ValueError
            if row['type']=='TREATMENT_CLAIM':
                if row['method'] not in ACTIONS or row['request_id_sha256'] is not None or row['enforcer_id'] is not None:raise ValueError
            elif row['method'] is not None or row['restriction_claims']:raise ValueError
            if row['type']=='COPY_INVENTORY_CLAIM' and (row['request_id_sha256'] is not None or row['enforcer_id'] is not None):raise ValueError
            if row['type']=='ISSUER_CLAIM' and row['request_id_sha256'] is not None:raise ValueError
        return value
    except (KeyError,TypeError,ValueError):raise ValueError('COPY_RECORD_INVALID') from None


def project(inventory,snapshots,values):
    start,end=normalize_policy(values)
    result={'inventory':inventory['copies'],'events':[],'treatment_candidates':[],'reuse_candidates':[],
        'issuer_declarations':[],'missing':[],'conflicts':[],
        'producer_authenticated':False,'actual_copy_membership_verified':False,'actual_treatment_verified':False,
        'reuse_enforcer_verified':False,'issuer_verified':False,'whole_scope_complete':False}
    unique={};occurrences={};sequences={};producer_ids=set();generations=set()
    for snapshot in snapshots:
        packet=snapshot.get('packet')
        if not snapshot.get('collected') or packet is None:continue
        producer_ids.add(packet['producer_id']);generations.add(packet['snapshot_generation'])
        if not packet['declared_complete']:result['missing'].append('copy_producer_declared_partial')
        seqs=sorted({r['seq'] for r in packet['events']})
        if seqs and seqs!=list(range(len(seqs))):result['missing'].append('copy_declared_event_sequence_gap')
        for row in packet['events']:
            identity=(row['event_id'],digest(canonical_json(row)))
            unique[identity]=row;occurrences.setdefault(row['event_id'],set()).add(identity[1])
            sequences.setdefault((packet['producer_id'],packet['snapshot_generation'],row['seq']),set()).add(identity)
            delay=(stamp(packet['recorded_at'])-stamp(row['at'])).total_seconds()*1000
            if delay<0:result['missing'].append('copy_declared_record_clock_order_unverified')
            elif delay>values['copy_max_record_delay_ms']:result['missing'].append('copy_declared_record_delay_exceeded')
    ambiguous={key for key,variants in occurrences.items() if len(variants)!=1}
    if ambiguous:result['conflicts'].append('copy_event_id_conflict')
    if any(len(variants)>1 for variants in sequences.values()):result['conflicts'].append('copy_event_sequence_conflict')
    if len(producer_ids)>1 or len(generations)>1:result['conflicts'].append('copy_record_generation_or_producer_changed')
    valid=list(unique.values())
    result['events']=[{**row,'declaration_only':True,'event_id_ambiguous':row['event_id'] in ambiguous} for row in valid]
    expected={row['copy_id']:row for row in inventory['copies']}
    if not expected:result['missing'].append('copy_declared_inventory_empty')
    for row in valid:
        copy=expected.get(row['copy_id'])
        if copy is None:result['conflicts'].append('copy_record_outside_declared_inventory');continue
        if (row['copy_generation'],row['location_id'],row['recipient_id'])!=(copy['generation'],copy['location_id'],copy['recipient_id']):
            result['conflicts'].append('copy_record_generation_or_assignment_conflict')
        if not start<=stamp(row['at'])<=end:result['missing'].append('copy_declared_event_outside_window')
    for copy in inventory['copies']:
        rows=[r for r in valid if (r['copy_id'],r['copy_generation'],r['location_id'],r['recipient_id'])
              ==(copy['copy_id'],copy['generation'],copy['location_id'],copy['recipient_id']) and r['event_id'] not in ambiguous]
        for kind in EVENT_TYPES:
            if not any(r['type']==kind for r in rows):result['missing'].append('copy_'+kind.lower()+'_unavailable')
        treatments=[r for r in rows if r['type']=='TREATMENT_CLAIM']
        for row in treatments:
            at=stamp(row['at']);exception=copy['retention_exception']
            restricted=bool(exception and at<=stamp(exception['expires_at']) and set(exception['restrictions_required'])<=set(row['restriction_claims']) and row['outcome']=='retained')
            result['treatment_candidates'].append({'copy_id':copy['copy_id'],'copy_generation':copy['generation'],'event_id':row['event_id'],
                'declared_deadline_exceeded':at>stamp(copy['deadline_at']),
                'declared_method_conflict':row['method']!=copy['required_action'],
                'declared_failure':row['outcome']=='failed','declared_restricted_retention_match':restricted,
                'actual_treatment_verified':False,'confirmed_violation':False})
        for row in (r for r in rows if r['type']=='REUSE_CLAIM'):
            result['reuse_candidates'].append({'copy_id':copy['copy_id'],'copy_generation':copy['generation'],'event_id':row['event_id'],
                'declared_reuse_allowed':row['outcome']=='allowed','declared_reuse_denied':row['outcome']=='denied',
                'request_correlation_declared':row['request_id_sha256'] is not None,'enforcer_declared':row['enforcer_id'] is not None,
                'reuse_enforcer_verified':False,'unauthorized_reuse_verified':False,'confirmed_violation':False})
        result['issuer_declarations'].extend({**row,'issuer_verified':False} for row in rows if row['type']=='ISSUER_CLAIM')
    result['missing']=sorted(set(result['missing']+['actual_copy_membership_treatment_reuse_and_authority_unverified']))
    result['conflicts']=sorted(set(result['conflicts']))
    return result
