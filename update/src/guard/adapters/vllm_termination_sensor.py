"""Opt-in native23 request lifecycle body observations over immutable native8.

PEP669 local-code callbacks read the actual reviewed body frame, preserving
original decorators, results and exceptions. Observer-owned CUDA events record
only the selected source operation's main/copy streams; no global sync, tensor
copy, allocator change or native request cleanup is performed.
"""
from __future__ import annotations
import functools,hashlib,inspect,json,os,stat,sys,threading,time,types,weakref;from pathlib import Path

PROFILE='vllm-native-lifecycle-metadata/23';SCHEMA='guard-native-lifecycle-events/23'
EVENT_SCHEMA='guard-native-request-termination/1';COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39'
RUNNER='vllm.v1.worker.gpu.model_runner';STATES='vllm.v1.worker.gpu.states';ASYNC='vllm.v1.worker.gpu.async_utils'
CHAT='vllm.entrypoints.openai.chat_completion.api_router';KV='vllm.v1.worker.gpu.kv_connector';EC='vllm.v1.worker.gpu.ec_connector';QWEN='vllm.model_executor.models.qwen2';OFFLOAD='vllm.model_executor.offloader.base'
SCHED='vllm.v1.core.sched.scheduler';REQUEST='vllm.v1.request'
SOURCE_ADDITIONS={SCHED:'f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a',REQUEST:'dd7c30bf8b0894dc123a739c299c0133566f8cc075b081e45905f1c29506e69d',RUNNER:'725c47114e148ade3a26b4cc9c5f7295588fdd387afc3cb5fdb2d16513a1bb5c',STATES:'8c899b8ef63d95f450876d3f01e56772f9534938a95383a46659fddf85573fa3',ASYNC:'10875f3e7ab28fea5b6ec0dfa1a840ae69675882468cb90f9f3ca0a66db1598f',CHAT:'77f858fe0079b9dd131c26d22641c79d4ac0f2b22688d033863954a8a669d7d3',KV:'dad1aa1764f1bd0b95fe08a1b2bff6f6fffb633fd167bd65c0657fcb3d296fba',EC:'ea65e79446ce84fa0573ed4dc52129a80d80be66a08380189c25e3811a793cc7',QWEN:'6684104ba6dc40325bb1f6e632ec90633fa83a7b95883bb2181e9dc0008c3e21',OFFLOAD:'982e295299e2a282703ef6bec91897883f187f1ca31c21b6b46bda9566e09303'}
TARGETS={SCHED:{'Scheduler':('finish_requests','_free_request_blocks','_drain_deferred_frees')},RUNNER:{'GPUModelRunner':('execute_model','sample_tokens','finish_requests','_remove_request')},STATES:{'RequestState':('remove_request',)},ASYNC:{'AsyncOutput':('__init__','get_output',)}}
MAX_ROWS=2048;MAX_BYTES=2*1024**2;MAX_PENDING=32;MAX_CALLS=256;MAX_IDS=8
TOOL_ID=4;FENCE_BUDGET_NS=300_000_000_000;CODE_FILENAME='guard/adapters/vllm_termination_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.13':'0cd24629292880ee330b2e3897e74b77c1ede5d8262699d5e49e12e0e2096558','cpython-3.12.3':'bf7efdb048d8e3dcf1059500d699dd3796a0d88a2e96e2a40c88d17a6b24322a','cpython-3.12.15':'0cd24629292880ee330b2e3897e74b77c1ede5d8262699d5e49e12e0e2096558','cpython-3.12.16':'0cd24629292880ee330b2e3897e74b77c1ede5d8262699d5e49e12e0e2096558'}
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def fields(value):
    try:result=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):result={}
    return result if type(result)is dict else {}
def member(value,name,default=None):
    data=fields(value)
    if name in data:return data[name]
    cls=type(value);names=vars(cls).get('_fields')
    if (cls.__module__==RUNNER and cls.__name__=='ExecuteModelState' and isinstance(value,tuple)
            and type(names)is tuple and name in names):return tuple.__getitem__(value,names.index(name))
    descriptor=inspect.getattr_static(cls,name,None)
    if type(descriptor)is types.MemberDescriptorType:return descriptor.__get__(value,cls)
    return default

def uint(value):return value if type(value)is int and 0<=value<2**63 else None
def request_ids(value):
    if type(value)is dict:value=value.keys()
    if type(value)not in (list,tuple,set,frozenset,type({}.keys()))or len(value)>MAX_IDS:return ()
    return tuple(value)if all(type(v)is str and 0<len(v)<=512 for v in value)else()
