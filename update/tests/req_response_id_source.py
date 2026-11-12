"""Test-only exact db952 random_uuid producer; no vLLM/Torch import or worker."""
import hashlib
import json
from pathlib import Path
import uuid

DATA = json.loads((Path(__file__).parent / 'fixtures/vllm-0.31.0/vllm_db952_response_id_generator.json').read_bytes())
assert DATA['source_commit'] == 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
assert hashlib.sha256(DATA['function_source'].encode()).hexdigest() == DATA['function_sha256']
_namespace = {'uuid': uuid}
exec(compile(DATA['mask_source'] + '\n' + DATA['function_source'],
             'db9527a/vllm/utils/__init__.py:exact-random_uuid', 'exec', dont_inherit=True), _namespace)
random_uuid = _namespace['random_uuid']


def response_id(chat):
    # Same default producer and fixed public prefixes as the pinned serving
    # paths. No header/body override is used by the supported request contract.
    return ('chatcmpl-' if chat else 'cmpl-') + random_uuid()
