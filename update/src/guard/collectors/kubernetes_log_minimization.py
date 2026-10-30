"""선정 Pod stdout의 시험 요청 연결 predicate만 반환하는 B 전용 수집기."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time
import uuid
from urllib.parse import urlunsplit

from guard.adapters import kubernetes_response_error as response_adapter
from guard.automation_policy import string_list
from guard.executors.content_common import leaves
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, validate_target
from guard.executors.log_analysis_contract import STRICT_JSON_PROFILE, analyze_log_captures, events
from guard.models import DataOrigin, Ref, canonical_json
from guard.runtime import kubernetes_http
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime.kubernetes_binding import verify_owned_pod

SCHEMA='guard-log-minimization-capture/1'
PROFILE='kubernetes-owned-pod-log-minimization/1'
MAX_LOG_BYTES=65536
MAX_LOG_LINES=128
POLICY_KEYS=('allowed_log_fields','forbidden_log_fields','forbidden_marker_contexts','required_sources','required_branches')
_BRANCHES={'normal','error','exception_dump','debug','trace'}
_CONTEXTS={'caller_input','private_system','error_input'}


def _digest(value):return hashlib.sha256(canonical_json(value)).hexdigest()


def _policy_ref(value):
    raw=value.model_dump(mode='json') if isinstance(value,Ref) else value
    if (type(raw) is not dict or set(raw)!={'kind','id','revision','sha256'} or raw['kind']!='POL'
            or type(raw['id']) is not str or not raw['id'].strip() or raw['id']!=raw['id'].strip()
            or any(ord(c)<32 for c in raw['id']) or type(raw['revision']) is not int or raw['revision']<1
            or type(raw['sha256']) is not str or re.fullmatch('[0-9a-f]{64}',raw['sha256']) is None):
        raise ValueError('LOG_MIN_POLICY_REF_INVALID')
    return deepcopy(raw)


def _validate(env,target,values,source_id,policy_ref,binary,duration,timeout,injected):
    if injected and env.data_origin!=DataOrigin.EXAMPLE:raise ValueError('LOG_MIN_INJECTION_REQUIRES_EXAMPLE')
    if type(values) is not dict or set(values)!=set(POLICY_KEYS):raise ValueError('LOG_MIN_POLICY_INVALID')
    for key in POLICY_KEYS:string_list(values,key,empty=key in {'forbidden_log_fields','forbidden_marker_contexts'})
    if (set(values['forbidden_marker_contexts'])-_CONTEXTS
            or not {'normal','error','exception_dump'}<=set(values['required_branches'])
            or set(values['required_branches'])-_BRANCHES):raise ValueError('LOG_MIN_POLICY_SCOPE_INVALID')
    if (type(source_id) is not str or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',source_id) is None
            or source_id not in values['required_sources']):raise ValueError('LOG_MIN_SOURCE_INVALID')
    ref=_policy_ref(policy_ref)
    if type(binary) is not str or not binary or binary.startswith('-') or any(c.isspace() or ord(c)<32 for c in binary):
        raise ValueError('LOG_MIN_BINARY_INVALID')
    for value,maximum in ((duration,300),(timeout,5)):
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=maximum:raise ValueError('LOG_MIN_BUDGET_INVALID')
    if env.engine.name!='vllm' or env.engine.version!='0.31.0':raise ValueError('LOG_MIN_ENGINE_UNSUPPORTED')
    # 공개 생성자는 I/O 없이 정확 ENV·모델 alias·Pod/probe 소유 대상을 검사한다.
    try:
        transport=kubernetes_http.KubernetesHTTP(target,env,duration=duration,binary=binary,profile=kubernetes_http.REQUEST_BINDING_PROFILE)
    except kubernetes_http.KubernetesBindingError:
        raise ValueError('LOG_MIN_HTTP_TARGET_INVALID') from None
    capture_target={'schema':'guard-kubernetes-lifecycle-target/1','run_id':target['kubernetes']['run_id'],
        'environment_ref':env.ref().model_dump(mode='json'),'lab':target['lab'],
        'kubernetes':{'context':target['kubernetes']['context'],'engine':deepcopy(target['kubernetes']['engine'])}}
    return ref,capture_target,validate_target(capture_target,env),transport.api_model


def _pipe_complete(value,limit):
    pipe=value.get('capture',{})
    return (type(value.get('returncode')) is int and type(value.get('stdout_bytes')) is bytes
        and type(value.get('stderr_bytes')) is bytes and len(value['stdout_bytes'])<=limit and len(value['stderr_bytes'])<=4096
        and pipe.get('complete') is True and pipe.get('bounded_at_source') is True and pipe.get('timed_out') is False
        and pipe.get('truncated') is False and pipe.get('pipes_closed') is True and pipe.get('read_errors')==[])


def _normal_response(value):
    if (value.get('outcome')!=200 or type(value.get('body')) is not str or value.get('capture_complete') is not True
            or value.get('body_truncated') is not False or value.get('utf8_valid') is not True or value.get('eof') is not True):return None
    raw=value['body'].encode('utf-8')
    if len(raw)>131072 or type(value.get('body_bytes')) is not int or value['body_bytes']!=len(raw):return None
    try:
        body=strict_json(raw,limit=131072);tokens=body['tokens']
        return (type(tokens) is list and bool(tokens) and len(tokens)<=262144
            and all(type(token) is int and token>=0 for token in tokens)
            and type(body.get('count')) is int and body['count']==len(tokens)
            and type(body.get('max_model_len')) is int and body['max_model_len']>0)
    except (TypeError,ValueError,KeyError):return None


def _predicates(capture,values,marks,correlation,source_id):
    actual=analyze_log_captures([capture],values,marks,correlation,profile=STRICT_JSON_PROFILE)
    field_prefix=source_id+': nonpermitted log field '
    field_count=sum(item.startswith(field_prefix) for item in actual['log_violations'])
    marker_count=sum(item==source_id+': forbidden error_input marker present' for item in actual['log_violations'])
    parsed,structured=events(capture['text'],profile=STRICT_JSON_PROFILE)
    linked=0
    for row in parsed:
        fields=leaves(row['event'])
        identifiers=[item for key,items in fields.items() if '["' not in key and (key=='request_id' or key.endswith('.request_id')) for item in items]
        if any(value in identifiers for value in correlation.values()):linked+=1
    return {'field_violation_count':field_count,'forbidden_error_input_count':marker_count,
        'marker_contexts_seen':['error_input'] if 'error_input' in actual['log_marker_contexts_seen'] else [],
        'branches_tested':actual['log_sources_tested'].get(source_id,[]),'event_count':len(parsed),
        'trial_linked_event_count':linked,'structured':structured}


def capture_log_minimization(env,http_target,policy_values,*,source_id,expected_policy_ref,workdir,
        binary='kubectl',duration=60,timeout=5,_runner=None,_transport_factory=None,_reader_factory=None)->dict:
    """원문·내용 해시·동적 필드명 없이 부분 관측과 확인된 predicate를 보존한다."""
    target,values=deepcopy(http_target),deepcopy(policy_values)
    policy_ref,capture_target,binding,alias=_validate(env,target,values,source_id,expected_policy_ref,binary,duration,timeout,
        any(hook is not None for hook in (_runner,_transport_factory,_reader_factory)))
    workdir=Path(workdir);deadline=time.monotonic()+duration;token=uuid.uuid4().hex
    since=datetime.now(timezone.utc).isoformat();marker='b2error-'+token[:12]
    result={'schema':SCHEMA,'profile':PROFILE,'environment_ref':env.ref().model_dump(mode='json'),
        'target_sha256':_digest(target),'policy_ref':policy_ref,'policy_values_sha256':_digest(values),
        'source_id':source_id,'source_kind':'kubernetes_pod_logs','collection_id':'log-minimization-'+uuid.uuid4().hex,
        'started_at':since,'finished_at':None,'data_origin':'EXAMPLE' if env.data_origin==DataOrigin.EXAMPLE else 'DECLARED',
        'request_summaries':[{'case_id':case,'attempted':False,'binding_verified':False,'outcome':None} for case in ('before','primary','after')],
        'normal_control':{'attempted':False,'succeeded':None},'normal_control_scope':'tokenize_api_shape_only',
        'model_inference_verified':False,'coverage_complete':False,'log_fields_seen':None,
        'field_violation_count':0,'forbidden_error_input_count':0,'violation_codes':[],
        'marker_contexts_seen':[],'branches_tested':[],'exception_dump_verified':None,
        'analysis_binding_verified':False,'source_generation_verified':False,'source_reads':[],
        'cleanup':{'complete':False,'collection_gap':True},'gaps':['CALLER_INPUT_NOT_SENT','PRIVATE_SYSTEM_NOT_SENT']}
    if len(values['required_sources'])>1:result['gaps'].append('OTHER_SOURCES_NOT_COLLECTED')
    scratch=None;transport=None;child_cleanup=True;controls={};run=_runner or capture_pipes
    def gap(code):result['gaps'].append(code)
    def remaining():
        budget=min(timeout,deadline-time.monotonic())
        if budget<=0:raise TimeoutError('LOG_MIN_BUDGET_EXHAUSTED')
        return budget
    def dispatch(argv,*,log_row=None,**kwargs):
        nonlocal child_cleanup
        kwargs.update(timeout=min(kwargs['timeout'],remaining()),env_extra={'TMPDIR':scratch.name,'TMP':scratch.name,'TEMP':scratch.name})
        argv=[argv[0],'--cache-dir='+str(Path(scratch.name)/'cache'),*argv[1:]]
        try:
            if log_row is not None:log_row['attempted']=True
            raw=run(argv,**kwargs);pipe=raw.get('capture',{})
            if pipe.get('pipes_closed') is not True or pipe.get('read_errors')!=[]:child_cleanup=False
            return raw
        except Exception:child_cleanup=False;raise
    def snapshot(reader):
        value=reader.snapshot(timeout=remaining())
        owner=verify_owned_pod(value['pod'],value['namespace']['metadata'],binding['engine'],binding['run_id'],require_ready=False)
        return _digest(owner)
    def capability():
        if _transport_factory is None:response_adapter.verify_a_capability()
    def send(case,reader):
        row=next(row for row in result['request_summaries'] if row['case_id']==case)
        try:
            before=snapshot(reader);capability();budget=remaining()
            path='/v1/chat/completions' if case=='primary' else '/tokenize'
            body=({'model':alias,'messages':[{'role':'user','content':'hello'}],'max_tokens':marker,'stream':False}
                if case=='primary' else {'model':alias,'prompt':'hello','add_special_tokens':False,'return_token_strs':False})
            label=response_adapter.CASE_IDS[case]
            request={'id':label,'mode':'json','method':'POST',
                'url':urlunsplit((transport.endpoint.scheme,transport.endpoint.netloc,path,'','')),
                'headers':{'X-Request-Id':'byul-req13-error-'+token+'-'+case},'body':body}
            row['attempted']=True
            response=transport('kubernetes-probe',[request],timeout=budget)
            if type(response) is not list or len(response)!=1 or type(response[0]) is not dict or response[0].get('id')!=label:
                raise ValueError('response unavailable')
            response=response[0];report=transport.report()
            requests=[item for item in report.get('requests',[]) if type(item) is dict and item.get('id')==label]
            if (report.get('profile')!=response_adapter.VALIDATION_ERROR_PROFILE or report.get('binding_sha256')!=_digest(transport.binding)
                or len(requests)!=1 or requests[0].get('binding_verified_before_after') is not True
                or requests[0].get('outcome')!=response.get('outcome') or snapshot(reader)!=before):raise ValueError('binding unavailable')
            capability();row['binding_verified']=True
            row['outcome']=response['outcome'] if type(response.get('outcome')) is int else None
            if case!='primary':controls[case]=_normal_response(response)
            return response
        except Exception:
            gap('BUDGET_EXHAUSTED' if deadline<=time.monotonic() else 'REQUEST_BINDING_UNAVAILABLE')
            return None
    def read_logs(reader):
        row={'attempted':False,'binding_verified':False,'complete':False,'returncode':None,
            'byte_count':None,'event_count':0,'trial_linked_event_count':0}
        result['source_reads'].append(row)
        try:
            before=snapshot(reader);capability();budget=remaining();engine=binding['engine']
            argv=[binary,'--context='+binding['context'],'--request-timeout='+f'{budget:.3f}s',
                '--namespace='+engine['namespace'],'logs',engine['pod'],'--container='+engine['container'],
                '--since-time='+since,'--tail='+str(MAX_LOG_LINES),'--limit-bytes='+str(MAX_LOG_BYTES),'--timestamps=false',
                '--pod-running-timeout='+f'{budget:.3f}s']
            raw=dispatch(argv,log_row=row,timeout=budget,env_extra={},max_stdout_bytes=MAX_LOG_BYTES,max_stderr_bytes=4096)
            row['returncode']=raw.get('returncode') if type(raw.get('returncode')) is int else None
            # 사후 metadata가 실패한 원문은 predicate 자격을 부여하지 않는다.
            row['binding_verified']=snapshot(reader)==before;capability()
            if not row['binding_verified']:gap('SOURCE_BINDING_UNAVAILABLE');return
            if row['returncode']!=0 or type(raw.get('stdout_bytes')) is not bytes:gap('SOURCE_READ_UNAVAILABLE');return
            if raw.get('capture',{}).get('bounded_at_source') is not True:gap('SOURCE_CAPTURE_INCOMPLETE');return
            row['byte_count']=len(raw['stdout_bytes'])
            if row['byte_count']>MAX_LOG_BYTES:gap('SOURCE_CAPTURE_INCOMPLETE');return
            lines=[];invalid_utf8=False
            for line in raw['stdout_bytes'].splitlines(keepends=True):
                try:lines.append(line.decode('utf-8','strict'))
                except UnicodeDecodeError:invalid_utf8=True
            text=''.join(lines)
            if invalid_utf8:gap('SOURCE_UTF8_INCOMPLETE')
            row['complete']=(_pipe_complete(raw,MAX_LOG_BYTES) and row['byte_count']<MAX_LOG_BYTES
                and len(raw['stdout_bytes'].splitlines())<MAX_LOG_LINES and not invalid_utf8)
            if not row['complete']:gap('SOURCE_CAPTURE_INCOMPLETE')
            correlation={'normal':'byul-req13-error-'+token+'-before','error':'byul-req13-error-'+token+'-primary'}
            captures={'source':{'id':source_id,'kind':'kubernetes_pod_logs','role':'engine'},
                'text':text,'window':{'since':since,'until':datetime.now(timezone.utc).isoformat()},
                'returncode':0,'identity_stable':True,'source_complete':row['complete'],
                'truncated':not row['complete'],'timed_out':bool(raw.get('capture',{}).get('timed_out'))}
            facts=_predicates(captures,values,{'error_input':marker},correlation,source_id)
            row.update(event_count=facts['event_count'],trial_linked_event_count=facts['trial_linked_event_count'])
            if not facts['structured']:gap('SOURCE_JSON_UNAVAILABLE')
            if not facts['trial_linked_event_count']:gap('SOURCE_EVENT_UNLINKED');return
            result.update(analysis_binding_verified=True,source_generation_verified=True,
                field_violation_count=facts['field_violation_count'],forbidden_error_input_count=facts['forbidden_error_input_count'],
                marker_contexts_seen=facts['marker_contexts_seen'],branches_tested=facts['branches_tested'],
                exception_dump_verified=True if 'exception_dump' in facts['branches_tested'] else None)
        except Exception:gap('BUDGET_EXHAUSTED' if deadline<=time.monotonic() else 'SOURCE_READ_UNAVAILABLE')
    try:
        try:capability()
        except Exception:gap('A_CAPABILITY_UNAVAILABLE');return result
        if workdir.is_symlink():raise ValueError('scratch unavailable')
        workdir.mkdir(parents=True,exist_ok=True);scratch=tempfile.TemporaryDirectory(prefix='log-minimization-',dir=workdir)
        factory=_transport_factory or kubernetes_http.KubernetesHTTP
        transport=factory(target,env,duration=max(0,deadline-time.monotonic()),binary=binary,runner=dispatch,
            profile=response_adapter.VALIDATION_ERROR_PROFILE)
        if (transport.profile!=response_adapter.VALIDATION_ERROR_PROFILE or transport.target_sha256!=_digest(target)
                or transport.api_model!=alias or transport.binding!=target['kubernetes']):raise ValueError('transport identity unavailable')
        reader=(_reader_factory or KubernetesLifecycleReader)(capture_target,env,{'session':uuid.uuid4().hex},
            duration=max(0,deadline-time.monotonic()),binary=binary,runner=dispatch)
        send('before',reader)
        if controls.get('before') is True:
            send('primary',reader)
            if all(row['binding_verified'] for row in result['request_summaries'][:2]):read_logs(reader)
            else:gap('PRIMARY_UNAVAILABLE')
        else:gap('BEFORE_CONTROL_UNAVAILABLE')
        send('after',reader)
        if controls.get('after') is not True:gap('AFTER_CONTROL_UNAVAILABLE')
    except Exception:gap('BUDGET_EXHAUSTED' if deadline<=time.monotonic() else 'HTTP_UNAVAILABLE')
    finally:
        transport_clean=True;cache_clean=True
        if transport is not None:
            try:
                report=transport.report()
                transport_clean=(report.get('profile')==response_adapter.VALIDATION_ERROR_PROFILE
                    and report.get('binding_sha256')==_digest(transport.binding)
                    and report.get('cleanup_errors')==[] and report.get('collection_gap') is False)
            except Exception:transport_clean=False
        try:capability()
        except Exception:gap('SOURCE_POSTCHECK_UNAVAILABLE')
        if scratch is not None:
            try:scratch.cleanup()
            except Exception:cache_clean=False
        if not(child_cleanup and transport_clean and cache_clean):gap('CLEANUP_UNVERIFIED')
        if result['exception_dump_verified'] is not True:gap('EXCEPTION_DUMP_NOT_OBSERVED')
        result['violation_codes']=[code for count,code in ((result['field_violation_count'],'FIELD_POLICY_VIOLATION'),
            (result['forbidden_error_input_count'],'FORBIDDEN_ERROR_INPUT')) if count>0]
        result['normal_control']={'attempted':any(row['attempted'] for row in result['request_summaries'] if row['case_id']!='primary'),
            'succeeded':False if False in controls.values() else True if controls=={'before':True,'after':True} else None}
        result['gaps']=sorted(set(result['gaps']))
        result['cleanup']={'complete':child_cleanup and transport_clean and cache_clean,'collection_gap':bool(result['gaps'])}
        result['finished_at']=datetime.now(timezone.utc).isoformat()
    return result
