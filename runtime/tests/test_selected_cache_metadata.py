from pathlib import Path
import importlib.util,json,os
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('selected_cache_metadata',ROOT/'tools/selected_cache_metadata.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)


def line(call):return '1700000000.000001 '+call

def test_raw_read_write_maps_only_selected_descriptor_without_contents():
    rows=[line('openat(AT_FDCWD, "cache.bin", O_RDWR|O_CREAT, 0600) = 3</cache/triton/hash/cache.bin>'),
          line('write(0x3, 0xdeadbeef, 0x4) = 0x4'),line('read(0x3, 0xbeef, 0x4) = 0x4'),
          line('mmap(0, 0x1000, 0x1, 0x2, 0x3, 0) = 0x70000000'),line('close(0x3) = 0'),
          line('read(0x3, 0xbeef, 0x4) = 4')]
    result=t.normalize_strace(rows,pid=10,uid=2000)
    assert [x['kind'] for x in result['events']]==['open','write_candidate','read_candidate','mapping_candidate']
    assert result['events'][0]['path']=='triton/hash/cache.bin'
    assert all(x['candidate_path']=='triton/hash/cache.bin' and x['descriptor_path_verified'] is False for x in result['events'][1:])
    assert result['events'][2]['bytes']==4 and result['counts']['unbound_fd']==1
    assert result['security_verdict']=='UNKNOWN' and result['coverage_complete'] is False
    assert 'deadbeef' not in json.dumps(result)


def test_content_paths_pids_unfinished_and_decoded_buffers_cannot_qualify():
    secret='PRIVATE_MODEL_OR_TOKEN_MARKER'
    rows=[line(f'openat(AT_FDCWD, "/models/config.json", O_RDONLY) = 4</models/config.json>'),
          line('openat(AT_FDCWD, "/cache/hf/token", O_RDONLY) = 5</cache/hf/token>'),
          line('openat(AT_FDCWD, "/cache-secret/triton/key.bin", O_RDONLY) = 6</cache-secret/triton/key.bin>'),
          line('openat(AT_FDCWD, "artifact.bin", O_RDONLY) = 7</cache/triton/artifact.bin>'),
          line(f'read(7, "{secret}", 20) = 20'),
          line('read(0x7, UNQUOTED_DATA, 0x4) = 4'),
          '11 '+line('read(0x7, 0xdead, 0x4) = 4'),
          line('read(0x7, 0xdead, 0x4 <unfinished ...>'),
          line('read(0x7, 0xdead, 0x4) = -1 EACCES (Permission denied)')]
    out=t.normalize_strace(rows,pid=10,uid=2000)
    assert [e['kind'] for e in out['events']]==['open']
    assert secret not in json.dumps(out) and '/models' not in json.dumps(out) and 'UNQUOTED_DATA' not in json.dumps(out)
    assert out['counts']['unsafe_decoded_buffer']==1 and out['counts']['other_pid']==1
    assert out['state']=='partial'


@pytest.mark.parametrize('path',['/cache/../models/config.json','/cache/triton/a\n.bin','/cache/triton/secret.txt','/cache2/triton/a.bin','relative/triton/a.bin'])
def test_path_scope_is_not_a_string_prefix_or_arbitrary_file_whitelist(path):
    assert t.allowed_path(path,'/cache',t.DEFAULT_PREFIXES) is None


def test_fd_not_inferred_from_another_pid_and_events_are_bounded():
    rows=[line('openat(AT_FDCWD, "x", O_RDONLY) = 3</cache/inductor/a.so>')]*5
    out=t.normalize_strace(rows,pid=10,uid=2000,max_events=2)
    assert len(out['events'])==2 and out['counts']['event_bound']==1
    gap=t.normalize_strace([line('read(0x3, 0xdead, 0x4) = 4')],pid=10,uid=2000)
    assert gap['events']==[] and gap['counts']['unbound_fd']==1


def test_existing_tg_write_permission_check_is_not_actual_read_or_successful_write():
    row={'source':'tetragon','rule_id':'TG-CACHE-01','path':'/cache/triton/a.cubin','access':'write','observed_at':'2026-11-06T15:00:00Z',
         'arguments':'DO_NOT_EXPORT','identity':{'state':'confirmed','pod_uid':'pod','container_id':'a'*64,'pid':10}}
    result=t.normalize_tetragon([row],pod_uid='pod',container_id='containerd://'+'a'*64)
    assert result['events'][0]['kind']=='permission_check'
    assert result['events'][0]['io_success_verified'] is False and result['actual_cache_read_verified'] is False
    assert 'DO_NOT_EXPORT' not in json.dumps(result)
    row['identity']['pod_uid']='other'
    assert t.normalize_tetragon([row],pod_uid='pod',container_id='a'*64)['events']==[]


def test_inventory_hash_stat_is_bounded_no_symlinks_no_content_export(tmp_path):
    cache=tmp_path/'cache';cache.mkdir();(cache/'triton').mkdir();(cache/'hf').mkdir()
    secret='MODEL_AND_SECRET_CONTENT_NOT_EXPORTED';(cache/'hf'/'token.json').write_text(secret)
    (cache/'triton'/'a.cubin').write_bytes(b'compiler artifact')
    (cache/'triton'/'link.bin').symlink_to(cache/'hf'/'token.json')
    result=t.bounded_inventory(cache,max_file_bytes=1024)
    assert [x['path'] for x in result['files']]==['triton/a.cubin']
    assert result['files'][0]['hash_state']=='stable' and result['counts']['symlink']==1
    assert secret not in json.dumps(result) and 'token.json' not in json.dumps(result)
    bounded=t.bounded_inventory(cache,max_file_bytes=1)
    assert bounded['files'][0]['hash_state']=='byte_bound' and 'sha256' not in bounded['files'][0]
    stopped=t.bounded_inventory(cache,max_entries=1)
    assert stopped['state']=='partial'
    out=cache/'new-report.json'
    assert t.main(['inventory','--root',str(cache),'--out',str(out)])==2 and not out.exists()


def test_reports_never_replace_existing_file(tmp_path):
    out=tmp_path/'exists';out.write_text('old')
    with pytest.raises(FileExistsError):t._write_new(out,{'state':'UNKNOWN'})
    assert out.read_text()=='old'


@pytest.mark.parametrize('change', [
    'openat(AT_FDCWD, "foreign", O_RDONLY) = 3</foreign.bin>',
    'dup2(0x4, 0x3) = 3',
    'read(0x4, 0xbeef, 0x4 <unfinished ...>',
    '[pid 11] 1700000000.000001 close(0x3) = 0',
])
def test_descriptor_mutation_or_gap_never_reuses_cache_path(change):
    rows=[line('openat(AT_FDCWD, "x", O_RDONLY) = 3</cache/triton/a.bin>'),
          change if change.startswith('[pid') else line(change),
          line('read(0x3, 0xbeef, 0x4) = 4')]
    out=t.normalize_strace(rows,pid=10,uid=2000)
    assert [e['kind'] for e in out['events']]==['open']
    assert out['actual_cache_read_verified'] is False


def test_same_inode_changed_between_stat_and_open_is_partial(tmp_path,monkeypatch):
    root=tmp_path/'cache';(root/'triton').mkdir(parents=True)
    file=root/'triton'/'a.bin';file.write_bytes(b'A')
    original=t.os.open
    def changed_open(path,*args,**kwargs):
        if path=='a.bin':file.write_bytes(b'BBBB')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(t.os,'open',changed_open)
    out=t.bounded_inventory(root)
    assert out['state']=='partial'
    assert out['files'][0]['hash_state']=='changed_before_hash'
    assert 'sha256' not in out['files'][0]


def test_path_replaced_after_open_is_not_stable(tmp_path,monkeypatch):
    root=tmp_path/'cache';(root/'triton').mkdir(parents=True)
    file=root/'triton'/'a.bin';file.write_bytes(b'A')
    original=t.os.read;changed=False
    def changed_read(fd,n):
        nonlocal changed
        value=original(fd,n)
        if not changed:
            changed=True;file.unlink();file.write_bytes(b'BBBB')
        return value
    monkeypatch.setattr(t.os,'read',changed_read)
    out=t.bounded_inventory(root)
    assert out['state']=='partial'
    assert out['files'][0]['hash_state']=='changed_during_hash'
    assert 'sha256' not in out['files'][0]
