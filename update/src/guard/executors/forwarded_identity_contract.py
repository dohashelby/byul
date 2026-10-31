"""REQ04 declared issuer/normalization/consumption records; never trusted by hash alone."""
from datetime import datetime
from copy import deepcopy
import re
from guard.models import canonical_json,sha256_hex

PROFILE='kubernetes-forwarded-identity-observation-v1'
SCHEMA='guard-forwarded-identity-observations/1'
POLICY_TYPES={'forwarded_environment_sha256':'str','forwarded_target_sha256':'str','forwarded_session':'str',
    'forwarded_gateway_source_sha256':'str','forwarded_engine_source_sha256':'str','forwarded_identity_policy_sha256':'str',
    'forwarded_validity_start':'str','forwarded_validity_end':'str'}
OBSERVATIONS={'forwarded_trials':'list','forwarded_records':'list','forwarded_matches':'list','forwarded_missing':'list',
    'forwarded_conflict_candidates':'list','forwarded_collection':'list','forwarded_producer_authenticated':'bool',
    'forwarded_principal_identity_verified':'bool','forwarded_scope_complete':'bool'}
PHASES={'gateway':{'INGRESS','NORMALIZED','AUTHORIZED'},'engine':{'CONSUMED','AUTHORIZED'}}
FIELDS={'seq','event_id_sha256','request_sha256','phase','claim_sha256','principal_sha256','issuer_sha256',
    'call_sha256','parent_call_sha256','operation_sha256','action','authorization','monotonic_ns'}


