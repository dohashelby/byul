"""Explicit EXAMPLE rejection oracle for transport-independent contract tests."""
import json
from pathlib import Path
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.models import CheckDef, load_pack
from tests.builders import completion_response


def synthetic_input_response(request, outcome=200):
    response = completion_response(request, outcome)
    if outcome == 400:
        prompt = (request.get('body') or {}).get('prompt')
        case = 'token_id' if isinstance(prompt, list) and len(prompt) == 1 else 'length'
        response['body'] = json.dumps({'error': {'type': 'EXAMPLE_BOUNDARY_REJECTION', 'case': case}})
    return response


class SyntheticInputAdapter(VllmAdapter):
    """A fake source oracle, never registered by the installed product."""
    def input_rejection_proof(self, **kwargs):
        try:
            body = kwargs.get('response_body')
            value = body if isinstance(body, dict) else json.loads(body or '')
        except (TypeError, ValueError):
            value = {}
        verified = (kwargs.get('response_status') == 400
                    and value == {'error': {'type': 'EXAMPLE_BOUNDARY_REJECTION', 'case': kwargs.get('case')}})
        return {'state': 'verified' if verified else 'unverified',
                'reason': 'EXAMPLE_REJECTION_ORACLE' if verified else 'EXAMPLE_REJECTION_UNVERIFIED',
                'profile': 'EXAMPLE-test-only-input-contract',
                'sources': [{'path': 'EXAMPLE-test-only-oracle', 'sha256': 'e' * 64, 'lines': 'EXAMPLE'}]}


def with_legacy_check(pack, check_id):
    """Historical component definition only; this is not a saved-pack replay.

    The exact PACK21 YAML is retained so status-only executor contracts keep
    their original expectations. New-definition tests use baseline_pack().
    """
    path = Path(__file__).parent / 'fixtures' / 'pack21-checks' / (check_id + '.yaml')
    check = CheckDef.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
    return pack.model_copy(update={'checks': {**pack.checks, check_id: check}})


def materialize_pack21(destination):
    """Reconstruct and verify the exact historical pack, including its hashes.

    The other 75 definitions and id map are unchanged by this batch. A future
    edit to them must retain the relevant historical file too; load_pack must
    reject any accidental mismatch instead of silently revising old rules.
    """
    from tests.builders import PACK_DIR
    history = Path(__file__).parent / 'fixtures' / 'pack21-checks'
    destination.mkdir(parents=True, exist_ok=False)
    for source in sorted(PACK_DIR.rglob('*')):
        if not source.is_file():
            continue
        relative = source.relative_to(PACK_DIR)
        preserved = history / source.name
        body = preserved.read_bytes() if preserved.is_file() else source.read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body)
    return load_pack(destination)
