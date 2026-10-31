"""Closed selected-resource census over the existing owned lifecycle reader."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,time
from guard.adapters import resource_recovery_probe as probe
from guard.adapters import vllm_initialization_sensor as initialization,vllm_rank_failure_sensor as rank
from guard.models import canonical_json,sha256_hex
from guard.runtime.bounded import strict_json
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError,supervised_source

PROFILE='kubernetes-owned-selected-resource-census-v1'
PROBE_SOURCE_SHA256='86f51b71a70b494562c04bb518afdab420a4f70d2577d6f7f205cec5a067240c'


def source():
    raw=Path(probe.__file__).read_bytes()
    if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=PROBE_SOURCE_SHA256:raise ValueError('RESOURCE_PROBE_SOURCE_UNREVIEWED')
    return raw.decode('utf-8')


class KubernetesResourceRecoveryReader(KubernetesLifecycleReader):
    def __init__(self,target,env,prepared,**kwargs):
        if prepared.get('profile') not in {initialization.PROFILE,rank.PROFILE}:raise KubernetesCaptureError()
        super().__init__(target,env,prepared,**kwargs)
        self.census_source=source();self.census_wrapped=supervised_source(self.census_source);self.censuses=[]

    def resource_census(self,selection,*,max_clock_skew_ms):
        """Only explicit numeric PID/FD identities, fixed helper and bounded stdin."""
        probe.validate_selection(selection)
        if type(max_clock_skew_ms) is not int or not 0<=max_clock_skew_ms<=60000:raise KubernetesCaptureError()
        row={'profile':PROFILE,'selection_sha256':sha256_hex(canonical_json(selection)),'probe_source_sha256':PROBE_SOURCE_SHA256,
            'started_at':datetime.now(timezone.utc).isoformat(),'finished_at':None,'collected':False,'owner_binding_verified':False,
            'clock_window_verified':False,'packet':None,'diagnostic_codes':[]}
        host_started_ns=time.time_ns()
        deadline=min(self.deadline,time.monotonic()+5);cleanup=self._parent_cleanup_failures
        try:
            before=self._owner(self._remaining(deadline));role=self.binding['engine'];budget=self._remaining(deadline)
            argv=[self.binary,'--context='+self.binding['context'],'--request-timeout='+str(budget)+'s',
                '--namespace='+role['namespace'],'exec','-i',role['pod'],'--container='+role['container'],'--',
                'python3','-I','-S','-B','-c',self.census_wrapped,repr(budget)]
            result=self.runner(argv,timeout=budget,env_extra={},input_bytes=canonical_json(selection),
                max_stdout_bytes=probe.MAX_OUTPUT,max_stderr_bytes=4096)
            pipe=result['capture'];raw=result['stdout_bytes']
            if pipe.get('pipes_closed') is False or 'child_reap' in pipe.get('read_errors',[]):
                self._parent_cleanup_failures+=1;self.gap=True
            if (result['returncode']!=0 or type(raw) is not bytes or len(raw)>probe.MAX_OUTPUT or pipe.get('complete') is not True
                    or pipe.get('bounded_at_source') is not True or pipe.get('timed_out') is not False or pipe.get('truncated') is not False):raise KubernetesCaptureError()
            packet=probe.validate_result(strict_json(raw,limit=probe.MAX_OUTPUT),selection)
            row.update(packet=packet,collected=True)
            row['owner_binding_verified']=self._owner(self._remaining(deadline))==before and cleanup==self._parent_cleanup_failures
        except Exception:
            self.gap=True;row['diagnostic_codes'].append('RESOURCE_CENSUS_COLLECTION_OR_OWNER_UNAVAILABLE')
        host_finished_ns=time.time_ns()
        row['finished_at']=datetime.now(timezone.utc).isoformat()
        if row['collected']:
            skew=max_clock_skew_ms*10**6;packet=row['packet']
            row['clock_window_verified']=bool(host_started_ns<=host_finished_ns and host_started_ns-skew<=packet['started_wall_ns']<=packet['finished_wall_ns']<=host_finished_ns+skew)
        if not row['owner_binding_verified'] or not row['clock_window_verified']:self.gap=True
        self.censuses.append(row);return row

    def report(self):
        value=super().report();value['resource_census_profile']=PROFILE
        value['resource_census_source_sha256']=PROBE_SOURCE_SHA256
        value['resource_census_wrapper_sha256']=sha256_hex(self.census_wrapped.encode())
        value['resource_census_calls']=[{k:v for k,v in row.items() if k!='packet'} for row in self.censuses]
        return value
