"""PLT-06 marker-level process identity/UID metadata from an owned Kubernetes Pod."""
from collections import defaultdict

from guard.models import canonical_json, sha256_hex
from . import ipc_trust_scope
from .container_config import kubernetes_process_rows, read_kubernetes_process_observations

PROFILE = 'kubernetes-owned-worker-trust-metadata-v1'
VERSION = '0.1.0+worker-trust-metadata'
CONFIG_KEYS = {'target', 'worker_trust_metadata_only', 'kubectl_binary', 'max_duration_seconds'}


def validate_policy(check, values):
    if (check.id != 'PLT-06' or check.executor != 'unix_ipc' or check.collection_profile != ipc_trust_scope.PROFILE
            or type(values) is not dict or set(values) != {'worker_isolation_requirement', 'worker_trust_group_markers'}):
        raise ValueError('KUBERNETES_WORKER_TRUST_METADATA_UNSUPPORTED')
    ipc_trust_scope.validate_policy(values)


def collect(ctx):
    record = read_kubernetes_process_observations(ctx)
    if record['candidate_paths_requested'] is not False:
        raise ValueError('KUBERNETES_WORKER_TRUST_METADATA_SCOPE_INVALID')
    markers = ctx.policy_values['worker_trust_group_markers']
    groups, ambiguous = defaultdict(list), []
    stable_rows = kubernetes_process_rows(record)
    for row in stable_rows:
        if any(row[key] is None for key in ('name', 'uid_effective', 'start_ticks', 'mount_namespace_inode')):
            continue
        matches = ipc_trust_scope.matching_markers(row['name'], markers)
        if len(matches) > 1:
            ambiguous.append({'pid': row['pid'], 'matching_markers': matches})
        elif matches:
            groups[matches[0]].append({'pid': row['pid'], 'start_ticks': row['start_ticks'],
                'effective_uid': row['uid_effective'],
                'mount_namespace_sha256': sha256_hex(canonical_json(row['mount_namespace_inode']))})
    mapping = [{'marker': marker, 'processes': items} for marker, items in sorted(groups.items())]
    missing = sorted(set(markers) - set(groups))
    cleanup = list(record['cleanup_errors'])
    ctx._ipc_metadata_cleanup = cleanup
    ctx._ipc_metadata_gap = not mapping or bool(cleanup)
    diagnostics = list(record['diagnostic_codes'])
    if missing:
        diagnostics.append('WORKER_TRUST_MARKERS_NOT_OBSERVED')
    if ambiguous:
        diagnostics.append('WORKER_TRUST_MARKERS_AMBIGUOUS')
    if not stable_rows:
        diagnostics.append('WORKER_TRUST_PROCESS_METADATA_UNAVAILABLE')
    observations = {
        'worker_trust_group_map': {'value': mapping if mapping else None, 'collected': bool(mapping)},
        'worker_trust_groups_missing': {'value': missing if stable_rows else None, 'collected': bool(stable_rows)},
        'worker_shared_resources': {'value': None, 'collected': False},
        'worker_isolation_violations': {'value': None, 'collected': False},
        'worker_trust_map_complete': {'value': False, 'collected': True},
        'worker_command_impact_verified': {'value': False, 'collected': True},
    }
    summary = {'profile': PROFILE, 'target_sha256': record['target_sha256'], 'binding_verified': record['binding_verified_before_after'],
               'process_inventory_stable': (record['packet'] or {}).get('pid_inventory_stable') is True,
               'stable_process_rows': len(stable_rows), 'ambiguous_processes': ambiguous, 'diagnostic_codes': diagnostics,
               'worker_role_authenticated': False, 'scope': 'policy-marker matches in the selected owned reader PID namespace',
               'fixture': {'profile': PROFILE, 'process_profile': record['profile'], 'source_sha256': record['program_sha256']}}
    normal = {'attempted': False, 'succeeded': None, 'detail': 'Read-only marker, process identity and UID observations.'}
    challenges = [{'step': step, 'attempted': False} for step in ctx.check.challenge_steps]
    return observations, normal, challenges, summary
