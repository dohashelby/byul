"""EXAMPLE GPU metadata plus real owned CPU children; no GPU qualification."""
from copy import deepcopy
from hashlib import sha256
import os,stat,subprocess,sys
import pytest
from guard.adapters import gpu_process_probe as probe,resource_recovery_probe as selected

UUID='GPU-00000000-0000-0000-0000-000000000001'
OTHER='GPU-00000000-0000-0000-0000-000000000002'


def context(pid,start,*,namespace=11,uid=1000):
    return {'pid':pid,'start_ticks':start,'uid':uid,'boot_sha256':'a'*64,'pid_namespace':namespace,
        'ipc_namespace':12,'mount_namespace':13,'time_namespace':14,'time_offsets_sha256':'b'*64}


def example_scope():
    return {'schema':probe.SELECTION_SCHEMA,'anchor':{'host_pid':101,'start_ticks':100,'pid_namespace':11},
        'owners':[{'id':'c'*64,'context':context(7,700)}]}


def process(pid,start,*,inner=None,handles=False,namespace=11,uid=1000):
    return {'host_pid':pid,'context':context(pid,start,namespace=namespace,uid=uid),
        'nspid':[pid]if inner is None else[pid,inner],
        'handles':[{'fd':3,'major':195,'minor':3}]if handles else[],
        'stable':True,'diagnostic_codes':[]}


def packet(*,after=False):
    scope=example_scope();observer=process(200,2000,namespace=99,uid=0)
    rows=[process(101,100,inner=1)]
    if not after:rows.append(process(102,700,inner=7,handles=True))
    rows.append(observer);began=1_000_000_000+(10_000_000 if after else 0)
    binary={'sha256':'d'*64,'bytes':100,'device':1,'inode':2,'uid':0,'mode':stat.S_IFREG|0o755,'mtime_ns':1,'ctime_ns':1}
    return {'schema':probe.SCHEMA,'selection_sha256':sha256(probe.canonical(scope)).hexdigest(),
        'observer':deepcopy(observer['context']),'started_ns':began,'finished_ns':began+1_000_000,
        'started_wall_ns':began+10_000_000_000,'finished_wall_ns':began+10_001_000_000,
        'devices':[{'uuid':UUID,'major':195,'minor':3,'memory_used_mib':0 if after else 12}],
        'compute':[]if after else[{'uuid':UUID,'host_pid':102,'memory_used_mib':12}],
        'compute_confirm':[]if after else[{'uuid':UUID,'host_pid':102,'memory_used_mib':12}],
        'processes':rows,'queries':{k:{'bytes':0,'sha256':sha256(b'').hexdigest()}for k in probe.QUERIES},
        'binary_before':deepcopy(binary),'binary_after':deepcopy(binary),
        'pid_inventory_before':[p['host_pid']for p in rows],'pid_inventory_after':[p['host_pid']for p in rows],
        'diagnostic_codes':[],'explicit_pid_subset':False,'host_inventory_complete':True,
        'producer_authenticated':False,'gpu_release_verified':False,'whole_group_recovery_verified':False,
        'target_processes_signalled':False,'resource_contents_read':False}


def test_exact_host_namespace_generation_uid_and_device_references_are_separate_from_gpu_recovery():
    before,after=packet(),packet(after=True)
    result=probe.compare(before,after,example_scope())
    assert result['owner_bindings']==[{'id':'c'*64,'host_pids':[102]}]
    assert result['selected_gpu_uuids']==[UUID]and result['selected_gpu_reference_absence_observed']is True
    assert not result['remaining_compute']and not result['remaining_device_handles']
    assert result['producer_authenticated']is result['gpu_release_verified']is result['whole_group_recovery_verified']is False


@pytest.mark.parametrize('reused',[False,True])
def test_an_empty_compute_query_never_hides_selected_device_handles_or_reused_pid(reused):
    after=packet(after=True);row=process(102,701 if reused else 700,inner=7,handles=True)
    after['processes'].insert(1,row)
    after['pid_inventory_before']=after['pid_inventory_after']=[101,102,200]
    result=probe.compare(packet(),after,example_scope())
    assert result['remaining_device_handles'][0]['same_selected_generation']is(not reused)
    assert result['selected_gpu_reference_absence_observed']is False and result['gpu_release_verified']is False


