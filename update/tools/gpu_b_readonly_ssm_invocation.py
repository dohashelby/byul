"""One read-only SSM SDK call; the fixed operator retains its 65s child limit.

No service/operation selector, credentials export, mutation, polling or retry.
Success stdout is the complete SDK response, including actual ResponseMetadata.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

REGION = 'ap-northeast-2'
MAX_REQUEST_BYTES = 4096


def validate_request(request, expected_instance):
    if (type(request) is not dict or set(request) != {'CommandId', 'InstanceId'}
            or type(expected_instance) is not str
            or re.fullmatch(r'i-[a-f0-9]{17}', expected_instance) is None
            or request.get('InstanceId') != expected_instance
            or type(request.get('CommandId')) is not str
            or re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', request['CommandId']) is None):
        raise ValueError('EXACT_READONLY_COMMAND_AND_SELECTED_INSTANCE_REQUIRED')
    return request


def read_request(path, expected_sha256):
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts
            or re.fullmatch(r'[a-f0-9]{64}', expected_sha256) is None):
        raise ValueError('FIXED_ABSOLUTE_REQUEST_AND_SHA_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 1000
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > MAX_REQUEST_BYTES):
            raise PermissionError('OWNER1000_REGULAR600_REQUEST_REQUIRED')
        raw = os.read(fd, MAX_REQUEST_BYTES + 1)
        after = os.fstat(fd)
        named = path.lstat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
                              s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(named)
                or len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError('REQUEST_IDENTITY_OR_BYTES_CHANGED')
        return json.loads(raw)
    finally:
        os.close(fd)


def get_once(request, expected_instance, client):
    validate_request(request, expected_instance)
    response = client.get_command_invocation(**request)
    if (type(response) is not dict or response.get('CommandId') != request['CommandId']
            or response.get('InstanceId') != expected_instance):
        raise ValueError('ACTUAL_INVOCATION_IDENTITY_CHANGED')
    return response


def sdk_response(request, expected_instance):
    # Validation precedes SDK import/client creation and credential lookup.
    validate_request(request, expected_instance)
    import boto3
    from botocore.config import Config
    client = boto3.client('ssm', region_name=REGION,
        config=Config(connect_timeout=10, read_timeout=45,
                      retries={'total_max_attempts': 1, 'mode': 'standard'}))
    try:
        return get_once(request, expected_instance, client)
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--region', choices=(REGION,), required=True)
    args = parser.parse_args()
    if os.getuid() != 1000 or os.geteuid() != 1000:
        raise PermissionError('UID1000_READONLY_OPERATOR_REQUIRED')
    request = read_request(args.request, args.request_sha256)
    response = sdk_response(request, args.instance)
    # No synthesized HTTP status, timestamps, terminal state or result fields.
    sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Preserve the real SDK exception string, including InvocationDoesNotExist
        # for the existing operator's classification, plus actual error metadata.
        sys.stderr.write(str(error) + '\n')
        response = getattr(error, 'response', None)
        if type(response) is dict:
            sys.stderr.write(json.dumps({'source': 'official-SDK-error-response',
                'exception_type': type(error).__name__, 'response': response},
                ensure_ascii=False, sort_keys=True) + '\n')
        raise SystemExit(1 if type(response) is dict else 2)
