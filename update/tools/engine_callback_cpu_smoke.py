"""실제 vLLM CPU 컨테이너에서 REQ-14~18 콜백 경로를 돌린다. Kubernetes 없이 HTTP와 세션 폴더만 쓴다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection
from guard.executors import engine_sensor
from guard.models import DataOrigin, Environment, canonical_json, sha256_hex

PREFIX = ' '.join(f'word{i}' for i in range(40))


class LocalTransport:
    """engine_sensor._callback이 쓰는 표면만 구현한 로컬 전송."""
    process_observation_origin = 'MEASURED'

    def __init__(self, base, model, data):
        self.base = base.rstrip('/'); self.api_model = model; self.data = Path(data)
        self.endpoint = SimpleNamespace(scheme='http', netloc=base.split('//')[1])

    def __call__(self, network, requests, timeout):
        rows = []
        for item in requests:
            raw = json.dumps(item['body']).encode()
            request = urllib.request.Request(self.base + '/v1/chat/completions', raw, {'Content-Type': 'application/json', **item['headers']})
            began = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read().decode(); outcome = response.status
            except urllib.error.HTTPError as error:
                body = error.read().decode(); outcome = error.code
            rows.append({'outcome': outcome, 'capture_complete': True, 'body': body, 'elapsed_ms': (time.monotonic() - began) * 1000})
        return rows

    def engine_callback_events(self, session):
        time.sleep(1)  # 마지막 응답 뒤 worker 기록이 끝날 시간
        events = []
        for path in sorted((self.data / session).glob('callbacks-*.jsonl')):
            events.extend(json.loads(line) for line in path.read_text().rpartition('\n')[0].splitlines())
        return {'events': events, 'truncated': False}


def group_cases():
    cases = []
    for index, (first, second) in enumerate((('A', 'B'), ('B', 'A'))):
        prompt = f'pair {index} {PREFIX}'
        cases += [{'case_id': f'{index}-prime', 'role': 'prime', 'group': first, 'prefix': str(index), 'prompt': prompt, 'cache_salt': 'salt-' + first},
            {'case_id': f'{index}-control', 'role': 'control', 'group': first, 'prefix': str(index), 'prompt': prompt, 'cache_salt': 'salt-' + first},
            {'case_id': f'{index}-probe', 'role': 'probe', 'group': second, 'prefix': str(index), 'prompt': prompt, 'cache_salt': 'salt-' + second}]
    return cases


def context_cases():
    prompt = 'context ' + PREFIX
    base = {'context': 'cache_salt', 'prefix': 'c', 'prompt': prompt}
    return [{**base, 'case_id': 'prime', 'role': 'prime', 'cache_salt': 'one'}, {**base, 'case_id': 'control', 'role': 'control', 'cache_salt': 'one'},
        {**base, 'case_id': 'probe', 'role': 'probe', 'cache_salt': 'two'}, {'case_id': 'post', 'role': 'post', 'prefix': 'c', 'prompt': prompt, 'cache_salt': 'one'}]


def timing_cases(samples):
    cases = []
    for number in range(samples):
        secret = f'timing {number} {PREFIX}'
        cases += [{'case_id': f'prime-{number}', 'role': 'prime', 'prompt': secret, 'cache_salt': 'owner'},
            {'case_id': f'cp-{number}', 'role': 'control-present', 'prompt': secret, 'cache_salt': 'owner'},
            {'case_id': f'ca-{number}', 'role': 'control-absent', 'prompt': f'unseen {number} {PREFIX}', 'cache_salt': 'owner-' + str(number)},
            {'case_id': f'p-{number}', 'role': 'present', 'prompt': secret, 'cache_salt': 'other'},
            {'case_id': f'a-{number}', 'role': 'absent', 'prompt': f'never {number} {PREFIX}', 'cache_salt': 'other'}]
    return cases


def slot_cases():
    return [{'case_id': 's1', 'role': 'standalone', 'prompt': 'tell me', 'max_tokens': 4},
        {'case_id': 's2', 'role': 'standalone', 'prompt': 'tell me', 'max_tokens': 4},
        {'case_id': 'r', 'role': 'restricted', 'prompt': 'tell me', 'max_tokens': 4, 'allowed_token_ids': [5]},
        {'case_id': 'b', 'role': 'reused', 'prompt': 'tell me', 'max_tokens': 4}]


def gateway_cases():
    cases = []
    for index, (first, second) in enumerate((('A', 'B'), ('B', 'A'))):
        prompt = f'gateway {index} {PREFIX}'
        cases += [{'case_id': f'g{index}-prime', 'role': 'prime', 'group': first, 'prefix': str(index), 'prompt': prompt, 'principal': first},
            {'case_id': f'g{index}-control', 'role': 'control', 'group': first, 'prefix': str(index), 'prompt': prompt, 'principal': first},
            {'case_id': f'g{index}-forged', 'role': 'probe', 'group': second, 'prefix': 'forged', 'prompt': 'forged', 'principal': second, 'forged_salt': 'salt-' + first},
            {'case_id': f'g{index}-probe', 'role': 'probe', 'group': second, 'prefix': str(index), 'prompt': prompt, 'principal': second}]
    return cases


def run_check(check_id, cases, values, env, assets, transport, workdir, extra=None):
    prepared = engine_callbacks.load_prepared(assets)
    target = {'kubernetes': {}, 'lab': False}
    workdir = Path(workdir) / check_id; workdir.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': projection.SCHEMA, 'session': prepared['session'], 'target_sha256': sha256_hex(canonical_json(target)),
        'environment_ref': env.ref().model_dump(mode='json'), 'cases': cases}
    (workdir / 'target.json').write_bytes(canonical_json(target)); (workdir / 'manifest.json').write_bytes(canonical_json(manifest))
    executor = engine_sensor.EngineSensorExecutor({'target': str(workdir / 'target.json'), 'prepared': str(Path(assets) / 'manifest.json'),
        'manifest': str(workdir / 'manifest.json'), **(extra or {})}, implementation='kubernetes')
    executor.transport = transport
    check = SimpleNamespace(id=check_id, executor='engine_sensor', collection_profile=engine_sensor.PROFILES[check_id], challenge_steps=[])
    draft = executor.run(check, env, {'sensor_environment_sha256': env.ref().sha256, 'sensor_target_sha256': sha256_hex(canonical_json(target)),
        'sensor_prepared_sha256': sha256_hex(canonical_json(prepared)), 'sensor_manifest_sha256': sha256_hex(canonical_json(manifest)), **values},
        None, workdir / 'work')
    facts = json.loads(draft.artifacts[0].path.read_text(encoding='utf-8'))['safe_facts']
    return {'check_id': check_id, 'status': draft.status.value, 'violation': draft.observations['engine_sensor_violation'].value,
        'collected': draft.observations['engine_sensor_violation'].collected,
        'full_scope': draft.observations['engine_sensor_full_scope'].value, 'normal': draft.normal_control.succeeded,
        'facts': {k: v for k, v in facts.items() if k != 'trials'}, 'requests': len(facts['trials'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18000'); parser.add_argument('--model', default='opt')
    parser.add_argument('--assets', type=Path, required=True); parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True); parser.add_argument('--output', type=Path)
    parser.add_argument('--gateway-url', help='per_principal Gateway. 토큰은 BYUL_TOKEN_A·BYUL_TOKEN_B 환경변수')
    args = parser.parse_args()
    env = Environment(id='ENV-CPU-CALLBACK', data_origin=DataOrigin.MEASURED, engine={'name': 'vllm', 'version': '0.31.0'}).seal()
    transport = LocalTransport(args.url, args.model, args.data)
    plans = [('REQ-14', [{'case_id': f'n{i}', 'role': 'normal', 'prompt': f'normal {i}'} for i in range(3)], {}),
        ('REQ-15', group_cases(), {'isolated_cache_groups': ['A', 'B']}),
        ('REQ-16', context_cases(), {'required_cache_contexts': ['cache_salt']}),
        ('REQ-17', timing_cases(6), {'cache_timing_max_auc_percent': 75, 'cache_timing_min_samples': 6}),
        ('REQ-18', slot_cases(), {})]
    results = [run_check(c, cases, values, env, args.assets, transport, args.work) for c, cases, values in plans]
    if args.gateway_url:
        gateway = LocalTransport(args.gateway_url, args.model, args.data)
        results.append({**run_check('REQ-15', gateway_cases(), {'isolated_cache_groups': ['A', 'B']}, env, args.assets, gateway,
            args.work / 'gateway', {'principal_token_env': {'A': 'BYUL_TOKEN_A', 'B': 'BYUL_TOKEN_B'}}), 'path': 'gateway-per-principal'})
    text = json.dumps(results, ensure_ascii=False, indent=1)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
