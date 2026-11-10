"""Durable engine health transitions in the existing normalized observations journal."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import threading
from uuid import uuid4


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class EngineHealthEvents:
    def __init__(self, store, context):
        self.store=store;self.context=context;self._states={};self._pending=[];self._lock=threading.Lock()
        self._lost_pending=0;self._restore_complete=False
        try:
            records,corrupt,truncated=store.read_recent()
            self._restore_complete=not truncated and not corrupt
            for row in records:
                if row.get('source')!='engine_health' or row.get('schema_version')!='runtime-engine-health-event/1.0':continue
                key=row.get('health_scope_sha256')
                if not isinstance(key,str) or row.get('health_state') not in {'healthy','unavailable'}:continue
                self._states[key]={**row,'failure_attempt_count_complete':False}
                while len(self._states)>128:self._states.pop(next(iter(self._states)))
        except (OSError,ValueError):
            # The next callback retries the actual write and exposes failure.
            pass

    def _flush(self):
        while self._pending:
            self.store.append(self._pending[0]);self._pending.pop(0)
        if self._lost_pending:raise OSError('engine health transition history incomplete')

    def observe(self,sample):
        # Collector invokes consumers outside its own lock; context snapshots
        # can therefore read target/collector status without lock inversion.
        context=deepcopy(self.context());target=context.get('target') or {}
        key=digest({k:context.get(k) for k in ('environment','run_id','backend_kind','target_id')}|{
            'instance':{k:target.get(k) for k in ('instance_id','pod_uid','container_id','restart_count','image_digest')}})
        healthy=sample['success'] is True;state='healthy' if healthy else 'unavailable'
        with self._lock:
            previous=self._states.get(key)
            count=0 if healthy else (previous.get('failure_attempt_count',0)+1 if previous and previous['health_state']=='unavailable' else 1)
            complete=previous.get('failure_attempt_count_complete',True) if previous and previous['health_state']=='unavailable' else bool(previous and previous['health_state']=='healthy')
            if previous and previous['health_state']==state:
                if not healthy:
                    previous['failure_attempt_count']=count;previous['last_failure_at']=sample['observed_at']
                try:self._flush()
                except (OSError,ValueError):raise OSError('engine health event journal unavailable') from None
                return
            kind='engine_recovered' if healthy and previous and previous['health_state']=='unavailable' else 'engine_healthy' if healthy else 'engine_unavailable'
            row={'schema_version':'runtime-engine-health-event/1.0','source':'engine_health','kind':kind,
                'observed_at':sample['observed_at'],'attempted_at':sample['attempted_at'],
                'data_origin':'synthetic' if context.get('backend_kind')=='mock' else 'real',
                'health_state':state,'health_scope_sha256':key,'transition_id':uuid4().hex,
                'verdict':'recovered' if kind=='engine_recovered' else 'healthy' if healthy else 'unavailable',
                'reason':'engine_health_recovered' if kind=='engine_recovered' else 'engine_health_ok' if healthy else 'engine_health_check_failed',
                'error_kind':sample.get('error_kind') if not healthy else None,
                'environment':context.get('environment'),'run_id':context.get('run_id'),
                'observation_session_id':context.get('observation_session_id'),'backend_kind':context.get('backend_kind'),
                'target_id':context.get('target_id'),'target_instance_id':target.get('instance_id'),
                'target':target,'target_binding':'last_observed_target_context','policy':context.get('policy'),
                'policy_bundle':context.get('policy_bundle'),
                'previous_event_key':previous.get('event_key') if previous else None,
                'first_failure_at':previous.get('first_failure_at') if previous and previous['health_state']=='unavailable' else sample['observed_at'] if not healthy else None,
                'last_failure_at':previous.get('last_failure_at') if healthy and previous else sample['observed_at'] if not healthy else None,
                'failure_attempt_count':previous.get('failure_attempt_count',0) if healthy and previous else count,
                'failure_attempt_count_complete':complete,'history_window_complete':self._restore_complete,'inference_verified':False,'worker_failure_causality_verified':False}
            row['event_key']=digest(row)
            self._states[key]=deepcopy(row)
            while len(self._states)>128:self._states.pop(next(iter(self._states)))
            self._pending.append(row)
            if len(self._pending)>128:
                self._pending.pop(0);self._lost_pending=min(self._lost_pending+1,2**63-1)
            try:self._flush()
            except (OSError,ValueError):raise OSError('engine health event journal unavailable') from None
