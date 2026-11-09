"""Read-only exact EVD/runtime linkage and bounded runtime HTTP cohort runner."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from pathlib import Path

from guard.models.objects import Evidence
from guard.runtime.bounded import strict_json
from serving_runtime.mvp.update_engine_link import link_evidence, project_termination
from serving_runtime.mvp.target import _image_digest

ROLLING_CAPTURE_LIMIT=64*1024*1024


def read(path, limit=16 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('ENGINE_LINK_INPUT_TOO_LARGE')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    link = sub.add_parser('link')
    for name in ('records', 'request-id', 'evidence', 'artifact', 'artifact-name', 'output'):
        link.add_argument('--' + name, required=True)
    probe = sub.add_parser('probe')
    probe.add_argument('--requests', required=True, help='existing stream_probe request array; synthetic fixture text')
    probe.add_argument('--timeout', type=float, required=True)
    probe.add_argument('--output', required=True)
    probe.add_argument('--normal-id')
    probe.add_argument('--max-normal-ms', type=float)
    native = sub.add_parser('termination')
    for name in ('records', 'request-id', 'packet', 'output'):
        native.add_argument('--' + name, required=True)
    native.add_argument('--max-delay-ms', type=int, required=True)
    native.add_argument('--required-scopes', nargs='+', required=True)
    watch = sub.add_parser('watch', help='host-only continuous native proof feed; never seals observer sessions')
    for name in ('environment','target','prepared','records','output','capture-dir'):
        watch.add_argument('--'+name,required=True)
    watch.add_argument('--reader-gid',type=int,required=True)
    watch.add_argument('--gateway-writer-uid',type=int,default=10000)
    watch.add_argument('--interval',type=float,default=5)
    watch.add_argument('--duration',type=float,default=30)
    watch.add_argument('--max-delay-ms',type=int,required=True)
    watch.add_argument('--required-scopes',nargs='+',required=True)
    watch.add_argument('--node-image-verification',choices=['local-containerd-v1'])
    watch.add_argument('--packet',help='original immutable sealed capture, never rewritten')
    watch.add_argument('--packet-sha256')
    watch.add_argument('--once',action='store_true')
    capture = sub.add_parser('capture')
    for name in ('environment', 'target', 'prepared', 'output'):
        capture.add_argument('--' + name, required=True)
    capture.add_argument('--duration', type=float, default=30)
    capture.add_argument('--node-image-verification',choices=['local-containerd-v1'])
    capture.add_argument('--rolling',action='store_true',help='read opt-in rolling native segments')
    capture.add_argument('--checkpoint', action='store_true', help='observe immutable live checkpoint prefixes without sealing')
    capture.add_argument('--seal',action='store_true',help='seal exactly the selected observer session before capture')
    args = parser.parse_args(argv)
    if args.command == "watch":
        return watch_native_receipts(args)
    if args.command == 'probe' and (args.normal_id is not None or args.max_normal_ms is not None):
        if (not args.normal_id or args.max_normal_ms is None or not math.isfinite(args.max_normal_ms)
                or args.max_normal_ms <= 0):
            raise ValueError('ENGINE_LINK_EXPLICIT_NORMAL_SLO_REQUIRED')
    if args.command == 'link':
        rows = [strict_json(line, limit=1024 * 1024) for line in read(args.records).splitlines() if line.strip()]
        evd = Evidence.model_validate(strict_json(read(args.evidence), limit=16 * 1024 * 1024))
        result = link_evidence(rows, args.request_id, evd, args.artifact_name, read(args.artifact))
    elif args.command == 'termination':
        rows = [strict_json(line, limit=1024 * 1024) for line in read(args.records).splitlines() if line.strip()]
        raw = read(args.packet)
        source = strict_json(raw, limit=16 * 1024 * 1024)
        result = project_termination(rows, args.request_id, source['packet'], args.max_delay_ms, args.required_scopes,
                                     source_binding=source.get('transport',{}).get('binding'),source_data_origin=source.get('data_origin'))
        # Keep the exact source capture and its immutable target/environment
        # declarations with the projection; the projector's truth flags remain.
        result['source_capture'] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                                    'environment_ref': source['environment_ref'],
                                    'target_sha256': source['target_sha256'], 'transport': source['transport']}
        expected = result['request']['context'].get('target') or {}
        binding = source['transport'].get('binding') or {}
        engine = binding.get('engine') or {}
        expected_image = (_image_digest(expected['image_digest'])
                          if isinstance(expected.get('image_digest'), str) else None)
        observed_image = (_image_digest(engine['image_id'])
                          if isinstance(engine.get('image_id'), str) else None)
        result['runtime_target_generation_observed'] = bool(
            all(expected.get(key) for key in ('pod_uid', 'container_id', 'instance_id'))
            and expected_image is not None and observed_image is not None
            and engine.get('pod_uid') == expected.get('pod_uid')
            and engine.get('container_id') == expected.get('container_id')
            and observed_image == expected_image
            and result['request']['context'].get('run_id')
            and binding.get('run_id') == result['request']['context']['run_id'])
    elif args.command == 'capture':
        from guard.models import Environment, canonical_json, sha256_hex
        from guard.collectors.lifecycle import load_prepared
        from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
        from guard.executors.lifecycle_session import capture_session
        env = Environment.model_validate(strict_json(read(args.environment), limit=16 * 1024 * 1024))
        if not env.hash_ok():
            raise ValueError('ENGINE_LINK_ENVIRONMENT_NOT_SEALED')
        target = strict_json(read(args.target), limit=16 * 1024 * 1024)
        prepared = load_prepared(args.prepared)
        options={'duration':args.duration}
        if args.node_image_verification is not None:options['node_image_verification']=args.node_image_verification
        reader = KubernetesLifecycleReader(target, env, prepared, **options)
        reader.snapshot()
        sealed=None
        if args.seal and args.checkpoint:raise ValueError('ENGINE_LINK_SEAL_CHECKPOINT_EXCLUSIVE')
        if args.seal:sealed=reader.seal_session().get('sealed')is True
        engine = reader.binding['engine']
        packet = capture_session(reader, target, container=engine['container'], image=engine['image'],
                                 prepared=prepared, owner_identity=reader.owner_identity,
                                 read_file=reader.read_file, image_check=reader.image_check, checkpoint=args.checkpoint, rolling=args.rolling)
        result = {'schema': 'byul-runtime-native-capture/1', 'packet': packet,
                  'data_origin':'EXAMPLE'if getattr(reader,'injected',True)is not False else env.data_origin.value,
                  'environment_ref': env.ref().model_dump(mode='json'),
                  'target_sha256': sha256_hex(canonical_json(target)),
                  'transport': {**reader.report(), 'binding': reader.binding},
                  **({'session_sealed':sealed}if args.seal else{})}
    else:
        from guard.executors.stream_probe import collect
        requests = strict_json(read(args.requests, 128 * 1024), limit=128 * 1024)
        rows = collect(requests, args.timeout)
        # Credentials, fixture text and generated response text never enter the
        # persisted runtime adapter artifact. The original probe supplies hashes.
        keys = ('id', 'outcome', 'capture_complete', 'body_bytes', 'body_sha256', 'dispatch_ms',
                'request_sent_ms', 'headers_ms', 'end_ms', 'client_closed', 'stop_reason', 'terminal_seen')
        result = {'schema': 'byul-runtime-probe/1', 'rows': [
            {**{key: row.get(key) for key in keys},
             'runtime_request_id': (row.get('response_headers') or {}).get('x-request-id')}
            for row in rows], 'actual_engine_execution_verified': False,
            'probe_source_sha256': hashlib.sha256(Path(__import__('guard.executors.stream_probe', fromlist=['x']).__file__).read_bytes()).hexdigest()}
        if args.normal_id is not None or args.max_normal_ms is not None:
            normal = [row for row in rows if row.get('id') == args.normal_id]
            if len(normal) != 1:
                raise ValueError('ENGINE_LINK_UNIQUE_NORMAL_REQUEST_REQUIRED')
            row = normal[0]
            latency = (row['end_ms'] - row['dispatch_ms'] if type(row.get('end_ms')) in (int, float)
                       and type(row.get('dispatch_ms')) in (int, float) else None)
            result['normal_http_slo'] = {'id': args.normal_id, 'budget_ms': args.max_normal_ms,
                'latency_ms': latency, 'http_completed': row.get('outcome') == 200 and row.get('capture_complete') is True,
                'within_budget': latency <= args.max_normal_ms if latency is not None else None}
    # A new artifact is never allowed to silently replace an earlier capture.
    with Path(args.output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return 0


@contextmanager
def _temporary_capture(archive):
    """Remove this poll's partial capture on every exit, including parse failure."""
    temporary = archive / ('capture-' + uuid.uuid4().hex + '.json')
    try:
        yield temporary
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _archive_capture(temporary, saved, raw, reader_gid, limit):
    """Publish immutable capture bytes without replacing a concurrent original."""
    from serving_runtime.mvp.native_receipts import read_protected
    try:
        os.link(temporary, saved, follow_symlinks=False)
    except FileExistsError:
        if read_protected(saved, reader_gid, owner_uid=0, limit=limit) != raw:
            raise ValueError('NATIVE_RECEIPT_CAPTURE_ARCHIVE_CONFLICT')
    temporary.unlink()


