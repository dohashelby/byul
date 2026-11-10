"""Explicit-file, nonrecursive copy minimization and OS-reader acceptance.

No source bytes, marker values, credential values, chmod or deletion are emitted.
Only caller-selected existing files are read, with fixed file/count/byte limits.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import pwd

from tools.selected_runtime_check import fact

MAX_FILES = 16
MAX_FILE_BYTES = 8 * 1024 * 1024


def metadata(value):
    return tuple(getattr(value,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_gid'))


def reader_access(path, user, timeout):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('bounded existing reader account name required')
    try:
        pwd.getpwnam(user)
        # Sentinel exit statuses distinguish an actual denied read from sudo
        # failure (commonly exit1). File paths are positional arguments.
        result = subprocess.run(['sudo','-n','-u',user,'--','/bin/sh','-c',
                                 'if test -r "$1"; then exit 80; else exit 81; fi','byul-reader-test',str(path)],
                                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                timeout=min(timeout,5),check=False)
    except (OSError, KeyError, subprocess.TimeoutExpired) as error:
        return {'state':'unknown','error_class':type(error).__name__}
    return {'state':'observed' if result.returncode in (80,81) else 'unknown',
            'readable':result.returncode==80 if result.returncode in (80,81) else None,'exit_code':result.returncode}


def scan_file(path, forbidden, *, allowed_reader=None, denied_reader=None, timeout=15):
    path = Path(path).absolute()
    descriptor = None
    details = {'path':str(path),'max_bytes':MAX_FILE_BYTES,'raw_bytes_saved':False}
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return fact('COPY-SCAN',None,{**details,'reason':'regular_file_required'},scope='explicit file only; symlinks/directories are not followed')
        descriptor = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino) or not stat.S_ISREG(opened.st_mode):
            return fact('COPY-SCAN',None,{**details,'reason':'file_identity_changed'},scope='selected file identity')
        if opened.st_size > MAX_FILE_BYTES:
            return fact('COPY-SCAN',None,{**details,'file_bytes':opened.st_size,'reason':'file_exceeds_complete_scan_limit'},scope='no partial absence verdict')
        raw = bytearray()
        while len(raw) <= MAX_FILE_BYTES:
            part = os.read(descriptor,min(65536,MAX_FILE_BYTES-len(raw)+1))
            if not part:break
            raw.extend(part)
        after, current = os.fstat(descriptor), path.lstat()
        stable = metadata(opened)==metadata(after)==metadata(current) and len(raw)<=MAX_FILE_BYTES
        hits = {label:bytes(raw).count(value.encode()) for label,value in forbidden.items()}
        details.update(file_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),uid=opened.st_uid,gid=opened.st_gid,
                       mode=oct(stat.S_IMODE(opened.st_mode)),stable_complete_file=stable,forbidden_occurrences=hits)
        access_ok = True
        if allowed_reader and denied_reader:
            allowed = reader_access(path,allowed_reader,timeout); denied = reader_access(path,denied_reader,timeout)
            details.update(allowed_reader={'account':allowed_reader,**allowed},denied_reader={'account':denied_reader,**denied})
            access_ok = (allowed.get('readable') is True and denied.get('readable') is False
                         if allowed['state']==denied['state']=='observed' else None)
        else:
            details['os_reader_acceptance']='not_run'
        # Reader probes are separate OS calls. Bind their final path/metadata to
        # the held descriptor too; rotation/chmod must not certify a new file
        # with the old safe snapshot's scan result.
        try:
            bound = stable and metadata(opened)==metadata(os.fstat(descriptor))==metadata(path.lstat())
        except OSError as error:
            bound=False;details['final_stat_error_class']=type(error).__name__
        details.update(snapshot_complete_stable=stable,stable_complete_file=bound,
                       reader_generation_bound=bound if allowed_reader and denied_reader else None,
                       current_path_matches_snapshot=bound)
        passed = False if any(hits.values()) else None if not bound else False if access_ok is False else True if forbidden and access_ok is True else None
        return fact('COPY-SCAN',passed,details,scope='selected complete file forbidden-value scan; OS reader test only when explicitly supplied')
    except (OSError, ValueError) as error:
        return fact('COPY-SCAN',None,{**details,'error_class':type(error).__name__},scope='selected file unavailable')
    finally:
        if descriptor is not None:os.close(descriptor)


def copies(paths, forbidden, *, allowed_reader=None, denied_reader=None, timeout=15, volume_evidence=None):
    if not 1<=len(paths)<=MAX_FILES or bool(allowed_reader)!=bool(denied_reader):
        raise ValueError('one to16 explicit files and both reader roles together required')
    if not forbidden or any(not isinstance(v,str) or not 1<=len(v.encode())<=4096 for v in forbidden.values()):
        raise ValueError('bounded nonempty environment marker/credential values required')
    cases=[]
    for index,path in enumerate(paths):
        row=scan_file(path,forbidden,allowed_reader=allowed_reader,denied_reader=denied_reader,timeout=timeout)
        row['id']='COPY-SCAN-'+str(index);cases.append(row)
    volume={'state':'not_run','file_volume_binding_verified':False}
    if volume_evidence:
        path=Path(volume_evidence)
        try:
            with path.open('rb') as source:raw=source.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('volume evidence cap')
            value=json.loads(raw)
            entries=value.get('Volumes',[])
            selected=[{'volume_id':v.get('VolumeId') if isinstance(v.get('VolumeId'),str) and re.fullmatch(r'vol-[a-f0-9]{8,32}',v['VolumeId']) else None,
                       'encrypted':v.get('Encrypted') if type(v.get('Encrypted')) is bool else None,
                       'kms_key_ref_sha256':hashlib.sha256(str(v['KmsKeyId']).encode()).hexdigest() if v.get('KmsKeyId') else None}
                      for v in entries if isinstance(v,dict)]
            volume={'state':'observed' if selected else 'unknown','source_sha256':hashlib.sha256(raw).hexdigest(),
                    'volumes':selected,'file_volume_binding_verified':False}
        except (OSError,ValueError,AttributeError) as error:
            volume={'state':'unknown','error_class':type(error).__name__,'file_volume_binding_verified':False}
    return {'cases':cases,'volume_settings_evidence':volume,'recursive_scan':False,'files_mutated':False,
            'all_active_copies_inventoried':False,'security_verdict':None}
