"""SUP-10 plugin runner 실제 생산자를 지정한 Pod 컨테이너 안에서 돌린다(개발 담당, B 의존 반환 2).

    uv run python -m tools.plugin_runner_capture --context <ctx> --namespace <ns> --pod <pod> --container <c> --model-dir /models/<m> --out <새 파일>

엔진과 같은 이미지·같은 모델 폴더를 읽기 전용으로 붙인 **별도 생산자 Pod**에서 돌린다. 생산자는 사례마다 vLLM 설정을 새로 만들어
메모리를 쓰므로, 메모리 상한이 빠듯한 서빙 엔진 컨테이너 안에서 돌리면 엔진이 OOM으로 내려갈 수 있다(2026-11-08 k3d에서 실제로 발생).
결과 행과 `plugin_runner.project` 투영을 함께 쓴다. CPU·lab 결과는 EXAMPLE이다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from guard.executors import plugin_runner as runner


def capture(*, context, namespace, pod, container, model_dir, timeout=1800):
    argv = ['kubectl', f'--context={context}', f'--namespace={namespace}', 'exec', pod, f'--container={container}', '--',
            'python3', '-c', runner.RUNNER, *runner.runner_args(model_dir)]
    done = subprocess.run(argv, capture_output=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError('PLUGIN_RUNNER_CAPTURE_FAILED')
    value = json.loads(done.stdout.decode().strip().splitlines()[-1])
    if value.get('contract') != runner.CONTRACT or type(value.get('rows')) is not list:
        raise ValueError('PLUGIN_RUNNER_CAPTURE_INVALID')
    return {**value, 'data_origin': 'EXAMPLE', 'projection': runner.project(value['rows'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('--context', '--namespace', '--pod', '--container', '--model-dir', '--out'):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit('output exists')
    result = capture(context=args.context, namespace=args.namespace, pod=args.pod, container=args.container, model_dir=args.model_dir)
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + '\n')
    print(json.dumps({'complete': result['projection']['complete'], 'errors': result['projection']['errors']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