def _current_checkpoint_health(packet):
    """Current census health only; retain every historical row for adjudication."""
    from guard.executors.lifecycle_checkpoint import covered, recording_covered, marker, canonical
    from guard.adapters.vllm_lifecycle_profiles import source_hashes
    try:
        if not covered(packet):return False
        before=packet['before'];owner=before['owner'];session=packet['session']
        live={(row['pid'],row['start_ticks']) for row in before['metadata']['processes'] if row['same_executable']}
        canonical_items=[item for item in packet['recordings'] if item.get('segment') is None]
        def context(item):return item['capture']['records'][0]['payload']['context']
        def identity(item):
            value=context(item);return value['pid'],value['start_ticks']
        current=[item for item in canonical_items if identity(item) in live]
        if not live or len(current)!=len(live) or {identity(item) for item in current}!=live:return False
        reference=current[0]['capture']['records'][0]['payload'];clock=context(current[0])
        header_fields=('profile','session','sensor_sha256','settings_sha256')
        if reference.get('session')!=session or any(re.fullmatch(r'[0-9a-f]{64}',reference.get(key) or '') is None for key in ('sensor_sha256','settings_sha256')):return False
        sources=source_hashes(reference['profile'])
        cutoffs=[]
        for item in current:
            cap=item['capture'];rows=cap['records'];header=rows[0]['payload']
            if any(header.get(key)!=reference.get(key) for key in header_fields):return False
            stamp=rows[-1]['monotonic_ns']
            if not recording_covered(item,session,stamp,clock,packet=packet):return False
            cutoffs.append(stamp)
        cutoff=min(cutoffs)
        for item in canonical_items:
            if identity(item) in live:continue
            cap=item['capture'];rows=cap['records'];header=rows[0]['payload'];old=context(item);summary=cap['summary']
            expected={'pid':old['pid'],'start_ticks':old['start_ticks'],'recording_end':'PROCESS_EXIT','recording_closed':True,'result':'process_exited'}
            if (not all(item.get(key) is True for key in ('session_owner_match','listed_file_identity_match'))
                    or cap.get('process_context_verified') is not False or cap.get('image_digest_verified') is not True
                    or cap.get('provenance',{}).get('owner')!=owner or cap.get('process_recheck')!=expected
                    or len(rows)<2 or rows[0].get('event')!='START' or rows[-1].get('event')!='END'
                    or rows[-1].get('payload')!={'reason':'PROCESS_EXIT'}
                    or summary.get('recording_closed') is not True or summary.get('recording_end')!='PROCESS_EXIT'
                    or summary.get('incomplete_final_row') is not False or summary.get('discarded_tail_bytes')!=0
                    or any(header.get(key)!=reference.get(key) for key in header_fields)
                    or any(old.get(key)!=clock.get(key) for key in ('boot_sha256','pid_namespace','time_namespace','time_offsets_sha256','uid'))
                    or type(rows[-1].get('monotonic_ns')) is not int or not 0<=rows[-1]['monotonic_ns']<cutoff):return False
            previous='0'*64
            for seq,row in enumerate(rows):
                if row.get('seq')!=seq or row.get('previous_sha256')!=previous:return False
                previous=hashlib.sha256(canonical(row)).hexdigest()
            if hashlib.sha256(b''.join(canonical(row) for row in rows)).hexdigest()!=cap.get('provenance',{}).get('capture_sha256'):return False
            for row in rows[1:-1]:
                if marker(row,session):continue
                payload=row.get('payload')or{}
                if (row.get('event')!='MODULE' or payload.get('verified') is not True
                        or payload.get('module') not in sources or payload.get('source_sha256')!=sources[payload['module']]
                        or re.fullmatch(r'[0-9a-f]{64}',payload.get('code_sha256')or'') is None):return False
        return True
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):return False


