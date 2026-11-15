"""기준 V1(0.7.0) 입력 지점의 live 선택 연결. 상속 문맥·반환값만으로 새 연결을 만들지 않는다."""
import ast
from pathlib import Path

import pytest

from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_links as links
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_request_termination as termination
from tests.test_configured_lifecycle import CANARY, FIXTURE, PROFILE, recording, world  # noqa: F401
from tests.test_lifecycle_capture import external  # noqa: F401

POINT = 'Processor.process_inputs'
ASGI = 'a' * 64


def test_release_row_names_the_reviewed_input_point_and_argument():
    selection = catalog.row(PROFILE)['selection']
    assert selection['point'] == POINT == catalog.selection_point(PROFILE)
    assert POINT in catalog.SELECTION_POINTS and POINT in termination.SELECTION_POINTS
    tree = ast.parse((FIXTURE / 'vllm/v1/engine/processor.py.txt').read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Processor')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'process_inputs')
    assert [a.arg for a in fn.args.args][selection['request_id_argument']] == 'request_id'
    assert catalog.runtime_spec(PROFILE)['selection'] == {'point': POINT, 'request_id_argument': 1}
    assert catalog.selection_point('vllm-native-lifecycle-metadata/8') is None


class Processor:
    def process_inputs(self, request_id, prompt, params, *rest):
        return object()


def _call(world, *, live, inherited=True, request_id=CANARY):
    base = world.base
    wrapped = world.observer.wrap(profiles.point_sources(PROFILE)[POINT], POINT, Processor.process_inputs)
    route = {'pid': base.os.getpid(), 'closed': False, 'request': ASGI, 'invocation': 'b' * 64,
             'path': 'c' * 64, 'root': 'd' * 64, 'method': 'e' * 64}
    tokens = []
    if inherited:
        tokens.append((base._ASGI_REQUEST, base._ASGI_REQUEST.set((base.os.getpid(), ASGI))))
    if live:
        tokens.append((base._ROUTE_REQUEST, base._ROUTE_REQUEST.set(route)))
    try:
        wrapped(Processor(), request_id, 'EXAMPLE-PROMPT', object())
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    return recording(world)


def test_live_selection_links_the_asgi_request_to_the_callers_native_id(world):  # noqa: F811
    records, _ = _call(world, live=True)
    native = [r for r in records if r['event'] == 'NATIVE' and r['payload']['point'] == POINT]
    selection = [r for r in records if r['event'] == 'SELECTION' and r['payload']['point'] == POINT]
    assert [r['payload']['phase'] for r in native] == ['CALL', 'RETURN']
    assert [r['payload']['phase'] for r in selection] == ['CALL', 'RETURN']
    returned = native[-1]
    pair = links.assignment(returned)
    native_id = world.base.id_sha(CANARY)
    # The release keeps the caller's ID: the external and native IDs are the same value.
    assert pair == (native_id, native_id) and links.asgi_assignment(returned) == ASGI
    chosen = selection[-1]['payload']['metadata']['request_selection']
    assert chosen['selected_request_id_sha256'] == native_id and chosen['request_id_observed'] is True
    assert chosen['asgi_invocation_sha256'] == 'b' * 64
    assert not any(chosen[k + '_observed'] for k in ('priority', 'session', 'rank'))


@pytest.mark.parametrize('live,inherited', [(False, True), (True, False), (False, False)])
def test_inherited_context_or_missing_live_route_never_claims_a_link(world, live, inherited):  # noqa: F811
    records, _ = _call(world, live=live, inherited=inherited)
    native = [r for r in records if r['event'] == 'NATIVE' and r['payload']['point'] == POINT]
    assert native and all(links.assignment(r) is None for r in native)
    if not live:
        assert not [r for r in records if r['event'] == 'SELECTION']


def test_invalid_request_id_keeps_native_behavior_and_claims_nothing(world):  # noqa: F811
    records, _ = _call(world, live=True, request_id=['not', 'a', 'string'])
    native = [r for r in records if r['event'] == 'NATIVE' and r['payload']['point'] == POINT]
    assert all(links.assignment(r) is None for r in native)
    chosen = [r['payload']['metadata']['request_selection'] for r in records
              if r['event'] == 'SELECTION' and r['payload']['metadata']]
    assert all(v['request_id_observed'] is False and v['selected_request_id_sha256'] is None for v in chosen)
