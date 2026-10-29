"""Explicit REQ06 policy and declared HTTP effects; no fabricated principal gate."""
from datetime import datetime
import re
from guard.adapters.vllm_stored_responses import REVIEWED_SOURCES
from guard.runtime.kubernetes_stored_response_scope import normalize_scope

PROFILE='kubernetes-stored-response-ownership-v1'
POLICY_TYPES={'stored_environment_sha256':'str','stored_target_sha256':'str','stored_session':'str',
 'stored_identity_policy_sha256':'str','stored_api_router_source_sha256':'str','stored_serving_source_sha256':'str','stored_protocol_source_sha256':'str',
 'stored_validity_start':'str','stored_validity_end':'str','stored_origins':'list[str]','stored_operations':'list[str]','stored_retention':'str'}
OBSERVATIONS={'stored_trials':'list','stored_objects':'list','stored_effects':'list','stored_missing':'list','stored_conflict_candidates':'list',
 'stored_cleanup':'list','stored_producer_authenticated':'bool','stored_object_owner_verified':'bool','stored_worker_effect_verified':'bool','stored_scope_complete':'bool'}


def stamp(v):
    if type(v) is not str or len(v)>40:raise ValueError
    value=datetime.fromisoformat(v.replace('Z','+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:raise ValueError
    return value


def normalize_policy(values,roles):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        if any(type(values[k]) is not str or re.fullmatch('[0-9a-f]{64}',values[k]) is None for k in POLICY_TYPES if k.endswith('_sha256')):raise ValueError
        for key,sha in REVIEWED_SOURCES.items():
            if values['stored_'+key+'_source_sha256']!=sha:raise ValueError
        if values['stored_retention']!='retain-owned-lab-objects':raise ValueError
        start,end=stamp(values['stored_validity_start']),stamp(values['stored_validity_end'])
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        scope=normalize_scope({'session':values['stored_session'],'origins':values['stored_origins'],'operations':values['stored_operations'],'roles':roles})
        return start,end,scope
    except (ValueError,TypeError,KeyError):raise ValueError('STORED_RESPONSE_POLICY_INVALID') from None
