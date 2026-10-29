"""Versioned selected Kubernetes API hop; no verdict or imported capture.

Reuse the exact normal-control source and generations. The policy has no
Docker network/endpoint fields, defaults, inferred prohibition or exception.
"""
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import math
from pathlib import Path
import re

from guard.models import canonical_json, sha256_hex, utcnow
from .kubernetes_http import KubernetesBindingError, REQUEST_BINDING_PROFILE
from .kubernetes_functional import LOGICAL_ID

PROFILE='explicit-owned-kubernetes-api-plaintext-v1'
SCOPE='guard_probe_to_owned_kubernetes_direct_api'
POLICY_TYPES={'api_hop_scope':'str','api_environment_sha256':'str','api_target_sha256':'str',
    'api_binding_sha256':'str','api_ip':'str','api_port':'int','plaintext_forbidden':'bool',
    'validity_start':'str','validity_end':'str'}


def _require(value):
    if not value:raise ValueError('KUBERNETES_API_HOP_POLICY_INVALID')


def _when(value):
    _require(type(value) is str and 1<=len(value)<=40)
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        _require(result.tzinfo is not None and result.utcoffset() is not None)
        return result.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise ValueError('KUBERNETES_API_HOP_POLICY_INVALID') from None


def normalize_policy(values):
    _require(type(values) is dict and set(values)==set(POLICY_TYPES))
    _require(values['api_hop_scope']==SCOPE
        and all(type(values[k]) is str and re.fullmatch(r'[0-9a-f]{64}',values[k])
                for k in ('api_environment_sha256','api_target_sha256','api_binding_sha256'))
        and type(values['api_port']) is int and 1<=values['api_port']<=65535
        and type(values['plaintext_forbidden']) is bool)
    try:
        ip=ipaddress.IPv4Address(values['api_ip'])
        _require(type(values['api_ip']) is str and str(ip)==values['api_ip']
            and not ip.is_unspecified and not ip.is_multicast)
    except (ValueError,TypeError):
        raise ValueError('KUBERNETES_API_HOP_POLICY_INVALID') from None
    _require(_when(values['validity_start'])<_when(values['validity_end']))
    return deepcopy(values)


def capture(transport,values,timeout):
    started=utcnow()
    result={'schema':'guard-kubernetes-api-hop-capture/1','profile':PROFILE,
        'data_origin':transport.process_observation_origin,'available':False,
        'policy_binding_verified':False,'policy_window_verified':False,
        'capture_executed':False,
        'binding_verified_before_after':False,'normal_response_schema_verified':False,
        'initial_response':None,'diagnostic_codes':[],
        'started_at':started.isoformat(),'environment_ref':deepcopy(transport.environment_ref),
        'target_sha256':transport.target_sha256,
        'binding_sha256':sha256_hex(canonical_json(transport.binding)),
        'collector_source_sha256':sha256_hex(Path(__file__).read_bytes()),
        'normal_control_source_sha256':sha256_hex(Path(__file__).with_name('kubernetes_functional.py').read_bytes()),
        'transport_source_sha256':sha256_hex(Path(__file__).with_name('kubernetes_http.py').read_bytes()),
        'probe_source_sha256':transport.source_sha256,
        'transport':'plain_http','cryptographic_peer_identity_verified':False,
        'deployed_caller_path_verified':False,'api_transport_scope_complete':False,
        'internal_IPC_TP_scope_complete':False}
    try:
        policy=normalize_policy(values)
        _require(transport.profile==REQUEST_BINDING_PROFILE
            and policy['api_environment_sha256']==transport.environment_ref['sha256']
            and policy['api_target_sha256']==transport.target_sha256
            and policy['api_binding_sha256']==result['binding_sha256']
            and policy['api_ip']==transport.endpoint.hostname and policy['api_port']==transport.endpoint.port
            and _when(policy['validity_start'])<=started<=_when(policy['validity_end'])
            and type(timeout) in (int,float) and math.isfinite(timeout) and 0<timeout<=30)
        result.update(policy_binding_verified=True,policy_sha256=sha256_hex(canonical_json(policy)),
            plaintext_forbidden=policy['plaintext_forbidden'],hop_scope=SCOPE,
            binding=deepcopy(transport.binding),port=policy['api_port'])
        requests=[{'id':LOGICAL_ID,'method':'POST','url':transport.endpoint.geturl()+'/v1/completions',
            'headers':{},'body':{'model':transport.api_model,'prompt':'ping','max_tokens':1},'want_body':True}]
        rows=transport.normal_requests('kubernetes-probe',requests,timeout)
        # The public entry provides exact response correlation and binding;
        # use the existing structural validator without persisting its body.
        _require(type(rows) is list and len(rows)==1 and rows[0]['id']==LOGICAL_ID)
        response=rows[0]
        result['capture_executed']=True
        from guard.executors.content_common import control_state
        result['normal_response_schema_verified']=(response['capture_complete'] is True
            and control_state(response,transport.api_model,'/v1/completions')=='pass')
        result['initial_response']={k:response[k] for k in
            ('id','method','outcome','capture_complete','body_bytes','elapsed_ms') if k in response}
        result['binding_verified_before_after']=True
    except Exception:
        result['diagnostic_codes'].append('KUBERNETES_API_HOP_UNAVAILABLE')
    finally:
        finished=utcnow();result['finished_at']=finished.isoformat()
        if result['binding_verified_before_after']:
            result['policy_window_verified']=(_when(policy['validity_start'])<=started<=finished<=_when(policy['validity_end']))
            result['available']=result['policy_window_verified']
            if not result['policy_window_verified']:result['diagnostic_codes'].append('KUBERNETES_API_HOP_WINDOW_UNVERIFIED')
        result['cleanup_errors']=list(transport.cleanup_errors)
        result['parent_cleanup_verified']=not result['cleanup_errors']
    return result
