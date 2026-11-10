"""S2 boot/GPU/original timer read only. No source/engine/SSM/AWS mutation."""
import argparse,datetime,hashlib,json,os,re,signal,stat,subprocess,time
from pathlib import Path

def group_active(pgid):

    for entry in Path('/proc').iterdir():

        if not entry.name.isdigit():

            continue

        try:

            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()

            if int(fields[2]) == pgid and fields[0] != 'Z':

                return True

        except (FileNotFoundError, ProcessLookupError):

            continue

    return False

def wait_child(proc, timeout):

    result = {'timed_out': False, 'child_reaped': False, 'group_terminal': False,

        'cleanup_errors': []}

    error = None

    try:

        result['returncode'] = proc.wait(timeout=timeout)

        result['child_reaped'] = True

    except BaseException as caught:

        error = caught

        result['timed_out'] = isinstance(caught, subprocess.TimeoutExpired)

        result['wait_error'] = type(caught).__name__ + ': ' + str(caught)

    # Kill any remaining guard descendants even when the direct CLI already exited.

    try:

        if group_active(proc.pid):

            os.killpg(proc.pid, signal.SIGKILL)

    except ProcessLookupError:

        pass

    except OSError as caught:

        result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    if not result['child_reaped']:

        try:

            result['returncode'] = proc.wait(timeout=30)

            result['child_reaped'] = True

        except BaseException as caught:

            result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    until = time.monotonic() + 30

    while group_active(proc.pid) and time.monotonic() < until:

        time.sleep(.05)

    result['group_terminal'] = not group_active(proc.pid)

    if error is not None and not result['timed_out']:

        result['interrupted'] = True

    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    assert os.getuid()==os.geteuid()==0
    root=a.output.absolute()
    assert not any(v.is_symlink() for v in (root,*root.parents))
    assert root.parent.is_dir()
    root.mkdir(mode=0o700)
    original_umask=os.umask(0o077)
    records=[]
    def call(label,argv,timeout=30):
        with (root/(label+'.stdout')).open('xb') as out,(root/(label+'.stderr')).open('xb') as err:
            proc=subprocess.Popen(argv,stdout=out,stderr=err,start_new_session=True)
            terminal=wait_child(proc,timeout)
        raw=(root/(label+'.stdout')).read_bytes();error=(root/(label+'.stderr')).read_bytes()
        row={'label':label,'argv':argv,'terminal':terminal,'stdout_sha256':hashlib.sha256(raw).hexdigest(),'stderr_sha256':hashlib.sha256(error).hexdigest()}
        records.append(row)
        assert terminal['child_reaped'] and terminal['group_terminal'] and not terminal['cleanup_errors'] and not terminal['timed_out']
        assert terminal['returncode']==0 and len(raw)<=2*1024**2 and len(error)<=2*1024**2
        return raw
    result={'health_complete':False,'timer_reset':False,'engine_created':False,'factory_executed':False,'records':records,'output':str(root)}
    primary=None
    try:
        gpu=call('gpu',['nvidia-smi','--query-gpu=name,uuid,driver_version','--format=csv,noheader']).decode().strip().splitlines()
        assert len(gpu)==1
        name,uuid,driver=[v.strip() for v in gpu[0].split(',')]
        assert name=='NVIDIA L4' and re.fullmatch(r'GPU-[a-fA-F0-9-]+',uuid)
        processes=call('gpu-processes',['nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_memory','--format=csv,noheader'])
        assert not processes.strip(),'PRIOR_GPU_OCCUPANCY_NOT_EMPTY'
        timer=call('timer',['systemctl','show','byul-tp2-resume-budget.timer','--no-pager','-p','ActiveState','-p','ActiveEnterTimestamp','-p','ActiveEnterTimestampMonotonic','-p','TimersMonotonic','-p','NextElapseUSecMonotonic','-p','Unit']).decode()
        assert 'ActiveState=active' in timer and 'OnActiveUSec=2h' in timer,'ORIGINAL_ACTIVE_2H_TIMER_REQUIRED_NO_RESET'
        timer_units=call('timer-service-definition',['systemctl','cat','byul-tp2-resume-budget.timer','byul-tp2-resume-budget.service','--no-pager']).decode()
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        assert re.fullmatch(r'[a-f0-9-]{36}',boot)
        meminfo=Path('/proc/meminfo').read_text()
        total_kib=int(re.search(r'^MemTotal:\s+(\d+) kB$',meminfo,re.M).group(1))
        assert total_kib>28*1024**2 and os.cpu_count()==8,'EXPECTED_G6_2XL_HOST_DELTA_NOT_OBSERVED'
        result.update(health_complete=True,at=datetime.datetime.now(datetime.timezone.utc).isoformat(),boot_id=boot,GPU={'name':name,'uuid':uuid,'driver':driver},physical_GPU_count=1,prior_GPU_processes=0,host_cpu_count=os.cpu_count(),host_MemTotal_KiB=total_kib,timer=timer,timer_service_definition=timer_units,PG_terminal=True,source_origin_or_model_cache_verified=False)
    except BaseException as error:
        primary=error
        result['error_type']=type(error).__name__
        raise
    finally:
        try:
            raw=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode()
            with (root/'health-receipt.json').open('xb') as f:f.write(raw)
            print(json.dumps({k:v for k,v in result.items() if k not in ('records','timer_service_definition')},ensure_ascii=False),flush=True)
        except BaseException as secondary:
            if primary is None:raise
            primary.add_note('Health receipt preservation failed: '+type(secondary).__name__)
        finally:
            os.umask(original_umask)

if __name__=='__main__':main()
