"""Embedded stdlib helper; fixed context/file/receiver/sender sources are injected.

Only this new exec process is timed out. No credentials or other PIDs change.
"""
import contextlib
from datetime import datetime,timezone
import io
import json
import signal
import sys
import time

def read_program(source,argv):
    before=sys.argv;stream=io.StringIO()
    try:
        sys.argv=['guard-egress-owned-read',*argv]
        with contextlib.redirect_stdout(stream):exec(compile(source,'guard-egress-owned-read','exec'),{'__name__':'__main__'})
        raw=stream.getvalue().encode()
        if not 0<len(raw)<=32768:raise ValueError()
        return json.loads(raw)
    finally:sys.argv=before

def main():
    result={'schema':'guard-kubernetes-egress-helper/1','mode':None,'context_before':None,'context_after':None,
        'started_monotonic_ns':time.monotonic_ns(),'finished_monotonic_ns':None,
        'sender':None,'file':None,'receiver_process':None,'diagnostic_codes':[]}
    try:
        if sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12):raise ValueError()
        raw=sys.stdin.buffer.read(4097)
        if not 0<len(raw)<=4096:raise ValueError()
        request=json.loads(raw);mode=request['mode'];result['mode']=mode
        if mode not in ('sender','receiver') or not 0<request['budget']<=30:raise ValueError()
        budget=min(request['budget'],BOOT_DEADLINE-time.monotonic())
        if budget<=0:raise TimeoutError()
        def deadline(*args):raise TimeoutError()
        signal.signal(signal.SIGALRM,deadline);signal.setitimer(signal.ITIMER_REAL,budget)
        result['context_before']=context()
        if mode=='sender':
            p=request['policy_values'];a=request['actor_spec'];c=result['context_before']
            if (c['uids']!=[a['uid']]*4 or c['gids']!=[a['gid']]*4 or c['groups']!=a['groups']
                    or c['namespaces']['net']!='net:['+str(p['subject_net_namespace_inode'])+']'):raise ValueError()
            start=datetime.fromisoformat(p['validity_start'].replace('Z','+00:00'))
            end=datetime.fromisoformat(p['validity_end'].replace('Z','+00:00'));now=datetime.now(timezone.utc)
            if not start<=now<end:raise ValueError()
            budget=min(BOOT_DEADLINE-time.monotonic(),(end-now).total_seconds())
            if budget<=0:raise TimeoutError()
            ns=int(c['namespaces']['time'][6:-1]);cutoff=time.monotonic_ns()+int(budget*1e9)
            result['sender']=sender.send_probe(p['forbidden_receiver_ip'],p['forbidden_receiver_port'],request['correlation'],
                request['connect_timeout'],p['subject_uid'],p['subject_net_namespace_inode'],
                expected_time_namespace_inode=ns,deadline_monotonic_ns=cutoff)
        else:
            result['file']=read_program(FILE_READER,['/guard-observations/egress.jsonl','16384'])
            result['receiver_process']=read_program(RECEIVER_PROCESS,[str(request['receiver_pid'])])
    except TimeoutError:result['diagnostic_codes'].append('EGRESS_HELPER_DEADLINE')
    except BaseException:result['diagnostic_codes'].append('EGRESS_HELPER_UNAVAILABLE')
    try:result['context_after']=context()
    except BaseException:result['diagnostic_codes'].append('EGRESS_HELPER_CONTEXT_UNAVAILABLE')
    result['finished_monotonic_ns']=time.monotonic_ns()
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
    if len(raw.encode())>65536:raise ValueError('EGRESS_HELPER_OUTPUT_BOUND')
    print(raw)
    return 0
