#!/usr/bin/env python3
"""Bounded metadata-only cache inventory and stopped-trace projection.

No tracer, engine, process signal, approval, or security judgement is started.
The root actor supplies exact process/Pod binding and before/after context.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

from serving_runtime.mvp.assets import _safe_relative
from serving_runtime.mvp.policy_bundle import _strict_json

SCHEMA='selected-cache-metadata/1.0'
SUFFIXES={'.so','.cubin','.ptx','.o','.a','.bin','.json','.py','.pkl'}
DEFAULT_PREFIXES=('triton','inductor','cuda','vllm/torch_compile_cache','home/.cache/flashinfer','xdg/flashinfer')
_SAFE=re.compile(r'[A-Za-z0-9_./@:+-]{1,1024}')
_NUM=r'(?:0x[0-9a-fA-F]+|-?[0-9]+)'


def allowed_path(value,root,prefixes):
    if type(value) is not str or _SAFE.fullmatch(value) is None:return None
    path=PurePosixPath(value);base=PurePosixPath(root)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(base):return None
    relative=str(path.relative_to(base))
    if not any(PurePosixPath(relative).is_relative_to(PurePosixPath(p)) for p in prefixes):return None
    return relative if path.suffix in SUFFIXES else None


def _version(st):
    return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def bounded_inventory(root,*,prefixes=DEFAULT_PREFIXES,max_entries=2000,max_files=256,max_file_bytes=64*1024*1024,max_total_bytes=256*1024*1024,max_seconds=10):
    root=Path(root);started=time.monotonic();counts=Counter();files=[];read_bytes=0;seen=0
    root_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    def walk(directory,relative=''):
        nonlocal read_bytes,seen
        with os.scandir(directory) as entries:
            for entry in entries:
                seen+=1
                if seen>max_entries or time.monotonic()-started>=max_seconds:
                    counts['entry_or_time_bound']+=1;return False
                name=entry.name;rel=(relative+'/'+name).lstrip('/')
                if _SAFE.fullmatch(rel) is None or '..' in PurePosixPath(rel).parts:
                    counts['unsupported_name']+=1;continue
                try:
                    st=os.stat(name,dir_fd=directory,follow_symlinks=False)
                    if stat.S_ISLNK(st.st_mode):counts['symlink']+=1;continue
                    if stat.S_ISDIR(st.st_mode):
                        if not any(PurePosixPath(rel).is_relative_to(PurePosixPath(p)) or PurePosixPath(p).is_relative_to(PurePosixPath(rel)) for p in prefixes):continue
                        child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                        try:
                            if _version(st)!=_version(os.fstat(child)):
                                counts['directory_changed']+=1;continue
                            if not walk(child,rel):return False
                            if _version(st)!=_version(os.stat(name,dir_fd=directory,follow_symlinks=False)):
                                counts['directory_changed']+=1
                        finally:os.close(child)
                    elif stat.S_ISREG(st.st_mode) and allowed_path('/cache/'+rel,'/cache',prefixes):
                        if len(files)>=max_files:counts['file_bound']+=1;return False
                        row={'path':rel,'file_type':Path(rel).suffix,'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode)),
                             'device':st.st_dev,'inode':st.st_ino,'size':st.st_size,'mtime_ns':st.st_mtime_ns,'ctime_ns':st.st_ctime_ns}
                        if st.st_size>max_file_bytes or read_bytes+st.st_size>max_total_bytes:
                            row['hash_state']='byte_bound';counts['byte_bound']+=1;files.append(row);continue
                        fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
                        try:
                            before=os.fstat(fd);digest=hashlib.sha256();size=0
                            if not stat.S_ISREG(before.st_mode) or _version(before)!=_version(st):
                                row['hash_state']='changed_before_hash';counts['changed_before_hash']+=1;files.append(row);continue
                            while True:
                                if time.monotonic()-started>=max_seconds or size>max_file_bytes or read_bytes>=max_total_bytes:
                                    counts['hash_bound']+=1;row['hash_state']='bound';break
                                chunk=os.read(fd,min(65536,max_file_bytes-size+1,max_total_bytes-read_bytes))
                                if not chunk:
                                    after=os.fstat(fd)
                                    final_path=os.stat(name,dir_fd=directory,follow_symlinks=False)
                                    same=_version(before)==_version(after)==_version(final_path) and size==after.st_size
                                    row['hash_state']='stable' if same else 'changed_during_hash'
                                    if same:row['sha256']=digest.hexdigest()
                                    else:counts['changed_during_hash']+=1
                                    break
                                size+=len(chunk);read_bytes+=len(chunk);digest.update(chunk)
                        finally:os.close(fd)
                        files.append(row)
                except OSError:counts['unavailable']+=1
        return True
    try:
        root_before=os.fstat(root_fd);complete=walk(root_fd)
        if _version(root_before)!=_version(os.stat(root,follow_symlinks=False)):
            counts['root_changed']+=1
    finally:os.close(root_fd)
    return {'schema':SCHEMA,'kind':'inventory','state':'observed' if complete and not counts else 'partial',
            'files':files,'counts':dict(counts),'seen_entries':seen,'hashed_bytes':read_bytes,
            'original_file_contents_exported':False,'actual_cache_read_verified':False,'security_verdict':'UNKNOWN'}


def normalize_strace(lines,*,pid,uid,cache_path='/cache',prefixes=DEFAULT_PREFIXES,max_lines=10000,max_events=512):
    """Only completed calls. Raw read/write arguments must be numeric addresses.

    Per-PID fd mapping is deliberately not inferred across threads or forks.
    Lost/resumed calls and unsupported descriptor changes invalidate hints.
    Read/write paths are candidates even with an uninterrupted local hint: this
    bounded trace does not prove shared descriptor-table mutation coverage.
    """
    fds={};events=[];counts=Counter();seen=0
    for line in lines:
        seen+=1
        if seen>max_lines:counts['line_bound']+=1;break
        if len(events)>=max_events:counts['event_bound']+=1;break
        if len(line)>65536:counts['line_byte_bound']+=1;fds.clear();continue
        text=line.strip()
        if not text or text.startswith(('+++','---','strace:')):continue
        match=re.fullmatch(r'(?:(?:\[pid\s+(\d+)\]|(\d+))\s+)?(\d+\.\d+)\s+([a-z0-9_]+)\((.*)\)\s+=\s+(-?(?:0x[0-9a-fA-F]+|[0-9]+))(?:<([^>]+)>)?(?:\s+.*)?',text)
        if not match:
            counts['unparsed_or_incomplete']+=1;fds.clear();continue
        explicit=match[1] or match[2]
        if explicit and int(explicit)!=pid:counts['other_pid']+=1;fds.clear();continue
        at,call,args,result,result_path=match.group(3,4,5,6,7)
        try:returned=int(result,0)
        except ValueError:counts['unsupported_return']+=1;continue
        if returned<0:counts['failed_call']+=1;fds.clear();continue
        record={'pid':pid,'uid':uid,'at_host_epoch':at,'syscall':call,'return':returned}
        if call in ('openat','openat2'):
            # -yy result path is the selected process's resolved descriptor path.
            fds.pop(returned,None)
            path=allowed_path(result_path,cache_path,prefixes)
            if path is None:continue
            fds[returned]=path;record.update(kind='open',path=path)
        elif call in ('read','pread64','write','pwrite64','mmap','close'):
            # A decoded quoted buffer is never accepted, retained or exported.
            if '"' in args:counts['unsafe_decoded_buffer']+=1;continue
            parts=[x.strip() for x in args.split(',')]
            index=4 if call=='mmap' else 0
            if len(parts)<=index or any(re.fullmatch(_NUM,part) is None for part in parts):counts['unparsed_args']+=1;continue
            fd=int(parts[index],0);path=fds.get(fd)
            if call=='close':fds.pop(fd,None);continue
            if path is None:counts['unbound_fd']+=1;continue
            if call in ('read','pread64'):
                if returned==0:continue
                record.update(kind='read_candidate',candidate_path=path,bytes=returned,descriptor_path_verified=False)
            elif call in ('write','pwrite64'):
                if returned==0:continue
                record.update(kind='write_candidate',candidate_path=path,bytes=returned,descriptor_path_verified=False)
            else:
                record.update(kind='mapping_candidate',candidate_path=path,descriptor_path_verified=False)
        else:counts['unsupported_call']+=1;fds.clear();continue
        events.append(record)
    return {'schema':SCHEMA,'kind':'strace_metadata','state':'observed' if not counts else 'partial',
            'events':events,'counts':dict(counts),'seen_lines':seen,
            'process_binding':'requires_exact_external_before_after_PID_start_UID_container_context','uid_source':'operator_supplied',
            'coverage_complete':False,'all_cache_consumption_verified':False,'actual_cache_read_verified':False,
            'argv_env_or_buffers_exported':False,'security_verdict':'UNKNOWN'}


def normalize_tetragon(rows,*,pod_uid,container_id,cache_path='/cache',prefixes=DEFAULT_PREFIXES,max_events=512):
    """Reuse already normalized existing file events; a permission check is not a write."""
    events=[];counts=Counter()
    cid=container_id.split('://')[-1]
    for row in rows:
        if len(events)>=max_events:counts['event_bound']+=1;break
        if type(row) is not dict or row.get('source')!='tetragon' or row.get('rule_id')!='TG-CACHE-01':continue
        path=allowed_path(row.get('path'),cache_path,prefixes)
        if path is None:continue
        if row.get('access')!='write':counts['unsupported_access']+=1;continue
        identity=row.get('identity',{})
        if type(identity) is not dict or identity.get('state')!='confirmed' or identity.get('pod_uid')!=pod_uid or identity.get('container_id')!=cid:
            counts['unbound_identity']+=1;continue
        pid=identity.get('node_pid',identity.get('pid'))
        if type(pid) is not int or pid<1:counts['unavailable_pid']+=1;continue
        try:
            at=row.get('observed_at')
            if type(at) is not str or len(at)>64 or datetime.fromisoformat(at.replace('Z','+00:00')).tzinfo is None:raise ValueError
        except ValueError:counts['unavailable_time']+=1;continue
        events.append({'kind':'permission_check','access':row.get('access'),'path':path,'pod_uid':pod_uid,
                       'container_id':cid,'pid':pid,
                       'uid':None,'at':row.get('observed_at'),'actual_io_bytes':None,'io_success_verified':False})
    return {'schema':SCHEMA,'kind':'existing_tetragon_metadata','events':events,'counts':dict(counts),
            'actual_cache_read_verified':False,'permission_check_is_successful_io':False,'security_verdict':'UNKNOWN'}


def _write_new(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:json.dump(value,out,ensure_ascii=False,indent=2);out.write('\n')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind',choices=('inventory','strace','tetragon'));p.add_argument('--input');p.add_argument('--root',required=True)
    p.add_argument('--cache-path',default='/cache');p.add_argument('--pid',type=int);p.add_argument('--uid',type=int)
    p.add_argument('--pod-uid');p.add_argument('--container-id');p.add_argument('--prefix',action='append')
    p.add_argument('--out',required=True);p.add_argument('--max-input-bytes',type=int,default=8*1024*1024)
    a=p.parse_args(argv)
    try:
        root=Path(a.root)
        if not root.is_absolute() or '..' in root.parts or root.is_symlink():raise ValueError
        if Path(a.out).resolve().is_relative_to(root.resolve()):raise ValueError
        prefixes=tuple(_safe_relative(x) for x in (a.prefix or DEFAULT_PREFIXES))
        if not 1<=a.max_input_bytes<=8*1024*1024:raise ValueError
        if a.kind=='inventory':
            if not a.root:raise ValueError
            result=bounded_inventory(a.root,prefixes=prefixes)
        else:
            with open(a.input,'rb') as source:raw=source.read(a.max_input_bytes+1)
            bounded=len(raw)>a.max_input_bytes;raw=raw[:a.max_input_bytes]
            if a.kind=='strace':
                if type(a.pid) is not int or a.pid<1 or type(a.uid) is not int or a.uid<0:raise ValueError
                result=normalize_strace(raw.decode('utf-8',errors='strict').splitlines(),pid=a.pid,uid=a.uid,cache_path=a.cache_path,prefixes=prefixes)
            else:
                if not a.pod_uid or not a.container_id:raise ValueError
                rows=[_strict_json(line,'normalized cache event') for line in raw.splitlines()[:10000] if line.strip()]
                result=normalize_tetragon(rows,pod_uid=a.pod_uid,container_id=a.container_id,cache_path=a.cache_path,prefixes=prefixes)
            result['input_truncated']=bounded
            if bounded:result['state']='partial'
        result['generation_root']=str(root)
        result['generation_binding']='operator declaration; exact existing asset/Pod binding must accompany this metadata'
        _write_new(a.out,result)
    except (OSError,ValueError,TypeError) as error:
        print(json.dumps({'state':'error','error_type':type(error).__name__}));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
