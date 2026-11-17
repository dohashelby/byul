"""실제 사전 결합된 원 RPC 파일만 공통 부분 입력으로 조립한다. 기동·주입·등록은 없다."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from guard.collectors import rank_rpc_fault_producer as producer
from guard.collectors import gpu_common_input_v4 as v4
from guard.models import canonical_json
from tools.rank_rpc_product_binding import binding_for


def prepare(prepared, capture, environment, target, output, *, wire_schema=v4.SCHEMA, expected_uid=None):
    owner = {} if expected_uid is None else {'expected_uid': expected_uid}
    binding, _, origin = binding_for(prepared, environment, target, **owner)
    pod = {k: binding[k] for k in ('pod_uid', 'container_id', 'restart_count')}
    common, validation, projection = producer.record(prepared, capture, pod=pod,
        **{k: binding[k] for k in ('environment_ref', 'target_sha256', 'prepared_sha256', 'session')},
        wire_schema=wire_schema)
    raw = canonical_json(common)
    result = {'schema': 'guard-rank-rpc-fault-inputs/1', 'data_origin': origin,
        'common_input_sha256': sha256(raw).hexdigest(), 'source_set_sha256': projection['source_set_sha256'],
        'binding': binding, 'validation': validation, 'source_projection': projection,
        'engine_executed': False, 'live_target_verified': False, 'input_preflight_passed': False,
        'ready_for_execution': False, 'actual_results': None}
    output = Path(output)
    output.mkdir(mode=0o700, exist_ok=False)
    for name, data in [('common-input.json', raw), ('manifest.json', canonical_json(result))]:
        path = output / name
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'capture', 'environment', 'target', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--wire-schema', choices=['guard-gpu-common-input/1', v4.SCHEMA], default=v4.SCHEMA)
    parser.add_argument('--expected-uid', type=int)
    args = parser.parse_args()
    result = prepare(args.prepared, args.capture, args.environment, args.target, args.output,
                     wire_schema=args.wire_schema, expected_uid=args.expected_uid)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
