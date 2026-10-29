"""EXAMPLE contract tests, not serving or renderer execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest
from guard.adapters import vllm_template_boundary as n

@pytest.mark.parametrize('point',['SandboxedEnvironment.is_safe_attribute','ImmutableSandboxedEnvironment.is_safe_attribute'])
@pytest.mark.parametrize('allowed',[True,False,None])
def test_attribute_bool_is_actual_return(point,allowed):
    v=n.snapshot_template_boundary(point,(object(),1,'__class__',int),{},phase='RETURN',result=allowed)
    assert v['metadata']=={'attribute':'__class__','object_case':'integer_one','attribute_allowed':allowed,'attribute_result_status':'collected' if type(allowed) is bool else 'unavailable','evaluation_context':'unavailable'}


def test_fixed_message_template_and_render_digests_only():
    value=n.snapshot_template_boundary('tokenizer.apply_chat_template',(),{'conversation':n.CHALLENGE,'chat_template':'PRIVATE_TEMPLATE'},phase='RETURN',result='PRIVATE_RENDER')
    assert value['metadata']['messages_case']=='challenge'
    assert value['metadata']['messages_sha256']==n.digest(n.CHALLENGE)
    assert value['metadata']['result_summary']['sha256']
    assert 'PRIVATE' not in json.dumps(value)


def test_resolved_template_actual_return_and_missing_separate():
    actual=n.snapshot_template_boundary('resolve_chat_template',(),{},phase='RETURN',result='x')
    assert actual['metadata']['resolved_template']==n._text('x')
    assert n.snapshot_template_boundary('resolve_chat_template',(),{},phase='RETURN')['metadata']['resolved_template']=={'status':'unavailable'}


def test_generator_and_properties_are_not_consumed():
    def gen():raise AssertionError('MUST_NOT_ITERATE');yield 1
    v=n.snapshot_template_boundary('tokenizer.apply_chat_template',(),{'conversation':gen(),'chat_template':object()},phase='RETURN',result=gen())
    assert v['metadata']['result_summary']=={'status':'unavailable'}
    assert 'messages_sha256' not in v['metadata']


def test_bounds_binding_and_exception_no_text():
    binding={'call_id_sha256':'a'*64,'invocation_id_sha256':'b'*64,'parent_call_id_sha256':'c'*64,'secret':'PRIVATE','pid':1}
    v=n.snapshot_template_boundary('safe_apply_chat_template',(),{},phase='RAISE',exception=ValueError('PRIVATE'),binding=binding)
    assert v['metadata']['exception_type']=='ValueError' and 'PRIVATE' not in json.dumps(v)
    assert len(v['binding'])==4
    assert n._summary('x'*(n.MAX_TEXT+1))=={'status':'unavailable'}
    assert n._summary([True])=={'status':'unavailable'}


def test_standalone_stdlib_export():
    code="import importlib.util,sys; s=importlib.util.spec_from_file_location('snapshot',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);assert m.snapshot_template_boundary('unknown',(),{},phase='CALL')['status']=='unavailable';assert 'guard' not in sys.modules and 'torch' not in sys.modules"
    p=subprocess.run([sys.executable,'-I','-S','-c',code,n.__file__],capture_output=True)
    assert p.returncode==0


@pytest.mark.parametrize('context',['compile_or_optimize','template_runtime','unavailable'])
def test_attribute_evaluation_phase_preserves_actual_context(context):
    value=n.snapshot_template_boundary('SandboxedEnvironment.is_safe_attribute',(object(),1,'__class__',int),{'evaluation_context':context},phase='RETURN',result=False)
    assert value['metadata']['evaluation_context']==context
    assert value['metadata']['attribute_result_status']=='collected'