def fingerprints(value):return tuple(sha(v.encode('utf-8','surrogatepass'))for v in request_ids(value))
def normalized(code):return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized(v)if type(v)is types.CodeType else v for v in code.co_consts))


class BoundedFences:
    """One bounded daemon per observer; retains own events and numeric facts only."""
    def __init__(self,emit,*,clock=None,wait=None,checkpoint=None):
        self.emit=emit;self.clock=clock or time.monotonic_ns;self.wait=wait or time.sleep
        self.lock=threading.RLock();self.pending={};self.inflight={};self.thread=None;self.closed=False;self.checkpoint=checkpoint;self.finish_requested=False;self.finished=threading.Event()
    def add(self,key,event,facts):
        with self.lock:
            if self.closed or len(self.pending)>=MAX_PENDING:return False
            if key in self.pending:return False
            self.pending[key]=(event,dict(facts),self.clock()+FENCE_BUDGET_NS)
            if self.thread is None:
                self.finished.clear();self.thread=threading.Thread(target=self.run,name='guard-request-fences',daemon=True)
                try:self.thread.start()
                except BaseException:self.pending.pop(key,None);self.thread=None;return False
            return True
    def keep_alive(self):
        with self.lock:
            if self.closed:return
            if self.thread is None:
                self.finished.clear();self.thread=threading.Thread(target=self.run,name="guard-request-fences",daemon=True)
                try:self.thread.start()
                except BaseException:self.thread=None
    def poll(self,final=False):
        with self.lock:items=list(self.pending.items())
        for key,(event,facts,deadline)in items:
            phase=None
            try:
                if event.query()is True:phase='FENCE_COMPLETE'
                elif final:phase='FENCE_PENDING'
                elif self.clock()>=deadline:phase='FENCE_TIMEOUT'
            except BaseException:phase='FENCE_ERROR'
            if phase:
                with self.lock:
                    if self.pending.pop(key,None)is None:continue
                    self.inflight[key]=facts
                try:self.emit(phase,facts)
                except BaseException:pass
                finally:
                    with self.lock:self.inflight.pop(key,None)
    def run(self):
        while True:
            final=self.finish_requested;self.poll(final=final)
            alive=False
            if self.checkpoint is not None:
                try:alive=self.checkpoint(final=final)is True
                except BaseException:alive=False
            with self.lock:
                if self.closed or final or not self.pending and not alive:
                    self.thread=None;self.finished.set();return
            self.wait(.01)
    def finish(self):
        if self.closed:return
        self.finish_requested=True;self.finished.clear();self.keep_alive()
        self.finished.wait(.1);self.stop()
    def stop(self):
        with self.lock:self.closed=True;self.pending.clear()


