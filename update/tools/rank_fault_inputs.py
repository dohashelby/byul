"""원 관측 묶음에서 rank-fault 후보 입력을 만든다. 엔진·신호를 실행하지 않는다.

범위와 한계: docs/dev-1/ma3-rank-fault-producer.md
"""
import argparse
from hashlib import sha256
from pathlib import Path
import stat

from guard.collectors import rank_fault_producer
from guard.models import canonical_json
from guard.runtime.bounded import strict_json

MAX_BYTES = 8 * 1024**2
FIELDS = frozenset({'packet', 'client', 'injection_receipt', 'source_scope', 'deadlines',
    'pod', 'environment_ref', 'target_sha256', 'prepared_sha256', 'session', 'recovery_client'})


def prepare(path, output):
    """범위는 입력자가 별도로 제공한다. 기존 파일을 덮어쓰지 않는다."""
    path, output = Path(path), Path(output)
    if any(p.is_symlink() for p in (path, *path.parents, output, *output.parents)):
        raise ValueError('RANK_FAULT_INPUT_SYMLINK')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
        raise ValueError('RANK_FAULT_INPUT_BOUND')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('RANK_FAULT_INPUT_CHANGED')
    value = strict_json(raw, limit=MAX_BYTES)
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError('RANK_FAULT_INPUT_FIELDS_INVALID')
    record, validation, projection = rank_fault_producer.record(value['packet'],value['client'],
        value['injection_receipt'],value['source_scope'],value['deadlines'],pod=value['pod'],
        environment_ref=value['environment_ref'],target_sha256=value['target_sha256'],
        prepared_sha256=value['prepared_sha256'],session=value['session'],recovery_client=value['recovery_client'])
    result = {'schema':'guard-rank-fault-input-preparation/1','data_origin':'DECLARED',
        'engine_executed':False,'input_sha256':sha256(raw).hexdigest(),'record':record,
        'validation':validation,'source_projection':projection}
    with output.open('xb') as stream:
        stream.write(canonical_json(result))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path,help='원 관측과 별도 예상 범위 JSON')
    parser.add_argument('--output',required=True,type=Path,help='새 후보 입력 파일')
    args=parser.parse_args()
    result=prepare(args.input,args.output)
    print(f"rank-fault 입력 생성: supported={result['validation']['supported']} complete={result['validation']['complete']}")


if __name__=='__main__':
    main()
