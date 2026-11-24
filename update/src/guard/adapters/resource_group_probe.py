"""Read bounded kernel references in an owned lab namespace, never resource contents.

Selected PID/FD absence is not whole-group or GPU recovery. The explicit PID
subset is for owned-child development only and cannot establish namespace absence.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,sys,time

from guard.adapters import resource_recovery_probe as selected

SCHEMA='guard-owned-namespace-resource-census/1'
SELECTION_SCHEMA='guard-owned-namespace-resource-selection/1'
MAX_PIDS=256
MAX_FDS=2048
MAX_REFERENCES=32768
MAX_MAP_BYTES=4*1024**2
MAX_OUTPUT=8*1024**2


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def selection(value):
    try:
        if (type(value)is not dict or set(value)!={'schema','owners'} or value['schema']!=SELECTION_SCHEMA
                or type(value['owners'])is not list or not 1<=len(value['owners'])<=64):raise ValueError
        ids=set();pids=set()
        for row in value['owners']:
            if type(row)is not dict or set(row)!={'id','context'} or not selected.fingerprint(row['id']):raise ValueError
            selected.validate_context(row['context'])
            if row['id']in ids or row['context']['pid']in pids:raise ValueError
            ids.add(row['id']);pids.add(row['context']['pid'])
            if any(row['context'][k]!=value['owners'][0]['context'][k]for k in selected.NAMESPACE_FIELDS):raise ValueError
        if len(canonical(value))>65536:raise ValueError
        return value
    except (KeyError,ValueError,TypeError):raise ValueError('RESOURCE_GROUP_SELECTION_INVALID')from None


def _same_namespace(a,b):return all(a[k]==b[k]for k in selected.NAMESPACE_FIELDS)


def _proc_ids():
    result=sorted(int(p)for p in os.listdir('/proc')if p.isascii()and p.isdecimal())
    if len(result)>MAX_PIDS:raise ValueError('RESOURCE_GROUP_PID_BOUND')
    return result


def _reference(pid,start,kind,device,inode,*,fd=None):
    return {'pid':pid,'start_ticks':start,'kind':kind,'device':device,'inode':inode,'fd':fd}


def observe(value,*,process_ids=None,max_seconds=5):
    selection(value)
    if sys.platform!='linux':raise ValueError('RESOURCE_GROUP_LINUX_REQUIRED')
    if type(max_seconds)is not int or not 1<=max_seconds<=10:raise ValueError('RESOURCE_GROUP_BUDGET_INVALID')
    subset=process_ids is not None
    if subset and (type(process_ids)is not list or not process_ids or len(process_ids)>MAX_PIDS
            or any(type(p)is not int or p<=0 for p in process_ids)or len(set(process_ids))!=len(process_ids)):
        raise ValueError('RESOURCE_GROUP_PID_SUBSET_INVALID')
    began=time.monotonic_ns();wall=time.time_ns();deadline=time.monotonic()+max_seconds
    observer=selected.context(os.getpid())
    if not _same_namespace(observer,value['owners'][0]['context']):raise ValueError('RESOURCE_GROUP_NAMESPACE_MISMATCH')
    references=[];processes=[];gaps=[];named=[]
    def check():
        if time.monotonic()>=deadline:raise ValueError('RESOURCE_GROUP_DEADLINE')
        if len(references)>MAX_REFERENCES:raise ValueError('RESOURCE_GROUP_REFERENCE_BOUND')
    try:
        pids=sorted(process_ids)if subset else _proc_ids()
        for pid in pids:
            check()
            row={'pid':pid,'context':None,'parent_pid':None,'stable':False,'generation_stable':False,'diagnostic_codes':[]}
            processes.append(row)
            local=[];before=None
            try:
                before=selected.context(pid);row['context']=before
                if not _same_namespace(before,observer):raise ValueError('RESOURCE_GROUP_PROCESS_NAMESPACE_MISMATCH')
                def parent_pid():
                    raw=selected.read_small('/proc/'+str(pid)+'/stat',4096)
                    fields=raw[raw.rfind(b')')+2:].split()
                    if int(fields[19])!=before['start_ticks']:raise ValueError('RESOURCE_GROUP_PROCESS_CHANGED')
                    return int(fields[1])
                row['parent_pid']=parent_pid()
                def add(reference):
                    if len(local)+len(references)>=MAX_REFERENCES:raise ValueError('RESOURCE_GROUP_REFERENCE_BOUND')
                    local.append(reference)
                # Keep scandir alive while examining the observer's own FDs.
                # listdir closes its directory FD before callers can stat it.
                names=[];fd_root='/proc/'+str(pid)+'/fd'
                with os.scandir(fd_root)as entries:
                    for entry in entries:
                        check();name=entry.name;names.append(name)
                        if len(names)>MAX_FDS:raise ValueError('RESOURCE_GROUP_FD_BOUND')
                        if not name.isascii()or not name.isdecimal():raise ValueError('RESOURCE_GROUP_FD_INVALID')
                        path=fd_root+'/'+name
                        link=os.readlink(path);state=os.stat(path)
                        if link!=os.readlink(path)or (state.st_dev,state.st_ino,state.st_mode)!=(
                                lambda s:(s.st_dev,s.st_ino,s.st_mode))(os.stat(path)):
                            raise ValueError('RESOURCE_GROUP_DESCRIPTOR_CHANGED')
                        if stat.S_ISSOCK(state.st_mode):kind='socket'
                        elif stat.S_ISREG(state.st_mode):
                            # Read only the kernel link name, not file/SHM contents.
                            kind='posix-shm'if link.startswith('/dev/shm/')else None
                        else:kind=None
                        if kind is not None:add(_reference(pid,before['start_ticks'],kind,state.st_dev,state.st_ino,fd=int(name)))
                raw=selected.read_small('/proc/'+str(pid)+'/maps',MAX_MAP_BYTES)
                seen=set()
                for line in raw.splitlines():
                    check();parts=line.split(None,5)
                    if len(parts)<6 or not parts[5].startswith(b'/dev/shm/'):continue
                    device=parts[3].split(b':');inode=int(parts[4])
                    if len(device)!=2 or inode<=0:raise ValueError('RESOURCE_GROUP_MAP_INVALID')
                    identity=(os.makedev(int(device[0],16),int(device[1],16)),inode)
                    if identity not in seen:
                        seen.add(identity);add(_reference(pid,before['start_ticks'],'posix-shm',*identity))
                with os.scandir(fd_root)as entries:after_names=sorted(entry.name for entry in entries)
                if sorted(names)!=after_names:
                    raise ValueError('RESOURCE_GROUP_DESCRIPTOR_INVENTORY_CHANGED')
                after=selected.context(pid)
                if before!=after or row['parent_pid']!=parent_pid():raise ValueError('RESOURCE_GROUP_PROCESS_CHANGED')
                row['stable']=row['generation_stable']=True;references.extend(local)
            except (OSError,ValueError,IndexError):
                try:
                    if before is not None and _same_namespace(before,observer)and before==selected.context(pid):
                        row['generation_stable']=True;references.extend(local)
                except (OSError,ValueError):pass
                row['diagnostic_codes'].append('RESOURCE_GROUP_PROCESS_UNAVAILABLE_OR_CHANGED')
                gaps.append('RESOURCE_GROUP_PROCESS_UNAVAILABLE_OR_CHANGED')
        if not subset:
            check()
            with os.scandir('/dev/shm')as entries:
                for count,entry in enumerate(entries):
                    check()
                    if count>=4096:raise ValueError('RESOURCE_GROUP_SHM_BOUND')
                    state=entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(state.st_mode):named.append({'device':state.st_dev,'inode':state.st_ino})
                    elif stat.S_ISLNK(state.st_mode):gaps.append('RESOURCE_GROUP_SHM_LINK_UNRESOLVED')
            if pids!=_proc_ids():gaps.append('RESOURCE_GROUP_PID_INVENTORY_CHANGED')
        if observer!=selected.context(os.getpid()):gaps.append('RESOURCE_GROUP_OBSERVER_CHANGED')
    except (OSError,ValueError):
        gaps.append('RESOURCE_GROUP_SCAN_INCOMPLETE')
        pids=locals().get('pids',[])
    owners=[]
    by_pid={row['pid']:row for row in processes}
    for owner in value['owners']:
        row=by_pid.get(owner['context']['pid'])
        status=('match'if row and row['generation_stable']and row['context']==owner['context']else
            'replaced'if row and row['generation_stable']and row['context']!=owner['context']else
            'absent'if not subset and not gaps and owner['context']['pid']not in pids else'unavailable')
        owners.append({'id':owner['id'],'status':status})
    result={'schema':SCHEMA,'selection_sha256':hashlib.sha256(canonical(value)).hexdigest(),
        'observer':observer,'started_wall_ns':wall,'finished_wall_ns':time.time_ns(),
        'started_monotonic_ns':began,'finished_monotonic_ns':time.monotonic_ns(),
        'namespace_scan_complete':not subset and not gaps,'explicit_pid_subset':subset,
        'processes':processes,'owners':owners,'references':references,'named_shm':named,
        'diagnostic_codes':sorted(set(gaps)),
        'unix_filesystem_paths_removed_verified':False,'gpu_release_verified':False,
        'whole_group_recovery_verified':False,'producer_authenticated':False,
        'resource_contents_read':False,'processes_signalled':False}
    if len(canonical(result))>MAX_OUTPUT:raise ValueError('RESOURCE_GROUP_OUTPUT_BOUND')
    return validate_result(result,value)


def validate_result(value,scope):
    try:
        selection(scope)
        keys={'schema','selection_sha256','observer','started_wall_ns','finished_wall_ns','started_monotonic_ns',
            'finished_monotonic_ns','namespace_scan_complete','explicit_pid_subset','processes','owners','references',
            'named_shm','diagnostic_codes','unix_filesystem_paths_removed_verified','gpu_release_verified',
            'whole_group_recovery_verified','producer_authenticated','resource_contents_read','processes_signalled'}
        flags={'namespace_scan_complete','explicit_pid_subset','unix_filesystem_paths_removed_verified','gpu_release_verified',
            'whole_group_recovery_verified','producer_authenticated','resource_contents_read','processes_signalled'}
        if (type(value)is not dict or set(value)!=keys or value['schema']!=SCHEMA
                or value['selection_sha256']!=hashlib.sha256(canonical(scope)).hexdigest()
                or any(type(value[k])is not bool for k in flags)
                or any(value[k]is not False for k in flags-{'namespace_scan_complete','explicit_pid_subset'})):
            raise ValueError
        selected.validate_context(value['observer'])
        if not _same_namespace(value['observer'],scope['owners'][0]['context']):raise ValueError
        for first,last in(('started_wall_ns','finished_wall_ns'),('started_monotonic_ns','finished_monotonic_ns')):
            if not selected.uint(value[first])or not selected.uint(value[last])or value[first]>value[last]:raise ValueError
        if type(value['processes'])is not list or len(value['processes'])>MAX_PIDS:raise ValueError
        processes={}
        for row in value['processes']:
            if (type(row)is not dict or set(row)!={'pid','context','parent_pid','stable','generation_stable','diagnostic_codes'}
                    or not selected.uint(row['pid'],1)or row['pid']in processes or type(row['stable'])is not bool
                    or type(row['generation_stable'])is not bool or type(row['diagnostic_codes'])is not list
                    or row['diagnostic_codes']not in([],['RESOURCE_GROUP_PROCESS_UNAVAILABLE_OR_CHANGED'])):raise ValueError
            if row['parent_pid']is not None and not selected.uint(row['parent_pid']):raise ValueError
            if row['context']is not None:
                selected.validate_context(row['context'])
                if row['context']['pid']!=row['pid']:raise ValueError
            if row['generation_stable']and(row['context']is None or not _same_namespace(row['context'],value['observer'])):raise ValueError
            if row['stable']and(not row['generation_stable']or row['diagnostic_codes']or row['parent_pid']is None):raise ValueError
            processes[row['pid']]=row
        allowed_gaps={'RESOURCE_GROUP_PROCESS_UNAVAILABLE_OR_CHANGED','RESOURCE_GROUP_SHM_LINK_UNRESOLVED',
            'RESOURCE_GROUP_PID_INVENTORY_CHANGED','RESOURCE_GROUP_OBSERVER_CHANGED','RESOURCE_GROUP_SCAN_INCOMPLETE'}
        if (type(value['diagnostic_codes'])is not list or any(type(c)is not str or c not in allowed_gaps for c in value['diagnostic_codes'])
                or value['diagnostic_codes']!=sorted(set(value['diagnostic_codes']))):raise ValueError
        if value['namespace_scan_complete']and(value['explicit_pid_subset']or value['diagnostic_codes']or not all(r['stable']for r in processes.values())):
            raise ValueError
        if value['namespace_scan_complete']and(value['observer']['pid']not in processes
                or processes[value['observer']['pid']]['context']!=value['observer']):raise ValueError
        if type(value['owners'])is not list or len(value['owners'])!=len(scope['owners']):raise ValueError
        for expected,row in zip(scope['owners'],value['owners'],strict=True):
            if (type(row)is not dict or set(row)!={'id','status'}or row['id']!=expected['id']
                    or row['status']not in{'match','absent','replaced','unavailable'}):raise ValueError
            process=processes.get(expected['context']['pid'])
            if row['status']=='match'and(not process or not process['generation_stable']or process['context']!=expected['context']):raise ValueError
            if row['status']=='replaced'and(not process or not process['generation_stable']or process['context']==expected['context']):raise ValueError
            if row['status']=='absent'and(not value['namespace_scan_complete']or process is not None):raise ValueError
        if type(value['references'])is not list or len(value['references'])>MAX_REFERENCES:raise ValueError
        for row in value['references']:
            if (type(row)is not dict or set(row)!={'pid','start_ticks','kind','device','inode','fd'}
                    or row['kind']not in{'socket','posix-shm'}or not selected.uint(row['device'])or not selected.uint(row['inode'],1)
                    or not selected.uint(row['pid'],1)or not selected.uint(row['start_ticks'],1)
                    or row['fd']is not None and not selected.uint(row['fd'],0,1048575)
                    or row['fd']is None and row['kind']!='posix-shm'):raise ValueError
            process=processes.get(row['pid'])
            if not process or not process['generation_stable']or process['context']['start_ticks']!=row['start_ticks']:raise ValueError
        if type(value['named_shm'])is not list or len(value['named_shm'])>4096:raise ValueError
        for row in value['named_shm']:
            if type(row)is not dict or set(row)!={'device','inode'}or not selected.uint(row['device'])or not selected.uint(row['inode'],1):raise ValueError
        if value['explicit_pid_subset']and value['named_shm']:raise ValueError
        if len(canonical(value))>MAX_OUTPUT:raise ValueError
        return value
    except (KeyError,ValueError,TypeError,IndexError):raise ValueError('RESOURCE_GROUP_CENSUS_INVALID')from None


def compare(before,after,value):
    """Preserve positive remaining references even when absence cannot be proved."""
    selection(value);validate_result(before,value);validate_result(after,value)
    digest=hashlib.sha256(canonical(value)).hexdigest()
    if (any(row.get('schema')!=SCHEMA or row.get('selection_sha256')!=digest for row in(before,after))
            or not _same_namespace(before['observer'],after['observer'])
            or before['finished_monotonic_ns']>after['started_monotonic_ns']
            or before['finished_wall_ns']>after['started_wall_ns']):
        raise ValueError('RESOURCE_GROUP_CENSUS_SCOPE_MISMATCH')
    selected_owners={(o['context']['pid'],o['context']['start_ticks'])for o in value['owners']}
    objects={(row['kind'],row['device'],row['inode'])for row in before['references']
        if(row['pid'],row['start_ticks'])in selected_owners}
    remaining={(row['kind'],row['device'],row['inode'])for row in after['references']}&objects
    remaining|={('posix-shm',row['device'],row['inode'])for row in after['named_shm']}&objects
    before_bound=all(row['status']=='match'for row in before['owners'])
    absence=(bool(objects)and before_bound and before['namespace_scan_complete']and after['namespace_scan_complete']and not remaining
        and all(row['status']in{'absent','replaced'}for row in after['owners']))
    return {'selected_kernel_objects':len(objects),'remaining_kernel_objects':[{'kind':k,'device':d,'inode':i}for k,d,i in sorted(remaining)],
        'selected_kernel_reference_absence_observed':bool(absence),
        'whole_group_recovery_verified':False,'unix_filesystem_paths_removed_verified':False,
        'gpu_release_verified':False,'producer_authenticated':False}


def main():
    try:
        if len(sys.argv)!=1:raise ValueError
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError
                result[key]=value
            return result
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v:(_ for _ in()).throw(ValueError()))
        sys.stdout.buffer.write(canonical(observe(value)));return 0
    except Exception:
        sys.stdout.write('{"diagnostic":"RESOURCE_GROUP_CENSUS_UNAVAILABLE"}');return 2


if __name__=='__main__':raise SystemExit(main())
