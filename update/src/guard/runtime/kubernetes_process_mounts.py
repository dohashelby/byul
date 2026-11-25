"""Observed mount bans only. No credential read or whole-worker assertion."""
from pathlib import Path
from copy import deepcopy
import re

from guard.credential_policy import candidate_paths
from guard.models import DataOrigin,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from .kubernetes_process import PROFILE as PROCESS_PROFILE,SOURCE_SHA256 as PROCESS_SOURCE_SHA256
from .source_assets import read_pinned_source

PROFILE='kubernetes-owned-process-mount-observations-v1'
SCHEMA='guard-process-mount-observations/1'
SOURCE_SHA256='590ac5729bf3cac321c9ac76ee267420e83a5bae8103975a35309dea2b022c16'

def source():
    return read_pinned_source(Path(__file__).parent / "assets/process_mount_observations_v1.py.txt",
                              SOURCE_SHA256, "PROCESS_MOUNT_SOURCE_UNAVAILABLE")

def decode(value,spec):
    if (type(value) is not dict or set(value)!={'schema','scope','rows'} or value['schema']!=SCHEMA
            or value['scope']!='selected-container-mount-metadata-only' or type(value['rows']) is not list
            or len(value['rows'])!=len(spec['processes'])):raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
    for expected,row in zip(spec['processes'],value['rows'],strict=True):
        if (type(row) is not dict or set(row)!={'pid','start_ticks','mount_namespace_inode','stable','matches','mountinfo_sha256','diagnostic'}
                or any(type(row[k]) is not int or row[k]!=v for k,v in expected.items())
                or type(row['stable']) is not bool or type(row['matches']) is not list
                or len(row['matches'])>len(spec['forbidden_mount_paths'])*len(spec['declared_mount_paths'])):raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
        if row['stable']:
            if (type(row['mountinfo_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',row['mountinfo_sha256']) is None
                    or row['diagnostic'] is not None):raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
        elif row['matches'] or row['mountinfo_sha256'] is not None or row['diagnostic']!='MOUNT_CAPTURE_UNAVAILABLE':raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
        seen=set()
        for match in row['matches']:
            if (type(match) is not dict or set(match)!={'policy_path','mount_path'}
                    or match['policy_path'] not in spec['forbidden_mount_paths']
                    or match['mount_path'] not in spec['declared_mount_paths']):raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
            key=(match['policy_path'],match['mount_path'])
            if key in seen:raise ValueError('PROCESS_MOUNT_PACKET_INVALID')
            seen.add(key)
    return deepcopy(value)

def capture(transport,record,forbidden):
    result={'profile':PROFILE,'program_sha256':SOURCE_SHA256,'target_sha256':transport.target_sha256,
        'data_origin':transport.process_observation_origin,'available':False,'violations':[],
        'whole_worker_coverage_verified':False,'packet':None,'diagnostic_codes':[],
        'started_at':utcnow().isoformat(),'finished_at':None}
    try:
        selected=candidate_paths(forbidden)
        if selected is None:raise ValueError('PROCESS_MOUNT_POLICY_REQUIRED')
        origin=min(DataOrigin(record['data_origin']),DataOrigin(transport.process_observation_origin),
            key=lambda value:ORIGIN_STRENGTH[value])
        if (transport.profile!='kubernetes-owned-request-binding-http-v1'
                or record['profile']!=PROCESS_PROFILE or record['target_sha256']!=transport.target_sha256
                or record['program_sha256']!=PROCESS_SOURCE_SHA256
                or record['available'] is not True or record['binding_verified_before_after'] is not True
                or record['mount_declarations_stable'] is not True
                or record['binding_before']!=transport.binding['engine'] or record['binding_after']!=transport.binding['engine']
                or record['mount_declarations']!=record['mount_declarations_after']):raise ValueError
        engine=transport.binding['engine'];packet=record['packet'];own=packet['capture_subject']['mount_namespace_inode']
        if packet['capture_subject']['identity_stable'] is not True or packet['capture_process_excluded'] is not True:raise ValueError
        processes=[{k:r[k] for k in ('pid','start_ticks','mount_namespace_inode')}
            for r in packet['processes'] if r['pid_identity_stable'] is True
            and r['mount_namespace_inode']==own and type(r['start_ticks']) is int and r['start_ticks']>0][:8]
        if not processes:raise ValueError
        spec={'processes':processes,'declared_mount_paths':sorted({m['mount_path'] for m in record['mount_declarations']}),
              'forbidden_mount_paths':selected}
        program=source()
        transport._verify()
        value=transport._call([f"--namespace={engine['namespace']}",'exec',engine['pod'],'-c',engine['container'],'--',
            'python3','-I','-S','-B','-c',program,canonical_json(spec).decode('utf-8')],cap=262144,budget=8)
        transport._verify()
        value=decode(value,spec)
        result.update(available=True,packet=value,violations=sorted({m['policy_path'] for row in value['rows']
            if row['stable'] for m in row['matches']}))
        result['data_origin']=origin.value
        if not all(row['stable'] for row in value['rows']):result['diagnostic_codes'].append('PROCESS_MOUNT_PARTIAL')
    except Exception:result['diagnostic_codes'].append('PROCESS_MOUNT_CAPTURE_UNAVAILABLE')
    result['finished_at']=utcnow().isoformat()
    return result