def _capture_health(packet):
    canonical_items=[row for row in packet.get('recordings',[]) if row.get('segment') is None]
    endings=[(row.get('capture')or{}).get('summary',{}).get('recording_end') for row in canonical_items]
    if 'BOUND_REACHED' in endings:return 'bound_reached',None
    if all((packet.get(side)or{}).get('metadata',{}).get('sealed') is True for side in ('before','after')):return 'closed',None
    checkpoint=packet.get('capture_mode') in {'live_checkpoint','live_checkpoint_rolling'}
    if checkpoint and _current_checkpoint_health(packet):return 'active',None
    if any(value is not None for value in endings):return 'stopped',None
    if not checkpoint and endings and all((row.get('capture')or{}).get('process_context_verified') is True for row in canonical_items):return 'active',None
    if any('LIFECYCLE_CHECKPOINT_STALE_OR_UNAVAILABLE' in (row.get('capture')or{}).get('diagnostic_codes',[]) for row in packet.get('recordings',[])):return 'unavailable','checkpoint_stalled'
    return 'unavailable',None


def watch_native_receipts(args):
    """Run only in the existing root host observer/UPDATE environment.

    Capture and project with the established functions. Gateway receives no
    packet reader, kubectl command, original sensor rows, or native privilege.
    """
    from datetime import datetime, timezone
    from guard.models import Environment, canonical_json, sha256_hex
    from guard.collectors.lifecycle import load_prepared
    from serving_runtime.mvp.native_receipts import SCHEMA,canonical,read_protected
    from serving_runtime.mvp.update_engine_link import select_request
    if (os.geteuid()!=0 or type(args.reader_gid)is not int or args.reader_gid<0
            or not math.isfinite(args.interval) or not .1<=args.interval<=300
            or not math.isfinite(args.duration) or not 0<args.duration<=300
            or type(args.gateway_writer_uid)is not int or args.gateway_writer_uid<0
            or bool(args.packet)!=bool(args.packet_sha256)):
        raise ValueError('NATIVE_RECEIPT_ROOT_OBSERVER_CONFIGURATION_REQUIRED')
    # The original source-qualified constructors still check ENV/target/prepared.
    env=Environment.model_validate(strict_json(read(args.environment),limit=16*1024*1024))
    if not env.hash_ok():raise ValueError('ENGINE_LINK_ENVIRONMENT_NOT_SEALED')
    target=strict_json(read(args.target),limit=16*1024*1024)
    target_sha=sha256_hex(canonical_json(target));prepared=load_prepared(args.prepared)
    from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
    KubernetesLifecycleReader(target,env,prepared,duration=args.duration,node_image_verification=args.node_image_verification)
    output=Path(args.output);archive=Path(args.capture_dir)
    for directory in (output.parent,archive):
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022:
            raise ValueError('NATIVE_RECEIPT_ROOT_OWNED_DIRECTORY_REQUIRED')
    if stat.S_IMODE(archive.stat().st_mode)&0o077:
        raise ValueError('NATIVE_RECEIPT_PRIVATE_CAPTURE_DIRECTORY_REQUIRED')
    archive=archive/prepared['session']
    archive.mkdir(mode=0o700,exist_ok=True)
    info=archive.lstat()
    if not stat.S_ISDIR(info.st_mode)or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('NATIVE_RECEIPT_PRIVATE_SESSION_DIRECTORY_REQUIRED')
    producer_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    from guard.adapters.vllm_request_termination import SCOPES
    if type(args.max_delay_ms)is not int or not 1<=args.max_delay_ms<=300000 or not args.required_scopes or not set(args.required_scopes)<=SCOPES:
        raise ValueError('NATIVE_RECEIPT_ORIGINAL_SCOPE_CONFIGURATION_REQUIRED')
    while True:
        document={'schema':SCHEMA,'producer_source_sha256':producer_sha,'generated_at':datetime.now(timezone.utc).isoformat(),'state':'unavailable','producer_authenticated':False,'source_data_origin':None,'capture_state':'unavailable','capture_observed_at':None,'source_event_times':[],'entries':[]}
        current_live=None
        try:
            if args.packet:
                raw=read_protected(args.packet,args.reader_gid,owner_uid=0,limit=16*1024*1024)
                if hashlib.sha256(raw).hexdigest()!=args.packet_sha256:raise ValueError('NATIVE_RECEIPT_CAPTURE_HASH_MISMATCH')
            else:
                with _temporary_capture(archive) as temporary:
                    capture_args=['capture','--environment',args.environment,'--target',args.target,'--prepared',args.prepared,'--output',str(temporary),'--duration',str(args.duration),'--checkpoint','--rolling']
                    if args.node_image_verification:capture_args+=['--node-image-verification',args.node_image_verification]
                    # No --seal argument exists on this continuous route.
                    main(capture_args)
                    temporary.chmod(0o600);raw=read(temporary,limit=ROLLING_CAPTURE_LIMIT)
                    document['capture_observed_at']=datetime.now(timezone.utc).isoformat()
                    digest=hashlib.sha256(raw).hexdigest()
                    archived=strict_json(raw,limit=ROLLING_CAPTURE_LIMIT)
                    was_sealed=all((archived.get('packet',{}).get(side)or{}).get('metadata',{}).get('sealed')is True for side in ('before','after'))
                    saved=archive/(('sealed-'+prepared['session'])if was_sealed else('live-'+digest))
                    saved=saved.with_suffix('.json')
                    _archive_capture(temporary, saved, raw, args.reader_gid, ROLLING_CAPTURE_LIMIT)
                    if not was_sealed:current_live=saved
            source=strict_json(raw,limit=ROLLING_CAPTURE_LIMIT if not args.packet else 16*1024*1024)
            packet=source['packet'];transport=source['transport']
            if (source.get('schema')!='byul-runtime-native-capture/1' or source.get('target_sha256')!=target_sha
                    or source.get('environment_ref')!=env.ref().model_dump(mode='json')
                    or packet.get('session')!=prepared['session'] or transport.get('collection_gap') is not False
                    or type(transport.get('injected_runner',True))is not bool
                    or source.get('data_origin')!=('EXAMPLE'if transport.get('injected_runner',True) else env.data_origin.value)):
                raise ValueError('NATIVE_RECEIPT_ORIGINAL_CAPTURE_BINDING_REQUIRED')
            if args.packet and source.get('session_sealed') is not True:
                raise ValueError('NATIVE_RECEIPT_SEALED_IMPORT_REQUIRED')
            observed_sealed=all((packet.get(side)or{}).get('metadata',{}).get('sealed')is True for side in ('before','after'))
            capture_state,health_reason=_capture_health(packet)
            if health_reason is not None:document['capture_health_reason']=health_reason
            document['capture_state']=capture_state
            document['source_data_origin']=source['data_origin']
            for recording in packet.get('recordings',[])[:64]:
                captured=recording.get('capture')or{};events=captured.get('records')or[]
                if not events:continue
                context=events[0].get('payload',{}).get('context',{})
                stamp=events[-1].get('monotonic_ns')
                if type(stamp)is not int:continue
                document['source_event_times'].append({'monotonic_ns':stamp,**{k:context.get(k)for k in ('pid','start_ticks','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')}})
            rows=[strict_json(line,limit=1024*1024)for line in read_protected(args.records,args.reader_gid,owner_uid=args.gateway_writer_uid,limit=16*1024*1024,tail=True).splitlines()if line.strip()]
            request_ids=list(dict.fromkeys(r.get('request_id')for r in rows if isinstance(r,dict)and isinstance(r.get('request_id'),str)))[-100:]
            encoded_bytes=0
            projection_deadline=time.monotonic()+args.duration
            for rid in reversed(request_ids):
                if time.monotonic()>projection_deadline:
                    document['truncated']=True;break
                try:
                    selected=select_request(rows,rid)
                    if selected['context'].get('run_id')!=transport.get('binding',{}).get('run_id'):continue
                    projection=project_termination(rows,rid,packet,args.max_delay_ms,args.required_scopes,
                        source_binding=transport.get('binding'),source_data_origin=source.get('data_origin'),
                        checkpoint=packet.get('capture_mode') in {'live_checkpoint','live_checkpoint_rolling'})
                    native=projection.get('native_projection')or{}
                    if projection.get('runtime_native_binding',{}).get('state')!='bound' or native.get('binding_observed')is not True:continue
                    entry={'request_id':rid,'projection':projection,
                        'capture_state':capture_state,'capture_observed_at':document['capture_observed_at'],
                        'selected_external_request_sha256':hashlib.sha256((selected['context'].get('upstream_request_id')or rid).encode()).hexdigest(),
                        'projection_sha256':hashlib.sha256(canonical(projection)).hexdigest(),
                        'session_sealed':observed_sealed,
                        'source_capture':{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                            'environment_ref':source['environment_ref'],'target_sha256':target_sha,'session':packet['session'],
                            **({'capture_mode':'live_checkpoint_rolling'} if packet.get('capture_mode')=='live_checkpoint_rolling' else {})}}
                    entry['entry_sha256']=hashlib.sha256(canonical(entry)).hexdigest()
                    encoded_bytes+=len(canonical(entry))
                    if encoded_bytes>4*1024*1024-4096:
                        document['truncated']=True;break
                    document['entries'].append(entry)
                except (ValueError,KeyError,TypeError,AttributeError):continue
            document['state']='ok'
        except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError):
            document['entries']=[]
        document['generated_at']=datetime.now(timezone.utc).isoformat()
        encoded=canonical(document)+b'\n'
        if len(encoded)>4*1024*1024:
            document.update(state='unavailable',entries=[]);encoded=canonical(document)+b'\n'
        temporary=output.with_name('.'+output.name+'.'+uuid.uuid4().hex+'.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.fchown(fd,0,args.reader_gid);os.fchmod(fd,0o640)
            with os.fdopen(fd,'wb',closefd=False)as stream:stream.write(encoded);stream.flush();os.fsync(fd)
            os.replace(temporary,output)
        finally:
            os.close(fd)
            try:temporary.unlink()
            except FileNotFoundError:pass
        if not args.packet:
            snapshots=sorted(archive.glob('live-'+'[0-9a-f]'*64+'.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
            keep=set([current_live]) if current_live is not None else set()
            for candidate in snapshots:
                if len(keep)<2:keep.add(candidate)
            for old in snapshots:
                if old in keep:continue
                info=old.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o600:
                    old.unlink()
        if args.once:return 0
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())
