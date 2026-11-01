"""Closed read-only Linux census of explicitly selected processes/descriptors.

No process/FD scan, mutation, content read, signal, wait, connect or path output.
Selected descriptor absence is never global IPC/GPU resource release.
"""
from __future__ import annotations
import errno,hashlib,json,os,re,stat,sys,time

SELECTION_SCHEMA='guard-resource-recovery-selection/1'
SCHEMA='guard-selected-resource-census/1'
CONTEXT_FIELDS=frozenset({'pid','start_ticks','uid','boot_sha256','pid_namespace','ipc_namespace','mount_namespace','time_namespace','time_offsets_sha256'})
NAMESPACE_FIELDS=frozenset({'boot_sha256','pid_namespace','ipc_namespace','mount_namespace','time_namespace','time_offsets_sha256'})
OWNER_FIELDS=frozenset({'id','rank','context','require_absent'})
RESOURCE_FIELDS=frozenset({'id','owner_id','fd','kind','device','inode','require_absent'})
KINDS=frozenset({'unix-socket','posix-shm-fd'})
MAX_OWNERS=64
MAX_RESOURCES=128
MAX_INPUT=65536
MAX_OUTPUT=262144


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def sha(raw):return hashlib.sha256(raw).hexdigest()


def uint(value,low=0,high=2**63-1):return type(value) is int and low<=value<=high


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_context(value):
    if (type(value) is not dict or set(value)!=CONTEXT_FIELDS or not fingerprint(value['boot_sha256']) or not fingerprint(value['time_offsets_sha256'])
            or any(not uint(value[k],0 if k=='uid' else 1) for k in CONTEXT_FIELDS-{'boot_sha256','time_offsets_sha256'})
            or value['pid']>=10**10):raise ValueError('RESOURCE_CONTEXT_INVALID')


def validate_selection(value):
    try:
        if type(value) is not dict or set(value)!={'schema','owners','resources'} or value['schema']!=SELECTION_SCHEMA:raise ValueError
        owners,resources=value['owners'],value['resources']
        if type(owners) is not list or not 1<=len(owners)<=MAX_OWNERS or type(resources) is not list or len(resources)>MAX_RESOURCES:raise ValueError
        ids=set();pids=set();ranks=set();fds=set()
        for row in owners:
            if (type(row) is not dict or set(row)!=OWNER_FIELDS or not fingerprint(row['id']) or row['id'] in ids
                    or not uint(row['rank'],0,63) or row['rank'] in ranks or type(row['require_absent']) is not bool):raise ValueError
            validate_context(row['context']);pid=row['context']['pid']
            if pid in pids:raise ValueError
            ids.add(row['id']);pids.add(pid);ranks.add(row['rank'])
        owner_ids=set(ids)
        if any(any(row['context'][k]!=owners[0]['context'][k] for k in NAMESPACE_FIELDS) for row in owners):raise ValueError
        for row in resources:
            if (type(row) is not dict or set(row)!=RESOURCE_FIELDS or not fingerprint(row['id']) or row['id'] in ids
                    or row['owner_id'] not in owner_ids or row['kind'] not in KINDS or not uint(row['fd'],0,1048575)
                    or not uint(row['device']) or not uint(row['inode'],1) or type(row['require_absent']) is not bool):raise ValueError
            key=(row['owner_id'],row['fd'])
            if key in fds:raise ValueError
            fds.add(key);ids.add(row['id'])
        if len(canonical(value))>MAX_INPUT:raise ValueError
        return value
    except (KeyError,TypeError,ValueError):raise ValueError('RESOURCE_SELECTION_INVALID') from None


def read_small(path,limit):
    with open(path,'rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('RESOURCE_READ_BOUND')
    return raw


def context(pid):
    prefix='/proc/'+str(pid)
    raw=read_small(prefix+'/stat',4096);tail=raw[raw.rfind(b')')+2:].split()
    if len(tail)<20:raise ValueError('RESOURCE_STAT_UNAVAILABLE')
    status=read_small(prefix+'/status',16384)
    uids=[line.split()[1:] for line in status.splitlines() if line.startswith(b'Uid:')]
    if len(uids)!=1 or len(uids[0])!=4:raise ValueError('RESOURCE_UID_UNAVAILABLE')
    value={'pid':pid,'start_ticks':int(tail[19]),'uid':int(uids[0][0]),
        'boot_sha256':sha(read_small('/proc/sys/kernel/random/boot_id',128).strip()),
        'time_offsets_sha256':sha(read_small(prefix+'/timens_offsets',4096)),
        **{k:os.stat(prefix+'/ns/'+n).st_ino for k,n in (('pid_namespace','pid'),('ipc_namespace','ipc'),('mount_namespace','mnt'),('time_namespace','time'))}}
    validate_context(value);return value


def error_state(error):
    return 'absent' if isinstance(error,OSError) and error.errno in {errno.ENOENT,errno.ESRCH} else 'unavailable'


def descriptor(pid,row):
    path='/proc/'+str(pid)+'/fd/'+str(row['fd'])
    try:
        before=os.stat(path);link=os.readlink(path) if row['kind']=='posix-shm-fd' else None;after=os.stat(path)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_mode)
        if identity(before)!=identity(after):return 'unstable'
        if (before.st_dev,before.st_ino)!=(row['device'],row['inode']):return 'replaced'
        if row['kind']=='unix-socket':return 'match' if stat.S_ISSOCK(before.st_mode) else 'unsupported'
        return 'match' if stat.S_ISREG(before.st_mode) and type(link) is str and link.startswith('/dev/shm/') else 'unsupported'
    except (OSError,ValueError) as error:return error_state(error)


