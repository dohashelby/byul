"""REQ-17 GPU 재시험의 사례 목록을 만든다. 설계 근거는 담당 A 기록 폴더(docs/gpu-a)의 REQ-17 설계 문서.

출력은 EXAMPLE 입력이다. manifest의 session·target_sha256·environment_ref는 실행할 때 붙인다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import re

from guard.executors import engine_callback_projection as projection
from guard.models import canonical_json, sha256_hex

SCHEMA = 'byul-gpu-a-req17-cohort/1'
GROUP_SCHEMA = 'byul-gpu-a-req17-cohort-group/1'
OWNER_SALT = 'owner'
MAX_PROMPT_CHARS = 4096  # validate_cases 상한
MAX_BLOCKS = 256  # vllm_cache_callback.MAX_BLOCKS를 넘으면 블록 해시가 비고 공백이 생긴다
TEMPLATE_TOKENS = 64  # 채팅 템플릿과 실행 nonce 여유


def _digits(rng, size, used):
    # Qwen2.5 토크나이저는 숫자를 한 자리씩 끊으므로 길이가 같으면 토큰 수도 같다.
    while True:
        value = ''.join(rng.choice('0123456789') for _ in range(size))
        if value not in used:
            used.add(value)
            return value


def build(seed, quartets=24, prompt_chars=1500, warmups=2, block_size=16):
    if not 0 < prompt_chars <= MAX_PROMPT_CHARS:
        raise ValueError('REQ17_COHORT_PROMPT_LENGTH_UNSUPPORTED')
    if -(-(prompt_chars + TEMPLATE_TOKENS) // block_size) > MAX_BLOCKS:
        raise ValueError('REQ17_COHORT_BLOCKS_OVER_LIMIT')
    rng = random.Random(seed)
    used = set()
    cases = []
    # 첫 요청의 초기화 비용이 표본에 섞이지 않게 미적중·적중 경로를 한 번씩 먼저 지난다.
    for k in range(warmups):
        prompt = _digits(rng, prompt_chars, used)
        cases += [{'case_id': f'w{k}-miss', 'role': 'prime', 'prompt': prompt, 'cache_salt': f'warmup-{k}'},
            {'case_id': f'w{k}-hit', 'role': 'prime', 'prompt': prompt, 'cache_salt': f'warmup-{k}'}]
    for n in range(quartets):
        secret = _digits(rng, prompt_chars, used)
        # 공격 쪽 salt를 사례마다 달리해야 자기 요청끼리의 템플릿 적중이 교차 적중으로 세어지지 않는다.
        block = [{'case_id': f'cp-{n}', 'role': 'control-present', 'prompt': secret, 'cache_salt': OWNER_SALT},
            {'case_id': f'ca-{n}', 'role': 'control-absent', 'prompt': _digits(rng, prompt_chars, used),
                'cache_salt': f'owner-absent-{n}'},
            {'case_id': f'p-{n}', 'role': 'present', 'prompt': secret, 'cache_salt': f'other-present-{n}'},
            {'case_id': f'a-{n}', 'role': 'absent', 'prompt': _digits(rng, prompt_chars, used),
                'cache_salt': f'other-absent-{n}'}]
        rng.shuffle(block)
        cases += [{'case_id': f'prime-{n}', 'role': 'prime', 'prompt': secret, 'cache_salt': OWNER_SALT}, *block]
    projection.validate_cases('REQ-17', cases)
    return {'schema': SCHEMA, 'data_origin': 'EXAMPLE', 'seed': seed,
        'design': {'quartets': quartets, 'prompt_chars': prompt_chars, 'warmups': warmups, 'block_size': block_size},
        'cases_sha256': sha256_hex(canonical_json(cases)), 'cases': cases}


def write_split(output, seed, *, prompt_chars=1500, block_size=16):
    from guard.executors import cache_timing_group as group
    output = Path(output)
    if output.exists():
        raise ValueError('REQ17_SPLIT_OUTPUT_EXISTS')
    cohorts = [build(seed + index, quartets=100, prompt_chars=prompt_chars, warmups=2, block_size=block_size)
               for index in range(group.PARTS)]
    group.validate_case_parts([p['cases'] for p in cohorts])
    raws = [canonical_json(c) for c in cohorts]
    if any(len(raw) > group.MAX_PART_BYTES for raw in raws):
        raise ValueError('REQ17_SPLIT_PART_TOO_LARGE')
    result = {'schema': GROUP_SCHEMA, 'data_origin': 'EXAMPLE', 'ready_for_execution': False,
        'parts': [{'index': i, 'file': f'cohort-{i}.json', 'file_sha256': sha256_hex(raw)}
                  for i, raw in enumerate(raws, 1)]}
    output.mkdir(parents=True)
    for entry, raw in zip(result['parts'], raws, strict=True):
        (output / entry['file']).write_bytes(raw)
    (output / 'group.json').write_bytes(canonical_json(result))
    return result, cohorts


def load_split(path):
    from guard.executors import cache_timing_group as group
    from guard.runtime.bounded import strict_json
    path = Path(path)
    value = strict_json(group.read_regular(path, limit=262144), limit=262144)
    if (set(value) != {'schema', 'data_origin', 'ready_for_execution', 'parts'}
            or value['schema'] != GROUP_SCHEMA or value['data_origin'] != 'EXAMPLE'
            or value['ready_for_execution'] is not False or type(value['parts']) is not list
            or len(value['parts']) != group.PARTS):
        raise ValueError('REQ17_SPLIT_INPUT_INVALID')
    cohorts = []
    for i, entry in enumerate(value['parts'], 1):
        if (type(entry) is not dict or set(entry) != {'index', 'file', 'file_sha256'}
                or type(entry['index']) is not int or entry['index'] != i or entry['file'] != f'cohort-{i}.json'
                or type(entry['file_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', entry['file_sha256'])):
            raise ValueError('REQ17_SPLIT_INPUT_INVALID')
        part = strict_json(group.read_regular(path.parent / entry['file'],
            expected_sha256=entry['file_sha256']), limit=group.MAX_PART_BYTES)
        if (part.get('schema') != SCHEMA or part.get('data_origin') != 'EXAMPLE'
                or sha256_hex(canonical_json(part.get('cases'))) != part.get('cases_sha256')):
            raise ValueError('REQ17_SPLIT_INPUT_INVALID')
        cohorts.append(part['cases'])
    group.validate_case_parts(cohorts)
    return cohorts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, required=True, help='실행 전에 정해 기록한 값')
    parser.add_argument('--quartets', type=int)
    parser.add_argument('--split', action='store_true', help='504개씩 두 파일과 그룹 입력을 새 폴더에 준비')
    parser.add_argument('--prompt-chars', type=int, default=1500)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--block-size', type=int, default=16)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.split:
        if args.quartets not in (None, 100) or args.warmups != 2:
            parser.error('--split은 묶음별100 quartet·2warmup을 사용합니다.')
        value, cohorts = write_split(args.out, args.seed, prompt_chars=args.prompt_chars, block_size=args.block_size)
        print(json.dumps({'schema': value['schema'], 'parts': len(cohorts), 'cases_per_part': 504,
                          'ready_for_execution': False}))
        return
    cohort = build(args.seed, 24 if args.quartets is None else args.quartets, args.prompt_chars, args.warmups, args.block_size)
    args.out.write_text(json.dumps(cohort, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'cases': len(cohort['cases']), 'cases_sha256': cohort['cases_sha256']}))


if __name__ == '__main__':
    main()
