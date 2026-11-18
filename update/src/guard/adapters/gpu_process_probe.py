"""Bounded host PID/NSpid and GPU-device reference facts, without GPU contents.

An empty compute-process query alone is not resource recovery. In particular,
NVIDIA documents inherited open handles and unidentified processes:
https://docs.nvidia.com/deploy/nvidia-smi/
This probe never signals processes or qualifies whole GPU/group recovery.
"""
from __future__ import annotations
import csv,hashlib,io,json,os,re,select,stat,subprocess,sys,time
from . import resource_recovery_probe as selected
from guard.runtime.bounded import capture_pipes

SCHEMA='guard-owned-node-gpu-process-census/1'
SELECTION_SCHEMA='guard-owned-node-gpu-process-selection/1'
MAX_PIDS=4096
MAX_FDS=4096
MAX_HANDLES=32768
MAX_OUTPUT=8*1024**2
QUERIES={'devices':'--query-gpu=uuid,minor_number,mig.mode.current,memory.used',
         'compute':'--query-compute-apps=pid,gpu_uuid,used_gpu_memory',
         'compute_confirm':'--query-compute-apps=pid,gpu_uuid,used_gpu_memory'}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def _uint(value):return type(value)is int and 0<=value<2**63


def _uuid(value):return type(value)is str and re.fullmatch(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',value)is not None


def selection(value):
    try:
        if (type(value)is not dict or set(value)!={'schema','anchor','owners'}
                or value['schema']!=SELECTION_SCHEMA or type(value['anchor'])is not dict
                or set(value['anchor'])!={'host_pid','start_ticks','pid_namespace'}
                or any(not _uint(v)or v==0 for v in value['anchor'].values())
                or value['anchor']['host_pid']<=1
                or type(value['owners'])is not list or not 1<=len(value['owners'])<=64):raise ValueError
        ids=set();pids=set()
        for owner in value['owners']:
            if type(owner)is not dict or set(owner)!={'id','context'}or not selected.fingerprint(owner['id']):raise ValueError
            selected.validate_context(owner['context']);ctx=owner['context']
            if owner['id']in ids or ctx['pid']in pids or ctx['pid_namespace']!=value['anchor']['pid_namespace']:raise ValueError
            if any(ctx[k]!=value['owners'][0]['context'][k]for k in selected.NAMESPACE_FIELDS):raise ValueError
            ids.add(owner['id']);pids.add(ctx['pid'])
        if len(canonical(value))>65536:raise ValueError
        return value
    except(KeyError,TypeError,ValueError):raise ValueError('GPU_PROCESS_SELECTION_INVALID')from None


def _number(text):
    if type(text)is not str or re.fullmatch(r'0|[1-9][0-9]{0,17}',text)is None:raise ValueError('GPU_PROCESS_QUERY_INVALID')
    return int(text)


def parse_query(kind,raw):
    """Closed numeric metadata only; N/A and unidentified output stay gaps."""
    if kind not in QUERIES or type(raw)is not bytes or len(raw)>262144:raise ValueError('GPU_PROCESS_QUERY_INVALID')
    try:
        rows=list(csv.reader(io.StringIO(raw.decode('ascii')),strict=True));result=[];seen=set()
        if len(rows)>MAX_PIDS:raise ValueError
        for row in rows:
            row=[v.strip()for v in row]
            if kind=='devices':
                if len(row)!=4 or not _uuid(row[0])or row[2]not in{'Disabled','N/A','[N/A]'}:raise ValueError
                value={'uuid':row[0],'minor':_number(row[1]),'memory_used_mib':_number(row[3])}
                key=value['uuid']
                if value['minor']>=256 or any(r['minor']==value['minor']for r in result):raise ValueError
            else:
                if len(row)!=3 or not _uuid(row[1]):raise ValueError
                value={'host_pid':_number(row[0]),'uuid':row[1],'memory_used_mib':_number(row[2])}
                if value['host_pid']<=0:raise ValueError
                key=(value['host_pid'],value['uuid'])
            if key in seen:raise ValueError
            seen.add(key);result.append(value)
        if kind=='devices'and not result:raise ValueError
        return sorted(result,key=(lambda row:row['uuid'])if kind=='devices'else(lambda row:(row['host_pid'],row['uuid'])))
    except(ValueError,UnicodeError,csv.Error):raise ValueError('GPU_PROCESS_QUERY_INVALID')from None


def _query(kind,deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise ValueError('GPU_PROCESS_BUDGET_EXHAUSTED')
    result=capture_pipes(['/usr/bin/nvidia-smi',QUERIES[kind],'--format=csv,noheader,nounits'],
        timeout=min(5,remaining),env_extra={'LC_ALL':'C'},max_stdout_bytes=262144,max_stderr_bytes=32768)
    if result['returncode']or result['capture']['complete']is not True or result['stderr_bytes']:
        raise ValueError('GPU_PROCESS_QUERY_UNAVAILABLE')
    raw=result['stdout_bytes']
    return parse_query(kind,raw),{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def _pids():
    result=sorted(int(v)for v in os.listdir('/proc')if v.isascii()and v.isdecimal())
    if len(result)>MAX_PIDS:raise ValueError('GPU_PROCESS_PID_BOUND')
    return result


def _nspid(pid):
    raw=selected.read_small('/proc/'+str(pid)+'/status',32768)
    values=[line.split(b':',1)[1].split()for line in raw.splitlines()if line.startswith(b'NSpid:')]
    if len(values)!=1 or not 1<=len(values[0])<=32:raise ValueError('GPU_PROCESS_NAMESPACE_UNAVAILABLE')
    result=[_number(v.decode('ascii'))for v in values[0]]
    if result[0]!=pid or any(v==0 for v in result):raise ValueError('GPU_PROCESS_NAMESPACE_UNAVAILABLE')
    return result


def _devices(rows):
    result=[]
    for row in rows:
        value=os.stat('/dev/nvidia'+str(row['minor']),follow_symlinks=False)
        if not stat.S_ISCHR(value.st_mode)or os.minor(value.st_rdev)!=row['minor']:
            raise ValueError('GPU_PROCESS_DEVICE_UNVERIFIED')
        result.append({**row,'major':os.major(value.st_rdev)})
    return result


def _binary(deadline=None):
    path='/usr/bin/nvidia-smi';before=os.stat(path,follow_symlinks=False)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened=os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode)or opened.st_uid!=0 or opened.st_mode&0o022 or not 0<opened.st_size<=64*1024**2:
            raise ValueError('GPU_PROCESS_BINARY_UNVERIFIED')
        digest=hashlib.sha256();total=0
        while raw:=os.read(fd,65536):
            if deadline is not None and time.monotonic()>=deadline:raise ValueError('GPU_PROCESS_BUDGET_EXHAUSTED')
            total+=len(raw)
            if total>64*1024**2:raise ValueError('GPU_PROCESS_BINARY_UNVERIFIED')
            digest.update(raw)
        states=(before,opened,os.fstat(fd),os.stat(path,follow_symlinks=False))
        identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len({identity(s)for s in states})!=1 or total!=opened.st_size:raise ValueError('GPU_PROCESS_BINARY_UNVERIFIED')
        return {'sha256':digest.hexdigest(),'bytes':total,'device':opened.st_dev,'inode':opened.st_ino,
            'uid':opened.st_uid,'mode':opened.st_mode,'mtime_ns':opened.st_mtime_ns,'ctime_ns':opened.st_ctime_ns}
    finally:os.close(fd)


def _scan(pid,devices,check):
    row={'host_pid':pid,'context':None,'nspid':[],'handles':[],'stable':False,'diagnostic_codes':[]}
    pidfd=None
    try:
        pidfd=os.pidfd_open(pid,0)
        def alive():
            poll=select.poll();poll.register(pidfd,select.POLLIN)
            if poll.poll(0):raise ValueError('GPU_PROCESS_GENERATION_CHANGED')
        alive()
        before=selected.context(pid);names_before=_nspid(pid);row.update(context=before,nspid=names_before)
        targets={(d['major'],d['minor'])for d in devices};fd_root='/proc/'+str(pid)+'/fd';names=[]
        with os.scandir(fd_root)as entries:
            for entry in entries:
                check();name=entry.name;names.append(name)
                if len(names)>MAX_FDS or not name.isascii()or not name.isdecimal():raise ValueError
                path=fd_root+'/'+name;a=os.stat(path);b=os.stat(path)
                identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_rdev)
                if identity(a)!=identity(b):raise ValueError
                device=(os.major(a.st_rdev),os.minor(a.st_rdev))
                if stat.S_ISCHR(a.st_mode)and device in targets:
                    row['handles'].append({'fd':int(name),'major':device[0],'minor':device[1]})
        with os.scandir(fd_root)as entries:names_after=sorted(entry.name for entry in entries)
        if sorted(names)!=names_after or before!=selected.context(pid)or names_before!=_nspid(pid):raise ValueError
        alive()
        row['stable']=True
    except(OSError,ValueError,IndexError,UnicodeError):row['diagnostic_codes'].append('GPU_PROCESS_METADATA_UNAVAILABLE_OR_CHANGED')
    finally:
        if pidfd is not None:os.close(pidfd)
    return row


def _anchor_matches(value,row):
    anchor=value['anchor'];ctx=row['context']
    return bool(row['stable']and ctx is not None and row['host_pid']==anchor['host_pid']
        and ctx['start_ticks']==anchor['start_ticks']and ctx['pid_namespace']==anchor['pid_namespace']
        and row['nspid']and row['nspid'][-1]==1)


def owner_matches(value,processes):
    result=[]
    for owner in value['owners']:
        expected=owner['context'];matches=[]
        for row in processes:
            current=row['context']
            if(row['stable']and current is not None and row['nspid']and row['nspid'][-1]==expected['pid']
                    and all(current[k]==v for k,v in expected.items()if k!='pid')):matches.append(row['host_pid'])
        result.append({'id':owner['id'],'host_pids':matches})
    return result


def observe(value,*,process_ids=None,max_seconds=10):
    """No subset establishes host absence; the live PID1 anchor is mandatory."""
    selection(value)
    if sys.platform!='linux':raise ValueError('GPU_PROCESS_LINUX_REQUIRED')
    if type(max_seconds)is not int or not 1<=max_seconds<=20:raise ValueError('GPU_PROCESS_BUDGET_INVALID')
    if process_ids is not None and(type(process_ids)is not list or not process_ids or len(process_ids)>MAX_PIDS
            or any(not _uint(p)or p==0 for p in process_ids)or len(set(process_ids))!=len(process_ids)):
        raise ValueError('GPU_PROCESS_PID_SUBSET_INVALID')
    frozen=canonical(value);began=time.monotonic_ns();wall=time.time_ns();deadline=time.monotonic()+max_seconds
    before=selected.context(os.getpid());processes=[];devices=[];compute=[];confirmed=[];queries={};gaps=[];pids=[];end_pids=[]
    binary_before=binary_after=None
    def check():
        if time.monotonic()>=deadline:raise ValueError('GPU_PROCESS_BUDGET_EXHAUSTED')
        if sum(len(p['handles'])for p in processes)>MAX_HANDLES:raise ValueError('GPU_PROCESS_HANDLE_BOUND')
    try:
        binary_before=_binary(deadline)
        raw,queries['devices']=_query('devices',deadline);devices=_devices(raw)
        compute,queries['compute']=_query('compute',deadline)
        if any(r['uuid']not in{d['uuid']for d in devices}for r in compute):raise ValueError
        pids=sorted(process_ids)if process_ids is not None else _pids()
        for pid in pids:check();processes.append(_scan(pid,devices,check))
        check()
        confirmed,queries['compute_confirm']=_query('compute_confirm',deadline)
        if compute!=confirmed:gaps.append('GPU_PROCESS_NVML_INVENTORY_CHANGED')
        end_pids=sorted(process_ids)if process_ids is not None else _pids()
        if pids!=end_pids:gaps.append('GPU_PROCESS_PID_INVENTORY_CHANGED')
        if any(not p['stable']for p in processes):gaps.append('GPU_PROCESS_METADATA_UNAVAILABLE_OR_CHANGED')
        anchors=[p for p in processes if _anchor_matches(value,p)]
        if len(anchors)!=1:gaps.append('GPU_PROCESS_HOST_ANCHOR_UNVERIFIED')
        if any(p['host_pid']not in pids for p in compute):gaps.append('GPU_PROCESS_NVML_PID_UNRESOLVED')
        if before!=selected.context(os.getpid()):gaps.append('GPU_PROCESS_OBSERVER_CHANGED')
        if process_ids is None and not any(p['context']==before for p in processes):gaps.append('GPU_PROCESS_OBSERVER_CHANGED')
        binary_after=_binary(deadline)
        if binary_before!=binary_after:gaps.append('GPU_PROCESS_BINARY_CHANGED')
    except(OSError,ValueError,subprocess.SubprocessError):gaps.append('GPU_PROCESS_SCAN_INCOMPLETE')
    if canonical(value)!=frozen:raise ValueError('GPU_PROCESS_SELECTION_CHANGED')
    if process_ids is not None:gaps.append('GPU_PROCESS_EXPLICIT_PID_SUBSET')
    finished=time.monotonic_ns();wall_finished=time.time_ns()
    if abs((wall_finished-wall)-(finished-began))>100_000_000:gaps.append('GPU_PROCESS_CLOCK_UNVERIFIED')
    packet={'schema':SCHEMA,'selection_sha256':hashlib.sha256(frozen).hexdigest(),'observer':before,
        'started_ns':began,'finished_ns':finished,'started_wall_ns':wall,'finished_wall_ns':wall_finished,
        'devices':devices,'compute':compute,'compute_confirm':confirmed,'processes':processes,'queries':queries,
        'binary_before':binary_before,'binary_after':binary_after,
        'pid_inventory_before':pids,'pid_inventory_after':end_pids,'diagnostic_codes':sorted(set(gaps)),
        'explicit_pid_subset':process_ids is not None,'host_inventory_complete':not gaps,
        'producer_authenticated':False,'gpu_release_verified':False,'whole_group_recovery_verified':False,
        'target_processes_signalled':False,'resource_contents_read':False}
    if len(canonical(packet))>MAX_OUTPUT:raise ValueError('GPU_PROCESS_OUTPUT_BOUND')
    validate_packet(packet,value)
    return packet


PACKET_FIELDS=frozenset({'schema','selection_sha256','observer','started_ns','finished_ns','started_wall_ns',
    'finished_wall_ns','devices','compute','processes','queries','pid_inventory_before','pid_inventory_after',
    'diagnostic_codes','explicit_pid_subset','host_inventory_complete','producer_authenticated',
    'gpu_release_verified','whole_group_recovery_verified','target_processes_signalled','resource_contents_read',
    'binary_before','binary_after','compute_confirm'})
GAPS=frozenset({'GPU_PROCESS_PID_INVENTORY_CHANGED','GPU_PROCESS_METADATA_UNAVAILABLE_OR_CHANGED',
    'GPU_PROCESS_HOST_ANCHOR_UNVERIFIED','GPU_PROCESS_NVML_PID_UNRESOLVED','GPU_PROCESS_OBSERVER_CHANGED',
    'GPU_PROCESS_SCAN_INCOMPLETE','GPU_PROCESS_EXPLICIT_PID_SUBSET','GPU_PROCESS_CLOCK_UNVERIFIED',
    'GPU_PROCESS_BINARY_CHANGED','GPU_PROCESS_NVML_INVENTORY_CHANGED'})


def validate_packet(packet,value):
    """Recompute coverage from closed metadata; no successful input booleans."""
    selection(value)
    try:
        if(type(packet)is not dict or set(packet)!=PACKET_FIELDS or packet['schema']!=SCHEMA
                or packet['selection_sha256']!=hashlib.sha256(canonical(value)).hexdigest()):raise ValueError
        selected.validate_context(packet['observer'])
        times=('started_ns','finished_ns','started_wall_ns','finished_wall_ns')
        if any(not _uint(packet[k])for k in times)or packet['finished_ns']<packet['started_ns']:raise ValueError
        flags=('explicit_pid_subset','host_inventory_complete','producer_authenticated','gpu_release_verified',
               'whole_group_recovery_verified','target_processes_signalled','resource_contents_read')
        if any(type(packet[k])is not bool for k in flags)or any(packet[k]for k in flags[2:]):raise ValueError
        gaps=packet['diagnostic_codes']
        if type(gaps)is not list or any(type(g)is not str or g not in GAPS for g in gaps)or gaps!=sorted(set(gaps)):raise ValueError
        for key in('pid_inventory_before','pid_inventory_after'):
            ids=packet[key]
            if type(ids)is not list or len(ids)>MAX_PIDS or any(not _uint(p)or p==0 for p in ids)or ids!=sorted(set(ids)):raise ValueError
        devices=packet['devices'];pairs=set();uuids=set()
        if type(devices)is not list or len(devices)>64:raise ValueError
        for row in devices:
            if(type(row)is not dict or set(row)!={'uuid','major','minor','memory_used_mib'}or not _uuid(row['uuid'])
                    or any(not _uint(row[k])for k in('major','minor','memory_used_mib'))or row['minor']>=256
                    or row['uuid']in uuids or(row['major'],row['minor'])in pairs):raise ValueError
            pairs.add((row['major'],row['minor']));uuids.add(row['uuid'])
        compute=packet['compute']
        for name in('compute','compute_confirm'):
            seen=set();records=packet[name]
            if type(records)is not list or len(records)>MAX_PIDS:raise ValueError
            for row in records:
                if(type(row)is not dict or set(row)!={'host_pid','uuid','memory_used_mib'}or row['uuid']not in uuids
                        or not _uint(row['host_pid'])or row['host_pid']==0 or not _uint(row['memory_used_mib'])
                        or(row['host_pid'],row['uuid'])in seen):raise ValueError
                seen.add((row['host_pid'],row['uuid']))
        processes=packet['processes'];count=0
        if type(processes)is not list or len(processes)>MAX_PIDS:raise ValueError
        for row in processes:
            if(type(row)is not dict or set(row)!={'host_pid','context','nspid','handles','stable','diagnostic_codes'}
                    or not _uint(row['host_pid'])or row['host_pid']==0 or type(row['stable'])is not bool
                    or row['diagnostic_codes']not in([],['GPU_PROCESS_METADATA_UNAVAILABLE_OR_CHANGED'])):raise ValueError
            ctx=row['context'];ns=row['nspid'];handles=row['handles']
            if ctx is not None:
                selected.validate_context(ctx)
                if ctx['pid']!=row['host_pid']:raise ValueError
            if(type(ns)is not list or len(ns)>32 or any(not _uint(p)or p==0 for p in ns)
                    or ns and ns[0]!=row['host_pid']or type(handles)is not list or len(handles)>MAX_FDS):raise ValueError
            fds=set();count+=len(handles)
            for handle in handles:
                if(type(handle)is not dict or set(handle)!={'fd','major','minor'}or any(not _uint(v)for v in handle.values())
                        or handle['fd']>=2**31 or handle['fd']in fds or(handle['major'],handle['minor'])not in pairs):raise ValueError
                fds.add(handle['fd'])
            if row['stable']and(ctx is None or not ns or row['diagnostic_codes']):raise ValueError
        if count>MAX_HANDLES or [r['host_pid']for r in processes]!=packet['pid_inventory_before']:raise ValueError
        if type(packet['queries'])is not dict or set(packet['queries'])-set(QUERIES):raise ValueError
        for query in packet['queries'].values():
            if(type(query)is not dict or set(query)!={'bytes','sha256'}or not _uint(query['bytes'])
                    or query['bytes']>262144 or not selected.fingerprint(query['sha256'])):raise ValueError
        for key in('binary_before','binary_after'):
            binary=packet[key]
            if binary is None:continue
            if(type(binary)is not dict or set(binary)!={'sha256','bytes','device','inode','uid','mode','mtime_ns','ctime_ns'}
                    or not selected.fingerprint(binary['sha256'])or any(not _uint(v)for k,v in binary.items()if k!='sha256')
                    or binary['uid']!=0 or binary['mode']&0o022 or not stat.S_ISREG(binary['mode'])
                    or not 0<binary['bytes']<=64*1024**2 or binary['inode']==0):raise ValueError
        complete=(not gaps and not packet['explicit_pid_subset']and bool(devices)and set(packet['queries'])==set(QUERIES)
            and packet['pid_inventory_before']==packet['pid_inventory_after']and all(r['stable']for r in processes)
            and len([r for r in processes if _anchor_matches(value,r)])==1
            and all(r['host_pid']in packet['pid_inventory_before']for r in compute)
            and any(r['context']==packet['observer']for r in processes)
            and packet['binary_before']is not None and packet['binary_before']==packet['binary_after']
            and packet['compute']==packet['compute_confirm']
            and abs((packet['finished_wall_ns']-packet['started_wall_ns'])-(packet['finished_ns']-packet['started_ns']))<=100_000_000)
        if packet['host_inventory_complete']is not complete or len(canonical(packet))>MAX_OUTPUT:raise ValueError
        return packet
    except(KeyError,TypeError,ValueError,OverflowError):raise ValueError('GPU_PROCESS_PACKET_INVALID')from None


def compare(before,after,value):
    """Preserve all selected-device holders, including unrelated/reused PIDs."""
    validate_packet(before,value);validate_packet(after,value)
    try:
        if(before['finished_ns']>after['started_ns']or any(before['observer'][k]!=after['observer'][k]
                for k in selected.NAMESPACE_FIELDS|{'uid'})
                or abs((after['started_wall_ns']-before['finished_wall_ns'])-
                       (after['started_ns']-before['finished_ns']))>100_000_000):raise ValueError
        first={r['uuid']:(r['major'],r['minor'])for r in before['devices']}
        second={r['uuid']:(r['major'],r['minor'])for r in after['devices']}
        if first!=second or before['binary_before']!=after['binary_before']:raise ValueError
    except(KeyError,TypeError,ValueError):raise ValueError('GPU_PROCESS_INTERVAL_UNVERIFIED')from None
    bindings=owner_matches(value,before['processes']);selected_pids={};selected_devices=set();gaps=[]
    for owner in bindings:
        if len(owner['host_pids'])!=1:gaps.append('GPU_PROCESS_OWNER_BINDING_UNVERIFIED');continue
        pid=owner['host_pids'][0];row=next(p for p in before['processes']if p['host_pid']==pid)
        native=[r for r in before['compute']if r['host_pid']==pid]
        if not native:gaps.append('GPU_PROCESS_NORMAL_OWNER_ALLOCATION_UNOBSERVED')
        held={(h['major'],h['minor'])for h in row['handles']}
        if any(first[r['uuid']]not in held for r in native):gaps.append('GPU_PROCESS_NORMAL_DEVICE_HANDLE_UNOBSERVED')
        selected_pids[pid]=row['context']['start_ticks']
        selected_devices.update(r['uuid']for r in native)
        selected_devices.update(uuid for uuid,pair in first.items()if pair in held)
    if not before['host_inventory_complete']or not after['host_inventory_complete']:gaps.append('GPU_PROCESS_HOST_COVERAGE_INCOMPLETE')
    if not selected_devices:gaps.append('GPU_PROCESS_SELECTED_DEVICES_UNAVAILABLE')
    remaining_compute=[r for r in after['compute']if r['uuid']in selected_devices]
    remaining_handles=[]
    for row in after['processes']:
        for handle in row['handles']:
            if any(second[uuid]==(handle['major'],handle['minor'])for uuid in selected_devices):
                remaining_handles.append({'host_pid':row['host_pid'],'start_ticks':row['context']['start_ticks']if row['context']else None,
                    **handle,'same_selected_generation':bool(row['context']and selected_pids.get(row['host_pid'])==row['context']['start_ticks'])})
    return {'schema':'guard-owned-node-gpu-reference-comparison/1','selected_gpu_uuids':sorted(selected_devices),
        'owner_bindings':bindings,'remaining_compute':remaining_compute,'remaining_device_handles':remaining_handles,
        'selected_gpu_memory_used_mib':{r['uuid']:r['memory_used_mib']for r in after['devices']if r['uuid']in selected_devices},
        'diagnostic_codes':sorted(set(gaps)),
        'selected_gpu_reference_absence_observed':not gaps and not remaining_compute and not remaining_handles,
        'producer_authenticated':False,'gpu_release_verified':False,'whole_group_recovery_verified':False}
