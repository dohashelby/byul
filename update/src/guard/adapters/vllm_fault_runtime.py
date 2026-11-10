"""Bounded LAB control mechanics for source-qualified native24 call sites.

This module does not import an engine, attach hooks, send signals or qualify a
native judgment. The native observer supplies an already verified operation and
records these mechanical receipts in its own source recording. A private file
alone remains an actuation selection, never native execution evidence.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
import threading

ASSIGNMENT_FIELDS=frozenset({'schema','nonce','fault_kind','rank','external_request_sha256',
    'native_request_sha256','session','target_sha256','asgi_invocation_sha256',
    'source_module','source_sha256','frontend_context'})
INPUT='vllm.v1.engine.input_processor'
INPUT_SHA='d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b'
MAX_CONTROL_ENTRIES=32
MAX_ASSIGNMENTS=8


class NativeLabFault(RuntimeError):
    """Intentional LAB exception raised inside the reviewed worker RPC try."""


def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def validate_assignment(control,value,*,nonce,session,target_sha256):
    try:
        if(type(value)is not dict or set(value)!=ASSIGNMENT_FIELDS
            or value['schema']!=control.ASSIGNED_SCHEMA or value['nonce']!=nonce
            or not control.fingerprint(nonce,32)or value['session']!=session
            or not control.fingerprint(session,32)or value['target_sha256']!=target_sha256
            or not control.fingerprint(target_sha256)or value['fault_kind']not in control.KINDS
            or type(value['rank'])is not int or not 0<=value['rank']<=63
            or value['source_module']!=INPUT or value['source_sha256']!=INPUT_SHA):raise ValueError
        for key in ('native_request_sha256','external_request_sha256','asgi_invocation_sha256'):
            if not control.fingerprint(value[key]):raise ValueError
        context=value['frontend_context']
        if type(context)is not dict or set(context)!={'pid','start_ticks','pid_namespace','clock_context'}:raise ValueError
        for key in ('pid','start_ticks','pid_namespace'):
            if type(context[key])is not int or not 0<context[key]<2**64:raise ValueError
        control.clock_context(context['clock_context'])
    except (ValueError,KeyError,TypeError):raise ValueError('FAULT_SOURCE_ASSIGNMENT_INVALID')from None
    return value


class SourceControl:
    """One observer/process, at most eight assignments and one live barrier."""
    def __init__(self,control,*,directory,session,target_sha256,emit,context,
                 observer_runtime,observer_code_sha256,clock=None,wait=None):
        if not control.fingerprint(session,32)or not control.fingerprint(target_sha256):raise ValueError('FAULT_SOURCE_SCOPE_INVALID')
        if not control.fingerprint(observer_code_sha256):raise ValueError('FAULT_SOURCE_CODE_INVALID')
        fd=control._directory(directory);os.close(fd)
        self.control=control;self.directory=directory;self.session=session;self.target=target_sha256
        self.emit=emit;self.context=context;self.runtime=observer_runtime;self.code=observer_code_sha256
        self.clock=clock or time.monotonic_ns;self.wait=wait or time.sleep
        self.pid=os.getpid();self.assigned=set();self.consumed=set();self.barrier_lock=threading.Lock()

    def reset(self):
        if self.pid!=os.getpid():
            self.pid=os.getpid();self.assigned=set();self.consumed=set();self.barrier_lock=threading.Lock()

    def entries(self,suffix):
        root=self.control._directory(self.directory)
        try:names=os.listdir(root)
        finally:os.close(root)
        if len(names)>MAX_CONTROL_ENTRIES:raise ValueError('FAULT_SOURCE_DIRECTORY_BOUND')
        result=[v[:-len(suffix)]for v in names if v.endswith(suffix)]
        if len(result)>MAX_ASSIGNMENTS or any(not self.control.fingerprint(v,32)for v in result):raise ValueError('FAULT_SOURCE_SELECTION_BOUND')
        return sorted(result)

    def assign(self,external_sha,native_sha,invocation_sha):
        """Called only after exact InputProcessor assignment RETURN/ASGI bind."""
        self.reset()
        if not all(self.control.fingerprint(v)for v in (external_sha,native_sha,invocation_sha)):raise ValueError('FAULT_SOURCE_ASSIGNMENT_INVALID')
        selected=[]
        for nonce in self.entries('.arm.json'):
            arm=self.control.load_arm(self.directory,nonce,session=self.session,target_sha256=self.target)['receipt']
            if arm['external_request_sha256']==external_sha:selected.append(arm)
        if not selected:return None
        if len(selected)!=1 or len(self.assigned)>=MAX_ASSIGNMENTS:raise ValueError('FAULT_SOURCE_ASSIGNMENT_AMBIGUOUS')
        arm=selected[0];nonce=arm['nonce']
        if nonce in self.assigned:raise ValueError('FAULT_SOURCE_ASSIGNMENT_REPEATED')
        value={'schema':self.control.ASSIGNED_SCHEMA,'nonce':nonce,'fault_kind':arm['fault_kind'],
            'rank':arm['rank'],'external_request_sha256':external_sha,'native_request_sha256':native_sha,
            'session':self.session,'target_sha256':self.target,'asgi_invocation_sha256':invocation_sha,
            'source_module':INPUT,'source_sha256':INPUT_SHA,'frontend_context':self.context()}
        validate_assignment(self.control,value,nonce=nonce,session=self.session,target_sha256=self.target)
        receipt=self.control.publish_control_receipt(self.directory,nonce,'assigned',value)
        self.assigned.add(nonce);self.emit('ASSIGNED',value,receipt['receipt_sha256']);return value

    def select(self,native_ids,rank,point):
        self.reset()
        if(type(native_ids)not in (tuple,list)or not 0<len(native_ids)<=8
            or len(set(native_ids))!=len(native_ids)or any(not self.control.fingerprint(v)for v in native_ids)
            or type(rank)is not int or rank!=0 or point not in self.control.READY_POINTS):return None
        matches=[]
        for nonce in self.entries('.assigned.json'):
            if nonce in self.consumed:continue
            value,_=self.control._read_owned(self.directory,nonce+'.assigned.json')
            assignment=validate_assignment(self.control,value,nonce=nonce,session=self.session,target_sha256=self.target)
            if assignment['native_request_sha256']not in native_ids or assignment['rank']!=rank:continue
            if (point=='WorkerProc.enqueue_output')!=(assignment['fault_kind']=='communication-stall'):continue
            arm=self.control.load_arm(self.directory,nonce,session=self.session,target_sha256=self.target)['receipt']
            if any(arm[k]!=assignment[k]for k in ('fault_kind','rank','external_request_sha256')):raise ValueError('FAULT_SOURCE_ARM_CHANGED')
            matches.append((assignment,arm))
        if len(matches)>1:raise ValueError('FAULT_SOURCE_OPERATION_AMBIGUOUS')
        return matches[0]if matches else None

    def action(self,nonce,action):
        try:return self.control.load_action(self.directory,nonce,session=self.session,target_sha256=self.target,action=action)['receipt_sha256']
        except FileNotFoundError:return None

    def hold(self,selected,*,point,call_sha256,operation_started_ns):
        """Hold only the chosen source call. Observer errors never raise faults.

        The return value describes the intentional LAB activation. The caller
        raises NativeLabFault only for a successfully source-bound exception
        activation inside the original worker RPC try/catch region.
        """
        if selected is None:return None
        self.reset()
        assignment,arm=selected;nonce=assignment['nonce']
        if nonce in self.consumed:raise ValueError('FAULT_SOURCE_OPERATION_REPEATED')
        if not self.barrier_lock.acquire(blocking=False):raise ValueError('FAULT_SOURCE_BARRIER_BUSY')
        self.consumed.add(nonce)
        try:
            context=self.context();now=self.clock();remaining=arm['expires_unix_ns']-time.time_ns()
            if remaining<=0:raise ValueError('FAULT_SOURCE_ARM_EXPIRED')
            deadline=now+min(arm['hold_timeout_ms']*1_000_000,remaining)
            ready={'schema':self.control.READY_SCHEMA,'nonce':nonce,'fault_kind':arm['fault_kind'],'rank':arm['rank'],
                'external_request_sha256':assignment['external_request_sha256'],'native_request_sha256':assignment['native_request_sha256'],
                'session':self.session,'target_sha256':self.target,'pid':context['pid'],'start_ticks':context['start_ticks'],
                'pid_namespace':context['pid_namespace'],'call_sha256':call_sha256,'point':point,
                'clock_context':context['clock_context'],'operation_started_ns':operation_started_ns,'ready_ns':now,
                'barrier_deadline_ns':deadline,'active':True,'bounded_barrier':True,'expires_unix_ns':arm['expires_unix_ns'],
                'observer_runtime':self.runtime,'observer_code_sha256':self.code,'source_module':self.control.MP,'source_sha256':self.control.MP_SHA256}
            self.control.validate_ready(ready,nonce=nonce,session=self.session,target_sha256=self.target,
                expected_external_sha=assignment['external_request_sha256'],rank=arm['rank'],point=point)
            receipt=self.control.publish_control_receipt(self.directory,nonce,'ready',ready)
            self.emit('READY',ready,receipt['receipt_sha256']);activated=False
            while self.clock()<deadline:
                if self.action(nonce,'release')is not None:
                    self.emit('RELEASE',ready,None);return {'activated':activated,'timed_out':self.clock()>=deadline,'kind':arm['fault_kind']}
                if self.clock()>=deadline:break
                if arm['fault_kind']in {'exception','communication-stall'}and not activated:
                    activation=self.action(nonce,'activate')
                    if self.clock()>=deadline:break
                    if activation is not None:
                        self.emit('ACTIVATE',ready,activation)
                        if self.clock()>=deadline:break
                        activated=True
                        if arm['fault_kind']=='exception':return {'activated':True,'timed_out':False,'kind':'exception'}
                self.wait(.01)
            self.emit('EXPIRED',ready,None);return {'activated':activated,'timed_out':True,'kind':arm['fault_kind']}
        finally:self.barrier_lock.release()
