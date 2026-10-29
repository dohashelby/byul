"""EXAMPLE reducer contracts; no source engine or live capture."""
import ast
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import inspect
import json
import pytest
from guard.adapters import template_boundary_evidence as p
COLLECTOR='c'*64
CALLBACK='d'*64
VALIDATION={'pre':'verified','post':{'status':'verified'},'boundary':'source_component'}


def text(value):
    return {'status': 'collected', 'kind': 'text', 'count': len(value), 'sha256': hashlib.sha256(value.encode()).hexdigest()}

def row(case, *, consumed=False, compile_only=False):
    messages = p.CHALLENGE if case == 'challenge' else p.NORMAL
    invocation = p.native_digest(case)
    root, tokenizer, attr = ('1' * 64, '2' * 64, '3' * 64)
    def snap(point, phase, call, parent, metadata):
        return {'schema': 'byul-template-boundary-snapshot/1', 'point': point, 'phase': phase,
            'status': 'collected', 'metadata': metadata, 'binding': {'invocation_id_sha256': invocation,
                'call_id_sha256': call, 'parent_call_id_sha256': parent, 'pid': 123}}
    fields = {'template': {'status': 'collected', 'sha256': p.TYPE_TEMPLATE_SHA256},
        'messages_sha256': p.native_digest(messages), 'messages_case': 'challenge' if case == 'challenge' else 'normal'}
    value = p.REFERENCE_TEXT if case == 'challenge' and consumed else p.NORMAL_TEXT
    snapshots = [snap('safe_apply_chat_template', 'CALL', root, None, fields),
        snap('tokenizer.apply_chat_template', 'CALL', tokenizer, root, fields),
        snap('ImmutableSandboxedEnvironment.is_safe_attribute', 'CALL', attr, tokenizer, {}),
        snap('ImmutableSandboxedEnvironment.is_safe_attribute', 'RETURN', attr, tokenizer,
            {'attribute': '__class__', 'object_case': 'integer_one', 'attribute_result_status': 'collected',
             'attribute_allowed': consumed, 'evaluation_context': 'compile_or_optimize' if compile_only else 'template_runtime'}),
        snap('tokenizer.apply_chat_template', 'RETURN', tokenizer, root, {'result_summary': text(value)}),
        snap('safe_apply_chat_template', 'RETURN', root, None, {'result_summary': text(value)})]
    return {'case_id': case, 'fixture_sha256': p.TYPE_TEMPLATE_SHA256, 'messages_sha256': p.native_digest(messages),
        'invocation_id_sha256': invocation, 'snapshots': snapshots, 'source_validation': deepcopy(VALIDATION),
        'outcome': 'returned', 'result_summary': text(value), 'private': 'PRIVATE'}

def record(consumed=False):
    now = datetime.now(timezone.utc).isoformat()
    return {'schema': 'byul-sup13-template-cpu-capture/1', 'source_profile': 'target031',
        'source_commit': p.SOURCE_COMMIT, 'own_producer_sha256': COLLECTOR,
        'native_callback_sha256': CALLBACK, 'fixture_sha256': p.TYPE_TEMPLATE_SHA256,
        'source_validation': deepcopy(VALIDATION), 'capture_started_at': now, 'capture_finished_at': now,
        'collection_id': 'sup13-template-cpu-' + 'a' * 32, 'capture_status': 'completed',
        'results': [row('before'), row('challenge', consumed=consumed), row('after')], 'private': 'PRIVATE'}

def test_actual_reference_and_runtime_attribute_decision_are_both_required():
    assert p.analyze_row(row('challenge', consumed=True), 'challenge')['reference_consumed']
    assert not p.analyze_row(row('challenge', consumed=True, compile_only=True), 'challenge')['reference_consumed']
    denied = row('challenge', consumed=True)
    denied['snapshots'][3]['metadata']['attribute_allowed'] = False
    assert not p.analyze_row(denied, 'challenge')['reference_consumed']
    changed = row('challenge', consumed=True)
    changed['result_summary'] = text('PRIVATE')
    assert not p.analyze_row(changed, 'challenge')['reference_consumed']

@pytest.mark.parametrize('mutation', ['invocation', 'messages', 'fixture', 'parent', 'source'])
def test_wrong_native_attribution_cannot_supply_reference(mutation):
    value = row('challenge', consumed=True)
    if mutation == 'invocation': value['snapshots'][3]['binding']['invocation_id_sha256'] = 'f' * 64
    elif mutation == 'messages': value['snapshots'][0]['metadata']['messages_sha256'] = 'f' * 64
    elif mutation == 'fixture': value['fixture_sha256'] = 'f' * 64
    elif mutation == 'parent': value['snapshots'][1]['binding']['parent_call_id_sha256'] = 'f' * 64
    else: value['source_validation']['post']['status'] = 'mismatch'
    assert not p.analyze_row(value, 'challenge')['reference_consumed']

ACCEPTED_AST_SHA256={'analyze_row': '23909dfc07f0e5389b600275822ee89456ad63abb7501fd39de6014552991d77', '_cpu_result': '87786cd9450b3f829ffd13114f90456a8d1ba9380641407105e6e035d74a1216'}


def analyze(value,**pins):
    return p.analyze_cpu_capture(value,source_commit=pins.get('source_commit',p.SOURCE_COMMIT),collector_sha256=pins.get('collector_sha256',COLLECTOR),callback_sha256=pins.get('callback_sha256',CALLBACK))


def test_accepted_reducer_ast_is_preserved_without_runtime_git_history():
    for name,expected in ACCEPTED_AST_SHA256.items():
        node=ast.parse(inspect.getsource(getattr(p,name))).body[0]
        assert hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()==expected


def test_complete_reference_and_safe_summaries():
    result=analyze(record(True))
    assert result['complete'] and result['reference_consumed'] and result['witness']
    assert result['installed_target'] is None and not result['gpu_execution_verified']
    assert p.REFERENCE_TEXT not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('kind',['post','after','aux','overflow'])
def test_partial_capture_preserves_independent_reference(kind):
    value=record(True);value['capture_status']='incomplete'
    if kind=='post':value['source_validation']['post']['status']='unavailable'
    elif kind=='after':value['results'][2]['source_validation']['post']['status']='unavailable'
    elif kind=='aux':value['auxiliary_write']={'status':'failed'}
    else:value['results'][1].update(observation_gaps=['event_limit_exceeded'],dropped_event_count=1)
    result=analyze(value)
    assert not result['complete'] and result['reference_consumed']
    if kind=='overflow':assert result['rows'][1]['dropped_event_count']==1


@pytest.mark.parametrize('key',['collector_sha256','callback_sha256','source_commit'])
@pytest.mark.parametrize('bad',[None,'','not-a-sha'])
def test_missing_or_invalid_explicit_pin_is_unavailable(key,bad):
    assert analyze(record(True),**{key:bad}) is None


@pytest.mark.parametrize('key',['own_producer_sha256','native_callback_sha256','source_commit'])
def test_record_pin_mismatch_is_unavailable(key):
    value=record(True);value[key]='f'*len(value[key])
    assert analyze(value) is None


def test_unknown_timestamp_remains_unavailable():
    value=record();value['capture_started_at']='2026-10-30T01:00:00'
    assert analyze(value) is None