def fingerprint(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None


def stamp(v):
    if type(v) is not str or len(v)>40:raise ValueError
    t=datetime.fromisoformat(v.replace('Z','+00:00'))
    if t.tzinfo is None or t.utcoffset() is None:raise ValueError
    return t


def normalize_policy(v):
    try:
        if type(v) is not dict or set(v)!=set(POLICY_TYPES):raise ValueError
        if any(not fingerprint(v[k]) for k in POLICY_TYPES if k.endswith('_sha256')):raise ValueError
        if type(v['forwarded_session']) is not str or re.fullmatch('[0-9a-f]{32}',v['forwarded_session']) is None:raise ValueError
        start,end=(stamp(v[k]) for k in ('forwarded_validity_start','forwarded_validity_end'))
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        return start,end
    except (ValueError,TypeError,KeyError):raise ValueError('FORWARDED_IDENTITY_POLICY_INVALID') from None


def validate_record(v,*,role,environment_ref,target_sha256,run_sha256,session,source_sha256,policy_sha256,header_contract,forbidden_values=()):
    try:
        if (role not in PHASES or type(v) is not dict or set(v)!={'schema','role','environment_ref','target_sha256','run_sha256','session',
                'producer_source_sha256','producer_authenticated','identity_policy_sha256','header_name_sha256','field_kind',
                'source_ended','dropped_events','events'} or v['schema']!=SCHEMA or v['role']!=role
                or canonical_json(v['environment_ref'])!=canonical_json(environment_ref) or v['target_sha256']!=target_sha256
                or v['run_sha256']!=run_sha256 or v['session']!=session or v['producer_source_sha256']!=source_sha256
                or v['producer_authenticated'] is not False or v['identity_policy_sha256']!=policy_sha256
                or v['header_name_sha256']!=sha256_hex(header_contract['header_name'].lower().encode())
                or v['field_kind']!=header_contract['field_kind'] or type(v['source_ended']) is not bool
                or type(v['dropped_events']) is not int or not 0<=v['dropped_events']<=2**31-1
                or type(v['events']) is not list or len(v['events'])>64):raise ValueError
        def strings(item):
            if type(item) is str:yield item
            elif type(item) is list:
                for part in item:yield from strings(part)
            elif type(item) is dict:
                for part in item.values():yield from strings(part)
        # A numeric selector such as '0' is not the text of every hash that
        # happens to contain that digit. Closed fields still reject a secret
        # copied verbatim into a hash; longer secret substrings are rejected too.
        if any(type(secret) is str and secret and any(leaf==secret or len(secret)>=8 and secret in leaf for leaf in strings(v)) for secret in forbidden_values):raise ValueError
        for index,row in enumerate(v['events']):
            if (type(row) is not dict or set(row)!=FIELDS or type(row['seq']) is not int or row['seq']!=index
                    or type(row['phase']) is not str or row['phase'] not in PHASES[role]
                    or not fingerprint(row['event_id_sha256']) or not fingerprint(row['request_sha256'])
                    or type(row['monotonic_ns']) is not int or not 0<=row['monotonic_ns']<2**64):raise ValueError
            for key in ('claim_sha256','principal_sha256','issuer_sha256','call_sha256','parent_call_sha256','operation_sha256'):
                if row[key] is not None and not fingerprint(row[key]):raise ValueError
            if row['action'] is not None and (type(row['action']) is not str or row['action'] not in {'preserved','removed','replaced'}):raise ValueError
            if row['authorization'] is not None and (type(row['authorization']) is not str or row['authorization'] not in {'allowed','denied','unknown'}):raise ValueError
            if row['phase']!='NORMALIZED' and row['action'] is not None:raise ValueError
            if row['phase']!='AUTHORIZED' and row['authorization'] is not None:raise ValueError
            if row['action']=='removed' and row['claim_sha256'] is not None:raise ValueError
        return deepcopy(v)
    except Exception:raise ValueError('FORWARDED_IDENTITY_RECORD_INVALID') from None


def correlate(snapshots,trials):
    records=[];seen=set();ids={};changed=set();conflicts=[];missing=[];matches=[]
    for snapshot in snapshots:
        role=snapshot['role']
        for row in snapshot['packet']['events']:
            key=(role,row['event_id_sha256']);identity=sha256_hex(canonical_json(row))
            ids.setdefault(key,set()).add(identity)
            if len(ids[key])>1:changed.add(key)
            if (role,identity) in seen:continue
            if len(records)>=512:
                missing.append({'case_id':None,'role':role,'reason':'record_bound_reached'});continue
            seen.add((role,identity));records.append({'role':role,**row})
    for role,event in sorted(changed):
        conflicts.append({'kind':'event_id_content_changed','role':role,'event_id_sha256':event,'confirmed_violation':False})
    for trial in trials:
        if not trial['attempted']:continue
        candidates=[r for r in records if r['request_sha256']==trial['request_id_sha256']]
        selected={}
        for role,phase in (('gateway','INGRESS'),('gateway','NORMALIZED'),('engine','CONSUMED')):
            rows=[r for r in candidates if r['role']==role and r['phase']==phase]
            if len(rows)==1 and (role,rows[0]['event_id_sha256']) not in changed:selected[phase]=rows[0]
            else:missing.append({'case_id':trial['case_id'],'role':role,'phase':phase,'reason':'unique_unchanged_record_unavailable'})
        a,b,c=(selected.get(k) for k in ('INGRESS','NORMALIZED','CONSUMED'))
        chain=bool(a and b and c and a['call_sha256'] and b['call_sha256'] and c['call_sha256']
            and b['parent_call_sha256']==a['call_sha256'] and c['parent_call_sha256']==b['call_sha256']
            and a['claim_sha256']==trial['claim_sha256']
            and a['operation_sha256']==b['operation_sha256']==c['operation_sha256']==trial['operation_sha256']
            and b['claim_sha256']==c['claim_sha256'])
        auths=[r for r in candidates if r['phase']=='AUTHORIZED' and (r['role'],r['event_id_sha256']) not in changed]
        auth=auths[0] if len(auths)==1 else None
        auth_link=bool(chain and auth and auth['parent_call_sha256']==(b['call_sha256'] if auth['role']=='gateway' else c['call_sha256'])
            and auth['call_sha256'] and auth['operation_sha256']==trial['operation_sha256'])
        matches.append({'case_id':trial['case_id'],'declared_chain_observed':chain,'declared_authorization_link_observed':auth_link,
            'normalized_claim_sha256':b['claim_sha256'] if b else None,'consumed_claim_sha256':c['claim_sha256'] if c else None,
            'principal_sha256':auth['principal_sha256'] if auth_link else None,'authorization':auth['authorization'] if auth_link else None,
            'producer_authenticated':False,'principal_identity_verified':False,'authorization_policy_verified':False})
        if not chain or not auth_link:missing.append({'case_id':trial['case_id'],'role':None,'reason':'normalization_consumption_or_authorization_link_unverified'})
        if trial['case_id']=='req04-alternate' and chain and c['claim_sha256']==trial['claim_sha256']:
            conflicts.append({'case_id':trial['case_id'],'kind':'alternate_declared_field_reaches_consumption',
                'field_kind':trial['field_kind'],'authorization':auth['authorization'] if auth_link else None,'confirmed_violation':False})
    byid={m['case_id']:m for m in matches};a=byid.get('req04-normal-before');b=byid.get('req04-alternate')
    if (a and b and a['declared_authorization_link_observed'] and b['declared_authorization_link_observed']
            and a['principal_sha256'] and b['principal_sha256'] and a['principal_sha256']!=b['principal_sha256']):
        conflicts.append({'case_id':'req04-alternate','kind':'same_transport_credential_changes_declared_principal','confirmed_violation':False})
    return {'records':records,'matches':matches,'missing':missing,'conflict_candidates':conflicts,
        'producer_authenticated':False,'principal_identity_verified':False,'scope_complete':False}
