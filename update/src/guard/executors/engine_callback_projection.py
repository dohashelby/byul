"""Cohort validation and callback event projection for REQ-14~17. No I/O.

A violation needs a source-bound native fact. Missing rows stay uncollected and
never become violation=false. Elapsed time alone never decides anything.
"""
from __future__ import annotations

from collections import deque
import hashlib
import hmac

SCHEMA = 'byul-engine-callback-cohort/1'
COMPUTED = 'KVCacheManager.get_computed_blocks'
ROLES = {'REQ-14': {'normal'}, 'REQ-15': {'prime', 'control', 'probe'}, 'REQ-16': {'prime', 'control', 'probe', 'post'},
    'REQ-17': {'prime', 'control-present', 'control-absent', 'present', 'absent'},
    'REQ-18': {'standalone', 'restricted', 'reused'}}
MAX_CASES = 512
# 비동기 스케줄링에서는 출력이 다음 배치 뒤에 도착할 수 있다.
PIPELINE_DEPTH = 2


def tag_digest(secret, tag):
    return hmac.new(secret, b's:' + tag.encode(), hashlib.sha256).hexdigest()


def window(events, secret, barrier):
    """같은 세션의 앞선 시험 행을 버린다. 프로세스마다 이번 경계 요청이 처음 보인 곳부터 쓴다.

    시계를 쓰지 않으므로 노드와 실행기의 시각 차이에 영향받지 않는다. 경계 요청을 보지 못한
    프로세스(이미 끝난 이전 세대 포함)의 행은 이번 시험의 근거가 아니다.
    """
    marker = tag_digest(secret, barrier); kept = []; started = set()
    for event in events:
        process = event.get('process_sha256'); fields = event.get('fields') or {}
        if process not in started and marker in (fields.get('request_sha256'), *(fields.get('native_request_sha256') or ())):
            started.add(process)
        if process in started:
            kept.append(event)
    return kept


def validate_cases(check_id, cases):
    if (type(cases) is not list or not 1 <= len(cases) <= MAX_CASES
            or len({c.get('case_id') for c in cases if type(c) is dict}) != len(cases)):
        raise ValueError('ENGINE_CALLBACK_CASES_INVALID')
    for case in cases:
        if (set(case) - {'case_id', 'role', 'group', 'context', 'prefix', 'prompt', 'cache_salt', 'principal', 'forged_salt',
                'allowed_token_ids', 'max_tokens'}
                or case.get('role') not in ROLES[check_id] or type(case.get('prompt')) is not str
                or not 0 < len(case['prompt']) <= 4096 or type(case['case_id']) is not str
                or 'cache_salt' in case and (type(case['cache_salt']) is not str or not 0 < len(case['cache_salt']) <= 64)
                or type(case.get('max_tokens', 1)) is not int or not 1 <= case.get('max_tokens', 1) <= 8
                or 'allowed_token_ids' in case and (type(case['allowed_token_ids']) is not list or not case['allowed_token_ids'])):
            raise ValueError('ENGINE_CALLBACK_CASES_INVALID')


def computed_rows(events, secret, tags):
    """Keep only rows whose entry stayed qualified through the return."""
    cases = {tag_digest(secret, tag): case_id for case_id, tag in tags.items()}
    rows = {}
    for event in events:
        if (event.get('point') != COMPUTED or event.get('phase') != 'RETURN'
                or event.get('entry_qualified_before') is not True or event.get('entry_qualified_after') is not True):
            continue
        fields = event.get('fields') or {}
        case_id = cases.get(fields.get('request_sha256'))
        if case_id is not None and type(fields.get('num_computed_tokens')) is int:
            rows.setdefault(case_id, []).append({'tokens': fields['num_computed_tokens'],
                'hashes': fields.get('request_block_hashes_sha256'), 'salt': fields.get('request_cache_salt_sha256')})
    return rows


def sequence_rows(events, cases):
    """Gateway가 요청 ID를 다시 발급할 때 직렬 시험의 순서로 사례를 잇는다. 엔진에 닿은 사례만 넘긴다."""
    ordered = {}
    for event in events:
        fields = event.get('fields') or {}
        if (event.get('point') == COMPUTED and event.get('phase') == 'RETURN' and event.get('entry_qualified_before') is True
                and event.get('entry_qualified_after') is True and type(fields.get('num_computed_tokens')) is int):
            ordered.setdefault(fields.get('request_sha256'), []).append({'tokens': fields['num_computed_tokens'],
                'hashes': fields.get('request_block_hashes_sha256'), 'salt': fields.get('request_cache_salt_sha256')})
    # 경계 요청 ID도 Gateway가 바꾸므로, 직렬로 보낸 마지막 사례 수만큼을 이번 시험으로 본다.
    if len(ordered) < len(cases):
        return {}
    return {case['case_id']: rows for case, rows in zip(cases, list(ordered.values())[len(ordered) - len(cases):])}


