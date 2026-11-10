#!/usr/bin/env python3
"""Bounded Gateway mixed workload; original HTTP/stream/journal paths only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from guard.executors.stream_probe import collect
from tools.selected_runtime_check import request, document, header, json_body, origin, fact
from tools.selected_request_checks import record_for
from tools.selected_response_checks import generated_text
from tools.selected_acceptance_run import bounded
from serving_runtime.mvp.update_engine_link import select_request


def status_scope(value):
    return {'policy_sha256': (value.get('policy') or {}).get('sha256'),
            'target_id': (value.get('policy') or {}).get('target_id'), 'run_id': value.get('run_id'),
            'target_instance_id': (((value.get('target') or {}).get('instance') or {}).get('current') or {}).get('instance_id')}


def validate(cfg):
    keys = {'schema','expected_scope','principal_a','principal_b','normal_tokens','long_tokens','baseline_count','rounds',
            'global_concurrency_limit','expected_connection_limit','normal_slo_ms','required_underload_successes',
            'request_timeout_seconds','max_group_seconds','observe_load_probes','observe_interval_ms',
            'recovery_probe_count','recovery_interval_ms','caps_refs','lif010_refs'}
    if type(cfg) is not dict or set(cfg) != keys or cfg['schema'] != 'byul-gateway-mixed-budget/1':
        raise ValueError('closed LAB configuration required')
    bounds = {'baseline_count':(1,2),'rounds':(1,2),'global_concurrency_limit':(2,2),'expected_connection_limit':(4,512),
              'normal_slo_ms':(1,30000),'required_underload_successes':(1,2),'request_timeout_seconds':(1,30),
              'max_group_seconds':(10,180),'observe_load_probes':(1,25),'observe_interval_ms':(1,100),
              'recovery_probe_count':(1,3),'recovery_interval_ms':(1,1000)}
    if type(cfg['normal_tokens']) is not int or type(cfg['long_tokens']) is not int or cfg['normal_tokens'] != 8 or cfg['long_tokens'] != 32 or any(type(cfg[k]) is not int or not lo<=cfg[k]<=hi for k,(lo,hi) in bounds.items()):
        raise ValueError('bounded fixed 8-token normal and 32-token load workload required')
    if cfg['required_underload_successes'] > cfg['rounds'] or cfg['principal_a'] == cfg['principal_b'] or any(type(cfg[k]) is not str or not cfg[k] for k in ('principal_a','principal_b')):
        raise ValueError('distinct principals and feasible opportunity target required')
    if set(cfg['expected_scope']) != {'policy_sha256','target_id','run_id','target_instance_id'} or any(type(v) is not str or not v for v in cfg['expected_scope'].values()):
        raise ValueError('root-pinned exact scope required')
    refs = {}
    for key in ('caps_refs','lif010_refs'):
        if type(cfg[key]) is not list or len(cfg[key])>16: raise ValueError('bounded references required')
        refs[key] = []
        for row in cfg[key]:
            if type(row) is not dict or set(row) != {'path','sha256','scope'}: raise ValueError('exact reference required')
            path=Path(row['path'])
            if not path.is_absolute(): raise ValueError('absolute reference required')
            raw=bounded(path,cap=8*1024*1024)
            if hashlib.sha256(raw).hexdigest()!=row['sha256']: raise ValueError('reference changed')
            refs[key].append(row.copy())
    return refs


def run(gateway, model, token_a, token_b, viewer, cfg):
    refs = validate(cfg)
    if not all((token_a,token_b,viewer)) or token_a == token_b: raise ValueError('distinct inference credentials required')
    base=origin(gateway); deadline=time.monotonic()+cfg['max_group_seconds']; cases=[]; observations=[]; requests=[]
    def remaining():
        value=min(cfg['request_timeout_seconds'],deadline-time.monotonic())
        if value<=0: raise TimeoutError('group deadline')
        return value
    def status():
        reply=request('GET',base+'/api/status',token=viewer,timeout=remaining())
        if reply.status!=200: raise ValueError('viewer status unavailable')
        value=document(reply)
        if status_scope(value)!=cfg['expected_scope']: raise ValueError('Gateway scope changed')
        return value
    initial=status(); concurrency=initial.get('concurrency') or {}; connection=initial.get('connections') or {}
    policy_reply=request('GET',base+'/api/policy',token=viewer,timeout=remaining())
    current=document(policy_reply).get('current') or {}
    if (policy_reply.status!=200 or 'GET /api/*' not in (initial.get('auth') or {}).get('protected_routes',[]) or
            concurrency!={'limit':cfg['global_concurrency_limit'],'in_flight':0} or connection.get('limit')!=cfg['expected_connection_limit'] or
            current.get('sha256')!=cfg['expected_scope']['policy_sha256'] or current.get('output_max_tokens',0)<128):
        return {'execution':'not_run','cases':[fact('LIF003-PREFLIGHT',None,{'reason':'approved scope/caps/idle/protection unavailable'},scope='no POST')],'security_verdict':None}
    def trusted(raw, principal):
        return (raw.get('state')=='observed' and raw.get('record_request_ids_match_query') is True and raw.get('context_stable') is True
                and raw.get('principal_id')==principal and raw.get('auth_state')=='authenticated'
                and all(raw.get(k)==v for k,v in cfg['expected_scope'].items()))
    def interval(raw):
        # Reuse unique/context-stable producer intervals; export no request bodies.
        if raw.get('state') != 'observed' or raw.get('context_stable') is not True:
            return None
        reply=request('GET',base+'/api/requests/'+raw['request_id'],token=viewer,timeout=remaining())
        if reply.status != 200:
            return None
        rows=document(reply).get('gateway_records')
        if (not isinstance(rows,list) or not rows or any(not isinstance(r,dict) or
                r.get('request_id') != raw['request_id'] or
                (r.get('auth') or {}).get('principal_id') != raw.get('principal_id') or
                (r.get('auth') or {}).get('state') != 'authenticated' for r in rows)):
            return None
        try:
            selected=select_request(rows,raw['request_id']);context=selected['context']
            scope={'policy_sha256':(context.get('policy') or {}).get('sha256'),
                   'target_id':context.get('target_id'),'run_id':context.get('run_id'),
                   'target_instance_id':(context.get('target') or {}).get('instance_id')}
            session=context.get('observation_session_id')
            if scope != cfg['expected_scope'] or not isinstance(session,str) or not session:
                return None
            return {'started_at':selected['started_at'],'finished_at':selected['finished_at'],
                    'observation_session_id':session}
        except (ValueError,KeyError,TypeError,AttributeError):
            return None
    def actual_overlap(b,raws):
        bi=interval(b['journal']);ais=[interval(a) for a in raws]
        if bi is None or any(a is None for a in ais):
            return {'verified':False,'reason':'unique_same_scope_gateway_intervals_unavailable'}
        began=datetime.fromisoformat(bi['started_at'].replace('Z','+00:00'))
        verified=all(a['observation_session_id']==bi['observation_session_id'] and
                     datetime.fromisoformat(a['started_at'].replace('Z','+00:00')) <= began <
                     datetime.fromisoformat(a['finished_at'].replace('Z','+00:00')) for a in ais)
        return {'verified':verified,'reason':'gateway_intervals_overlap' if verified else 'no_same_session_overlap',
                'b_interval':bi,'a_intervals':ais}
    def normal(label, stream=False):
        began=time.monotonic()
        if stream:
            spec={'id':label,'mode':'sse','method':'POST','url':base+'/v1/chat/completions',
                  'headers':{'Authorization':'Bearer '+token_b},'body':json_body(model,max_tokens=8,stream=True)}
            row,=collect([spec],remaining());rid=(row.get('response_headers') or {}).get('x-request-id')
            text,valid=generated_text(row)
            normal_shape=bool(text) and valid and row.get('capture_complete') is True and row.get('terminal_seen') is True
            http=row.get('outcome');elapsed=(time.monotonic()-began)*1000
        else:
            reply=request('POST',base+'/v1/chat/completions',token=token_b,body=json.dumps(json_body(model,max_tokens=8)).encode(),timeout=remaining())
            elapsed=(time.monotonic()-began)*1000;rid=header(reply,'X-Request-ID');http=reply.status
            choices=document(reply).get('choices')
            normal_shape=(isinstance(choices,list) and bool(choices) and all(isinstance(c,dict) and
                          ((isinstance(c.get('message'),dict) and isinstance(c['message'].get('content'),str)) or isinstance(c.get('text'),str)) for c in choices))
        raw=record_for(base,rid,viewer,remaining())
        good=(http==200 and normal_shape and trusted(raw,cfg['principal_b']) and raw.get('decision_result')=='ALLOWED'
              and raw.get('delivery_attempted') is True and raw.get('final_outcome')=='completed')
        row={'label':label,'request_id':rid,'http_status':http,'latency_ms':elapsed,'normal_shape':normal_shape,
             'trusted_normal_success':good,'slo_met':good and elapsed<=cfg['normal_slo_ms'],'journal':raw}
        requests.append(row);return row
    def load(count):
        specs=[{'id':'long-a-'+str(i),'mode':'sse','method':'POST','url':base+'/v1/chat/completions',
                'headers':{'Authorization':'Bearer '+token_a},
                'body':{'model':model,'messages':[{'role':'user','content':'List integers from 1 to 500 separated by spaces.'}],
                        'stream':True,'max_tokens':cfg['long_tokens']}} for i in range(count)]
        return collect(specs,remaining())
    def overlap(pending, count):
        for _ in range(cfg['observe_load_probes']):
            value=status();active=(value.get('concurrency') or {}).get('in_flight')
            if active==count and not pending.done(): return {'observed':True,'in_flight':active}
            if pending.done(): break
            time.sleep(min(cfg['observe_interval_ms']/1000,remaining()))
        return {'observed':False,'in_flight':active}
    def mixed(label,count):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(load,count); observed=overlap(pending,count)
            b=normal(label+'-B'); rows=pending.result()
        raws=[record_for(base,(r.get('response_headers') or {}).get('x-request-id'),viewer,remaining()) for r in rows]
        observed['gateway_interval_overlap']=actual_overlap(b,raws)
        observed['observed']=observed['observed'] and observed['gateway_interval_overlap']['verified']
        stream_valid=all(r.get('outcome')==200 and r.get('capture_complete') is True and r.get('terminal_seen') is True and
                         generated_text(r)[1] and bool(generated_text(r)[0]) for r in rows)
        load_bound=(len(rows)==count and stream_valid and all(trusted(r,cfg['principal_a']) and r.get('delivery_attempted') is True
                    and r.get('decision_result')=='ALLOWED' and r.get('final_outcome')=='completed' for r in raws))
        cases.append(fact('LIF003-LONG-'+label,load_bound if all(r.get('state')=='observed' for r in raws) else None,
                          {'a_count':count,'stream_complete':stream_valid,'journals':raws,
                           'stream_captures':[{'request_id':(r.get('response_headers')or{}).get('x-request-id'),
                               'stop_reason':r.get('stop_reason'),'capture_complete':r.get('capture_complete'),
                               'terminal_seen':r.get('terminal_seen'),'body_truncated':r.get('body_truncated'),
                               'event_count':len(r.get('events')or[])}for r in rows]},scope='declared bounded32-token A normal completion'))
        observations.append({'phase':label,'a_count':count,'overlap':observed,'normal_b':b,'a_journals':raws,'load_bound':load_bound})
        return observed,b,load_bound
    try:
        baseline=[normal('baseline-B-'+str(i)) for i in range(cfg['baseline_count'])]
        elapsed=sum(r['latency_ms'] for r in baseline);completed=sum(r['trusted_normal_success'] for r in baseline)
        cases.append(fact('LIF003-BASELINE',all(r['slo_met'] for r in baseline),{'samples':baseline,
                          'completed_requests':completed,'summed_http_elapsed_ms':elapsed,
                          'bounded_sequential_sample_rate_per_s':completed*1000/elapsed if elapsed else None},
                          scope='declared 8-token B normal/SLO baseline; small sample rate, no capacity extrapolation'))
        opportunities=0; observed_rounds=0
        for i in range(cfg['rounds']):
            seen,b,bound=mixed('underload-'+str(i),1)
            if seen['observed'] and bound:
                observed_rounds+=1;opportunities+=int(b['slo_met'])
        opportunity=(opportunities>=cfg['required_underload_successes']) if observed_rounds else None
        cases.append(fact('LIF003-NORMAL-OPPORTUNITY',opportunity,{'observed_rounds':observed_rounds,'successes_within_slo':opportunities,
                          'required':cfg['required_underload_successes']},scope='normal B processing opportunity while one A leaves one global slot'))
        seen,b,bound=mixed('saturated',2)
        rejection=(b['http_status']==429 and trusted(b['journal'],cfg['principal_b']) and b['journal'].get('delivery_attempted') is False
                   and b['journal'].get('reason_code')=='CONCURRENCY_LIMIT')
        cases.append(fact('LIF003-SATURATION',True if rejection and seen['observed'] and bound else None,
                          {'overlap':seen,'normal_b':b,'load_bound':bound},scope='full global slots may reject B; distinct from reserved-slot opportunity'))
        recovery=[]
        for i in range(cfg['recovery_probe_count']):
            time.sleep(min(cfg['recovery_interval_ms']/1000,remaining()));recovery.append(normal('fresh-B-'+str(i)))
        stream=normal('fresh-B-stream',True);end=status();released=(end.get('concurrency') or {}).get('in_flight')==0
        cases.append(fact('LIF003-RECOVERY',any(r['slo_met'] for r in recovery) and stream['slo_met'] and released,
                          {'normal_probes':recovery,'stream':stream,'global_slots_released':released},scope='fixed declared recovery probes then fresh stream'))
    except Exception as error:
        cases.append(fact('LIF003-GROUP-INCOMPLETE',None,{'error_type':type(error).__name__},scope='bounded group stopped; earlier failures preserved'))
    cases.append(fact('LIF003-LIF010-LINK',None,{'refs':refs['lif010_refs'],'measurement':'not performed by this tool'},
                      scope='repeated cancel/residual cost requires separate same-request LIF010 witness'))
    return {'execution':'executed','lab_configuration':cfg,'cases':cases,'mixed_observations':observations,'request_samples':requests,
            'reused_caps_refs':refs['caps_refs'],'lif010_connection':{'state':'not_measured_by_this_tool','refs':refs['lif010_refs']},
            'unmeasured':['repeated-cancel engine residual cost; join separate LIF010 native evidence'],
            'request_gpu_accounting':'conditional policy not adopted; not a selected prerequisite','security_verdict':None,
            'group_elapsed_ms':(time.monotonic()-(deadline-cfg['max_group_seconds']))*1000,
            'declared_total_request_bound':cfg['baseline_count']+2*cfg['rounds']+3+cfg['recovery_probe_count']+1,
            'declared_output_token_upper_bound':(cfg['rounds']+2)*cfg['long_tokens']+(cfg['baseline_count']+cfg['rounds']+2+cfg['recovery_probe_count'])*8,
            'inference_retries':'only explicitly configured recovery_probe_count; no load/baseline retries','raw_generated_text_saved':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('gateway','model','config','inference-a-env','inference-b-env','viewer-env','out'):p.add_argument('--'+key,required=True)
    a=p.parse_args();out=Path(a.out).absolute();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        raw=bounded(a.config);cfg=json.loads(raw)
        fd=os.open(out/'CONFIG.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw)
        result=run(a.gateway,a.model,os.environ[a.inference_a_env],os.environ[a.inference_b_env],os.environ[a.viewer_env],cfg)
        result['config_sha256']=hashlib.sha256(raw).hexdigest()
        fd=os.open(out/'helper.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
        print(json.dumps({'execution':result['execution'],'cases':[{'id':c['id'],'result':c['result']} for c in result['cases']]}));return 0
    except Exception as error:
        print(json.dumps({'execution':'incomplete','error_type':type(error).__name__}));return 2


if __name__=='__main__':raise SystemExit(main())