def test_another_gpu_workload_is_retained_without_becoming_the_selected_gpu_leak():
    before,after=packet(),packet(after=True)
    for p in(before,after):
        p['devices'].append({'uuid':OTHER,'major':195,'minor':4,'memory_used_mib':20})
        p['compute'].append({'uuid':OTHER,'host_pid':300,'memory_used_mib':20})
        p['compute_confirm'].append({'uuid':OTHER,'host_pid':300,'memory_used_mib':20})
        row=process(300,3000,namespace=88,uid=7);row['handles']=[{'fd':9,'major':195,'minor':4}]
        p['processes'].append(row);p['pid_inventory_before'].append(300);p['pid_inventory_after'].append(300)
    result=probe.compare(before,after,example_scope())
    assert result['selected_gpu_uuids']==[UUID]and result['selected_gpu_reference_absence_observed']is True
    assert after['compute']==[{'uuid':OTHER,'host_pid':300,'memory_used_mib':20}]


def test_all_selected_owner_handles_extend_the_scope_instead_of_shrinking_it_to_nvml_rows():
    before,after=packet(),packet(after=True)
    for p in(before,after):p['devices'].append({'uuid':OTHER,'major':195,'minor':4,'memory_used_mib':0})
    before['processes'][1]['handles'].append({'fd':8,'major':195,'minor':4})
    result=probe.compare(before,after,example_scope())
    assert result['selected_gpu_uuids']==[UUID,OTHER]


@pytest.mark.parametrize('field,value',[('uid',2),('start_ticks',701),('pid_namespace',88),
    ('ipc_namespace',88),('mount_namespace',88),('time_namespace',88),('boot_sha256','f'*64),('time_offsets_sha256','e'*64)])
def test_native_owner_requires_every_closed_identity_component(field,value):
    before=packet();before['processes'][1]['context'][field]=value
    result=probe.compare(before,packet(after=True),example_scope())
    assert result['selected_gpu_reference_absence_observed']is False
    assert 'GPU_PROCESS_OWNER_BINDING_UNVERIFIED'in result['diagnostic_codes']


@pytest.mark.parametrize('damage',['claim','partial','anchor','nspid','boolean-pid','clock','device','pid-drop','binary','fd'])
def test_bad_packet_cannot_claim_complete_host_coverage(damage):
    p=packet()
    if damage=='claim':p['gpu_release_verified']=True
    elif damage=='partial':p.update(explicit_pid_subset=True)
    elif damage=='anchor':p['processes'][0]['nspid']=[101,2]
    elif damage=='nspid':p['processes'][1]['nspid']=[999,7]
    elif damage=='boolean-pid':p['processes'][1]['host_pid']=True
    elif damage=='clock':p['finished_wall_ns']+=100_000_001
    elif damage=='device':p['devices'][0]['minor']=True
    elif damage=='pid-drop':p['processes'].pop()
    elif damage=='binary':p['binary_after']['sha256']='e'*64
    else:p['processes'][1]['handles'][0]['minor']=99
    with pytest.raises(ValueError,match='GPU_PROCESS_PACKET_INVALID'):probe.validate_packet(p,example_scope())


def test_explicit_subset_with_honest_gap_preserves_references_but_never_absence():
    after=packet(after=True);after.update(explicit_pid_subset=True,host_inventory_complete=False,
        diagnostic_codes=['GPU_PROCESS_EXPLICIT_PID_SUBSET'])
    result=probe.compare(packet(),after,example_scope())
    assert result['selected_gpu_reference_absence_observed']is False
    assert 'GPU_PROCESS_HOST_COVERAGE_INCOMPLETE'in result['diagnostic_codes']


def test_a_compute_process_appearing_in_the_confirmation_query_blocks_absence():
    after=packet(after=True)
    after['compute_confirm']=[{'host_pid':101,'uuid':UUID,'memory_used_mib':12}]
    with pytest.raises(ValueError,match='GPU_PROCESS_PACKET_INVALID'):probe.validate_packet(after,example_scope())
    after.update(host_inventory_complete=False,diagnostic_codes=['GPU_PROCESS_NVML_INVENTORY_CHANGED'])
    result=probe.compare(packet(),after,example_scope())
    assert result['selected_gpu_reference_absence_observed']is False
    assert 'GPU_PROCESS_HOST_COVERAGE_INCOMPLETE'in result['diagnostic_codes']


