"""REQ-17 GPU 재시험의 사례 목록을 만든다. 설계 근거는 담당 A 기록 폴더(docs/gpu-a)의 REQ-17 설계 문서.

출력은 EXAMPLE 입력이다. manifest의 session·target_sha256·environment_ref는 실행할 때 붙인다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

from guard.executors import engine_callback_projection as projection
from guard.models import canonical_json, sha256_hex

SCHEMA = 'byul-gpu-a-req17-cohort/1'
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, required=True, help='실행 전에 정해 기록한 값')
    parser.add_argument('--quartets', type=int, default=24)
    parser.add_argument('--prompt-chars', type=int, default=1500)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--block-size', type=int, default=16)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    cohort = build(args.seed, args.quartets, args.prompt_chars, args.warmups, args.block_size)
    args.out.write_text(json.dumps(cohort, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'cases': len(cohort['cases']), 'cases_sha256': cohort['cases_sha256']}))


if __name__ == '__main__':
    main()
