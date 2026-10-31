"""Five pinned generator bodies; file readers, callbacks and model are EXAMPLE."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Iterable
import hashlib,itertools,json,os,time,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_weight_iteration_sensor as sensor,vllm_weight_iteration_metadata as metadata
from tests.test_lifecycle_sensor import CONTEXT
from tests.test_a1_rank_failure_sensor import codes,QuietLogger

FIXTURES=Path(__file__).parent/'fixtures/weight-iteration-native'
PRIVATE='EXAMPLE_WEIGHT_PATH_NAME_TENSOR_AND_EXCEPTION_DO_NOT_PERSIST'


class Tensor:pass
class Parameter:
    def __init__(self,name,world):self.name=name;self.shape=(1,);self.world=world
    def weight_loader(self,param,data):
        assert param is self
        if self.world.mode=='late-callback' and self.name=='other':raise OSError(PRIVATE)
        self.world.applied.append((self.name,data))
class Model:
    def __init__(self,world):self.parameters={name:Parameter(name,world) for name in ('weight','other')}
    def named_children(self):return iter(())
    def named_parameters(self,*,recurse):return iter(self.parameters.items())


def grouped(weights):
    for prefix,items in itertools.groupby(weights,key=lambda row:row[0].split('.',1)[0]):
        yield prefix,((name.partition('.')[2],value) for name,value in items)


def make_world(mode='normal',session='b'*32):
    loaded={};modules={}
    filenames={sensor.DEFAULT:'default_loader.py.txt',sensor.WEIGHTS:'weight_utils.py.txt',sensor.MODELS:'models-utils.py.txt'}
    for name,filename in filenames.items():
        path=FIXTURES/filename;raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==sensor.SOURCE_ADDITIONS[name]
        loaded[name]=codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
        module=types.ModuleType(name);module.__file__=str(path);module.logger=QuietLogger();modules[name]=module
    base=types.ModuleType('_weight_iteration_base');base.__file__=base8.__file__
    exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__);sensor.configure(base)
    class Observer(base.Observer):
        def __init__(self):super().__init__({'session':session},'c'*64);self.recorded=[]
        def _writer(self):return NS(context={**CONTEXT,'pid':os.getpid()})
        def emit(self,event,payload):self.recorded.append({'event':event,'payload':deepcopy(payload),'monotonic_ns':time.monotonic_ns()})
    observer=Observer();world=NS(mode=mode,applied=[],reads=[],closed_files=[],observer=observer,base=base,modules=modules,loaded=loaded)
    weights=modules[sensor.WEIGHTS];default=modules[sensor.DEFAULT];models=modules[sensor.MODELS]
    tensor_values={'weight':Tensor(),'other':Tensor()};world.tensor_values=tensor_values
    def read(path,**kw):world.reads.append(path);return dict(tensor_values)
    class Safe:
        def __init__(self,path,**kwargs):self.path=path
        def __enter__(self):world.reads.append(self.path);return self
        def __exit__(self,*args):world.closed_files.append(self.path)
        def keys(self):return list(tensor_values)
        def get_tensor(self,name):return tensor_values[name]
    weights.__dict__.update(torch=NS(load=read),tqdm=lambda values,**kw:values,enable_tqdm=lambda v:False,_BAR_FORMAT='',safe_open=Safe,
        _natural_sort_key=lambda v:v,_get_fs_type=lambda v:'ext4',_get_checkpoints_size_bytes=lambda v:4,_get_available_ram_bytes=lambda:1024,
        should_skip_weight=lambda *a:False)
    for method in base.FUNCTION_POINTS[sensor.WEIGHTS]:
        setattr(weights,method,types.FunctionType(loaded[sensor.WEIGHTS][method],weights.__dict__,method))
    default.DefaultModelLoader=type('DefaultModelLoader',(),{'__module__':sensor.DEFAULT})
    for method in (*base.POINTS[sensor.DEFAULT]['DefaultModelLoader'],'get_all_weights'):
        setattr(default.DefaultModelLoader,method,types.FunctionType(loaded[sensor.DEFAULT]['DefaultModelLoader.'+method],default.__dict__,method))
    default.__dict__.update(time=time,cast=lambda typ,value:value,Iterable=Iterable)
    # The get_all_weights body is exact. Its source preparation dependency is an
    # explicit EXAMPLE fixture; it does not download or open a checkpoint.
    default.DefaultModelLoader.Source=lambda model,revision,**kw:NS(model_or_path=model,revision=revision,**kw)
    models.AutoWeightsLoader=type('AutoWeightsLoader',(),{'__module__':sensor.MODELS})
    for method in sensor.TARGETS[sensor.MODELS]['AutoWeightsLoader']:
        setattr(models.AutoWeightsLoader,method,types.FunctionType(loaded[sensor.MODELS]['AutoWeightsLoader.'+method],models.__dict__,method))
    models.__dict__.update(StageMissingLayer=type('MissingStage',(),{}),PPMissingLayer=type('MissingPP',(),{}),default_weight_loader=lambda *a:None)
    for name,module in modules.items():
        observer.instrument(module)
        info={'source_sha256':sensor.SOURCE_ADDITIONS[name],'code_sha256':base.code_digest(loaded[name]['<module>'])}
        observer.modules[name]=info;observer.emit('MODULE',{'module':name,'verified':True,**info})
    world.model=Model(world);world.auto=models.AutoWeightsLoader();world.auto.module=world.model
    world.auto._get_qualname=lambda prefix,name:prefix+'.'+name if prefix and name else prefix or name
    world.auto._can_skip=lambda name:False;world.auto._can_ignore_unexpected=lambda name:False
    world.auto._groupby_prefix=grouped;world.auto._add_loadable_non_param_tensors=lambda *a:None
    world.loader=default.DefaultModelLoader();world.loader._get_weights_iterator=lambda source:weights.pt_weights_iterator([PRIVATE],False,'cpu')
    def iterator(kind='pt'):
        if kind=='safetensors':return weights.safetensors_weights_iterator([PRIVATE],False,None,None,safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1)
        if kind=='all':return world.loader.get_all_weights(NS(model=PRIVATE,revision=PRIVATE),world.model)
        return weights.pt_weights_iterator([PRIVATE],False,'cpu')
    world.iterator=iterator
    world.load=lambda kind='pt':world.auto._load_module('',world.model,iterator(kind))
    world.loader._init_ep_weight_filter=lambda *a:None;world.loader._init_mm_encoder_only_weight_filter=lambda *a:None
    world.loader.counter_before_loading_weights=0.;world.loader.enable_weights_track=False
    world.model.load_weights=lambda values:set(world.auto._load_module('',world.model,values))
    world.run_loader=lambda:world.loader.load_weights(world.model,NS(quantization=None,model=PRIVATE,revision=PRIVATE))
    return world


def packet(world,*,closed=True,bound=True):
    rows=[{'seq':0,'event':'START','payload':{'profile':sensor.PROFILE,'context':dict(CONTEXT)}}]
    rows.extend({'seq':number+1,**row} for number,row in enumerate(world.observer.recorded))
    return {'recordings':[{'capture':{'records':rows,'summary':{'recording_closed':closed},'process_context_verified':bound,'image_digest_verified':False},
        'session_owner_match':True,'listed_file_identity_match':True}]}


@pytest.mark.parametrize('kind',['pt','safetensors','all'])
def test_exact_generator_progress_and_callback_return_are_consumed_but_not_tensor_truth(kind):
    world=make_world();iterator=world.load(kind);assert type(iterator) is types.GeneratorType
    assert not world.reads and world.applied==[]
    before=metadata.project(packet(world));assert before['samples']==[] and not any(row['same_process_iteration_observed'] for row in before['model_candidates'])
    assert list(iterator)==['weight','other']
    assert [name for name,value in world.applied]==['weight','other'] and len(world.reads)==1
    projected=metadata.project(packet(world));assert projected['samples']
    assert any(row['same_process_iteration_observed'] and row['parameter_callback_returns_observed']==2 for row in projected['model_candidates'])
    assert all(not row[key] for row in projected['model_candidates'] for key in (*metadata.TRUTH,'scope_complete'))
    assert PRIVATE not in json.dumps(world.observer.recorded)
    for row in world.observer.recorded:
        if row['event']=='WEIGHT_ITERATION':metadata.validate_sample(row['payload'])


@pytest.mark.parametrize('mode',['late-callback','partial','unclosed'])
def test_earlier_valid_progress_survives_later_callback_error_close_or_unclosed_recording(mode):
    world=make_world(mode);iterator=world.load();assert next(iterator)=='weight'
    if mode=='late-callback':
        with pytest.raises(OSError,match=PRIVATE):next(iterator)
    elif mode=='partial':iterator.close()
    projected=metadata.project(packet(world,closed=mode!='unclosed'))
    assert any(row['same_process_iteration_observed'] and row['parameter_callback_returns_observed']==1 for row in projected['model_candidates'])
    assert not projected['tensor_storage_applied_verified']
    if mode=='unclosed':
        assert 'weight_iterator_not_observed_closed' in projected['missing'];iterator.close()
    assert PRIVATE not in json.dumps(world.observer.recorded)


def test_generator_send_throw_close_and_stop_value_preserve_the_original_protocol():
    world=make_world();original=world.modules[sensor.WEIGHTS].pt_weights_iterator
    iterator=original([PRIVATE],False,'cpu')
    assert next(iterator)==('weight',world.tensor_values['weight'])
    assert iterator.send(None)==('other',world.tensor_values['other'])
    with pytest.raises(StopIteration):next(iterator)
    iterator=original([PRIVATE],False,'cpu');next(iterator)
    error=RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError) as caught:iterator.throw(error)
    assert caught.value is error
    iterator=world.iterator('safetensors');next(iterator);iterator.close()
    assert world.closed_files==[PRIVATE]
    assert PRIVATE not in json.dumps(world.observer.recorded)


def test_unconsumed_closed_generator_is_not_reported_as_iterated():
    world=make_world();iterator=world.load();iterator.close()
    projected=metadata.project(packet(world))
    assert projected['samples']==[] and not world.reads and world.applied==[]
    assert not any(row['same_process_iteration_observed'] for row in projected['model_candidates'])


def test_same_immutable_native8_loader_call_is_retained_without_file_or_tensor_claim():
    world=make_world();assert world.run_loader() is None
    projected=metadata.project(packet(world));assert any(row['loader_call_model_link_observed'] for row in projected['model_candidates'])
    selected=[row['payload']['metadata']['weight_selection'] for row in world.observer.recorded if row['event']=='NATIVE' and 'weight_selection' in row['payload']['metadata']]
    calls={row['weight_call_sha256'] for row in selected if row['weight_call_sha256']}
    assert any(row['loader_call_sha256'] in calls for row in projected['model_candidates'])
    assert all(not row['file_read_verified'] and not row['tensor_storage_applied_verified'] for row in projected['model_candidates'])


def test_source_code_substitution_is_rejected_before_module_mutation():
    world=make_world();name=sensor.MODELS;fresh=types.ModuleType(name);fresh.__file__=world.modules[name].__file__
    cls=type('AutoWeightsLoader',(),{'__module__':name});fresh.AutoWeightsLoader=cls
    for method in sensor.TARGETS[name]['AutoWeightsLoader']:
        setattr(cls,method,types.FunctionType(world.loaded[name]['AutoWeightsLoader.'+method],fresh.__dict__,method))
    kept=cls._load_module
    def foreign(*args):yield PRIVATE
    cls._load_param=foreign
    with pytest.raises(ValueError,match='WEIGHT_ITERATION_METHOD_CODE_UNAVAILABLE'):world.observer.instrument(fresh)
    assert cls._load_module is kept and cls._load_param is foreign


def test_profile_requires_exact_base8_and_reviewed_compiler_contract():
    assert hashlib.sha256(Path(base8.__file__).read_bytes()).hexdigest()==sensor.BASE8_SHA256
    assert sensor.code_contract(base8) in set(sensor.RUNTIME_CODE_CONTRACTS.values())
    assert set(sensor.RUNTIME_POINT_CODES)=={'cpython-3.12.14','cpython-3.12.3','cpython-3.12.15'}
    assert all(set(value)==sensor.POINTS for value in sensor.RUNTIME_POINT_CODES.values())


@pytest.mark.parametrize('mutation',['truth','extra','clock','source','method','counter','duplicate','root'])
def test_closed_metadata_and_step_binding_reject_mutations(mutation):
    world=make_world();list(world.load());rows=[{'seq':index,**deepcopy(row)} for index,row in enumerate(world.observer.recorded) if row['event']=='WEIGHT_ITERATION']
    selected=next(row for row in rows if row['payload']['phase']=='STEP_YIELD' and row['payload']['point']=='AutoWeightsLoader._load_param')
    value=selected['payload']
    if mutation=='truth':value['tensor_storage_applied_verified']=True
    elif mutation=='extra':value['raw_tensor']=PRIVATE
    elif mutation=='clock':value['operation_finished_ns']=value['operation_started_ns']-1
    elif mutation=='source':value['source_sha256']='f'*64
    elif mutation=='method':value['source_method_code_sha256']='f'*64
    elif mutation=='counter':value['yield_index']+=1
    elif mutation=='duplicate':rows.insert(rows.index(selected)+1,deepcopy(selected))
    else:value['root_load_sha256']='f'*64
    with pytest.raises(ValueError):metadata.journal(rows)