@pytest.mark.parametrize('damage',['clock','backwards','boot','namespace','uid','device-reordered','binary-replaced'])
def test_comparison_requires_one_host_clock_and_the_same_uuid_minor_relation(damage):
    p=packet(after=True)
    if damage=='clock':p['started_wall_ns']+=101_000_000;p['finished_wall_ns']+=101_000_000
    elif damage=='backwards':p['started_ns']=p['finished_ns']=1
    elif damage=='boot':p['observer']['boot_sha256']='e'*64;p['processes'][-1]['context']['boot_sha256']='e'*64
    elif damage=='namespace':p['observer']['pid_namespace']=77;p['processes'][-1]['context']['pid_namespace']=77
    elif damage=='uid':p['observer']['uid']=1;p['processes'][-1]['context']['uid']=1
    elif damage=='binary-replaced':p['binary_before']['sha256']=p['binary_after']['sha256']='e'*64
    else:p['devices'][0]['minor']=4
    with pytest.raises(ValueError,match='GPU_PROCESS_(INTERVAL_UNVERIFIED|PACKET_INVALID)'):
        probe.compare(packet(),p,example_scope())


@pytest.mark.parametrize('kind,raw',[
 ('devices',b''),('devices',(UUID+', 3, Enabled, 1\n').encode()),
 ('devices',(UUID+', 3, Disabled, N/A\n').encode()),('compute',b'N/A, GPU-unknown, 0\n'),
 ('compute',('0, '+UUID+', 1\n').encode()),('compute',('1, '+UUID+', -1\n').encode()),
 ('compute',('1, '+UUID+', 1\n1, '+UUID+', 1\n').encode()),
 ('devices',(UUID+', 3, Disabled, 1\n'+OTHER+', 3, Disabled, 1\n').encode())])
def test_na_unidentified_mig_or_duplicate_metadata_never_means_zero(kind,raw):
    with pytest.raises(ValueError,match='GPU_PROCESS_QUERY_INVALID'):probe.parse_query(kind,raw)


def test_exact_numeric_cli_metadata_only():
    assert probe.parse_query('compute',b'')==[]
    assert probe.parse_query('devices',(UUID+', 3, [N/A], 12\n').encode())==[{'uuid':UUID,'minor':3,'memory_used_mib':12}]
    assert probe.parse_query('compute',('102, '+UUID+', 12\n').encode())==[{'host_pid':102,'uuid':UUID,'memory_used_mib':12}]


@pytest.mark.parametrize('damage',['stderr','timeout','truncated','failure'])
def test_cli_stderr_and_incomplete_bounded_capture_cannot_be_a_good_query(monkeypatch,damage):
    def capture(argv,**kwargs):
        assert argv==['/usr/bin/nvidia-smi',probe.QUERIES['compute'],'--format=csv,noheader,nounits']
        assert kwargs['max_stdout_bytes']==262144 and kwargs['max_stderr_bytes']==32768
        return {'returncode':1 if damage=='failure'else 0,'stdout_bytes':b'',
            'stderr_bytes':b'EXAMPLE-not-source'if damage=='stderr'else b'',
            'capture':{'complete':damage not in{'timeout','truncated'}}}
    monkeypatch.setattr(probe,'capture_pipes',capture)
    with pytest.raises(ValueError,match='GPU_PROCESS_QUERY_UNAVAILABLE'):probe._query('compute',probe.time.monotonic()+5)


def test_owned_cpu_child_fd_and_nspid_are_real_kernel_metadata_without_gpu_execution():
    child=subprocess.Popen([sys.executable,'-I','-S','-c',
        "import os,sys;f=open('/dev/null','rb');print(os.getpid(),flush=True);sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    try:
        assert int(child.stdout.readline())==child.pid
        device=os.stat('/dev/null');row=probe._scan(child.pid,
            [{'major':os.major(device.st_rdev),'minor':os.minor(device.st_rdev)}],lambda:None)
        assert row['stable']and row['nspid'][-1]==child.pid
        assert row['context']==selected.context(child.pid)and row['handles']
        assert b'/dev/null'not in probe.canonical(row)
    finally:
        child.communicate(b'x');assert child.returncode==0
