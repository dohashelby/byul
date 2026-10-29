"""Closed Kubernetes IPC inputs; dedicated product consumer remains separate.

The fixed A2 probe remains byte-identical. Actor credentials must be explicit;
collector/owner/Pod declarations never provide enterprise allowed identities.
"""
from pathlib import PurePosixPath
import math
import re

PROFILE='kubernetes-owned-unix-ipc-access-v1'
RESULT_SCHEMA='guard-kubernetes-unix-ipc-snapshot/1'
IPC_PROBE_SOURCE_SHA256='f096e9d95aed61315b4d129369cd273786fa9dc106ac264d48c670e004269ca2'
IPC_PROBE_COMMIT='3ecfd5f503b837b69bb67cb31eee279d4e398e87'
MAX_PATHS=8
MAX_ACTORS=4
MAX_GROUPS=16
MAX_OUTPUT_BYTES=65536
MAX_PROC_OR_FD_ENTRIES=4096
MAX_OWNERS=16
PUBLIC_TRANSPORT_AVAILABLE=True
PRODUCT_CONSUMER_REGISTERED=False


def _identifier(value):
    return type(value) is int and 0<=value<=2**31-1


def normalize_inputs(policy_values,*,actor_specs,connect_timeout,total_timeout):
    """Validate selected inputs only; this does not execute or approve an actor."""
    try:
        if type(policy_values) is not dict or set(policy_values)!={'protected_unix_paths','allowed_ipc_uids'}:raise ValueError
        paths=policy_values['protected_unix_paths'];allowed=policy_values['allowed_ipc_uids']
        if type(paths) is not list or not 1<=len(paths)<=MAX_PATHS:raise ValueError
        for raw in paths:
            if type(raw) is not str or not 1<=len(raw.encode('utf-8'))<=100 or any(ord(c)<32 or ord(c)==127 for c in raw):raise ValueError
            path=PurePosixPath(raw)
            if (not path.is_absolute() or path.parts[0]!='/' or str(path)!=raw or raw=='/'
                    or any(p in {'.','..'} for p in path.parts)):raise ValueError
        if len(set(paths))!=len(paths):raise ValueError
        if (type(allowed) is not list or not 1<=len(allowed)<=16
                or any(type(v) is not str or re.fullmatch(r'0|[1-9][0-9]{0,9}',v) is None or int(v)>2**31-1 for v in allowed)
                or len(set(allowed))!=len(allowed) or type(actor_specs) is not list or not 2<=len(actor_specs)<=MAX_ACTORS):raise ValueError
        actors=[]
        for actor in actor_specs:
            if type(actor) is not dict or set(actor)!={'uid','gid','groups'} or not _identifier(actor['uid']) or not _identifier(actor['gid']):raise ValueError
            groups=actor['groups']
            if (type(groups) is not list or len(groups)>MAX_GROUPS or any(not _identifier(v) for v in groups)
                    or len(set(groups))!=len(groups)):raise ValueError
            actors.append({'uid':actor['uid'],'gid':actor['gid'],'groups':sorted(groups)})
        uids=[a['uid'] for a in actors]
        allowed_numbers={int(v) for v in allowed}
        if len(set(uids))!=len(uids) or not set(uids)&allowed_numbers or not set(uids)-allowed_numbers:raise ValueError
        for value,upper in ((connect_timeout,2),(total_timeout,300)):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=upper:raise ValueError
        return {'paths':list(paths),'allowed_ipc_uids':list(allowed),'actors':actors,
            'connect_timeout':float(connect_timeout),'total_timeout':float(total_timeout)}
    except (KeyError,TypeError,ValueError,UnicodeError,OverflowError):
        raise ValueError('KUBERNETES_IPC_INPUT_INVALID') from None


def require_capability():
    raise ValueError('KUBERNETES_IPC_PROFILE_NOT_REGISTERED')
