"""Bounded viewer-only capture around externally controlled collector faults."""
from __future__ import annotations

import hashlib
import json
import time
import re

from tools.selected_runtime_check import document, fact, origin, request, validate_windows

FIELDS=('dropped_lines_total','redaction_dropped_lines_total','last_redaction_error_class','last_redaction_drop_at',
        'redaction_loss_detected','storage_errors_total','last_storage_error_class','last_storage_error_at',
        'last_storage_operation','checkpoint_pending','lost_events_total','last_lost_at','loss_detected')
WRITER=('configured','state','updated_at','reconnect_count','open_gap','last_gap')
GAP=('gap_id','opened_at','closed_at','backfill','attempts')


def canonical(collector, *, platform=False):
    if not isinstance(collector,dict):return None
    value=collector.get('detail' if platform else 'last_value')
    if not isinstance(value,dict):return None
    writer=value.get('writer') or {}
    if writer.get('configured') is not True:return None
    selected={k:writer.get(k) for k in WRITER}
    for name in ('open_gap','last_gap'):
        selected[name]={k:selected[name].get(k) for k in GAP} if isinstance(selected[name],dict) else None
    return {'state':collector.get('state'),'fields':{k:value.get(k) for k in FIELDS},'writer':selected}


def public_summary(value):
    if value is None:return {'available':False}
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    writer=value['writer'];fields=value['fields']
    numeric={k:v for k,v in fields.items() if type(v) in (int,bool) or v is None}
    safe_time=lambda text:text if isinstance(text,str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)',text) else None
    gaps={}
    for name in ('open_gap','last_gap'):
        gap=writer[name]
        gaps[name]={'present':gap is not None,'sha256':hashlib.sha256(json.dumps(gap,sort_keys=True).encode()).hexdigest() if gap else None,
                    'opened_at':safe_time((gap or {}).get('opened_at')),'closed_at':safe_time((gap or {}).get('closed_at')),
                    'attempts':gap.get('attempts') if gap and type(gap.get('attempts')) is int else None}
    known={'OSError','PermissionError','FileNotFoundError','JSONDecodeError','ValueError','UnicodeDecodeError','TimeoutError','RuntimeError','ConnectionError','ConnectionResetError'}
    diagnostics={k:(v if v in known else 'other') for k,v in fields.items() if k.endswith('_error_class') and isinstance(v,str)}
    diagnostics.update({k:safe_time(v) for k,v in fields.items() if k.endswith('_at')})
    return {'available':True,'canonical_sha256':hashlib.sha256(raw).hexdigest(),
            'collector_state':value['state'] if value['state'] in ('ok','partial','error','stale','disabled','not_configured','unknown') else 'unknown',
            'writer_state':writer['state'] if writer['state'] in ('running','stopped','reconnecting','error','not_configured') else 'unknown',
            'numeric_fields':numeric,'diagnostics':diagnostics,'gaps':gaps,'raw_error_messages_or_gap_ids_saved':False}


def compare_feed(before,api,after):
    raw_before=canonical(before);raw_after=canonical(after);view=canonical(api,platform=True)
    if raw_before is None or raw_after is None or view is None:
        return None,{'reason':'configured_source_not_available','before':public_summary(raw_before),'platform':public_summary(view),'after':public_summary(raw_after)}
    stable=raw_before==raw_after
    same=stable and raw_before==view
    return same if stable else None,{'reason':'stable_comparison' if stable else 'source_changed_during_snapshot',
                'before':public_summary(raw_before),'platform':public_summary(view),'after':public_summary(raw_after)}


def scope(document,*,platform=False):
    policy=document.get('policy') or {};target=document.get('target') or {}
    return {'policy_sha256':(policy.get('policy_ref') or {}).get('sha256') if platform else policy.get('sha256'),
            'target_id':target.get('target_id'),'target_instance_id':target.get('instance_id') if platform else
                ((target.get('instance') or {}).get('current') or {}).get('instance_id'),
            'run_id':document.get('run_id'),'observation_session_id':document.get('observation_session_id')}