def project_identity(cases, rows, rejected=()):
    """인증 주체가 salt를 정하는지 본다. 위조한 salt는 거부되거나 무시돼야 한다."""
    salts = {}; forged = {}; omitted = {}
    for case in cases:
        principal = case.get('principal'); found = rows.get(case['case_id'])
        if principal is not None and 'forged_salt' in case and case['case_id'] in rejected:
            forged[principal] = 0; continue
        if principal is None or not found:
            continue
        salts.setdefault(principal, set()).add(found[0]['salt'])
        (forged if 'forged_salt' in case else omitted).setdefault(principal, 0)
    changed = sorted(p for p, values in salts.items() if len(values) > 1)
    shared = sorted({p for p in salts for q in salts if p < q and salts[p] & salts[q]})
    complete = bool(salts) and set(forged) == set(omitted) == set(salts) and len(salts) > 1
    return {'principals': len(salts), 'client_changed_group': changed, 'principals_sharing_salt': shared,
        'bound': complete and not changed and not shared}


def _hit(rows, case):
    found = rows.get(case['case_id'])
    return None if not found else max(r['tokens'] for r in found) > 0


def _first_hash(rows, case):
    for row in rows.get(case['case_id'], ()):
        if row['hashes']:
            return row['hashes'][0]
    return None


def _related(cases, probe, role):
    return next((c for c in cases if c['role'] == role and c.get('prefix') == probe.get('prefix')), None)


def project_cache_group(cases, rows, policy, identity=None):
    groups = policy['isolated_cache_groups']
    wanted = {(a, b) for a in groups for b in groups if a != b}
    pairs = []
    for probe in (c for c in cases if c['role'] == 'probe'):
        prime, control = _related(cases, probe, 'prime'), _related(cases, probe, 'control')
        if prime is None or control is None:
            continue
        salts = {r['salt'] for case in (prime, probe) for r in rows.get(case['case_id'], ())}
        pairs.append({'from': prime.get('group'), 'to': probe.get('group'), 'control_hit': _hit(rows, control),
            'cross_hit': _hit(rows, probe), 'salts_distinct': len(salts) > 1})
    measured = {(p['from'], p['to']) for p in pairs if p['control_hit'] is True and p['cross_hit'] is not None}
    violations = [p for p in pairs if p['cross_hit'] is True and p['control_hit'] is True
        and (p['from'], p['to']) in wanted]
    missing = [] if identity and identity['bound'] else ['authenticated_principal_binding_unverified']
    if not wanted <= measured:
        missing.append('ordered_group_pairs_incomplete')
    changed = bool(identity and (identity['client_changed_group'] or identity['principals_sharing_salt']))
    violated = bool(violations) or changed
    return {'pairs': pairs, 'identity': identity, 'violation': True if violated else (False if wanted <= measured else None),
        'full_scope': not missing and not violated, 'missing': missing}


def project_cache_context(cases, rows, policy):
    required = set(policy['required_cache_contexts'])
    results = []
    for probe in (c for c in cases if c['role'] == 'probe'):
        prime, control = _related(cases, probe, 'prime'), _related(cases, probe, 'control')
        if prime is None or control is None:
            continue
        same_key = _first_hash(rows, prime) is not None and _first_hash(rows, prime) == _first_hash(rows, probe)
        results.append({'context': probe.get('context'), 'control_hit': _hit(rows, control),
            'changed_hit': _hit(rows, probe), 'same_key': same_key})
    measured = {r['context'] for r in results if r['control_hit'] is True and r['changed_hit'] is not None}
    violations = [r for r in results if r['changed_hit'] is True and r['control_hit'] is True and r['same_key']
        and r['context'] in required]
    posts = [_hit(rows, c) for c in cases if c['role'] == 'post']
    complete = required <= measured and bool(posts) and all(v is True for v in posts)
    missing = [] if complete else ['required_cache_context_cases_incomplete']
    return {'contexts': results, 'violation': True if violations else (False if complete else None),
        'full_scope': complete and not violations, 'missing': missing}


def advantage(faster, slower):
    """Probability that a sample from ``faster`` beats one from ``slower``."""
    wins = sum((a < b) + 0.5 * (a == b) for a in faster for b in slower)
    return 100 * wins / (len(faster) * len(slower))


