"""SUP-09·11 model-entry 실제 생산자를 지정한 엔진 Pod 컨테이너 안에서 돌린다(개발 담당, B 의존 반환 1).

    uv run python -m tools.model_entry_capture --context <ctx> --namespace <ns> --pod <pod> --container engine --out <새 파일>

컨테이너의 Python·transformers·torch로 무해 사용자 모델을 임시 폴더에 쓰고, trust × normal/-O/-OO 6사례를 새 자식 프로세스로
실행한다. 네트워크·모델 내려받기 없음(오프라인 설정). 결과 행과 `dynamic_model_entry.project` 투영을 함께 쓴다.
CPU·lab 실행 결과는 EXAMPLE이며 GPU·제품 실측으로 올리지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from guard.executors import dynamic_model_entry as entry


def capture(*, context, namespace, pod, container, timeout=1200):
    argv = ['kubectl', f'--context={context}', f'--namespace={namespace}', 'exec', pod, f'--container={container}', '--',
            'python3', '-c', entry.RUNNER, *entry.runner_args()]
    done = subprocess.run(argv, capture_output=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError('MODEL_ENTRY_CAPTURE_FAILED')
    value = json.loads(done.stdout.decode().strip().splitlines()[-1])
    if value.get('contract') != entry.CONTRACT or type(value.get('rows')) is not list:
        raise ValueError('MODEL_ENTRY_CAPTURE_INVALID')
    return {**value, 'data_origin': 'EXAMPLE', 'projection': entry.project(value['rows'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('--context', '--namespace', '--pod', '--container', '--out'):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit('output exists')
    result = capture(context=args.context, namespace=args.namespace, pod=args.pod, container=args.container)
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + '\n')
    print(json.dumps({'complete': result['projection']['complete'], 'errors': result['projection']['errors']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
