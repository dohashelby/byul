"""실제 vLLM CPU 컨테이너에 활성화 시나리오를 돌린다. kubectl 호출을 docker로 옮기는 대역만 쓴다.

장애·재기동·측정은 실제로 일어난다. 대역은 Pod 조회와 배포 명령만 흉내 낸다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from guard.executors import lab_activation as lab


class DockerCluster:
    def __init__(self, *, engine, probe, launch):
        self.engine = engine; self.probe = probe; self.launch = launch; self.generation = 'good'

    def _run(self, argv, timeout, input_bytes=None):
        done = subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout)
        return {'returncode': done.returncode, 'stdout_bytes': done.stdout, 'stderr_bytes': done.stderr[-4096:]}

    def _ready(self):
        check = self._run(['docker', 'exec', self.probe, 'python3', '-c',
            f"import urllib.request as u;print(u.urlopen('http://{self.engine}:8000/health',timeout=3).status)"], 10)
        return check['returncode'] == 0 and check['stdout_bytes'].strip() == b'200'

    def __call__(self, argv, *, timeout, env_extra, max_stdout_bytes, max_stderr_bytes, input_bytes=None):
        args = [a for a in argv[1:] if not a.startswith(('--namespace=', '--request-timeout=', '--context='))]
        if args[:2] == ['get', 'namespace']:
            value = {'metadata': {'labels': {lab.OWNED_LABEL: 'true'}}}
        elif args[:2] == ['get', 'pods']:
            status = 'True' if self._ready() else 'False'
            value = {'items': [{'metadata': {'name': 'engine', 'labels': {'byul.lab/generation': self.generation}},
                'status': {'conditions': [{'type': 'Ready', 'status': status}]}}]}
        elif args[:2] == ['rollout', 'restart']:
            return self._run(['docker', 'restart', '-t', '2', self.engine], min(timeout, 60))
        elif args[0] == 'apply':
            self.generation = input_bytes.decode().strip()
            subprocess.run(['docker', 'rm', '-f', self.engine], capture_output=True)
            return self._run(self.launch(self.generation), min(timeout, 60))
        elif args[0] == 'exec':
            stdin = '-i' in args
            separator = args.index('--')
            target = args[1 + stdin]
            container = self.probe if target == 'probe' else self.engine
            return self._run(['docker', 'exec', *(['-i'] if stdin else []), container, *args[separator + 1:]], timeout, input_bytes)
        else:
            raise ValueError(args)
        return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b''}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks', nargs='+', default=sorted(lab.SCENARIOS))
    parser.add_argument('--engine', default='byul-cb'); parser.add_argument('--probe', default='byul-probe')
    parser.add_argument('--launch-script', required=True, help='세대 이름을 받아 엔진을 기동할 명령을 출력하는 스크립트')
    parser.add_argument('--session', required=True); parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    def launch(generation):
        return json.loads(subprocess.check_output([args.launch_script, generation]))
    cluster = DockerCluster(engine=args.engine, probe=args.probe, launch=launch)
    args.work.mkdir(parents=True, exist_ok=True)
    for name in ('faulty', 'good', 'update'):
        (args.work / f'{name}.yaml').write_text(name)
    config = {'namespace': 'lab', 'selector': 'app=vllm', 'container': 'engine', 'probe_pod': 'probe', 'probe_container': 'probe',
        'service_url': f'http://{args.engine}:8000', 'model': 'opt', 'deployment': 'vllm', 'process_pattern': 'VLLM::Worker',
        'deadline_ms': 90000, 'burst_requests': 4, 'allowed_models': ['opt'], 'faulty_manifest': str(args.work / 'faulty.yaml'),
        'good_manifest': str(args.work / 'good.yaml'), 'update_manifest': str(args.work / 'update.yaml'), 'trace_directory': '/tmp/trace',
        'fault_directory': f'/guard-lifecycle/data/{args.session}', 'max_duration_seconds': 900}
    policy = {'buffer_max_process_rss_bytes': 6 * 1024 ** 3, 'buffer_max_send_wait_ms': 60000, 'buffer_release_deadline_ms': 15000,
        'termination_max_delay_ms': 15000, 'retry_policy_mode': 'forbidden', 'rank_death_detection_ms': 30000,
        'rank_delay_detection_ms': 30000, 'rank_communication_stall_detection_ms': 30000, 'rank_exception_detection_ms': 30000}
    results = []
    for check_id in args.checks:
        try:
            facts, _ = lab.run_scenario(check_id, config, policy, runner=cluster)
        except Exception as error:  # noqa: BLE001 - 다음 시나리오는 계속한다
            facts = {'error': type(error).__name__ + ': ' + str(error)[:200]}
        results.append({'check_id': check_id, **facts})
        print(json.dumps(results[-1], ensure_ascii=False)[:700], flush=True)
    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
