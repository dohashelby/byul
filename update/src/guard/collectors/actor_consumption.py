"""Group/actor scoped consumption. /1 and /2 remain readable as their own scope.

Topology is an independently observed bootstrap manifest, never a rank count
inferred from surviving rows. Unsupported/missing manifests retain facts/gaps.
"""
from copy import deepcopy
from pathlib import Path
from guard.models import canonical_json, sha256_hex
from . import scoped_consumption as previous
from .producer_approvals import qualified
from .producer_release import release_digest

SCHEMA = 'guard-gpu-common-input/3'
STAGES = ('runner_received', 'sampler_applied')
IDENTITY = ('pod_uid', 'container_id', 'restart_count', 'worker_pid', 'start_ticks', 'role')


def producer():
    old = previous.producer()
    return {'name': 'guard-actor-consumption', 'version': '3',
        'source_sha256': release_digest(SCHEMA + ':' + old['source_sha256'], [Path(__file__)])}


def topology_layout(topology):
    """Generation-free shape for a receipt, retaining groups and stage coverage."""
    if type(topology) is not dict or set(topology) != {'schema', 'bootstrap_sha256', 'bootstrap_binding', 'groups', 'actors'} or topology['schema'] != 'guard-observed-topology/1' or not previous._sha(topology['bootstrap_sha256']):
        raise ValueError('TOPOLOGY_UNOBSERVED')
    if sha256_hex(canonical_json({k: topology[k] for k in ('bootstrap_binding', 'groups', 'actors')})) != topology['bootstrap_sha256']:
        raise ValueError('TOPOLOGY_BOOTSTRAP_DIGEST_MISMATCH')
    groups, actors = topology['groups'], topology['actors']
    if type(groups) is not list or not groups or type(actors) is not list or not actors or len(actors) > 128:
        raise ValueError('TOPOLOGY_INVALID')
    group_ids, actor_ids, identities, shape = set(), set(), set(), []
    for group in groups:
        if type(group) is not dict or set(group) != {'id', 'role', 'ranks'} or not previous.legacy._check(group['id'], 'id') or not previous.legacy._check(group['role'], 'id') or group['id'] in group_ids or type(group['ranks']) is not list or not group['ranks'] or any(not previous._uint(r) for r in group['ranks']) or len(set(group['ranks'])) != len(group['ranks']):
            raise ValueError('TOPOLOGY_INVALID')
        group_ids.add(group['id'])
    counts = {g['id']: set() for g in groups}
    for actor in actors:
        if type(actor) is not dict or set(actor) != {'id', 'binding', 'group', 'rank', 'stages'} or not previous.legacy._check(actor['id'], 'id') or actor['id'] in actor_ids or actor['group'] not in counts or not previous._uint(actor['rank']) or type(actor['stages']) is not list or not actor['stages'] or not set(actor['stages']) <= set(STAGES) or len(set(actor['stages'])) != len(actor['stages']):
            raise ValueError('TOPOLOGY_INVALID')
        binding = actor['binding']
        if type(binding) is not dict or set(binding) != set(IDENTITY) or any(binding[k] is None for k in IDENTITY):
            raise ValueError('TOPOLOGY_PROCESS_UNOBSERVED')
        identity = tuple(binding[k] for k in IDENTITY)
        if identity in identities:
            raise ValueError('TOPOLOGY_PROCESS_AMBIGUOUS')
        identities.add(identity); actor_ids.add(actor['id']); counts[actor['group']].add(actor['rank'])
        shape.append({'group': actor['group'], 'rank': actor['rank'], 'stages': sorted(actor['stages'])})
    if len({(a['group'], a['rank']) for a in actors}) != len(actors):
        raise ValueError('TOPOLOGY_ACTOR_PLACEMENT_AMBIGUOUS')
    for group in groups:
        if counts[group['id']] != set(group['ranks']): raise ValueError('TOPOLOGY_RANK_COVERAGE_INVALID')
    # Group labels, like PIDs, may be regenerated at every deployment. Retain
    # multiplicity and stage/rank structure without qualifying a deployment ID.
    layouts = [{'role': group['role'], 'ranks': sorted(group['ranks']),
        'placements': sorted(({'rank': a['rank'], 'stages': a['stages']} for a in shape if a['group'] == group['id']),
            key=lambda a: (a['rank'], a['stages']))} for group in groups]
    return {'groups': sorted(layouts, key=canonical_json)}


