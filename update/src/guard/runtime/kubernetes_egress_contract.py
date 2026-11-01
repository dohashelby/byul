"""Closed common input for one owned UID/netns TCP operation; no adjudication."""
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import math
import re

PROFILE='kubernetes-owned-explicit-egress-v1'
SCHEMA='guard-kubernetes-egress-snapshot/1'
ACTOR_SCHEMA='guard-kubernetes-egress-helper/1'
KEYS={'subject_scope','subject_container_id','subject_uid','subject_net_namespace_inode',
    'forbidden_receiver_container_id','forbidden_receiver_ip','forbidden_receiver_port',
    'forbidden_protocol','forbidden_operation','validity_start','validity_end','source_target_sha256','receiver_target_sha256'}

def require(value):
    if not value:raise ValueError('KUBERNETES_EGRESS_INPUT_INVALID')

def integer(v,low=0,high=2**63-1):return type(v) is int and low<=v<=high
def digest(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None

def when(value):
    try:
        require(type(value) is str and 1<=len(value)<=40)
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
        return parsed.astimezone(timezone.utc)
    except Exception:raise ValueError('KUBERNETES_EGRESS_INPUT_INVALID') from None

def _normalize(policy_values,*,actor_spec,connect_timeout,total_timeout):
    p=policy_values;a=actor_spec
    require(type(p) is dict and set(p)==KEYS and type(a) is dict and set(a)=={'uid','gid','groups'})
    require(p['subject_scope']=='all_processes_in_container_uid_netns'
        and p['forbidden_protocol']=='tcp' and p['forbidden_operation']=='tcp_connect')
    require(all(type(p[k]) is str and re.fullmatch('containerd://[0-9a-f]{64}',p[k]) for k in
        ('subject_container_id','forbidden_receiver_container_id')) and p['subject_container_id']!=p['forbidden_receiver_container_id'])
    require(all(digest(p[k]) for k in ('source_target_sha256','receiver_target_sha256'))
        and integer(p['subject_uid'],0,2**31-1) and integer(p['subject_net_namespace_inode'],1)
        and integer(p['forbidden_receiver_port'],1,65535))
    address=ipaddress.IPv4Address(p['forbidden_receiver_ip'])
    require(type(p['forbidden_receiver_ip']) is str and str(address)==p['forbidden_receiver_ip']
        and not (address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved)
        and str(address)!='255.255.255.255')
    require(when(p['validity_start'])<when(p['validity_end']))
    require(integer(a['uid'],0,2**31-1) and a['uid']==p['subject_uid'] and integer(a['gid'],0,2**32-1)
        and type(a['groups']) is list and len(a['groups'])<=16 and all(integer(g,0,2**32-1) for g in a['groups'])
        and a['groups']==sorted(set(a['groups'])))
    require(type(connect_timeout) in (int,float) and math.isfinite(connect_timeout) and 0<connect_timeout<=2
        and type(total_timeout) in (int,float) and math.isfinite(total_timeout) and connect_timeout+1<total_timeout<=30)
    return deepcopy({'policy_values':p,'actor_spec':a,'connect_timeout':connect_timeout,'total_timeout':total_timeout})

def normalize(policy_values,*,actor_spec,connect_timeout,total_timeout):
    try:return _normalize(policy_values,actor_spec=actor_spec,connect_timeout=connect_timeout,total_timeout=total_timeout)
    except Exception:raise ValueError('KUBERNETES_EGRESS_INPUT_INVALID') from None