class TerminationObservation:
    def term_reset(self):
        if self.term_pid!=os.getpid():
            self.term_pid=os.getpid();self.term_lock=threading.RLock();self.term_serial=0;self.term_calls={};self.term_tracked={};self.term_owners=weakref.WeakKeyDictionary();self.term_last_snapshot=0;self.term_outputs=weakref.WeakKeyDictionary();self.term_output_requests=weakref.WeakKeyDictionary();self.term_fences=BoundedFences(self.term_fence_result,checkpoint=self.term_checkpoint)
    def term_nonce(self,kind):
        self.term_reset()
        with self.term_lock:
            self.term_serial+=1;return sha(canonical([kind,self.config['session'],self._writer().context,self.term_serial]))
    def term_payload(self,binding,phase,call,started,selected,states=(),**extra):
        return {'schema':EVENT_SCHEMA,'point':binding['point'],'phase':phase,'source_module':binding['module'],'source_sha256':binding['source'],'body_code_sha256':binding['body'],'observer_runtime':self.term_runtime,'observer_code_sha256':self.term_code,'call_sha256':call,'operation_started_ns':started,'operation_finished_ns':None if phase=='CALL'else time.monotonic_ns(),'request_sha256':list(selected),'gpu_work_selected_sha256':[],'states':list(states),'scheduler_before':[],'scheduler_after':[],'stream_kind':None,'stream_sha256':None,'stream_set_complete':False,'caller_stream_bound':False,'output_token':None,'error_kind':None,'asgi_request_sha256':None,'asgi_invocation_sha256':None,'asgi_path_sha256':None,**extra}
    def term_emit(self,binding,phase,call,started,selected,states=(),**extra):
        try:self.emit('TERMINATION',self.term_payload(binding,phase,call,started,selected,states,**extra))
        except BaseException:pass
    def term_selected(self,point,local):
        if point.startswith('Scheduler.'):
            raw=local.get('request_ids');rid=member(local.get('request'),'request_id')
            if type(rid)is str:raw=(rid,)
            elif type(raw)is str:raw=(raw,)
            elif raw is None:raw=fields(local.get('self')).get('requests')
            return fingerprints(raw)
        scheduler=local.get('scheduler_output');values=fields(scheduler)
        selected=fingerprints(values.get('num_scheduled_tokens'));finished=fingerprints(values.get('finished_req_ids'))
        if point in {'GPUModelRunner.execute_model','GPUModelRunner.finish_requests'}:return tuple(dict.fromkeys((*selected,*finished)))
        rid=local.get('req_id')
        if type(rid)is str:return fingerprints((rid,))
        if point=='GPUModelRunner.sample_tokens':return fingerprints(member(member(fields(local.get('self')).get('execute_model_state'),'input_batch'),'req_ids'))
        if point.startswith('AsyncOutput.'):
            return fingerprints(fields(local.get('model_runner_output',fields(local.get('self')).get('model_runner_output'))).get('req_ids'))
        return ()
    def term_dispatched(self,point,local):
        if point=='GPUModelRunner.execute_model':
            value=fields(local.get('scheduler_output')).get('num_scheduled_tokens')
            if type(value)is not dict or len(value)>MAX_IDS or any(type(v)is not int or v<=0 for v in value.values()):return None
            return fingerprints(value)
        if point=='GPUModelRunner.sample_tokens':return self.term_selected(point,local)
        return ()
    def term_states(self,point,local,selected):
        owner=local.get('self');data=fields(owner);mapping=fields(data.get('req_states',owner)).get('req_id_to_index');rows=[]
        complete=type(mapping)is dict and len(mapping)<=MAX_CALLS and all(type(k)is str for k in mapping)
        actual={sha(k.encode('utf-8','surrogatepass'))for k in mapping.copy()}if complete else set()
        for rid in selected:rows.append({'request_sha256':rid,'worker_slot_present':rid in actual if complete else None,'worker_mapping_complete':complete})
        return rows
    def term_scheduler(self,point,local,selected):
        if not point.startswith('Scheduler.'):return []
        mapping=fields(local.get('self')).get('requests');rows=[]
        if type(mapping)is not dict or len(mapping)>MAX_CALLS:return []
        actual={sha(k.encode('utf-8','surrogatepass')):v for k,v in mapping.copy().items()if type(k)is str}
        for rid in selected:
            request=actual.get(rid);data=fields(request)
            qualified=type(request).__module__==REQUEST and type(request).__name__=='Request'
            rows.append({'request_sha256':rid,'request_present':rid in actual,'source_request_type_verified':qualified,
                'last_sched_seq':uint(data.get('last_sched_seq'))if qualified else None,
                'num_computed_tokens':uint(data.get('num_computed_tokens'))if qualified else None,
                'num_preemptions':uint(data.get('num_preemptions'))if qualified else None,
                'num_in_flight_tokens':uint(data.get('num_in_flight_tokens'))if qualified else None,
                'streaming':data.get('streaming_queue')is not None if qualified else None})
        return rows
    def term_watch_owner(self,owner,binding,selected):
        if not selected or not binding['point'].startswith('GPUModelRunner.'):return
        with self.term_lock:
            token=self.term_owners.get(owner)
            if token is None or token not in self.term_tracked:
                if len(self.term_tracked)>=MAX_IDS:return
                token=self.term_nonce('runner');self.term_owners[owner]=token
                self.term_tracked[token]={'owner':weakref.ref(owner),'binding':binding,'requests':set(),'unresolved':set(),'expires':time.monotonic_ns()+FENCE_BUDGET_NS}
            if len(self.term_tracked[token]['requests']|set(selected))>MAX_IDS:
                self.term_prune_owner(self.term_tracked[token],owner)
            if len(self.term_tracked[token]['requests']|set(selected))>MAX_IDS:
                self.term_emit(binding,'OBSERVER_GAP',self.term_nonce('bound'),time.monotonic_ns(),selected,error_kind='observer-bound');return
            self.term_tracked[token]['requests'].update(selected)
        self.term_fences.keep_alive()
    def term_prune_owner(self,state,owner):
        if not getattr(self._writer(),'_rolling',False):return
        selected=tuple(sorted(state['requests']))
        snapshots=self.term_states(state['binding']['point'],{'self':owner},selected)
        self.term_emit(state['binding'],'STATE_SNAPSHOT',self.term_nonce('state'),time.monotonic_ns(),selected,snapshots)
        busy=set(state.get('unresolved',()))
        for call in list(self.term_calls.values()):busy.update(call[2]);busy.update(call[5])
        with self.term_fences.lock:
            facts=[entry[1]for entry in self.term_fences.pending.values()]+list(self.term_fences.inflight.values())
        for entry in facts:busy.update(entry['selected'])
        for requests in list(self.term_output_requests.values()):busy.update(requests)
        absent={row['request_sha256']for row in snapshots if row['worker_mapping_complete']is True and row['worker_slot_present']is False}
        state['requests'].difference_update(absent-busy)

    def term_checkpoint(self,final=False):
        now=time.monotonic_ns()
        if not self.active():return False
        with self.term_lock:
            if not final and now-self.term_last_snapshot<1_000_000_000:return bool(self.term_tracked)
            self.term_last_snapshot=now;alive=False
            for token,state in list(self.term_tracked.items()):
                owner=state['owner']()
                if owner is None or now>state['expires']:
                    self.term_tracked.pop(token,None)
                    if owner is not None and self.term_owners.get(owner)==token:self.term_owners.pop(owner,None)
                    continue
                alive=True;selected=tuple(sorted(state['requests']))[:MAX_IDS];call=self.term_nonce('state')
                self.term_emit(state['binding'],'STATE_SNAPSHOT',call,now,selected,self.term_states(state['binding']['point'],{'self':owner},selected))
                self.term_prune_owner(state,owner)
            return alive
    def term_stream_profile(self,owner):
        data=fields(owner);cfg=fields(data.get('vllm_config'));parallel=fields(cfg.get('parallel_config'));modelcfg=fields(data.get('model_config'));module=sys.modules.get(RUNNER);ec=sys.modules.get(EC);offload=sys.modules.get(OFFLOAD)
        required=('speculator','pp_handler','pcp_manager','aux_output_connector','ubatch_runner')
        return bool(all(k in data and data[k]is None for k in required)
            and modelcfg.get('enforce_eager')is True
            and all(type(parallel.get(k))is int and parallel[k]==1 for k in ('tensor_parallel_size','pipeline_parallel_size','data_parallel_size'))
            and parallel.get('enable_eplb')is False
            and all(k in cfg and cfg[k]is None for k in ('speculative_config','kv_transfer_config','ec_transfer_config','lora_config'))
            and data.get('kv_connector')is vars(module).get('NO_OP_KV_CONNECTOR')and data.get('kv_connector')is not None
            and type(data.get('ec_connector'))is vars(ec).get('ECConnector')
            and type(vars(offload).get('_instance'))is vars(offload).get('NoopOffloader')
            and type(data.get('model')).__module__==QWEN and type(data.get('model')).__name__=='Qwen2ForCausalLM'
            and all(self.modules.get(name,{}).get('source_sha256')==SOURCE_ADDITIONS[name]for name in (RUNNER,ASYNC,KV,EC,QWEN,OFFLOAD)))
    def term_current_stream(self,owner):
        try:
            import torch
            stream=torch.cuda.current_stream(fields(owner).get('device'))
            return (int(stream.device.index),int(stream.cuda_stream))
        except BaseException:return None
    def term_record_fences(self,binding,call,started,selected,owner,output_token=None,caller_stream=None):
        if not selected:return
        data=fields(owner)
        try:
            qualified=self.term_stream_profile(owner)
            import torch
            current=torch.cuda.current_stream(data.get('device'));main=data.get('main_stream');copy=data.get('output_copy_stream')
            if caller_stream!=(int(current.device.index),int(current.cuda_stream)):raise ValueError()
            if main is not None and (current.cuda_stream!=main.cuda_stream or current.device!=main.device):raise ValueError()
            streams=(('main',current),('copy',copy))
            if copy is None:raise ValueError()
            for kind,stream in streams:
                event=torch.cuda.Event();event.record(stream)
                token=self.term_nonce('stream');stream_id=sha(canonical([self.config['session'],self._writer().context,int(stream.device.index),int(stream.cuda_stream)]))
                facts={'binding':binding,'call':call,'started':started,'selected':selected,'stream_kind':kind,'stream_sha256':stream_id,'stream_set_complete':qualified,'caller_stream_bound':True,'output_token':output_token}
                self.term_emit(binding,'FENCE_RECORDED',call,started,selected,stream_kind=kind,stream_sha256=stream_id,stream_set_complete=qualified,caller_stream_bound=True,output_token=output_token,gpu_work_selected_sha256=list(selected))
                if not self.term_fences.add(token,event,facts):
                    self.term_mark_unresolved(selected);self.term_emit(binding,'FENCE_ERROR',call,started,selected,stream_kind=kind,stream_sha256=stream_id,error_kind='observer-bound')
        except BaseException:
            self.term_mark_unresolved(selected);self.term_emit(binding,'FENCE_ERROR',call,started,selected,error_kind='stream-unavailable')
    def term_mark_unresolved(self,selected):
        with self.term_lock:
            for state in self.term_tracked.values():
                state.setdefault('unresolved',set()).update(set(selected)&state['requests'])
    def term_fence_result(self,phase,facts):
        if phase!='FENCE_COMPLETE':self.term_mark_unresolved(facts['selected'])
        self.term_emit(facts['binding'],phase,facts['call'],facts['started'],facts['selected'],stream_kind=facts['stream_kind'],stream_sha256=facts['stream_sha256'],stream_set_complete=facts['stream_set_complete'],caller_stream_bound=facts['caller_stream_bound'],output_token=facts['output_token'],gpu_work_selected_sha256=list(facts['selected']))
    def term_output_released(self,binding,call,started,selected,token):
        self.term_emit(binding,'OUTPUT_RELEASED',call,started,selected,output_token=token)
    def term_callback(self,event,code,result=None):
        try:
            frame=sys._getframe(2)
            if frame.f_code is not code:return
            self.term_reset();binding=self.term_codes.get(code)
            if binding is None or not self.active():return
            key=(threading.get_ident(),id(frame));local=frame.f_locals
            module=sys.modules.get(binding['module']);cls=vars(module).get(binding['point'].split('.')[0])
            if type(local.get('self'))is not cls:return
            if event=='CALL':
                if len(self.term_calls)>=MAX_CALLS:
                    self.term_emit(binding,'OBSERVER_GAP',self.term_nonce('bound'),time.monotonic_ns(),(),error_kind='observer-bound');return
                selected=self.term_selected(binding['point'],local);work=self.term_dispatched(binding['point'],local);call=self.term_nonce('body');started=time.monotonic_ns()
                if work is None:
                    self.term_emit(binding,'OBSERVER_GAP',call,started,selected,error_kind='observer-bound');work=()
                self.term_calls[key]=(call,started,selected,self.term_current_stream(local.get('self'))if binding['point']in {'GPUModelRunner.execute_model','GPUModelRunner.sample_tokens'}and selected else None,self.term_scheduler(binding['point'],local,selected),work)
                self.term_watch_owner(local.get('self'),binding,selected)
                if selected:self.term_emit(binding,'CALL',call,started,selected,self.term_states(binding['point'],local,selected),output_token=self.term_outputs.get(local.get('self'))if binding['point'].startswith('AsyncOutput.')else None,scheduler_before=self.term_calls[key][4],gpu_work_selected_sha256=list(work))
                return
            state=self.term_calls.get(key)
            if state is None:return
            try:
                call,started,selected,caller_stream,scheduler_before,work=state
                if not selected:return
                output_token=self.term_outputs.get(local.get('self'))if binding['point'].startswith('AsyncOutput.')else None
                self.term_emit(binding,event,call,started,selected,self.term_states(binding['point'],local,selected),error_kind='native-exception'if event=='RAISE'else None,output_token=output_token,scheduler_before=scheduler_before,scheduler_after=self.term_scheduler(binding['point'],local,selected),gpu_work_selected_sha256=list(work))
                if event in {'RETURN','RAISE'}and binding['point']in {'GPUModelRunner.execute_model','GPUModelRunner.sample_tokens'}:self.term_record_fences(binding,call,started,work,local.get('self'),self.term_outputs.get(result)if type(result)is vars(sys.modules[ASYNC]).get('AsyncOutput')else None,caller_stream=caller_stream)
                if event=='RETURN'and binding['point']=='AsyncOutput.__init__':
                    if len(self.term_outputs)>=MAX_CALLS:
                        self.term_emit(binding,'OBSERVER_GAP',call,started,selected,error_kind='observer-bound');return
                    owner=local.get('self');token=self.term_nonce('output')
                    self.term_outputs[owner]=token
                    self.term_output_requests[owner]=tuple(selected)
                    weakref.finalize(owner,self.term_output_released,binding,call,started,selected,token)
                    self.term_emit(binding,'OUTPUT_CREATED',call,started,selected,output_token=token)
            finally:
                self.term_calls.pop(key,None)
        except BaseException:
            # Observation failures never become native results or exceptions.
            try:
                if binding is not None:self.term_emit(binding,'OBSERVER_GAP',sha(b'observer-gap'),time.monotonic_ns(),(),error_kind='observer-bound')
            except BaseException:pass