def validate(value, **scope):
    if type(value) is not dict or value.get('schema') != SCHEMA:
        raise previous.legacy.GpuInputError('GPU_INPUT_V3_ENVELOPE_INVALID')
    old = {k: deepcopy(v) for k, v in value.items() if k not in {'topology', 'request_actors'}}
    old['schema'] = previous.SCHEMA
    # The /2 validator still checks every row, source binding, field and
    # independent violation. Its receipts cannot qualify a /3 publication.
    result = previous.validate(old, **scope)
    gaps = set(result['gaps']) - {'GPU_INPUT_PRODUCER_UNSUPPORTED', 'GPU_INPUT_QUALIFICATION_SCOPE_UNREVIEWED'}
    processes = {}
    for row in value['events']:
        identity = tuple(value['bindings'][row['binding']][k] for k in IDENTITY)
        source = row['source_call']['process_sha256']
        if source in processes and processes[source] != identity:
            gaps.add('GPU_INPUT_SOURCE_PROCESS_AMBIGUOUS')
        processes[source] = identity
    try:
        topology = value['topology']; layout = topology_layout(topology)
        shape_sha = sha256_hex(canonical_json(layout))
        actors = {tuple(a['binding'][k] for k in IDENTITY): a for a in topology['actors']}
        gaps.discard('GPU_INPUT_EXPECTED_SCOPE_UNOBSERVED')
        for stage in STAGES: gaps.discard('GPU_INPUT_STAGE_RANK_COVERAGE_MISMATCH:' + stage)
        assignments = value.get('request_actors')
        if assignments is not None and (type(assignments) is not dict or topology['bootstrap_binding'].get('assignments_sha256') != sha256_hex(canonical_json(assignments))):
            raise ValueError('TOPOLOGY_ASSIGNMENTS_UNBOUND')
        if assignments is not None:
            for request, stages in assignments.items():
                if not previous._sha(request) or type(stages) is not dict or set(stages) != set(STAGES):
                    raise ValueError('TOPOLOGY_ASSIGNMENTS_INVALID')
                for ids in stages.values():
                    if type(ids) is not list or not ids or any(type(a) is not str for a in ids) or len(set(ids)) != len(ids):
                        raise ValueError('TOPOLOGY_ASSIGNMENTS_INVALID')
        if assignments is None and len(topology['groups']) != 1:
            gaps.add('GPU_INPUT_REQUEST_ASSIGNMENT_UNOBSERVED')
        for request in {r['request_sha256'] for r in value['events'] if r['event'] == 'api_validated'}:
            for stage in STAGES:
                capable = {a['id'] for a in actors.values() if stage in a['stages']}
                expected = capable if assignments is None else set(assignments.get(request, {}).get(stage, []))
                if not expected <= capable: gaps.add('GPU_INPUT_REQUEST_ASSIGNMENT_INVALID')
                observed = set()
                for row in value['events']:
                    if row['request_sha256'] != request or row['event'] != stage: continue
                    binding = value['bindings'][row['binding']]
                    actor = actors.get(tuple(binding[k] for k in IDENTITY))
                    if actor is None or binding['rank'] != actor['rank']:
                        gaps.add('GPU_INPUT_ACTOR_UNBOUND'); continue
                    observed.add(actor['id'])
                if not expected or observed != expected:
                    gaps.add('GPU_INPUT_ACTOR_COVERAGE_MISMATCH:' + stage)
        receipt_scope = {**value['qualification_scope'], 'topology_layout_sha256': shape_sha}
        binding = topology['bootstrap_binding']
        if (binding.get('schema') != 'guard-topology-bootstrap/1' or binding.get('source_ended') is not True
                or any(binding.get(k) != value[k] for k in ('environment_ref', 'target_sha256', 'session'))
                or not qualified(binding.get('producer'), 'guard-topology-bootstrap/1', receipt_scope)):
            gaps.add('GPU_INPUT_BOOTSTRAP_SOURCE_UNQUALIFIED')
    except (ValueError, KeyError, TypeError):
        gaps.add('GPU_INPUT_TOPOLOGY_UNOBSERVED'); receipt_scope = None
    supported = receipt_scope is not None and qualified(value['producer'], SCHEMA, receipt_scope)
    if not supported: gaps.add('GPU_INPUT_QUALIFICATION_SCOPE_UNREVIEWED')
    return {**result, 'supported': supported, 'runtime_qualified': supported,
        'complete': supported and not gaps, 'gaps': sorted(gaps), 'coverage_scope': 'observed actor groups and process generations'}


def record(processes, *, topology=None, qualification_scope=None, request_actors=None, **kwargs):
    value, _ = previous.record(processes, qualification_scope=qualification_scope, **kwargs)
    value.update(schema=SCHEMA, producer=producer(), topology=deepcopy(topology), request_actors=deepcopy(request_actors))
    scope = {k: kwargs[k] for k in ('environment_ref', 'target_sha256', 'prepared_sha256', 'session')}
    return value, validate(value, **scope)
