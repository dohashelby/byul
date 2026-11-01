"""REQ13 error-field subset; direct owned Kubernetes HTTP only after A approval.

No response/native-handler-consumption inference, inference request, Pod mutation,
raw artifact writing or external observation JSON ingestion is provided.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import math
from pathlib import Path
import re
import sys
import uuid
from urllib.parse import urlunsplit

from guard.models import DataOrigin, canonical_json
from guard.runtime import kubernetes_http
from guard.executors.response_error_contract import observe_error_response
from guard.executors.template_validation import _token_ids
from guard.executors.log_analysis_contract import strict_json_loads

PROFILE = 'byul-req13-error-kubernetes-capture/1'
VALIDATION_ERROR_PROFILE = 'kubernetes-owned-validation-error-http-v1'
SOURCE_PROFILE = 'vllm-031-validation-error'
SUPPORTED_ENGINE_VERSION = '0.31.0'
# Reviewed A profile/source must be fixed before real network use. Never learn
# an approval from runtime bytes, target JSON, an environment or a test adapter.
A_HTTP_SOURCE_SHA256 = None
HELPER_PINS = {
    'executors/response_error_contract.py':'2cf7d60d45c19114a17ca37ade0aeb46466e60e246a39076c4e3139ad5ed1a70',
    'executors/template_validation.py':'6c68fc91d1a9cb93ed83036c53a391381efe9e653a581e965a1e8290003b4b78',
    'executors/content_common.py':'0f410e2ff6c91d77f411b1529023fc2d50fa1f2b8e502c6c2019fa375bcd2421',
    'executors/log_analysis_contract.py':'e03e86274894a9e6e0d0dc69407e71976b4ff7c5a41f1b7308a204f795f3a3a1',
}
CASE_IDS = {'before':'req13-error-before','primary':'req13-error-primary','after':'req13-error-after'}
GAPS = frozenset({'UNSUPPORTED_ENGINE_VERSION','A_CAPABILITY_UNAVAILABLE','A_SOURCE_POSTCHECK_UNAVAILABLE','TRANSPORT_UNAVAILABLE',
    'TRANSPORT_IDENTITY_MISMATCH','BEFORE_CONTROL_UNAVAILABLE','BEFORE_CONTROL_FAILED',
    'PRIMARY_UNAVAILABLE','PRIMARY_NOT_ERROR_STATUS','PRIMARY_CAPTURE_INCOMPLETE',
    'PRIMARY_OBSERVATION_UNAVAILABLE','EXPECTED_ERROR_STATUS_UNCONFIRMED',
    'AFTER_CONTROL_UNAVAILABLE','AFTER_CONTROL_FAILED','CLEANUP_UNAVAILABLE','CLEANUP_INCOMPLETE','POLICY_UNSET'})
MAX_BODY_BYTES = 131072


def _digest(value):return hashlib.sha256(canonical_json(value)).hexdigest()
def _sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def verify_product_helpers():
    root=Path(__file__).resolve().parents[1]/'src/guard'
    for relative,expected in HELPER_PINS.items():
        file=root/relative
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
            raise ValueError('REQ13_HELPER_BYTES_MISMATCH')
    for fn,relative in ((observe_error_response,'executors/response_error_contract.py'),
                         (_token_ids,'executors/template_validation.py'),
                         (strict_json_loads,'executors/log_analysis_contract.py')):
        if not Path(inspect.getsourcefile(fn)).resolve().samefile(root/relative):
            raise ValueError('REQ13_HELPER_IMPORT_MISMATCH')


def verify_a_capability():
    if not _sha(A_HTTP_SOURCE_SHA256):raise ValueError('REQ13_A_CAPABILITY_UNAVAILABLE')
    expected=Path(__file__).resolve().parents[1]/'src/guard/runtime/kubernetes_http.py'
    if (expected.is_symlink() or not Path(kubernetes_http.__file__).resolve().samefile(expected)
            or hashlib.sha256(expected.read_bytes()).hexdigest()!=A_HTTP_SOURCE_SHA256
            or getattr(kubernetes_http,'VALIDATION_ERROR_PROFILE',None)!=VALIDATION_ERROR_PROFILE):
        raise ValueError('REQ13_A_CAPABILITY_UNAVAILABLE')
    for fn in (kubernetes_http.KubernetesHTTP.__init__,kubernetes_http.KubernetesHTTP.__call__,kubernetes_http.KubernetesHTTP.report):
        if not Path(inspect.getsourcefile(fn)).resolve().samefile(expected):
            raise ValueError('REQ13_A_CAPABILITY_UNAVAILABLE')


def _normal():
    return {'attempted':False,'collected':False,'succeeded':None,'http_status':None,'token_count':None,
        'max_model_len':None,'tokens_sha256':None,'response_sha256':None,'request_id_sha256':None,
        'binding_verified':None,'capture_complete':None}


def _report(transport,expected_binding):
    report=transport.report()
    if (type(report) is not dict or report.get('profile')!=VALIDATION_ERROR_PROFILE
            or report.get('binding_sha256')!=expected_binding or type(report.get('requests')) is not list):
        raise ValueError('REQ13_TRANSPORT_REPORT_MISMATCH')
    return report


def _bound_response(transport,response,label,expected_binding):
    report=_report(transport,expected_binding)
    rows=[row for row in report['requests'] if type(row) is dict and row.get('id')==label]
    if (len(rows)!=1 or rows[0].get('binding_verified_before_after') is not True
            or rows[0].get('outcome')!=response.get('outcome')):
        raise ValueError('REQ13_REQUEST_GENERATION_UNCONFIRMED')
    return report


def _capture_body(response):
    body=response.get('body')
    if type(body) is not str:return False
    raw=body.encode('utf-8')
    return (len(raw)<=MAX_BODY_BYTES and type(response.get('body_bytes')) is int
        and 0<=response['body_bytes']<=MAX_BODY_BYTES and _sha(response.get('body_sha256'))
        and response.get('capture_complete') is True and response.get('body_truncated') is False
        and response.get('utf8_valid') is True and response.get('eof') is True)


def _normal_response(response,summary):
    complete=_capture_body(response)
    summary.update(http_status=response['outcome'] if type(response.get('outcome')) is int else None,
        capture_complete=complete,binding_verified=True,
        response_sha256=response.get('body_sha256') if _sha(response.get('body_sha256')) else None)
    if not complete:return
    summary['collected']=True;summary['succeeded']=False
    if response.get('outcome')!=200:return
    try:value=strict_json_loads(response['body'])
    except (ValueError,TypeError,RecursionError):return
    if (type(value) is not dict or not _token_ids(value.get('tokens'),262144)
            or type(value.get('count')) is not int or value['count']!=len(value['tokens'])
            or type(value.get('max_model_len')) is not int or value['max_model_len']<=0):return
    summary.update(succeeded=True,token_count=value['count'],max_model_len=value['max_model_len'],tokens_sha256=_digest(value['tokens']))


def capture_validation_error(env,target,*,duration=60,timeout=10,binary='kubectl',allowed_error_fields=None,
                             forbidden_contexts=None,_transport_factory=None):
    """Collect directly; injected transports require a sealed EXAMPLE ENV."""
    if (sys.flags.optimize or not env.hash_ok() or env.engine.name!='vllm'
            or type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=300
            or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=120
            or type(binary) is not str or not binary.strip() or len(binary)>1024):
        raise ValueError('REQ13_CAPTURE_INPUT_INVALID')
    if _transport_factory is not None and env.data_origin!=DataOrigin.EXAMPLE:
        raise ValueError('REQ13_EXAMPLE_TRANSPORT_REQUIRES_EXAMPLE_ENV')
    if allowed_error_fields is not None and (type(allowed_error_fields) is not list or any(type(v) is not str or not v for v in allowed_error_fields)):
        raise ValueError('REQ13_FIELD_POLICY_INVALID')
    if forbidden_contexts is not None and (type(forbidden_contexts) is not list
            or any(type(v) is not str or v!='error_input' for v in forbidden_contexts)
            or len(set(forbidden_contexts))!=len(forbidden_contexts)):
        raise ValueError('REQ13_CONTEXT_POLICY_INVALID')
    target=deepcopy(target);target_sha=_digest(target)
    if target.get('environment_ref')!=env.ref().model_dump(mode='json'):
        raise ValueError('REQ13_ENV_TARGET_MISMATCH')
    token=uuid.uuid4().hex;started=datetime.now(timezone.utc).isoformat()
    record={'schema':PROFILE,'source_profile':SOURCE_PROFILE,'profile':VALIDATION_ERROR_PROFILE,'collection_id':'req13-error-k8s-'+token,
        'capture_started_at':started,'capture_finished_at':None,'environment_ref':env.ref().model_dump(mode='json'),
        'target_sha256':target_sha,'api_model_sha256':None,'data_origin':'EXAMPLE' if env.data_origin==DataOrigin.EXAMPLE else 'DECLARED',
        'attempted_requests':0,'request_summaries':[],'normal_controls':{'before':_normal(),'after':_normal()},'error_observation':None,
        'gaps':[],'cleanup':{'collected':False,'complete':None,'collection_gap':None,'error_sha256':[]},
        'capture_status':'incomplete','native_handler_consumption_verified':False,
        'source_routing_verified':False,'model_inference_verified':False,
        'allowed_error_fields_sha256':_digest(allowed_error_fields) if allowed_error_fields is not None else None,
        'forbidden_contexts_configured':forbidden_contexts is not None}
    gaps=set();transport=None;binding_sha=None
    def send(slot,path,body):
        nonlocal transport
        rid='byul-req13-error-'+token+'-'+slot;label=CASE_IDS[slot]
        request={'id':label,'mode':'json','method':'POST','url':urlunsplit((transport.endpoint.scheme,transport.endpoint.netloc,path,'','')),
            'headers':{'X-Request-Id':rid},'body':body}
        request_summary={'case_id':label,'method':'POST','path':path,
            'request_id_sha256':hashlib.sha256(rid.encode()).hexdigest(),'body_sha256':_digest(body),
            'attempted':False,'binding_verified':None,'started_at':None,'finished_at':None,'raw_response_bytes_sha256':None}
        record['request_summaries'].append(request_summary)
        try:
            verify_product_helpers()
            if _transport_factory is None:verify_a_capability()
            record['attempted_requests']+=1
            request_summary.update(attempted=True,started_at=datetime.now(timezone.utc).isoformat())
            if slot in record['normal_controls']:
                record['normal_controls'][slot].update(attempted=True,request_id_sha256=request_summary['request_id_sha256'])
            result=transport('kubernetes-probe',[request],timeout=min(timeout,duration))
            if type(result) is not list or len(result)!=1 or type(result[0]) is not dict or result[0].get('id')!=label:
                raise ValueError('REQ13_RESPONSE_ID_MISMATCH')
            _bound_response(transport,result[0],label,binding_sha)
            request_summary.update(binding_verified=True,raw_response_bytes_sha256=result[0].get('body_sha256') if _sha(result[0].get('body_sha256')) else None)
            verify_product_helpers()
            if _transport_factory is None:verify_a_capability()
            body=result[0].get('body')
            if type(body) is str and len(body.encode('utf-8'))>MAX_BODY_BYTES:
                raise ValueError('REQ13_BODY_BOUND_EXCEEDED')
            return result[0]
        except Exception:
            gaps.add('TRANSPORT_UNAVAILABLE');return None
        finally:
            if request_summary['attempted']:request_summary['finished_at']=datetime.now(timezone.utc).isoformat()
    try:
        if env.engine.version!=SUPPORTED_ENGINE_VERSION:
            gaps.add('UNSUPPORTED_ENGINE_VERSION');return record
        verify_product_helpers()
        if _transport_factory is None:
            try:verify_a_capability()
            except Exception:
                gaps.add('A_CAPABILITY_UNAVAILABLE');return record
            factory=kubernetes_http.KubernetesHTTP
        else:factory=_transport_factory
        transport=factory(target,env,duration=duration,binary=binary,profile=VALIDATION_ERROR_PROFILE)
        if (transport.profile!=VALIDATION_ERROR_PROFILE or transport.target_sha256!=target_sha
                or type(transport.api_model) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}',transport.api_model)):
            gaps.add('TRANSPORT_IDENTITY_MISMATCH');return record
        binding_sha=_digest(transport.binding)
        _report(transport,binding_sha)
        record['api_model_sha256']=hashlib.sha256(transport.api_model.encode()).hexdigest()
        normal={'model':transport.api_model,'prompt':'hello','add_special_tokens':False,'return_token_strs':False}
        before=send('before','/tokenize',normal)
        if before is None:gaps.add('BEFORE_CONTROL_UNAVAILABLE')
        else:
            _normal_response(before,record['normal_controls']['before'])
            if record['normal_controls']['before']['succeeded'] is not True:gaps.add('BEFORE_CONTROL_FAILED')
        if record['normal_controls']['before']['succeeded'] is True:
            marker='b2error-'+token[:12]
            response=send('primary','/v1/chat/completions',{'model':transport.api_model,
                'messages':[{'role':'user','content':'hello'}],'max_tokens':marker,'stream':False})
            if response is None:gaps.add('PRIMARY_UNAVAILABLE')
            elif type(response.get('outcome')) is not int or not 400<=response['outcome']<=599:
                gaps.add('PRIMARY_NOT_ERROR_STATUS')
            else:
                if not _capture_body(response):gaps.add('PRIMARY_CAPTURE_INCOMPLETE')
                try:
                    facts=observe_error_response(response,marks={'error_input':marker},
                        allowed_fields=allowed_error_fields,forbidden_contexts=forbidden_contexts,expected_status=400)
                    if facts['field_path_sha256'] or facts['violations']:record['error_observation']=facts
                    else:gaps.add('PRIMARY_OBSERVATION_UNAVAILABLE')
                except Exception:gaps.add('PRIMARY_OBSERVATION_UNAVAILABLE')
                if response['outcome']!=400:gaps.add('EXPECTED_ERROR_STATUS_UNCONFIRMED')
        else:gaps.add('PRIMARY_UNAVAILABLE')
        after=send('after','/tokenize',normal)
        if after is None:gaps.add('AFTER_CONTROL_UNAVAILABLE')
        else:
            _normal_response(after,record['normal_controls']['after'])
            if record['normal_controls']['after']['succeeded'] is not True:gaps.add('AFTER_CONTROL_FAILED')
    except Exception:gaps.add('TRANSPORT_UNAVAILABLE')
    finally:
        if transport is not None:
            try:
                report=_report(transport,binding_sha)
                errors=report.get('cleanup_errors')
                if type(errors) is not list or any(type(v) is not str for v in errors) or type(report.get('collection_gap')) is not bool:
                    raise ValueError
                complete=not errors and report['collection_gap'] is False
                record['cleanup']={'collected':True,'complete':complete,'collection_gap':report['collection_gap'],
                    'error_sha256':[hashlib.sha256(v.encode()).hexdigest() for v in errors]}
                if not complete:gaps.add('CLEANUP_INCOMPLETE')
            except Exception:gaps.add('CLEANUP_UNAVAILABLE')
        else:gaps.add('CLEANUP_UNAVAILABLE')
        try:
            verify_product_helpers()
            if _transport_factory is None:verify_a_capability()
        except Exception:gaps.add('A_SOURCE_POSTCHECK_UNAVAILABLE')
        if allowed_error_fields is None or forbidden_contexts is None:gaps.add('POLICY_UNSET')
        record['gaps']=sorted(gaps)
        record['capture_finished_at']=datetime.now(timezone.utc).isoformat()
        # Policy gaps remain visible; they are not request/capture failures.
        complete=(record['attempted_requests']==3 and record['error_observation'] is not None
            and all(v['succeeded'] is True for v in record['normal_controls'].values())
            and record['cleanup']['complete'] is True and not (gaps-{'POLICY_UNSET'}))
        record['capture_status']='completed' if complete else 'incomplete'
    return record
