"""Cohort validation and callback event projection for REQ-14~17. No I/O.

A violation needs a source-bound native fact. Missing rows stay uncollected and
never become violation=false. Elapsed time alone never decides anything.
"""
from __future__ import annotations

from collections import deque
import base64
import hashlib
import hmac
import re
import struct
import zlib

SCHEMA = 'byul-engine-callback-cohort/1'
COMPUTED = 'KVCacheManager.get_computed_blocks'
ROLES = {'REQ-14': {'normal'}, 'REQ-15': {'prime', 'control', 'probe'}, 'REQ-16': {'prime', 'control', 'probe', 'post', 'switch'},
    'REQ-17': {'prime', 'control-present', 'control-absent', 'present', 'absent'},
    'REQ-18': {'standalone', 'restricted', 'reused'}, 'REQ-19': {'boundary'}, 'REQ-20': {'replay'},
    'LIF-13': {'before', 'switch', 'after'}, 'REQ-21': {'before', 'switch', 'after'}}
MAX_CASES = 512
# REQ-16 lab contexts change the engine between cases: reload one LoRA in place, or reload weights.
# LIF-13은 소유 weights daemon에 전체 release를 보낸다.
LAB_ACTIONS = {'lora_reload': 'lora', 'weights_reload': 'weights', 'weights_release': 'weights_daemon', 'engine_restart': 'engine_restart'}
LAB_ROOTS = {'lora': '/guard-lora/', 'weights': '/guard-weights/', 'weights_daemon': '/guard-weights-daemon/'}
SWITCH_CHECKS = {'lora_reload': 'REQ-16', 'weights_reload': 'REQ-16', 'weights_release': 'LIF-13', 'engine_restart': 'REQ-21'}
# REQ-21: 같은 Pod 안에서 엔진 컨테이너만 한 번 다시 시작한다. 경로가 없는 동작이라 값이 고정이다.
ENGINE_RESTART_SPEC = {'variants': {'once': 'container'}}
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
_PATH = re.compile(r'(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){2,8}')
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


def _media(value):
    return (type(value) is dict and 'seed' in value and set(value) <= {'seed', 'uuid', 'text_after'}
        and type(value['seed']) is int and 0 <= value['seed'] < 2**31
        and ('text_after' not in value or type(value['text_after']) is str and 0 < len(value['text_after']) <= 4096)
        and ('uuid' not in value or type(value['uuid']) is str and re.fullmatch(r'[A-Za-z0-9-]{1,64}', value['uuid'])))


def _lab_path(value, kind):
    return type(value) is str and _PATH.fullmatch(value) is not None and value.startswith(LAB_ROOTS[kind])


def validate_lab_contexts(spec, cases=None):
    """Closed REQ-16 lab declaration: one LoRA name, its active path, and owned variant paths."""
    try:
        if type(spec) is not dict or not spec or set(spec) - {'lora', 'weights', 'weights_daemon', 'engine_restart'}:
            raise ValueError
        if 'engine_restart' in spec and spec['engine_restart'] != ENGINE_RESTART_SPEC:
            raise ValueError
        lora = spec.get('lora'); weights = spec.get('weights')
        if lora is not None and (type(lora) is not dict or set(lora) != {'name', 'active_path', 'variants'}
                or type(lora['name']) is not str or _NAME.fullmatch(lora['name']) is None
                or not _lab_path(lora['active_path'], 'lora')):
            raise ValueError
        for kind, part in (('lora', lora), ('weights', weights), ('weights_daemon', spec.get('weights_daemon'))):
            if part is None:
                continue
            if kind != 'lora' and (type(part) is not dict or set(part) != {'variants'}):
                raise ValueError
            variants = part['variants']
            if (type(variants) is not dict or not 1 <= len(variants) <= 4
                    or any(type(k) is not str or _NAME.fullmatch(k) is None or not _lab_path(v, kind) for k, v in variants.items())
                    or len(set(variants.values())) != len(variants)
                    or kind == 'lora' and lora['active_path'] in variants.values()):
                raise ValueError
        for case in cases or ():
            if case['role'] == 'switch':
                kind = LAB_ACTIONS[case['action']]
                if spec.get(kind) is None or case['variant'] not in spec[kind]['variants']:
                    raise ValueError
            elif 'adapter' in case and (lora is None or case['adapter'] != lora['name']):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('ENGINE_CALLBACK_LAB_CONTEXTS_INVALID') from None
    return spec


