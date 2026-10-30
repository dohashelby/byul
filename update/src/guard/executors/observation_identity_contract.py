"""OBS02 declared audit/native attribution candidates; no approval or use proof."""
import re
from guard.adapters.vllm_model_authorization import project_configured_models
from guard.models import canonical_json,sha256_hex
from .observation_contract import fingerprint
from .kubernetes_observation_state import POLICY_TYPES as BASE_POLICY,normalize_policy as normalize_base

PROFILE='kubernetes-observation-identity-candidates-v1'
POLICY_TYPES={**BASE_POLICY,'identity_prepared_sha256':'str','identity_record_session':'str','identity_record_source_sha256':'str'}
TRUTH_FIELDS=('identity_producer_authenticated','identity_approval_authority_verified','identity_approval_model_scope_verified',
 'identity_inspection_verified','identity_actual_consumption_verified','identity_record_worker_generation_verified',
 'identity_record_time_order_verified','identity_enterprise_policy_adopted','identity_scope_complete')
OBSERVATIONS={'identity_record_collections':'list','identity_declared_records':'list','identity_native_models':'list',
 'identity_binding_candidates':'list','identity_conflict_candidates':'list','identity_missing':'list',**{k:'bool' for k in TRUTH_FIELDS}}
SEMANTIC_GAPS=('approval_authority_unverified','approval_model_scope_not_in_OBS01_v1','inspection_contract_unavailable',
 'actual_tensor_consumption_unverified','record_worker_generation_unavailable','producer_clock_unavailable','enterprise_policy_unset')


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        start,end=normalize_base({key:values[key] for key in BASE_POLICY})
        if (not fingerprint(values['identity_prepared_sha256']) or not fingerprint(values['identity_record_source_sha256'])
                or type(values['identity_record_session']) is not str or re.fullmatch('[0-9a-f]{32}',values['identity_record_session']) is None):raise ValueError
        return start,end
    except (KeyError,ValueError,TypeError):raise ValueError('OBSERVATION_IDENTITY_POLICY_INVALID') from None


def native_models(packet,trials):
    """Only the internal validated capture_session packet, with explicit locators."""
    attempts=[t for t in trials if t['method']=='POST']
    return project_configured_models(packet,attempts,locators=True)


def correlate(snapshots,models,*,run_sha256,target_sha256,source_sha256,model_sha256,revision_sha256,inventory_sha256):
    records=[];seen=set();candidates=[];conflicts=[];missing=list(SEMANTIC_GAPS)
    for snapshot in snapshots:
        for event in snapshot['packet']['events']:
            digest=sha256_hex(canonical_json(event))
            if digest in seen:continue
            seen.add(digest)
            records.append({'record':event,'source_locator':{'stage':snapshot['stage'],'source_bytes_sha256':snapshot['source_bytes_sha256'],
                'seq':event['seq'],'event_id_sha256':event['event_id_sha256']}})
    for trial in models:
        if not trial['attempted']:continue
        consumed=[r for r in records if r['record']['event']=='ARTIFACT_CONSUMED' and r['record']['request_sha256']==trial['request_id_sha256']]
        for native in trial['configured_model_candidates']:
            locator=native['native_locator']
            native_link=(trial['binding_verified'] and trial['policy_window_verified'] and native['same_process_loader_object_linked']
                and native['source_process_context_verified'] and native['source_image_digest_verified']
                and locator['recording_closed'] and locator['loader_sequences_complete'] and locator['same_process_request_assignment_observed'])
            configured_match=(native['configured_model_sha256']==model_sha256 and native['configured_revision_sha256']==revision_sha256)
            row={'case_id':trial['case_id'],'request_id_sha256':trial['request_id_sha256'],
                'native':native,'expected_ENV_model_sha256':model_sha256,'expected_ENV_revision_sha256':revision_sha256,
                'declared_ENV_inventory_sha256':inventory_sha256,'actual_inventory_verified':False,
                'same_process_native_model_link_observed':bool(native_link),'configured_ENV_model_match_observed':bool(configured_match),
                'consumption_record_count':len(consumed),'approval_record_count':0,'consumption_locator':None,'approval_locator':None,
                'declared_record_pair_consistent':False,'mismatched_fields':[],'missing_fields':[],
                'approval_authority_verified':False,'approval_model_scope_verified':False,'inspection_verified':False,
                'actual_consumption_verified':False,'record_worker_generation_verified':False,'record_time_order_verified':False,
                'cross_process_RPC_verified':False,'confirmed_violation':False}
            if len(consumed)==1:
                consumption=consumed[0];event=consumption['record'];row['consumption_locator']=consumption['source_locator']
                approvals=[r for r in records if r['record']['event']=='APPROVAL' and fingerprint(event['approval_sha256'])
                    and r['record']['approval_sha256']==event['approval_sha256']]
                row['approval_record_count']=len(approvals)
                if len(approvals)==1:
                    approval=approvals[0];row['approval_locator']=approval['source_locator'];approved=approval['record']
                    for name,record in (('consumption',event),('approval',approved)):
                        for field in ('actor_sha256','target_sha256','run_sha256','policy_sha256','approval_sha256','source_sha256'):
                            if record[field] is None:row['missing_fields'].append(name+'.'+field)
                        for field,expected in (('target_sha256',target_sha256),('run_sha256',run_sha256),('source_sha256',source_sha256)):
                            if record[field] is not None and record[field]!=expected:row['mismatched_fields'].append(name+'.'+field)
                    if event['policy_sha256'] is not None and approved['policy_sha256'] is not None and event['policy_sha256']!=approved['policy_sha256']:
                        row['mismatched_fields'].append('declared_policy_pair')
                    # Consistent declarations still do not identify an approved
                    # physical model, authenticated issuer or actual tensor use.
                    row['declared_record_pair_consistent']=not row['missing_fields'] and not row['mismatched_fields']
            if not native_link:missing.append('same_process_native_model_link_unavailable')
            if not consumed or row['approval_record_count']==0:missing.append('declared_approval_consumption_pair_unavailable')
            if not configured_match or row['mismatched_fields'] or len(consumed)>1 or row['approval_record_count']>1:
                conflicts.append({'case_id':trial['case_id'],'configured_ENV_model_match_observed':bool(configured_match),
                    'mismatched_fields':row['mismatched_fields'],'ambiguous_consumption_record':len(consumed)>1,
                    'ambiguous_approval_record':row['approval_record_count']>1,'confirmed_violation':False})
            candidates.append(row)
        if not trial['configured_model_candidates']:missing.append('native_model_candidate_unavailable')
    if not any(r['record']['event']=='APPROVAL' for r in records):missing.append('approval_record_unavailable')
    if not any(r['record']['event']=='ARTIFACT_CONSUMED' for r in records):missing.append('consumption_record_unavailable')
    return {'schema':'guard-observation-identity-candidates/1','records':records,'native_models':models,
        'candidates':candidates,'conflict_candidates':conflicts,'missing':sorted(set(missing)),
        'producer_authenticated':False,'approval_authority_verified':False,'actual_consumption_verified':False,'whole_scope_complete':False}