def checked_codes(module,base):
    name=module.__name__;path=vars(module).get('__file__');before=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode)or not 0<before.st_size<=262144:raise ValueError('TERMINATION_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        a=os.fstat(fd);raw=os.read(fd,262145);z=os.fstat(fd);after=os.stat(path,follow_symlinks=False)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_uid,s.st_mode,s.st_mtime_ns,s.st_ctime_ns)
        if len(raw)!=a.st_size or len({identity(v)for v in (before,a,z,after)})!=1 or sha(raw)!=SOURCE_ADDITIONS[name]:raise ValueError('TERMINATION_SOURCE_UNAVAILABLE')
    finally:os.close(fd)
    result={}
    def visit(code):
        result[code.co_qualname]=base.code_digest(code)
        for value in code.co_consts:
            if type(value)is types.CodeType:visit(value)
    visit(compile(raw,path,'exec',dont_inherit=True,optimize=0));return result


async def observed_asgi(self,observer,scope,receive,send):
    if not observer.active():return await self.app(scope,receive,send)
    if scope.get('path')not in {'/v1/completions','/v1/chat/completions'}:return await self.app(scope,receive,send)
    try:
        context=observer.route_context();request=context.get('request')if context else None
        started=time.monotonic_ns();call=observer.term_nonce('transport')
        with open(__file__,'rb')as stream:source=sha(stream.read())
        binding={'module':'observer.asgi','point':'ASGI.request','source':source,'body':observer.term_base.code_digest(normalized(observed_asgi.__code__))}
        token=observer.term_base._ASGI_REQUEST.set((os.getpid(),request))
    except BaseException:return await self.app(scope,receive,send)
    def emit(phase,more=None):
        try:
            observer.emit('ASGI',{'request_sha256':request,'phase':phase,'more_body':more})
            observer.term_emit(binding,'ASGI_'+phase,call,started,(),asgi_request_sha256=request,asgi_invocation_sha256=context.get('invocation')if context else None,asgi_path_sha256=context.get('path')if context else None)
        except BaseException:pass
    async def received():
        value=await receive()
        if type(value)is dict and value.get('type')=='http.disconnect':emit('DISCONNECT_RECEIVED')
        return value
    async def sent(value):
        result=await send(value)
        if type(value)is dict and value.get('type')=='http.response.body':emit('BODY_SEND_RETURN',value.get('more_body',False)if type(value.get('more_body',False))is bool else None)
        return result
    good=False
    try:result=await self.app(scope,received,sent);good=True;return result
    finally:
        try:emit('APP_RETURN'if good else'APP_RAISE')
        finally:
            try:observer.term_base._ASGI_REQUEST.reset(token)
            except BaseException:pass


class CheckpointWriterMixin:
    """A healthy writer's observed prefix; request completion is adjudicated separately.

    The marker is part of the ordinary hash chain. It asserts no fsync durability,
    request result, absence of future work, or observer coverage of other processes.
    """
    def __init__(self, *args, **kwargs):
        self._rolling = os.environ.get("BYUL_NATIVE_ROLLING") == "1"
        self._segment_generation = 0
        self._segment_files = []
        self._segment_header = None
        self._segment_modules = {}
        super().__init__(*args, **kwargs)

    def _ensure_segment_capacity(self):
        if self._rolling and not self.closed:
            rows = getattr(self, "_rolling_max_rows", MAX_ROWS)
            size = getattr(self, "_rolling_max_bytes", MAX_BYTES)
            if self.seq >= rows - 4 or self.bytes > size - 32768:
                try:
                    self._rotate_segment()
                except Exception:
                    self.closed = True
                    if self.fd is not None:
                        os.close(self.fd)
                        self.fd = None
        return not self.closed and self.fd is not None

    def write(self, event, payload):
        with self.lock:
            if event != "END" and not self._ensure_segment_capacity():
                return
            if event == "START" and self._segment_header is None:
                self._segment_header = dict(payload)
            if event == "MODULE":
                module = payload.get("module")
                if isinstance(module, str) and (module in self._segment_modules or len(self._segment_modules) < 128):
                    self._segment_modules[module] = dict(payload)
                elif self._rolling:
                    self.close("BOUND_REACHED")
                    return
            return super().write(event, payload)

    def _rotate_segment(self):
        # Native tracking/fences remain in their Observer. Only the bounded
        # output shard changes; original bytes are never rewritten or repaired.
        directory = self._checkpoint_directory(self.config["output_dir"])
        try:
            info = os.fstat(directory)
            if info.st_uid != self.context["uid"] or info.st_mode & 0o077:
                raise ValueError("NATIVE_SEGMENT_DIRECTORY_UNAVAILABLE")
            name = f"pid-{self.context['pid']}-{self.context['start_ticks']}"
            current = name + ".jsonl"
            segment = name + f"-segment-{self._segment_generation}.jsonl"
            try:
                os.stat(segment, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("NATIVE_SEGMENT_ALREADY_EXISTS")
            self._append("END", {"reason": "SEGMENT_ROLLOVER"})
            os.close(self.fd)
            self.fd = None
            descriptor = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != self.context["uid"]
                        or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_size != self.bytes):
                    raise ValueError("NATIVE_SEGMENT_FILE_UNAVAILABLE")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    raw = source.read(getattr(self, "_rolling_max_bytes", MAX_BYTES) + 1)
                if len(raw) != self.bytes:
                    raise ValueError("NATIVE_SEGMENT_FILE_BOUND")
            finally:
                os.close(descriptor)
            # link is atomic no-replace: a raced archive destination is never
            # overwritten. Verify the linked inode before removing its alias.
            os.link(current, segment, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            linked = os.stat(segment, dir_fd=directory, follow_symlinks=False)
            current_info = os.stat(current, dir_fd=directory, follow_symlinks=False)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(linked) != identity(opened) or identity(current_info) != identity(opened):
                raise ValueError("NATIVE_SEGMENT_FILE_CHANGED")
            os.unlink(current, dir_fd=directory)
            self._segment_files.append(segment)
            previous_generation = self._segment_generation
            self._segment_generation += 1
            self.fd = os.open(current, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            self.seq, self.bytes, self.previous = 0, 0, "0" * 64
            self._append("START", self._segment_header)
            self._append("SEGMENT", {"schema": "guard-native-segment/1", "session": self.config["session"],
                "segment": self._segment_generation, "previous_segment": previous_generation,
                "previous_sha256": hashlib.sha256(raw).hexdigest(), "previous_bytes": len(raw)})
            for payload in self._segment_modules.values():
                self._append("MODULE", payload)
            if self.seq >= getattr(self, "_rolling_max_rows", MAX_ROWS) - 4 or self.bytes > getattr(self, "_rolling_max_bytes", MAX_BYTES) - 32768:
                self.close("BOUND_REACHED")
                return
            while len(self._segment_files) > 2:
                old = self._segment_files[0]
                archived = os.stat(old, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(archived.st_mode) or archived.st_uid != self.context["uid"] or stat.S_IMODE(archived.st_mode) != 0o600:
                    raise ValueError("NATIVE_SEGMENT_RETENTION_UNAVAILABLE")
                os.unlink(old, dir_fd=directory)
                self._segment_files.pop(0)
        finally:
            os.close(directory)

    def checkpoint(self):
        with self.lock:
            if not self._ensure_segment_capacity():
                return False
            sequence = self.seq
            self.write("CHECKPOINT", {
                "schema": "guard-native-checkpoint-ack/1",
                "session": self.config["session"],
                "prefix_seq": sequence,
                "prefix_sha256": self.previous,
            })
            # Writer.write closes silently on a bound or failed append. Such a
            # writer cannot acknowledge a new cutoff, even if it wrote an END.
            return not self.closed and self.fd is not None and self.seq == sequence + 1

    def _watch(self):
        next_checkpoint = time.monotonic() + 1
        try:
            while not self.closed:
                if Path(self.config["control_path"]).exists():
                    self.close("SEALED")
                    return
                if time.monotonic() >= next_checkpoint:
                    if not self.checkpoint():
                        return
                    next_checkpoint = time.monotonic() + 1
                threading.Event().wait(0.1)
        except Exception:
            try:
                self.close("CONTROL_UNAVAILABLE")
            except Exception:
                pass


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if sys.implementation.name!='cpython'or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected:raise ValueError('TERMINATION_OBSERVER_CODE_UNAVAILABLE')
    if not hasattr(sys,'monitoring')or sys.monitoring.get_tool(TOOL_ID)is not None:raise ValueError('TERMINATION_MONITOR_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES;base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    original_writer=base.Writer
    class TermWriter(CheckpointWriterMixin,original_writer):
        _checkpoint_directory = staticmethod(base._open_directory)
        def close(self,reason):
            observer=base._OBSERVER
            if observer is not None and getattr(observer,'writer',None)is self:
                try:observer.term_fences.finish()
                except BaseException:pass
            return super().close(reason)
    base.Writer=TermWriter
    class TermObserver(TerminationObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.term_base=base;self.term_runtime=runtime;self.term_code=expected;self.term_pid=os.getpid();self.term_lock=threading.RLock();self.term_serial=0;self.term_calls={};self.term_codes={};self.term_tracked={};self.term_owners=weakref.WeakKeyDictionary();self.term_last_snapshot=0;self.term_outputs=weakref.WeakKeyDictionary();self.term_output_requests=weakref.WeakKeyDictionary();self.term_fences=BoundedFences(self.term_fence_result,checkpoint=self.term_checkpoint)
            monitor=sys.monitoring;monitor.use_tool_id(TOOL_ID,'byul-lifecycle23')
            def start(code,offset):self.term_callback('CALL',code)
            def end(code,offset,result):self.term_callback('RETURN',code,result)
            def error(code,offset,exception):self.term_callback('RAISE',code)
            try:
                monitor.register_callback(TOOL_ID,monitor.events.PY_START,start);monitor.register_callback(TOOL_ID,monitor.events.PY_RETURN,end);monitor.register_callback(TOOL_ID,monitor.events.PY_UNWIND,error);monitor.set_events(TOOL_ID,monitor.events.PY_UNWIND)
            except BaseException:
                monitor.set_events(TOOL_ID,0);monitor.free_tool_id(TOOL_ID);raise
        def instrument(self,module):
            super().instrument(module)
            if module.__name__ not in TARGETS:return
            codes=checked_codes(module,base);changes=[]
            for cls,methods in TARGETS[module.__name__].items():
                owner=vars(module).get(cls)
                if not isinstance(owner,type):raise ValueError('TERMINATION_CLASS_UNAVAILABLE')
                for name in methods:
                    value=vars(owner).get(name);value=value.__func__ if isinstance(value,staticmethod)else value
                    body=inspect.unwrap(value)
                    if type(body)is not types.FunctionType or body.__globals__ is not vars(module)or base.code_digest(body.__code__)!=codes.get(cls+'.'+name):raise ValueError('TERMINATION_BODY_UNAVAILABLE')
                    changes.append((body.__code__,{'module':module.__name__,'point':cls+'.'+name,'source':SOURCE_ADDITIONS[module.__name__],'body':codes[cls+'.'+name]}))
            for code,binding in changes:
                self.term_codes[code]=binding;sys.monitoring.set_local_events(TOOL_ID,code,sys.monitoring.events.PY_START|sys.monitoring.events.PY_RETURN)
        def close(self):
            try:
                self.term_fences.stop()
                if sys.monitoring.get_tool(TOOL_ID)=='byul-lifecycle23':
                    sys.monitoring.set_events(TOOL_ID,0)
                    for code in self.term_codes:sys.monitoring.set_local_events(TOOL_ID,code,0)
                    sys.monitoring.free_tool_id(TOOL_ID)
            except BaseException:pass
            finally:super().close()
    base.Observer=TermObserver;base.Middleware._observed=observed_asgi;return base


def code_contract(base):
    functions=(sha,canonical,fields,member,uint,request_ids,fingerprints,normalized,checked_codes,observed_asgi,configure,
        *[v for k,v in vars(BoundedFences).items()if type(v)is types.FunctionType],
        *[v for k,v in vars(TerminationObservation).items()if type(v)is types.FunctionType],
        *[v for k,v in vars(CheckpointWriterMixin).items()if type(v)is types.FunctionType])
    return sha(''.join(base.code_digest(normalized(fn.__code__))for fn in functions).encode())


def _private_base():
    path='/guard-lifecycle/guard_lifecycle_base.py';fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);raw=os.read(fd,262145);after=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode)or len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)or sha(raw)!=BASE8_SHA256:raise ValueError('TERMINATION_BASE_UNAVAILABLE')
    finally:os.close(fd)
    base=types.ModuleType('_guard_termination_base');base.__file__=path;exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(base));return configure(base)

def install(path=None):
    global _BASE
    if _BASE is not None:return False
    try:
        base=_private_base()
        if not base.install(path,open_sink=True):return False
        _BASE=base;return True
    except BaseException:return False
class Middleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):return await _BASE.Middleware(self.app)(scope,receive,send)if _BASE is not None else await self.app(scope,receive,send)
def seal(path='/guard-lifecycle/settings.json'):return (_BASE if _BASE is not None else _private_base()).seal(path)