def media_png(seed):
    """A small deterministic RGB image for one media case. Only its seed is kept in evidence."""
    stream = hashlib.shake_256(b'byul-media:' + str(seed).encode()).digest(16 * 16 * 3)
    rows = b''.join(b'\x00' + stream[y * 48:(y + 1) * 48] for y in range(16))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 16, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def message_content(text, media):
    if media is None:
        return text
    part = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(media_png(media['seed'])).decode()}}
    if 'uuid' in media:
        part['uuid'] = media['uuid']
    return [{'type': 'text', 'text': text}, part, *(
        [{'type': 'text', 'text': media['text_after']}] if 'text_after' in media else [])]


def validate_cases(check_id, cases):
    if (type(cases) is not list or not 1 <= len(cases) <= MAX_CASES
            or len({c.get('case_id') for c in cases if type(c) is dict}) != len(cases)):
        raise ValueError('ENGINE_CALLBACK_CASES_INVALID')
    for case in cases:
        if type(case) is dict and case.get('role') == 'switch':
            # A switch sends no prompt. The executor runs it only against an owned lab engine.
            if (set(case) != {'case_id', 'role', 'action', 'variant'}
                    or type(case['case_id']) is not str or case.get('action') not in LAB_ACTIONS
                    or SWITCH_CHECKS[case['action']] != check_id
                    or type(case['variant']) is not str or _NAME.fullmatch(case['variant']) is None):
                raise ValueError('ENGINE_CALLBACK_CASES_INVALID')
            continue
        if (set(case) - {'case_id', 'role', 'group', 'context', 'prefix', 'prompt', 'cache_salt', 'principal', 'forged_salt',
                'allowed_token_ids', 'max_tokens', 'adapter', 'media'}
                or ('adapter' in case or 'media' in case) and check_id != 'REQ-16'
                or 'adapter' in case and (type(case['adapter']) is not str or _NAME.fullmatch(case['adapter']) is None)
                or 'media' in case and not _media(case['media'])
                or case.get('role') not in ROLES[check_id] or type(case.get('prompt')) is not str
                or not 0 < len(case['prompt']) <= 4096 or type(case['case_id']) is not str
                # The executor prepends a 32-character nonce and one space. Keep the existing 4096 text limit.
                or 'media' in case and 'text_after' in case['media'] and len(case['prompt']) + len(case['media']['text_after']) > 4096 - 33
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
    """REQ-14: worker 배치와 출력 귀속, TP rank별 실행·중간 전송·출력 rank를 대조한다.

    단일 프로세스 기록은 기존대로 배치와 다음 출력의 요청 ID만 본다. 여러 rank 기록이 있으면
    같은 요청 배치를 다른 모델·revision으로 실행한 rank, 지정 출력 rank가 아닌 rank의 요청 출력,
    실행 구간 안의 닫힌 통신기·미등록 buffer 전송을 직접 위반으로 본다.
    """
    wanted = {tag_digest(secret, tag) for tag in tags.values()}
    recent = {}; seen = set(); checked = 0; violations = []
    ranks = {}; open_rpc = {}; steps = []; comm = {}
    for event in events:
        fields = event.get('fields') or {}; process = event.get('process_sha256'); point = event.get('point')
        if event.get('entry_qualified_before') is not True:
            continue
        phase = event.get('phase')
        if point == COMM_INIT and phase == 'RETURN' and event.get('entry_qualified_after') is True:
            comm[(process, fields.get('communicator_token'))] = {'buffer': fields.get('buffer_sha256'), 'live': fields.get('live')}
            continue
        if point == 'CustomAllreduce.close' and phase == 'RETURN' and (process, fields.get('communicator_token')) in comm:
            comm[(process, fields.get('communicator_token'))]['live'] = False
            continue
        if point == 'CustomAllreduce.all_reduce' and process in open_rpc:
            step = open_rpc[process]; generation = comm.get((process, fields.get('communicator_token')))
            if phase == 'CALL':
                if generation is None:
                    step['transfer_unbound'] = True
                elif generation['live'] is not True or fields.get('live') is not True:
                    violations.append({'point': point, 'reason': 'closed_communicator_transfer'})
                elif fields.get('buffer_sha256') != generation['buffer']:
                    violations.append({'point': point, 'reason': 'unregistered_buffer_transfer'})
                else:
                    step['transfers'] += 1
            elif phase == 'RETURN':
                step['fences'].append(fields.get('completion_fence'))
            continue
        if point == 'WorkerProc._execute_worker_rpc' and fields.get('rpc_method') == 'execute_model':
            if phase == 'CALL':
                scheduled = fields.get('native_request_sha256')
                if scheduled:
                    seen.update(set(scheduled) & wanted)
                    recent.setdefault(process, deque(maxlen=PIPELINE_DEPTH)).append(set(scheduled))
                    if fields.get('rank') is not None:
                        ranks.setdefault(process, fields['rank'])
                    open_rpc[process] = {'process': process, 'rank': fields.get('rank'), 'requests': frozenset(scheduled),
                        'model': (fields.get('configured_model_sha256'), fields.get('configured_revision_sha256')),
                        'output_rank': fields.get('selected_output_rank'), 'transfers': 0, 'fences': [], 'transfer_unbound': False}
            elif process in open_rpc:
                steps.append(open_rpc.pop(process))
            continue
        if point == 'WorkerProc.handle_output' and phase == 'CALL' and fields.get('output_request_sha256'):
            allowed = set().union(*recent.get(process, ()))
            checked += 1
            extra = set(fields['output_request_sha256']) - allowed
            if extra:
                violations.append({'point': point, 'unscheduled_outputs': len(extra)})
            selected = {s['output_rank'] for s in steps + list(open_rpc.values()) if s['process'] == process and s['output_rank'] is not None}
            if ranks.get(process) is not None and selected and ranks[process] not in selected and set(fields['output_request_sha256']) & wanted:
                violations.append({'point': point, 'reason': 'unselected_rank_output'})
    complete = wanted <= seen and checked > 0
    missing = []
    if not complete:
        missing.append('distributed_batches_incomplete')
    tp = len(set(ranks.values())) >= 2
    if not tp:
        missing += ['intermediate_tensor_buffer_lineage_unobserved', 'model_generation_consumption_unobserved']
    else:
        by_batch = {}
        for step in steps:
            if step['requests'] & wanted:
                by_batch.setdefault(step['requests'], []).append(step)
        bound = [group for group in by_batch.values() if len({s['rank'] for s in group}) >= 2]
        for group in bound:
            if len({s['model'] for s in group}) != 1 or None in {m for s in group for m in s['model']}:
                violations.append({'reason': 'rank_model_generation_mismatch'})
        if not bound or {r for group in bound for r in group[0]['requests'] if r in wanted} < wanted:
            missing.append('rank_batch_binding_incomplete')
        transfers = [s for group in bound for s in group]
        if not transfers or any(s['transfers'] == 0 or s['transfer_unbound'] for s in transfers):
            missing.append('intermediate_tensor_buffer_lineage_unobserved')
        if not transfers or any(not s['fences'] or any(f is not True for f in s['fences']) for s in transfers):
            missing.append('intermediate_buffer_completion_unobserved')
        missing.append('model_generation_consumption_unobserved')  # 설정 지문은 가중치 실제 소비의 증명이 아니다
    return {'batches_checked': checked, 'ranks': len(set(ranks.values())), 'violations': violations,
        'violation': True if violations else (False if complete else None), 'full_scope': False, 'missing': missing}


def project_slot(cases, outputs, events, secret, tags):
    """A가 쓴 자리를 B가 다시 받았을 때 B의 출력이 단독 실행과 같아야 한다."""
    slots = {}; v2_slot = {}; v2_restricted = {}; sampled = {}
    for event in events:
        fields = event.get('fields') or {}
        point, phase = event.get('point'), event.get('phase')
        if event.get('entry_qualified_before') is not True:
            continue
        if point == 'Sampler.__call__' and phase == 'CALL':
            # 진입 시점 상태다. sampler가 받은 그대로 남는다.
            for item in fields.get('sampled') or ():
                if type(item) is dict and item.get('request_sha256'):
                    sampled.setdefault(item['request_sha256'], []).append(item)
            continue
        if phase != 'RETURN' or event.get('entry_qualified_after') is not True:
            continue
        if point == 'InputBatch.add_request':
            slots[fields.get('request_sha256')] = {'slot': fields.get('slot'), 'restricted': fields.get('restricted')}
        elif point == 'RequestState.add_request':
            v2_slot[fields.get('request_sha256')] = fields.get('slot')
        elif point == 'Sampler.add_request':
            v2_restricted[(fields.get('request_sha256'), fields.get('slot'))] = fields.get('restricted')
    v2 = bool(v2_slot)
    for request, number in v2_slot.items():
        slots.setdefault(request, {'slot': number, 'restricted': v2_restricted.get((request, number))})
    slot_of = lambda case: slots.get(tag_digest(secret, tags[case['case_id']]), {})
    stale = lambda item: (type(item.get('allowed_token_count')) is int and item['allowed_token_count'] > 0) or item.get('logit_bias_in_use') is True
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
        row = {'slot_reused': same, 'output_matches_standalone': bool(alone) and outputs.get(case['case_id']) == alone[0]}
        if v2:
            states = [i for i in sampled.get(tag_digest(secret, tags[case['case_id']]), ()) if i.get('slot') == slot_of(case).get('slot')]
            row.update(sampler_state_observed=bool(states), stale_restriction_at_sampling=any(stale(i) for i in states))
        rows.append(row)
    if not rows or not all(r['slot_reused'] for r in rows):
        missing.append('slot_reuse_not_observed')
    if v2 and not all(r.get('sampler_state_observed') for r in rows):
        missing.append('sampler_state_not_observed')
    violations = [r for r in rows if r['slot_reused'] and not r['output_matches_standalone']]
    if 'standalone_control_not_deterministic' in missing:
        violations = []
    # sampler가 받은 상태에 앞 요청의 제한이 남아 있으면 출력 비교와 무관한 직접 위반이다.
    violations += [r for r in rows if r['slot_reused'] and r.get('stale_restriction_at_sampling') and r not in violations]
    return {'rows': rows, 'runner': 'v2' if v2 else 'v1', 'violation': True if violations else (None if missing else False),
        'full_scope': not missing and not violations, 'missing': missing}


KERNEL_POINTS = ('prepare_prefill_inputs', 'prepare_pos_seq_lens')
KERNEL_BLOCK = 1024


def project_kernel_bounds(cases, events, secret, tags):
    """REQ-19: 실제 입력 준비 kernel 호출의 범위·초기화·완료·독립 대조.

    자리는 같은 프로세스의 V2 RequestState 행으로 요청에 잇는다. 범위 밖 주소나
    초기화되지 않은 padding은 어느 요청의 호출이든 직접 위반이다. 출력 불일치는
    메모리 결함의 증거로 보지 않고 미수집으로 남긴다.
    """
    wanted = {tag_digest(secret, tag): case_id for case_id, tag in tags.items()}
    holder = {}; calls = {}; order = []
    for event in events:
        point, phase = event.get('point'), event.get('phase'); fields = event.get('fields') or {}
        process = event.get('process_sha256')
        if point in ('RequestState.add_request', 'RequestState.remove_request'):
            if phase == 'RETURN' and event.get('entry_qualified_before') is True and event.get('entry_qualified_after') is True:
                key = (process, fields.get('slot'))
                if point == 'RequestState.add_request':
                    holder[key] = fields.get('request_sha256')
                else:
                    holder.pop(key, None)
            continue
        if point not in KERNEL_POINTS:
            continue
        call = (event.get('process_sha256'), event.get('call_sha256'))  # 호출 번호는 프로세스마다 따로 센다
        if phase == 'CALL':
            plan = fields.get('plan')
            if event.get('entry_qualified_before') is True and type(plan) is dict:
                bound = [dict(item, case_id=wanted.get(holder.get((process, item.get('slot'))))) for item in plan.get('items') or ()]
                calls[call] = {'point': point, 'plan': plan, 'items': bound, 'terminal': None}
                order.append(call)
        elif call in calls and event.get('entry_qualified_after') is True:
            calls[call]['terminal'] = {'phase': phase, **fields}
    violations = []; missing = set(); covered = {c['case_id']: set() for c in cases}; lengths = set()
    for call in order:
        entry = calls[call]; terminal = entry['terminal']; point = entry['point']
        for item in entry['items']:
            ranges = [item.get(k) for k in ('write_range_ok', 'read_range_ok', 'lookahead_write_ok', 'lookahead_read_ok') if k in item]
            if item.get('slot_in_range') is False or False in ranges:
                violations.append({'point': point, 'batch': item.get('batch'), 'reason': 'access_range'})
        if point == 'prepare_pos_seq_lens' and entry['plan'].get('padding_range_ok') is False:
            violations.append({'point': point, 'reason': 'padding_range'})
        if terminal is None or terminal['phase'] != 'RETURN':
            missing.add('kernel_terminal_unobserved'); continue
        if terminal.get('gpu_completion') is not True:
            missing.add('gpu_completion_unobserved'); continue
        if point == 'prepare_pos_seq_lens' and terminal.get('padding_initialized') is False:
            violations.append({'point': point, 'reason': 'padding_not_initialized'})
        matches = {o.get('batch'): o.get('reference_matches') for o in terminal.get('outputs') or ()}
        for item in entry['items']:
            if point == 'prepare_prefill_inputs' and not item.get('prefill'):
                continue
            result = matches.get(item.get('batch'))
            if result is False:
                missing.add('reference_output_mismatch')
            if item.get('case_id') in covered and result is True and (point != 'prepare_pos_seq_lens' or terminal.get('padding_initialized') is True):
                covered[item['case_id']].add(point)
                if point == 'prepare_prefill_inputs' and type(item.get('query_len')) is int:
                    lengths.add(item['query_len'])
    if any(points != set(KERNEL_POINTS) for points in covered.values()):
        missing.add('case_kernel_calls_unobserved')
    if not (any(n <= KERNEL_BLOCK for n in lengths) and any(n > KERNEL_BLOCK for n in lengths)):
        missing.add('block_boundary_not_crossed')
    return {'calls': len(order), 'covered_cases': sorted(c for c, p in covered.items() if p == set(KERNEL_POINTS)),
            'query_lengths': sorted(lengths), 'violations': violations,
            'violation': True if violations else (None if missing else False),
            'full_scope': not missing and not violations, 'missing': sorted(missing),
            'hardware_memory_checker': False}


REPLAY = 'ModelCudaGraphManager.run_fullgraph'
SAMPLE = 'GPUModelRunner.sample'
_SHA = re.compile('[0-9a-f]{64}')


def validate_reference_outputs(cases, references):
    """REQ-20 독립 대조: graph를 끈 엔진 세대에서 같은 사례의 답변 해시. 없으면 None."""
    if references is None:
        return None
    ids = {c['case_id'] for c in cases if c.get('role') == 'replay'}
    if (type(references) is not dict or not references or set(references) - ids
            or any(type(v) is not str or _SHA.fullmatch(v) is None for v in references.values())):
        raise ValueError('ENGINE_CALLBACK_REFERENCE_OUTPUTS_INVALID')
    return references


def project_graph_lifetime(cases, outputs, events, secret, tags, references=None):
    """REQ-20: FULL graph replay와 그 출력의 소비를 요청 단위로 잇는다.

    앞 replay 출력이 소비되기 전에 새 replay가 시작되거나, 소비 시점에 출력 값이 바뀌었거나,
    replay 직전 위치 입력이 이번 step 값이 아니면 직접 위반이다. HTTP 답변이 graph를 끈
    대조 세대와 다른 것만으로는 위반으로 보지 않는다.
    """
    wanted = {tag_digest(secret, tag): case_id for case_id, tag in tags.items()}
    replays = {}; samples = []
    for event in events:
        point = event.get('point'); fields = event.get('fields') or {}
        if point not in (REPLAY, SAMPLE) or event.get('entry_qualified_before') is not True:
            continue
        call = (event.get('process_sha256'), event.get('call_sha256'))  # 호출 번호는 프로세스마다 따로 센다
        if point == REPLAY:
            if event.get('phase') == 'CALL':
                replays[call] = {'entry': fields, 'terminal': None}
            elif call in replays and event.get('entry_qualified_after') is True:
                replays[call]['terminal'] = {'phase': event.get('phase'), **fields}
        elif event.get('phase') == 'CALL':
            samples.append(fields)
    violations = []; missing = set()
    for call, replay in replays.items():
        entry = replay['entry']
        if entry.get('pending_unconsumed') is True:
            violations.append({'reason': 'replay_before_output_consumed'})
        if entry.get('input_refresh') is False:
            violations.append({'reason': 'stale_graph_input'})
        if entry.get('input_refresh') is None:
            missing.add('graph_input_refresh_unobserved')
        if entry.get('captured') is not True or (entry.get('desc') or {}).get('cg_mode') != 'FULL':
            missing.add('graph_key_unverified')
        terminal = replay['terminal']
        if terminal is None or terminal['phase'] != 'RETURN' or terminal.get('gpu_completion') is not True:
            missing.add('graph_completion_unobserved')
    covered = set()
    for sample in samples:
        if sample.get('graph_output') is not True:
            continue
        if sample.get('output_intact') is False:
            violations.append({'reason': 'graph_output_changed_before_consumption'})
        if sample.get('output_intact') is True:
            covered |= {wanted[r] for r in sample.get('native_request_sha256') or () if r in wanted}
    replay_cases = [c['case_id'] for c in cases if c.get('role') == 'replay']
    if not replays:
        missing.add('graph_replay_unobserved')
    if set(replay_cases) - covered:
        missing.add('case_graph_consumption_unobserved')
    if references is None:
        missing.add('independent_output_control_unavailable')
    else:
        compared = {c: outputs.get(c) == references[c] for c in references}
        if set(replay_cases) - set(references):
            missing.add('independent_output_control_unavailable')
        if not all(compared.values()):
            missing.add('reference_output_mismatch')
    return {'replays': len(replays), 'covered_cases': sorted(covered), 'violations': violations,
            'violation': True if violations else (None if missing else False),
            'full_scope': not missing and not violations, 'missing': sorted(missing)}


DAEMON_STATE = 'WeightCacheDaemon._handle_get_state'
DAEMON_RELEASE = 'WeightCacheDaemon._handle_release'


def project_weights_lifetime(cases, outputs, events, switches):
    """LIF-13: release 앞뒤 daemon 공급·release 주체·release 뒤 취득·fallback·기존 참조 효과.

    release된 세대를 다시 공급하거나, release 뒤에도 가중치가 남아 공급되거나, fallback이 꺼진
    loader가 디스크 fallback을 쓰면 직접 위반이다. release 주체의 허용 여부는 기업 정책이 없어
    판단하지 않으므로 범위는 끝나지 않는다. release 뒤 답변이 달라진 것만으로는 위반이 아니다.
    """
    rows = {}; order = []
    for event in events:
        point = event.get('point')
        if point not in (DAEMON_STATE, DAEMON_RELEASE, 'IpcModelLoader._request_state', 'IpcModelLoader._fallback_load') \
                or event.get('entry_qualified_before') is not True:
            continue
        call = (event.get('process_sha256'), event.get('call_sha256'))  # 호출 번호는 프로세스마다 따로 센다
        if event.get('phase') == 'CALL':
            rows[call] = {'point': point, 'process': event.get('process_sha256'), 'entry': event.get('fields') or {}, 'terminal': None}
            order.append(call)
        elif call in rows and event.get('entry_qualified_after') is True:
            rows[call]['terminal'] = {'phase': event.get('phase'), **(event.get('fields') or {})}
    released = None; consumers = set(); actors = []; violations = []; missing = set(); after = {'refused': 0, 'fallback': 0}
    for call in order:
        row = rows[call]; entry = row['entry']; terminal = row['terminal'] or {}
        if row['point'] == DAEMON_STATE:
            served = terminal.get('phase') == 'RETURN' and entry.get('weights_present') is True and entry.get('config_matches') is True
            if released is None and served and entry.get('peer_pid_sha256'):
                consumers.add(entry['peer_pid_sha256'])
            if released is not None:
                if served:
                    violations.append({'reason': 'released_generation_served' if entry.get('weights_generation_token') == released
                                       else 'weights_served_after_release'})
                elif terminal.get('phase') == 'RETURN' and entry.get('weights_present') is False:
                    after['refused'] += 1
        elif row['point'] == DAEMON_RELEASE:
            if terminal.get('phase') == 'RETURN' and terminal.get('weights_present') is False:
                released = entry.get('weights_generation_token') or 'unknown'
                actors.append({'peer_uid_sha256': entry.get('peer_uid_sha256'), 'peer_pid_sha256': entry.get('peer_pid_sha256')})
            else:
                missing.add('release_not_completed')
        elif row['point'] == 'IpcModelLoader._fallback_load':
            if entry.get('fallback_enabled') is False:
                violations.append({'reason': 'fallback_used_while_disabled'})
            elif released is not None and terminal.get('phase') == 'RETURN' and terminal.get('model_token'):
                after['fallback'] += 1
    if len(consumers) < 2:
        missing.add('two_consumer_acquisition_unobserved')
    if not actors:
        missing.add('release_unobserved')
    elif any(a['peer_uid_sha256'] is None for a in actors):
        missing.add('release_actor_unobserved')
    missing.add('release_authorization_policy_unavailable')
    if not after['refused']:
        missing.add('post_release_acquisition_unobserved')
    if not after['fallback']:
        missing.add('fallback_consumption_unobserved')
    if not any(s.get('action') == 'weights_release' and s.get('completed') for s in switches or ()):
        missing.add('lab_release_action_unobserved')
    before = [outputs.get(c['case_id']) for c in cases if c['role'] == 'before']
    later = [outputs.get(c['case_id']) for c in cases if c['role'] == 'after']
    if not before or not later or None in before + later or len(set(before)) != 1:
        missing.add('existing_reference_control_unavailable')
    elif set(later) != set(before):
        missing.add('post_release_output_changed')
    # 주체 허용 여부는 정책이 없어 범위만 막는다. 관측한 사실의 위반 여부와는 따로 둔다.
    core = missing - {'release_authorization_policy_unavailable'}
    return {'consumers': len(consumers), 'release_actors': actors, 'post_release': after, 'violations': violations,
            'violation': True if violations else (None if core else False),
            'full_scope': not missing and not violations, 'missing': sorted(missing)}


COMM_INIT = 'CustomAllreduce.__init__'
COMM_POINTS = (COMM_INIT, 'CustomAllreduce.register_graph_buffers', 'CustomAllreduce.all_reduce', 'CustomAllreduce.close')


def project_comm_buffer(cases, outputs, events):
    """REQ-21: 통신기 세대별 등록·사용·완료·해제와 재기동 뒤 재등록·정상 회복.

    닫힌 통신기의 사용, 그 세대에 등록되지 않은 buffer의 사용, 닫힌 통신기의 graph buffer
    등록은 직접 위반이다. 주소가 세대 사이에 다시 나오는 것은 정상 재사용일 수 있어 보지 않는다.
    재기동 뒤 답변이 달라진 것만으로는 위반이 아니다.
    """
    calls = {}; order = []
    for event in events:
        if event.get('point') not in COMM_POINTS or event.get('entry_qualified_before') is not True:
            continue
        call = (event.get('process_sha256'), event.get('call_sha256'))  # 호출 번호는 프로세스마다 따로 센다
        if event.get('phase') == 'CALL':
            calls[call] = {'point': event['point'], 'process': event.get('process_sha256'), 'entry': event.get('fields') or {}, 'terminal': None}
            order.append(call)
        elif call in calls and event.get('entry_qualified_after') is True:
            calls[call]['terminal'] = {'phase': event.get('phase'), **(event.get('fields') or {})}
    generations = {}; violations = []; missing = set(); fences = []
    for call in order:
        row = calls[call]; entry = row['entry']; terminal = row['terminal'] or {}
        if row['point'] == COMM_INIT:
            if terminal.get('phase') == 'RETURN' and terminal.get('communicator_token'):
                # 객체 token은 프로세스마다 따로 세므로 프로세스와 함께 세대를 구분한다.
                generations[(row['process'], terminal['communicator_token'])] = {'process': row['process'], 'rank': terminal.get('rank'),
                    'world_size': terminal.get('world_size'), 'buffer': terminal.get('buffer_sha256'),
                    'live': terminal.get('live'), 'used': False, 'closed': False, 'order': len(generations)}
            continue
        generation = generations.get((row['process'], entry.get('communicator_token')))
        if generation is None:
            missing.add('communicator_registration_unobserved'); continue
        if row['point'] == 'CustomAllreduce.close':
            if terminal.get('phase') == 'RETURN' and terminal.get('live') is False:
                generation['closed'] = True
            elif generation['live']:
                missing.add('communicator_release_unverified')
            continue
        if generation['closed'] or entry.get('live') is False and generation['live']:
            violations.append({'reason': 'closed_communicator_used' if row['point'].endswith('all_reduce') else 'closed_communicator_registered'})
            continue
        if row['point'] == 'CustomAllreduce.all_reduce' and generation['live']:
            if entry.get('buffer_sha256') != generation['buffer']:
                violations.append({'reason': 'unregistered_buffer_used'})
            else:
                generation['used'] = True
            fences.append(terminal.get('completion_fence'))
    live = [g for g in generations.values() if g['live']]
    if not live or not any((g['world_size'] or 0) >= 2 for g in live):
        missing.add('tensor_parallel_communicator_inactive')
    if not any(g['used'] for g in live):
        missing.add('buffer_consumption_unobserved')
    if not fences or any(f is not True for f in fences):
        missing.add('completion_fence_unobserved')
    closed = [g for g in live if g['closed']]
    if not closed:
        missing.add('release_generation_unobserved')
    # 재기동: 닫힌 세대 뒤에 다른 프로세스의 새 세대가 등록·사용돼야 한다.
    if not any(g['used'] and g['process'] not in {c['process'] for c in closed} and g['order'] > min(c['order'] for c in closed)
               for g in live) if closed else True:
        missing.add('restart_and_reregistration_unobserved')
    before = [outputs.get(c['case_id']) for c in cases if c['role'] == 'before']
    after = [outputs.get(c['case_id']) for c in cases if c['role'] == 'after']
    if not before or not after or None in before + after:
        missing.add('normal_recovered_control_unavailable')
    elif set(after) != set(before):
        missing.add('post_restart_output_changed')
    return {'generations': len(generations), 'violations': violations,
            'violation': True if violations else (None if missing else False),
            'full_scope': not missing and not violations, 'missing': sorted(missing)}
