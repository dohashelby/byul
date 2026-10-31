"""A-owned startup binding shared by closed LIF09 and worker HTTP profiles.

Engine Running and probe Running/Ready are independent conditions. The
generic KubernetesHTTP Ready gate is unchanged; callers cannot toggle it here.
"""
from .kubernetes_binding import verify_owned_pod, project_owned_snapshot
from .kubernetes_http import KubernetesBindingError, REQUEST_BINDING_PROFILE


def read_startup_binding(base):
    """Read selected API identities; retain only the existing transient projection.

    No HTTP, exec, Pod mutation, native process or worker observation is made.
    The old LIF09 ambiguous-engine-readiness result remains None.
    """
    try:
        if (base.profile != REQUEST_BINDING_PROFILE or base.lab is not True
                or base.engine_name != 'vllm' or base.engine_version != '0.31.0'):
            raise ValueError
        namespaces = sorted({base.binding[role]['namespace'] for role in ('engine', 'probe')})
        result = base._call(['get', 'namespaces', *namespaces, '-o', 'json'])
        items = result['items'] if result.get('kind') == 'List' else [result]
        if type(items) is not list or any(type(row) is not dict or row.get('kind') != 'Namespace' for row in items):
            raise ValueError
        actual = {row['metadata']['name']: row for row in items}
        if len(actual) != len(items) or set(actual) != set(namespaces):
            raise ValueError
        pods = {}
        for role in ('engine', 'probe'):
            expected = base.binding[role]
            namespace = actual[expected['namespace']]['metadata']
            if namespace.get('uid') != expected['namespace_uid'] or namespace.get('deletionTimestamp'):
                raise ValueError
            pod = base._call([f"--namespace={expected['namespace']}", 'get', 'pod', expected['pod'], '-o', 'json'])
            if type(pod) is not dict or pod.get('kind') != 'Pod':
                raise ValueError
            # This explicit startup boundary accepts a Running/Unready engine.
            # Probe readiness is checked separately below, as in the old LIF09.
            verify_owned_pod(pod, namespace, expected, base.binding['run_id'], require_ready=False)
            pods[role] = pod
        snapshot = project_owned_snapshot(pods['engine'], actual[base.binding['engine']['namespace']],
            base.binding['engine'], base.binding['run_id'], require_ready=False)
        base.model_snapshots = [base.model_snapshots[0], snapshot] if base.model_snapshots else [snapshot]
        probe = pods['probe']
        probe_states = [row for row in probe['status']['containerStatuses'] if row.get('name') == base.binding['probe']['container']]
        probe_conditions = [row.get('status') for row in probe['status'].get('conditions', []) if row.get('type') == 'Ready']
        if len(probe_states) != 1 or probe_states[0].get('ready') is not True or 'True' not in probe_conditions:
            raise ValueError
        engine = pods['engine']
        states = [row for row in engine['status']['containerStatuses'] if row.get('name') == base.binding['engine']['container']]
        conditions = [row.get('status') for row in engine['status'].get('conditions', []) if row.get('type') == 'Ready']
        readiness = None
        if len(states) == len(conditions) == 1 and conditions[0] in {'True', 'False', 'Unknown'} and type(states[0].get('ready')) is bool:
            readiness = {'pod_ready': True if conditions[0] == 'True' else False if conditions[0] == 'False' else None,
                         'container_ready': states[0]['ready']}
        return {'engine_readiness': readiness, 'probe_ready_condition_count': len(probe_conditions),
                'probe_ready': True, 'actual_worker_prepared_verified': False}
    except Exception:
        raise KubernetesBindingError() from None