def observe(selection):
    validate_selection(selection)
    if sys.platform!='linux':raise ValueError('RESOURCE_LINUX_REQUIRED')
    wall=time.time_ns();started=time.monotonic_ns();owners=[];resources=[]
    for selected in selection['owners']:
        owner={'id':selected['id'],'rank':selected['rank'],'status':'unavailable','context':None}
        matches=[r for r in selection['resources'] if r['owner_id']==selected['id']]
        rows=[{'id':r['id'],'owner_id':selected['id'],'status':'owner-unavailable'} for r in matches]
        try:
            before=context(selected['context']['pid']);owner['context']=before
            if before!=selected['context']:owner['status']='replaced'
            else:
                for row,item in zip(rows,matches):row['status']=descriptor(before['pid'],item)
                after=context(before['pid'])
                owner['status']='match' if after==before else 'unstable'
                if owner['status']!='match':
                    for row in rows:row['status']='owner-unavailable'
        except (OSError,ValueError) as error:
            owner['status']=error_state(error)
            for row in rows:row['status']='owner-unavailable'
        owners.append(owner);resources.extend(rows)
    result={'schema':SCHEMA,'selection_sha256':sha(canonical(selection)),'observer':context(os.getpid()),
        'started_wall_ns':wall,'finished_wall_ns':time.time_ns(),'started_monotonic_ns':started,'finished_monotonic_ns':time.monotonic_ns(),
        'owners':owners,'resources':resources,'global_resource_release_verified':False,'gpu_release_verified':False,
        'producer_authenticated':False,'processes_signalled':False,'resource_contents_read':False}
    if len(canonical(result))>MAX_OUTPUT:raise ValueError('RESOURCE_OUTPUT_BOUND')
    return result


def validate_result(value,selection):
    try:
        validate_selection(selection)
        keys={'schema','selection_sha256','observer','started_wall_ns','finished_wall_ns','started_monotonic_ns','finished_monotonic_ns',
            'owners','resources','global_resource_release_verified','gpu_release_verified','producer_authenticated','processes_signalled','resource_contents_read'}
        if (type(value) is not dict or set(value)!=keys or value['schema']!=SCHEMA or value['selection_sha256']!=sha(canonical(selection))
                or any(value[k] is not False for k in ('global_resource_release_verified','gpu_release_verified','producer_authenticated','processes_signalled','resource_contents_read'))):raise ValueError
        validate_context(value['observer'])
        if any(value['observer'][k]!=selection['owners'][0]['context'][k] for k in NAMESPACE_FIELDS):raise ValueError
        for key in ('started_wall_ns','finished_wall_ns','started_monotonic_ns','finished_monotonic_ns'):
            if not uint(value[key]):raise ValueError
        if value['started_wall_ns']>value['finished_wall_ns'] or value['started_monotonic_ns']>value['finished_monotonic_ns']:raise ValueError
        if type(value['owners']) is not list or len(value['owners'])!=len(selection['owners']):raise ValueError
        for expected,actual in zip(selection['owners'],value['owners']):
            if (type(actual) is not dict or set(actual)!={'id','rank','status','context'} or actual['id']!=expected['id'] or type(actual['rank']) is not int or actual['rank']!=expected['rank']
                    or actual['status'] not in {'match','absent','replaced','unavailable','unstable'}):raise ValueError
            if actual['context'] is not None:validate_context(actual['context'])
            if actual['status']=='match' and actual['context']!=expected['context']:raise ValueError
            if actual['status']=='replaced' and (actual['context'] is None or actual['context']==expected['context']):raise ValueError
        if type(value['resources']) is not list or len(value['resources'])!=len(selection['resources']):raise ValueError
        selected={r['id']:r for r in selection['resources']};seen=set();owner_map={r['id']:r for r in value['owners']}
        for actual in value['resources']:
            if type(actual) is not dict or set(actual)!={'id','owner_id','status'} or actual['id'] not in selected or actual['id'] in seen:raise ValueError
            expected=selected[actual['id']]
            if actual['owner_id']!=expected['owner_id'] or actual['status'] not in {'match','absent','replaced','unstable','unsupported','unavailable','owner-unavailable'}:raise ValueError
            if owner_map[actual['owner_id']]['status']!='match' and actual['status']!='owner-unavailable':raise ValueError
            seen.add(actual['id'])
        if len(canonical(value))>MAX_OUTPUT:raise ValueError
        return value
    except (KeyError,TypeError,ValueError):raise ValueError('RESOURCE_CENSUS_INVALID') from None


def main():
    try:
        if len(sys.argv)!=1:raise ValueError
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:raise ValueError
        def pairs(items):
            result={}
            for k,v in items:
                if k in result:raise ValueError
                result[k]=v
            return result
        selection=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        result=observe(selection);validate_result(result,selection);sys.stdout.buffer.write(canonical(result));return 0
    except Exception:
        sys.stdout.write('{"diagnostic":"RESOURCE_CENSUS_UNAVAILABLE"}');return 2


if __name__=='__main__':raise SystemExit(main())
