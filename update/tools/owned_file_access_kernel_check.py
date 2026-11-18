"""Prepare an opt-in owned-file kernel development canary; never GPU qualify.

Preparation is unprivileged and refuses existing output/source drift. Execute
only the generated private program as root on an owned disposable Linux host.
The canary creates its own temporary files/process namespaces and reaps them;
it changes no shared clock, service, mount or target. No engine is imported.
"""
from pathlib import Path
import argparse
import hashlib
import json

from guard.runtime import file_access_source
if __package__:
    from . import verify_req10_consumption
else:
    import verify_req10_consumption

ROOT = Path(__file__).resolve().parents[1]
BODY = Path(__file__).with_name('assets') / 'owned_file_access_kernel_canary.py.txt'
BODY_SHA256 = '1573da391f3f63859a337d7c0b724d8107163c31514ed51b31276e8a19cfeede'


def prepare(output):
    manifest = verify_req10_consumption.verify_inventory(ROOT.parent)
    raw = (ROOT / 'release/reviewed-source.json').read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    receipts = json.loads((ROOT / 'tools/reviewed_manifest_receipts.json').read_bytes())
    if sum(row.get('manifest_sha256') == fingerprint for row in receipts['receipts']) != 1:
        raise ValueError('FILE_ACCESS_KERNEL_REVIEW_RECEIPT_REQUIRED')
    body = BODY.read_bytes()
    if len(body) > 32768 or hashlib.sha256(body).hexdigest() != BODY_SHA256:
        raise ValueError('FILE_ACCESS_KERNEL_CANARY_SOURCE_UNREVIEWED')
    prefix, separator, _ = file_access_source.source().partition('__byul_request=')
    if not separator or not prefix.endswith('\n'):
        raise ValueError('FILE_ACCESS_KERNEL_PROGRAM_UNAVAILABLE')
    program = prefix + 'SOURCE_MANIFEST_SHA256=' + repr(fingerprint) + '\n' + body.decode('utf-8')
    if len(program.encode()) > 131072:
        raise ValueError('FILE_ACCESS_KERNEL_PROGRAM_BOUND')
    output = Path(output).absolute()
    output.mkdir(mode=0o700, exist_ok=False)
    script = output / 'canary.py'
    with script.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(program)
    script.chmod(0o600)
    result = {'schema': 'guard-owned-file-kernel-canary-preparation/1',
        'source_manifest_sha256': fingerprint, 'pinned_source_files_verified': len(manifest['files']),
        'canary_program_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
        'canary_body_sha256': BODY_SHA256, 'run_argv': ['/usr/bin/python3.12', '-I', '-S', str(script)],
        'engine_executed': False, 'actual_gpu_source_qualified': False, 'producer_registered': False,
        'whole69_complete': False, 'kernel_probe_executed_by_preparation': False}
    with (output / 'prepared.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=True))


if __name__ == '__main__':
    main()
