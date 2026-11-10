"""Read host-observer projections without giving Gateway native capture privileges."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading

from .collectors import CollectionFailure
from .policy import _object_without_duplicates, _reject_json_constant
from .update_engine_link import select_request

SCHEMA = 'byul-runtime-native-receipt-feed/1'
LIMIT = 4 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}')
UUID = re.compile(r'[0-9a-f]{32}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def timestamp(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError('native receipt timestamp invalid')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('native receipt timezone required')
    return result


def read_protected(path, reader_gid, *, owner_uid=0, limit=LIMIT, tail=False):
    """Root-owned, explicitly group-readable, stable regular-file snapshot."""
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = -1
    try:
        parent = os.fstat(directory)
        if parent.st_uid not in {0, owner_uid} or stat.S_IMODE(parent.st_mode) & 0o022:
            raise PermissionError('native receipt parent must be root owned and protected')
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != owner_uid or not tail and before.st_size > limit
                or mode not in (0o600, 0o640) or mode == 0o640 and before.st_gid != reader_gid):
            raise PermissionError('native receipt root ownership and explicit reader permissions required')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            if tail:stream.seek(max(0,before.st_size-limit))
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid, s.st_gid)
        if len(raw) != (min(before.st_size,limit)if tail else before.st_size) or len(raw) > limit or identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('native receipt changed during read')
        if tail and before.st_size>limit:
            boundary=raw.find(b'\n');raw=raw[boundary+1:]if boundary>=0 else b''
        return raw
    finally:
        if descriptor >= 0:os.close(descriptor)
        os.close(directory)


def read_host_feed(path, reader_gid):
    return json.loads(read_protected(path,reader_gid).decode('utf-8','strict'), object_pairs_hook=_object_without_duplicates, parse_constant=_reject_json_constant)


class NativeReceiptFeed:
    def __init__(self, path, *, reader_gid, producer_source_sha256, stale_after_s=15, receipt_store=None):
        if type(reader_gid) is not int or reader_gid < 0 or not isinstance(producer_source_sha256,str) or HEX.fullmatch(producer_source_sha256) is None:
            raise ValueError('native receipt explicit reader GID and pinned producer source required')
        if type(stale_after_s)not in (int,float) or not math.isfinite(stale_after_s) or not 0<stale_after_s<=300:
            raise ValueError('native receipt finite freshness window required')
        self.path=Path(path);self.reader_gid=reader_gid;self.source_sha=producer_source_sha256
        self.stale_after_s=stale_after_s;self._document=None;self._historical={};self._lock=threading.Lock()
        self._receipt_store=receipt_store;self._saved_scores={};self._saved_failures={};self._related_history={};self._pending_history={};self._pending_history_dropped=0;self._history_storage_error=False
        self._restore_history()

    def _fresh(self, document):
        age=(datetime.now(timezone.utc)-timestamp(document['generated_at'])).total_seconds()
        return -5 <= age <= self.stale_after_s

    def _capture_fresh(self, document):
        if document.get('capture_state')!='active' or document.get('capture_observed_at')is None:return False
        age=(datetime.now(timezone.utc)-timestamp(document['capture_observed_at'])).total_seconds()
        return -5<=age<=self.stale_after_s

    def _status(self, document=None):
        document=document or {}
        return {'state':'observations_only'if document.get('capture_state')=='active'else'not_monitoring',
            'path':str(self.path),'producer_updated_at':document.get('generated_at'),
            'capture_state':document.get('capture_state','unavailable'),
            'capture_health_reason':document.get('capture_health_reason'),
            'producer_fresh':bool(document and self._fresh(document)),
            'capture_observed_at':document.get('capture_observed_at'),
            'capture_fresh':bool(document and self._capture_fresh(document)),
            'source_event_freshness':'unknown','source_event_times':document.get('source_event_times',[]),
            'future_observation_available':None if document.get('capture_state')=='active'else False,
            'observed_requests':len(document.get('entries',[])),'historical_receipts':len(self._historical)+sum(len(v) for v in self._related_history.values()),
            'historical_pending_receipts':len(self._pending_history),'historical_pending_dropped':self._pending_history_dropped,
            'historical_storage_state':'error' if self._history_storage_error or self._pending_history_dropped else 'connected' if self._receipt_store is not None else 'memory_only',
            'truncated':document.get('truncated')is True,'producer_source_sha256':self.source_sha}

    def _validate_document(self, document):
        if (type(document) is not dict or document.get('schema') != SCHEMA or document.get('producer_source_sha256') != self.source_sha
                or document.get('state') != 'ok' or document.get('producer_authenticated')is not False
                or document.get('source_data_origin')not in {'EXAMPLE','DECLARED','MEASURED'}
                or document.get('capture_state')not in {'active','closed','bound_reached','stopped','unavailable'}
                or type(document.get('source_event_times'))is not list or len(document['source_event_times'])>64
                or type(document.get('entries')) is not list or len(document['entries']) > 100):
            raise ValueError('native receipt producer unavailable')
        published=timestamp(document['generated_at'])
        if (published-datetime.now(timezone.utc)).total_seconds()>5:raise ValueError('native producer timestamp is in future')
        if document.get('capture_observed_at')is not None:
            captured=timestamp(document['capture_observed_at'])
            if (captured-datetime.now(timezone.utc)).total_seconds()>5:raise ValueError('native capture timestamp is in future')
        for event_time in document['source_event_times']:
            if type(event_time)is not dict or type(event_time.get('monotonic_ns'))is not int or event_time['monotonic_ns']<0:
                raise ValueError('native source event clock invalid')
            if set(event_time)!={'pid','start_ticks','monotonic_ns','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256'}:
                raise ValueError('native source clock context invalid')
            for key,value in event_time.items():
                if value is None:continue
                if key in {'boot_sha256','time_offsets_sha256'}:
                    if type(value)is not str or HEX.fullmatch(value)is None:raise ValueError('native source clock hash invalid')
                elif type(value)is not int or value<0:raise ValueError('native source clock number invalid')
        seen=set()
        for entry in document['entries']:
            if type(entry) is not dict or entry.get('capture_state')!=document['capture_state'] or entry.get('capture_observed_at')!=document.get('capture_observed_at') or UUID.fullmatch(str(entry.get('request_id',''))) is None or entry['request_id'] in seen:
                raise ValueError('native receipt request identity invalid')
            seen.add(entry['request_id'])
            if (HEX.fullmatch(str(entry.get('entry_sha256','')))is None
                    or hashlib.sha256(canonical({k:v for k,v in entry.items()if k!='entry_sha256'})).hexdigest()!=entry['entry_sha256']):
                raise ValueError('native receipt content hash mismatch')
            proof=entry.get('projection');ref=entry.get('source_capture')
            if (type(proof) is not dict or proof.get('schema') != 'byul-runtime-termination/1'
                    or type(ref) is not dict or HEX.fullmatch(str(ref.get('sha256',''))) is None
                    or type(proof.get('request'))is not dict or type(proof.get('native_projection'))is not dict
                    or type(proof.get('runtime_native_binding'))is not dict
                    or type(proof.get('native_projection',{}).get('anchor'))is not dict
                    or type(proof.get('native_termination_verified'))is not bool or type(proof.get('worker_gpu_release_verified'))is not bool
                    or HEX.fullmatch(str(entry.get('selected_external_request_sha256','')))is None
                    or type(ref.get('bytes')) is not int or not 0 < ref['bytes'] <= (64*1024*1024 if ref.get('capture_mode')=='live_checkpoint_rolling' and proof.get('source_capture_mode')=='live_checkpoint_rolling' and proof.get('checkpoint_coverage_verified')is True and proof['native_projection'].get('rolling_segments_verified')is True else 16*1024*1024)
                    or HEX.fullmatch(str(ref.get('target_sha256',''))) is None
                    or UUID.fullmatch(str(ref.get('session',''))) is None
                    or proof.get('source_data_origin') != document['source_data_origin']
                    or HEX.fullmatch(str(entry.get('projection_sha256',''))) is None
                    or hashlib.sha256(canonical(proof)).hexdigest() != entry['projection_sha256']):
                raise ValueError('native receipt projection reference invalid')
            checkpoint = (proof.get('closure_basis') == 'live_checkpoint'
                and proof.get('checkpoint_coverage_verified') is True
                and proof['native_projection'].get('closure_basis') == 'live_checkpoint'
                and proof['native_projection'].get('checkpoint_coverage_verified') is True)
            if entry.get('session_sealed') is not True and not checkpoint and (proof.get('native_termination_verified') is True or proof.get('worker_gpu_release_verified') is True or proof.get('native_projection',{}).get('whole_scope_complete') is True):
                raise ValueError('live native receipt lacks verified checkpoint coverage')

    @staticmethod
    def _score(entry):
        proof=entry.get('projection') or {};native=proof.get('native_projection') or {}
        if native.get('activation_violations'):return 3
        if proof.get('native_termination_verified') is True:return 2
        return 1

    @staticmethod
    def _failure_key(entry):
        violations=entry['projection']['native_projection'].get('activation_violations',[])
        identities=sorted({(v.get('code'),v.get('scope')) for v in violations},key=lambda v:canonical(v))
        return hashlib.sha256(canonical(identities)).hexdigest()

    def _remember(self,entry):
        key=entry['request_id'];previous=self._historical.get(key)
        if previous is None or self._score(entry)>=self._score(previous):
            if previous is not None and self._score(entry)==self._score(previous)==3 and self._failure_key(entry)!=self._failure_key(previous):
                related=self._related_history.setdefault(key,{})
                related[self._failure_key(previous)]=deepcopy(previous)
                related.pop(self._failure_key(entry),None)
            self._historical[key]=deepcopy(entry)
        self._trim_history()

    def _trim_history(self):
        def entries():
            return list(self._historical.values())+[e for related in self._related_history.values() for e in related.values()]
        while len(entries())>100 or sum(len(canonical(v)) for v in entries())>LIMIT:
            removed=next(iter(self._historical));self._historical.pop(removed)
            self._related_history.pop(removed,None);self._saved_scores.pop(removed,None);self._saved_failures.pop(removed,None)

    def restore_matching(self, records):
        """Accept already bounded journal matches; perform no filesystem reads.

        Current producer pin and every original content hash are revalidated.
        Exact Gateway context remains lookup's final attachment boundary.
        """
        if type(records) is not list or len(records)>4096:return 0
        candidates=[]
        for row in records:
            if type(row) is not dict or row.get('kind')!='source_qualified_native_receipt':continue
            document=row.get('native_feed_document')
            try:
                if type(document) is not dict or type(document.get('entries')) is not list or len(document['entries'])!=1:continue
                if len(canonical(document))>LIMIT:continue
                self._validate_document(document)
                entry=document['entries'][0];context=entry['projection']['request'].get('context') or {}
                if row.get('request_id')!=entry['request_id'] or context.get('request_id')!=entry['request_id']:continue
                if row.get('run_id') is not None and row['run_id']!=context.get('run_id'):continue
                if not (entry.get('session_sealed') is True or entry['projection'].get('checkpoint_coverage_verified') is True):continue
                candidates.append((timestamp(document['generated_at']),entry))
            except (ValueError,TypeError,KeyError,AttributeError,RecursionError):continue
        with self._lock:
            for _,entry in sorted(candidates,key=lambda row:row[0]):
                key=entry['request_id'];score=self._score(entry)
                self._remember(entry)
                if key in self._historical:
                    self._saved_scores[key]=max(score,self._saved_scores.get(key,0))
                    if score==3:self._saved_failures.setdefault(key,set()).add(self._failure_key(entry))
        return len(candidates)

    def _restore_history(self):
        if self._receipt_store is None:return
        try:
            rows,_,_=self._receipt_store.read_recent()
            self.restore_matching(rows)
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
            self._history_storage_error=True

    def _persist_entry(self, document, entry):
        if self._receipt_store is None:return
        score=self._score(entry);key=entry['request_id']
        previous_score=self._saved_scores.get(key,0)
        if score<previous_score or score==previous_score and (score!=3 or self._failure_key(entry) in self._saved_failures.get(key,set())):return
        pending_key=(key,self._failure_key(entry) if score==3 else str(score))
        saved=deepcopy(document);saved['entries']=[deepcopy(entry)]
        self._pending_history[pending_key]=saved
        while len(self._pending_history)>100 or sum(len(canonical(v)) for v in self._pending_history.values())>LIMIT:
            self._pending_history.pop(next(iter(self._pending_history)))
            self._pending_history_dropped=min(self._pending_history_dropped+1,2**63-1)
        self._flush_pending_history()

    def _flush_pending_history(self):
        if self._receipt_store is None or not self._pending_history:return
        # Retry original unsaved proofs before accepting a weaker later receipt.
        pending=sorted(list(self._pending_history.items()),key=lambda row:self._score(row[1]['entries'][0]),reverse=True)
        for pending_key,saved in pending:
            entry=saved['entries'][0];key=entry['request_id'];score=self._score(entry)
            previous=self._saved_scores.get(key,0)
            if score<previous or score==previous and (score!=3 or self._failure_key(entry) in self._saved_failures.get(key,set())):
                self._pending_history.pop(pending_key,None);continue
            try:
                self._receipt_store.append({'kind':'source_qualified_native_receipt','request_id':key,
                    'received_at':saved['generated_at'],'run_id':entry['projection']['request']['context'].get('run_id'),
                    'native_feed_document':saved,'source_bytes_retention':'not_guaranteed'})
                self._saved_scores[key]=score
                if score==3:self._saved_failures.setdefault(key,set()).add(self._failure_key(entry))
                self._pending_history.pop(pending_key,None)
            except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
                self._history_storage_error=True
                return
        self._history_storage_error=bool(self._pending_history)

    def collect(self):
        self._flush_pending_history()
        try:
            document=read_host_feed(self.path,self.reader_gid)
            self._validate_document(document)
            with self._lock:
                self._document=deepcopy(document)
                for entry in document['entries']:
                    if (document['capture_state']=='closed' or entry['projection'].get('closure_basis')=='live_checkpoint'
                            and entry['projection'].get('checkpoint_coverage_verified') is True):
                        self._remember(entry)
                        if entry['request_id'] in self._historical:self._persist_entry(document,entry)
                status=self._status(document)
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
            with self._lock:
                self._document=None
                status=self._status()
            raise CollectionFailure('native receipt observer unavailable',last_value=status,data_origin=None) from None
        origin={'EXAMPLE':'synthetic','DECLARED':'declared','MEASURED':'local_file'}[document['source_data_origin']]
        if self._history_storage_error or self._pending_history_dropped:
            raise CollectionFailure('native receipt history journal unavailable',last_value=status,data_origin=origin)
        if not self._fresh(document) or document['capture_state']!='active' or not self._capture_fresh(document):
            reason='checkpoint_stalled' if document.get('capture_health_reason')=='checkpoint_stalled' else document['capture_state']
            raise CollectionFailure('native source cannot observe future requests ('+reason+')',last_value=status,data_origin=origin)
        return status,origin

    def lookup(self, records, request_id, current_session_id):
        with self._lock:
            document=deepcopy(self._document);historical=deepcopy(self._historical.get(request_id))
            related=deepcopy(list(self._related_history.get(request_id,{}).values()))
        try:selected=select_request(records,request_id)
        except (ValueError,KeyError,TypeError,AttributeError):return None
        live=bool(document is not None and self._fresh(document)and self._capture_fresh(document))
        entry=(next((e for e in document['entries']if e['request_id']==request_id),None)if live else None)or historical
        if entry is not None and historical is not None and self._score(historical)>self._score(entry):entry=historical
        if entry is not None and entry is not historical and entry['capture_state']!='closed'and selected['context'].get('observation_session_id') != current_session_id:
            # A fresh producer can still include a prior Gateway session's
            # qualified checkpoint. Serve it as history, never as current work.
            proof=entry.get('projection')or{};native=proof.get('native_projection')or{}
            if not (proof.get('closure_basis')=='live_checkpoint'
                    and proof.get('checkpoint_coverage_verified')is True
                    and native.get('closure_basis')=='live_checkpoint'
                    and native.get('checkpoint_coverage_verified')is True):return None
            historical=entry
        if entry is None:return None
        if entry is historical:
            entry['historical_receipt']=True
            entry['source_bytes_retention']='not_guaranteed'
        proof=entry['projection'];native=proof.get('native_projection')or{};anchor=native.get('anchor')or{}
        observed=deepcopy(proof.get('request'))
        # QueryService adds this null only to legacy policy snapshots. Preserve
        # that exact existing normalization while comparing every context value.
        for request in (selected,observed):
            if type(request)is dict and type(request.get('context',{}).get('policy'))is dict:
                request['context']['policy'].setdefault('schema_version',None)
        upstream=selected['context'].get('upstream_request_id')or request_id
        if not isinstance(upstream,str):return None
        expected=hashlib.sha256(upstream.encode()).hexdigest()
        if (observed != selected or proof.get('runtime_native_binding',{}).get('state')!='bound'
                or native.get('binding_observed') is not True or entry['selected_external_request_sha256']!=expected):return None
        backend=selected['context'].get('backend_kind')
        if backend=='vllm':
            if anchor.get('asgi_request_sha256')!=expected or anchor.get('physical_process_binding_verified')is not True:return None
        elif backend!='sglang':return None
        if related:
            matching=[]
            for other in related:
                other_request=deepcopy(other['projection'].get('request'))
                if type(other_request) is dict and type(other_request.get('context',{}).get('policy')) is dict:
                    other_request['context']['policy'].setdefault('schema_version',None)
                if other_request!=selected or other.get('selected_external_request_sha256')!=expected:continue
                other['historical_receipt']=True;other['source_bytes_retention']='not_guaranteed';matching.append(other)
            if matching:entry['historical_related_receipts']=matching
        return deepcopy(entry)