def project_cache_timing(cases, rows, elapsed, policy):
    limit, minimum = policy['cache_timing_max_auc_percent'], policy['cache_timing_min_samples']
    samples = {role: [elapsed[c['case_id']] for c in cases if c['role'] == role and elapsed.get(c['case_id']) is not None]
        for role in ('control-present', 'control-absent', 'present', 'absent')}
    missing = []
    if min(map(len, samples.values())) < minimum:
        return {'samples': {k: len(v) for k, v in samples.items()}, 'violation': None, 'full_scope': False,
            'missing': ['timing_samples_below_policy_minimum']}
    control = advantage(samples['control-present'], samples['control-absent'])
    cross = advantage(samples['present'], samples['absent'])
    hits = [_hit(rows, c) for c in cases if c['role'] == 'present']
    # Without a native cross-group hit a gap is noise; without control power a clean result proves nothing.
    violation = cross > limit and any(h is True for h in hits)
    # 정상 대조는 캐시가 있는 쪽이 빨라야 한다. 반대로 갈리면 순서·워밍업 같은 교란을 먼저 의심한다.
    if control < 100 - limit:
        missing.append('timing_control_direction_reversed')
    elif control <= limit:
        missing.append('timing_control_has_no_power')
    if cross > limit and not violation:
        missing.append('timing_gap_without_native_hit')
    if any(h is None for h in hits):
        missing.append('timing_native_rows_incomplete')
    return {'samples': {k: len(v) for k, v in samples.items()}, 'control_auc_percent': control, 'cross_auc_percent': cross,
        'native_cross_hits': sum(h is True for h in hits), 'violation': True if violation else (None if missing else False),
        'full_scope': not missing and not violation, 'missing': missing}


def project_distributed(events, secret, tags):
    """worker가 받은 배치와 다음에 내보낸 출력의 요청 ID를 프로세스별 순서로 대조한다."""
    wanted = {tag_digest(secret, tag) for tag in tags.values()}
    recent = {}; seen = set(); checked = 0; violations = []
    for event in events:
        if event.get('entry_qualified_before') is not True or event.get('phase') != 'CALL':
            continue
        fields = event.get('fields') or {}; process = event.get('process_sha256')
        if event.get('point') == 'WorkerProc._execute_worker_rpc' and fields.get('rpc_method') == 'execute_model':
            scheduled = fields.get('native_request_sha256')
            if scheduled:
                seen.update(set(scheduled) & wanted)
                recent.setdefault(process, deque(maxlen=PIPELINE_DEPTH)).append(set(scheduled))
        elif event.get('point') == 'WorkerProc.handle_output' and fields.get('output_request_sha256'):
            allowed = set().union(*recent.get(process, ()))
            checked += 1
            extra = set(fields['output_request_sha256']) - allowed
            if extra:
                violations.append({'point': event['point'], 'unscheduled_outputs': len(extra)})
    complete = wanted <= seen and checked > 0
    return {'batches_checked': checked, 'violations': violations,
        'violation': True if violations else (False if complete else None), 'full_scope': False,
        'missing': ['intermediate_tensor_buffer_lineage_unobserved', 'model_generation_consumption_unobserved',
            *([] if complete else ['distributed_batches_incomplete'])]}


def project_slot(cases, outputs, events, secret, tags):
    """A가 쓴 자리를 B가 다시 받았을 때 B의 출력이 단독 실행과 같아야 한다."""
    slots = {}
    for event in events:
        fields = event.get('fields') or {}
        if (event.get('point') == 'InputBatch.add_request' and event.get('phase') == 'RETURN'
                and event.get('entry_qualified_before') is True and event.get('entry_qualified_after') is True):
            slots[fields.get('request_sha256')] = {'slot': fields.get('slot'), 'restricted': fields.get('restricted')}
    slot_of = lambda case: slots.get(tag_digest(secret, tags[case['case_id']]), {})
    alone = [outputs.get(c['case_id']) for c in cases if c['role'] == 'standalone']
    missing = []
    if len(alone) < 2 or None in alone or len(set(alone)) != 1:
        missing.append('standalone_control_not_deterministic')
    rows = []
    for index, case in enumerate(cases):
        if case['role'] != 'reused':
            continue
        previous = next((c for c in reversed(cases[:index]) if c['role'] == 'restricted'), None)
        former = slot_of(previous) if previous else {}
        same = former.get('slot') is not None and former.get('slot') == slot_of(case).get('slot') and former.get('restricted') is True
        rows.append({'slot_reused': same, 'output_matches_standalone': bool(alone) and outputs.get(case['case_id']) == alone[0]})
    if not rows or not all(r['slot_reused'] for r in rows):
        missing.append('slot_reuse_not_observed')
    violations = [r for r in rows if r['slot_reused'] and not r['output_matches_standalone']]
    if 'standalone_control_not_deterministic' in missing:
        violations = []
    return {'rows': rows, 'violation': True if violations else (None if missing else False),
        'full_scope': not missing and not violations, 'missing': missing}