def observe(gateway,platform,viewer,source,*,expected_state=None,wait_seconds=5,timeout=15):
    if source not in ('hubble','tetragon') or not .1<=wait_seconds<=30:
        raise ValueError('selected source and bounded wait required')
    base=origin(gateway);web=origin(platform);name='observation_feed_'+source
    deadline=time.monotonic()+wait_seconds;started=time.monotonic();samples=[];latest=None;contract_failure=False
    def read(url,token=None):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('OBSERVATION_CAPTURE_DEADLINE')
        reply=request('GET',url,token=token,timeout=min(timeout,remaining))
        return reply.status,document(reply) if reply.status==200 else {}
    while time.monotonic()<deadline and len(samples)<30:
        try:
            raw_status,before=read(base+'/api/status',viewer)
            if raw_status!=200 or 'GET /api/*' not in (before.get('auth') or {}).get('protected_routes',[]):
                return {'execution':'not_run','cases':[fact('OBS-PREFLIGHT',None,{'http_status':raw_status},scope='protected viewer required; no collector mutation')]}
            api_status,api=read(web+'/api/runtime/status')
            end_status,after=read(base+'/api/status',viewer)
            find=lambda rows:next((c for c in rows if c.get('source')==name),None)
            matched,details=compare_feed(find(before.get('collectors',[])),find((api.get('item') or {}).get('collectors',[])),find(after.get('collectors',[])))
            invalid=bool(api.get('invalid'));contract_failure=contract_failure or invalid
            context_stable=(scope(before)==scope(after) and bool(scope(before)['policy_sha256']) and
                            bool(scope(before)['target_id']) and bool(scope(before)['observation_session_id']))
            if not context_stable:matched=None
            elif scope(after)!=scope(api.get('item') or {},platform=True):matched=False
            expected=expected_state is None or details['after'].get('collector_state')==details['platform'].get('collector_state')==expected_state
            latest={'matched':matched,'expected_state_observed':expected,'api_contract_invalid':invalid,'api_status':api_status,
                    'runtime_after_status':end_status,'context_stable':context_stable,'scope':scope(after),
                    'platform_scope':scope(api.get('item') or {},platform=True),'comparison':details}
            samples.append({'client_elapsed_ms':int((time.monotonic()-started)*1000),**latest})
            if invalid or matched is True and expected:break
            time.sleep(min(.1,max(0,deadline-time.monotonic())))
        except Exception as error:
            samples.append({'client_elapsed_ms':int((time.monotonic()-started)*1000),'error_class':type(error).__name__});break
    state=False if contract_failure or latest and latest['matched'] is False else True if latest and latest['matched'] is True and latest['expected_state_observed'] else None
    cases=[fact('OBS-COLLECTOR-PROJECTION',state,{'source':source,'expected_state':expected_state,'samples':samples,
                'wait_bound_seconds':wait_seconds,'collector_mutated':False,'cross_clock_detection_latency_claim':False},scope='viewer read chronology; stable source fields to Platform API within caller-selected wait')]
    try:
        from byul_platform.sources.checklist_data import CHECKS,FIXED_SHA,PACK_REVISION
        events_status,events=read(web+'/api/runtime/events');checks_status,checks=read(web+'/api/runtime/checks')
        window_valid=validate_windows(events) if events_status==200 else None
        coverage_valid=validate_windows(checks) if checks_status==200 else None
        summary=lambda data:{source:{key:value for key,value in (data.get('source_windows') or {}).get(source,{}).items()
                                    if key in ('returned','total','limit','truncated') and (type(value) in (int,bool) or value is None)} for source in ('gateway','hubble','tetragon')}
        cases.append(fact('OBS-SOURCE-WINDOWS',window_valid and coverage_valid if window_valid is not None and coverage_valid is not None else None,
                          {'event_windows':summary(events),'checks_windows':summary(checks),'same_poll_snapshot_claim':False},scope='each API query window retains total/returned/limit/truncation; independent polls'))
        actual={r.get('framework_id') for r in checks.get('items',[]) if isinstance(r,dict)};expected_ids={r['framework_id'] for r in CHECKS}
        cases.append(fact('OBS-SELECTED37',checks.get('selected_count')==37 and checks.get('excluded_count')==40 and checks.get('pack_revision')==PACK_REVISION and
                          checks.get('fixed_sha')==FIXED_SHA and actual==expected_ids and len(checks.get('items',[]))==37 if checks_status==200 else None,
                          {'selected_count':checks.get('selected_count'),'excluded_count':checks.get('excluded_count'),'unique_ids':len(actual),'pack_revision':checks.get('pack_revision'),
                           'missing_ids':sorted(expected_ids-actual),'unexpected_id_count':len(actual-expected_ids)},scope='pinned selected coverage; related events are not security verdicts'))
    except Exception as error:
        cases.append(fact('OBS-SOURCE-WINDOWS',None,{'error_class':type(error).__name__},scope='query window capture incomplete within the same job deadline'))
    return {'cases':cases,'security_verdict':None,'collector_mutated':False,'inference_posts':0,
            'past_gap_backfill_independently_verified':False,'screen_render_verified':False}
