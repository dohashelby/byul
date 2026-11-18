"""PLT-02's bounded metadata view of approved paths in the owned Pod."""
import re
import stat

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_process import paths
from .container_config import read_kubernetes_process_observations

PROFILE = 'kubernetes-owned-ipc-path-metadata-v1'
VERSION = '0.1.0+ipc-path-metadata'
CONFIG_KEYS = {'target', 'protected_path_metadata_only', 'kubectl_binary', 'max_duration_seconds'}


def validate_policy(check, values):
    if (check.id != 'PLT-02' or check.executor != 'unix_ipc'
            or getattr(check, 'collection_profile', None) != 'unix-ipc-access-v1'
            or type(values) is not dict or set(values) != {'protected_unix_paths', 'allowed_ipc_uids'}):
        raise ValueError('KUBERNETES_IPC_PATH_METADATA_UNSUPPORTED')
    selected = paths(values['protected_unix_paths'])
    uids = values['allowed_ipc_uids']
    if (not selected or '/' in selected or type(uids) is not list
            or any(type(uid) is not str or re.fullmatch('[0-9]{1,10}', uid) is None
                   or int(uid) > 4294967295 for uid in uids)):
        raise ValueError('KUBERNETES_IPC_PATH_METADATA_POLICY_INVALID')


def collect(ctx):
    record = read_kubernetes_process_observations(ctx, ctx.policy_values['protected_unix_paths'])
    packet = record.get('packet') or {}
    subject = packet.get('capture_subject') or {}
    rows = []
    verified = (record['candidate_paths_requested'] is True and record['available'] is True
                and record['binding_verified_before_after'] is True
                and subject.get('identity_stable') is True and subject.get('mount_namespace_inode') is not None)
    if verified:
        for source in packet['candidate_metadata']:
            if not source['metadata_identity_stable'] or source['state'] not in {'metadata', 'absent'}:
                continue
            metadata = source['metadata']
            state = ('absent_in_reader_namespace' if metadata is None else
                     'socket' if stat.S_ISSOCK(metadata['mode']) else 'other_file_type')
            rows.append({'path_sha256': sha256_hex(source['path'].encode('utf-8')), 'state': state,
                         'owner_uid': metadata['uid'] if metadata else None,
                         'owner_gid': metadata['gid'] if metadata else None,
                         'permission_mode': stat.S_IMODE(metadata['mode']) if metadata else None,
                         'path_chain_sha256': source['path_chain_sha256'], 'access_tested': False})
    cleanup = list(record['cleanup_errors'])
    ctx._ipc_metadata_cleanup = cleanup
    ctx._ipc_metadata_gap = not rows or bool(cleanup)
    diagnostics = list(record['diagnostic_codes'])
    if len(rows) < len(ctx.policy_values['protected_unix_paths']):
        diagnostics.append('IPC_PATH_METADATA_UNAVAILABLE')
    snapshot = {'profile': PROFILE, 'target_sha256': record['target_sha256'],
                'binding_verified': verified, 'path_metadata': rows, 'diagnostic_codes': diagnostics,
                'mount_namespace_sha256': sha256_hex(canonical_json(subject['mount_namespace_inode'])) if verified else None,
                'fixture': {'profile': PROFILE, 'reader_source_sha256': record['program_sha256']}}
    observations = {'unix_ipc_trials': {'value': rows if rows else None, 'collected': bool(rows)},
                    'unix_ipc_violations': {'value': None, 'collected': False},
                    'ipc_scope_complete': {'value': False, 'collected': True}}
    normal = {'attempted': False, 'succeeded': None, 'detail': 'Observed configured path metadata in the bound Pod mount namespace.'}
    return observations, normal, [{'step': step, 'attempted': False} for step in ctx.check.challenge_steps], snapshot
