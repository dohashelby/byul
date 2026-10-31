"""Bounded process observations through the existing exact Kubernetes transport.

The reader's UID and a container-reported process UID are distinct subjects.
No process is classified as a worker by this metadata-only boundary.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import stat

from guard.credential_policy import candidate_paths as validate_paths
from guard.models import canonical_json, sha256_hex, utcnow

PROFILE = 'kubernetes-owned-process-observations-v1'
SCHEMA = 'guard-container-process-observations/1'
SOURCE_SHA256 = '37e2fd7ea0f35d5265839781d35e7555ba854676f5d2f90e47350fe46bb54bc2'
MAX_PACKET = 524288
DIAGNOSTICS = frozenset({'PROCESS_IDENTITY_UNAVAILABLE','PROCESS_STATUS_UNAVAILABLE',
    'PROCESS_MOUNT_NAMESPACE_UNAVAILABLE','PROCESS_ENVIRONMENT_NAMES_INCOMPLETE',
    'PROCESS_ENVIRONMENT_NAMES_UNAVAILABLE','PROCESS_IDENTITY_CHANGED','DIRECTORY_SCOPE_UNVERIFIED',
    'PATH_ABSENT','PATH_METADATA_UNAVAILABLE','SYMLINK_NOT_FOLLOWED','PARENT_NOT_DIRECTORY',
    'PATH_IDENTITY_CHANGED','PROCESS_SET_BOUND','PROCESS_SET_EMPTY','PROCESS_SET_CHANGED','CAPTURE_SUBJECT_CHANGED','PROCESS_OUTPUT_BOUND'})


def _require(value):
    if not value:
        raise ValueError('KUBERNETES_PROCESS_PACKET_INVALID')


def _integer(value, lower=0, upper=2**64-1):
    return type(value) is int and lower <= value <= upper


def _interval(value):
    first = datetime.fromisoformat(value['started_at'].replace('Z','+00:00'))
    last = datetime.fromisoformat(value['finished_at'].replace('Z','+00:00'))
    _require(first.tzinfo is not None and last.tzinfo is not None and first <= last)


def _diagnostics(value):
    _require(type(value) is list and len(value) <= len(DIAGNOSTICS)
             and all(type(x) is str and x in DIAGNOSTICS for x in value) and len(set(value)) == len(value))


def paths(value):
    result = validate_paths(value)
    if result is not None:
        _require(all(chr(127) not in x for x in result))
    return result


def _decode(packet, expected_paths):
    """Reject extra fields/claims; preserve validated partial per-process facts."""
    keys = {'schema','started_at','finished_at','capture_subject','capture_process_excluded',
            'processes','pid_count_before','pid_count_after','pid_inventory_stable','process_metadata_complete',
            'candidate_metadata','worker_subject_verified','worker_inventory_complete','diagnostic_codes'}
    _require(type(packet) is dict and set(packet) == keys and packet['schema'] == SCHEMA)
    _require(packet['capture_process_excluded'] is True and packet['worker_subject_verified'] is False
             and packet['worker_inventory_complete'] is False)
    _interval(packet)
    _diagnostics(packet['diagnostic_codes'])
    subject = packet['capture_subject']
    _require(type(subject) is dict and set(subject) == {'pid','uid_effective','pid_namespace_inode','mount_namespace_inode','identity_stable'})
    _require(_integer(subject['pid'],1,2147483647) and _integer(subject['uid_effective'],0,4294967295)
             and _integer(subject['pid_namespace_inode'],1) and _integer(subject['mount_namespace_inode'],1)
             and type(subject['identity_stable']) is bool)
    for key in ('pid_count_before','pid_count_after'):
        _require(_integer(packet[key],0,129))
    for key in ('pid_inventory_stable','process_metadata_complete'):
        _require(type(packet[key]) is bool)
    rows = packet['processes']
    _require(type(rows) is list and len(rows) <= 128)
    row_keys = {'pid','start_ticks','name','uids','uid_effective','cap_eff','mount_namespace_inode',
                'environment_names','environment_names_complete','pid_identity_stable','started_at','finished_at','diagnostic_codes'}
    pids = []
    for row in rows:
        _require(type(row) is dict and set(row) == row_keys)
        _interval(row)
        _diagnostics(row['diagnostic_codes'])
        _require(_integer(row['pid'],1,2147483647) and row['pid'] != subject['pid'])
        pids.append(row['pid'])
        _require(row['start_ticks'] is None or _integer(row['start_ticks'],0,2**63-1))
        _require(type(row['pid_identity_stable']) is bool and type(row['environment_names_complete']) is bool)
        status = [row[k] for k in ('name','uids','uid_effective','cap_eff')]
        if any(x is not None for x in status):
            _require(type(row['name']) is str and re.fullmatch(r'[A-Za-z0-9_.:/+ -]{1,64}',row['name']) is not None)
            _require(type(row['uids']) is list and len(row['uids']) == 4
                     and all(_integer(x,0,4294967295) for x in row['uids']))
            _require(_integer(row['uid_effective'],0,4294967295) and row['uid_effective'] == row['uids'][1])
            _require(type(row['cap_eff']) is str and re.fullmatch('[0-9a-f]{1,16}',row['cap_eff']) is not None)
        else:
            _require(bool({'PROCESS_STATUS_UNAVAILABLE','PROCESS_IDENTITY_UNAVAILABLE'} & set(row['diagnostic_codes'])))
        _require(row['mount_namespace_inode'] is None or _integer(row['mount_namespace_inode'],1))
        if row['mount_namespace_inode'] is None:
            _require(bool({'PROCESS_MOUNT_NAMESPACE_UNAVAILABLE','PROCESS_IDENTITY_UNAVAILABLE'} & set(row['diagnostic_codes'])))
        if row['start_ticks'] is None:
            _require('PROCESS_IDENTITY_UNAVAILABLE' in row['diagnostic_codes'] and not row['pid_identity_stable'])
        if not row['pid_identity_stable']:
            _require(bool({'PROCESS_IDENTITY_CHANGED','PROCESS_IDENTITY_UNAVAILABLE'} & set(row['diagnostic_codes'])))
        names = row['environment_names']
        _require(type(names) is list and len(names) <= 64
                 and all(type(x) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',x) is not None for x in names)
                 and names == sorted(set(names)))
        if not row['environment_names_complete']:
            _require(bool({'PROCESS_ENVIRONMENT_NAMES_INCOMPLETE','PROCESS_ENVIRONMENT_NAMES_UNAVAILABLE',
                           'PROCESS_IDENTITY_UNAVAILABLE'} & set(row['diagnostic_codes'])))
    _require(pids == sorted(set(pids)) and len(pids) <= packet['pid_count_before'])
    if max(packet['pid_count_before'],packet['pid_count_after']) > 128:
        _require('PROCESS_SET_BOUND' in packet['diagnostic_codes'] and not packet['pid_inventory_stable'])
    if min(packet['pid_count_before'],packet['pid_count_after']) == 0:
        _require('PROCESS_SET_EMPTY' in packet['diagnostic_codes'] and not packet['pid_inventory_stable'])
    if packet['pid_inventory_stable']:
        _require(subject['identity_stable'] and bool(rows) and len(rows) == packet['pid_count_before'] == packet['pid_count_after']
                 and all(x['pid_identity_stable'] for x in rows) and not packet['diagnostic_codes'])
    if packet['process_metadata_complete']:
        _require(packet['pid_inventory_stable'] and all(not x['diagnostic_codes'] for x in rows))
    candidates = packet['candidate_metadata']
    _require(type(candidates) is list and len(candidates) == len(expected_paths))
    _require([x.get('path') for x in candidates if type(x) is dict] == expected_paths)
    candidate_keys = {'path','state','metadata','metadata_identity_stable','path_chain_sha256',
                      'access_tested','started_at','finished_at','diagnostic_codes'}
    for row in candidates:
        _require(type(row) is dict and set(row) == candidate_keys)
        _interval(row)
        _diagnostics(row['diagnostic_codes'])
        _require(row['state'] in {'metadata','absent','unavailable','symlink_not_followed'}
                 and row['access_tested'] is False and type(row['metadata_identity_stable']) is bool)
        digest = row['path_chain_sha256']
        _require(digest is None or type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest) is not None)
        metadata = row['metadata']
        if metadata is not None:
            _require(type(metadata) is dict and set(metadata) == {'device','inode','mode','uid','gid','size','mtime_ns','ctime_ns'})
            _require(_integer(metadata['device']) and _integer(metadata['inode']) and _integer(metadata['mode'],0,65535)
                     and _integer(metadata['uid'],0,4294967295) and _integer(metadata['gid'],0,4294967295)
                     and _integer(metadata['size'],0,2**63-1)
                     and all(_integer(metadata[x],-2**63,2**63-1) for x in ('mtime_ns','ctime_ns')))
            _require((row['state'] == 'symlink_not_followed') == stat.S_ISLNK(metadata['mode']))
        else:
            _require(row['state'] not in {'metadata','symlink_not_followed'})
        if row['metadata_identity_stable']:
            _require(digest is not None)
    return deepcopy(packet)


def decode(packet, expected_paths):
    try:
        return _decode(packet,expected_paths)
    except Exception:
        raise ValueError('KUBERNETES_PROCESS_PACKET_INVALID') from None


def source():
    raw = (Path(__file__).parent/'assets/process_observations_v1.py.txt').read_bytes()
    if sha256_hex(raw) != SOURCE_SHA256:
        raise ValueError('KUBERNETES_PROCESS_SOURCE_UNAVAILABLE')
    return raw.decode('utf-8')


def _mounts(pod, binding):
    _require(pod['spec'].get('shareProcessNamespace',False) is False)
    container, = [x for x in pod['spec']['containers'] if x['name'] == binding['container']]
    result = []
    for row in container.get('volumeMounts',[]):
        name, path, readonly = row['name'], row['mountPath'], row.get('readOnly',False)
        _require(type(name) is str and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}',name) is not None
                 and type(path) is str and path.startswith('/') and len(path.encode()) <= 512
                 and all(ord(c) >= 32 and ord(c) != 127 for c in path) and type(readonly) is bool)
        result.append({'name':name,'mount_path':path,'declared_read_only':readonly})
    _require(len(result) <= 128)
    return result


def capture(transport, candidate_paths=None):
    if transport.profile != 'kubernetes-owned-request-binding-http-v1':
        raise ValueError('KUBERNETES_PROCESS_PROFILE_UNSUPPORTED')
    selected_paths = paths(candidate_paths)
    program = source()
    record = {'profile':PROFILE,'started_at':utcnow().isoformat(),'finished_at':None,
        'target_sha256':transport.target_sha256,'program_sha256':SOURCE_SHA256,
        'data_origin':transport.process_observation_origin,'available':False,
        'binding_before':None,'binding_after':None,'binding_verified_before_after':False,
        'candidate_paths_requested':selected_paths is not None,'mount_declarations':[],
        'mount_declarations_after':None,'mount_declarations_stable':False,
        'packet':None,'diagnostic_codes':[],'cleanup_errors':[],
        'worker_subject_verified':False,'worker_inventory_complete':False,'security':'UNKNOWN',
        'scope':'selected container-reported PID namespace; capture subject and observed processes remain distinct',
        'limitations':['No source/model worker classification or whole-worker coverage.',
                       'Environment names and explicit candidate lstat metadata only; no values/contents/access test.',
                       'API declarations and container-reported namespaces are not host-runtime isolation or consumption proof.']}
    try:
        pods = transport._verify(capture_snapshot=True)
        binding = transport.binding['engine']
        record['binding_before'] = deepcopy(binding)
        record['mount_declarations'] = _mounts(pods['engine'],binding)
        remaining = transport.deadline-transport.clock()
        _require(0 < remaining <= 300)
        raw = transport._call(['--namespace='+binding['namespace'],'exec','-i',binding['pod'],
            '--container='+binding['container'],'--','python3','-c',program,str(remaining)],
            payload=canonical_json({'candidate_paths':selected_paths or []}),cap=MAX_PACKET+1,budget=remaining)
        record['packet'] = decode(raw,selected_paths or [])
        pods = transport._verify(capture_snapshot=True)
        record['mount_declarations_after'] = _mounts(pods['engine'],binding)
        record['mount_declarations_stable'] = record['mount_declarations'] == record['mount_declarations_after']
        if not record['mount_declarations_stable']:
            record['diagnostic_codes'].append('PROCESS_MOUNT_DECLARATIONS_CHANGED')
        record.update(binding_after=deepcopy(binding),binding_verified_before_after=True,available=True)
    except Exception:
        record['diagnostic_codes'].append('KUBERNETES_PROCESS_CAPTURE_UNAVAILABLE')
    record['finished_at'] = utcnow().isoformat()
    record['cleanup_errors'] = list(transport.cleanup_errors)
    return record
