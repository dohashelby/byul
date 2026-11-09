#!/usr/bin/env python3
"""Read host asset metadata, or exercise new disposable UID fixtures only.

Existing cache/assets checkers are reused. The fixture is not a compiler and its
read results never qualify as actual engine cache consumption/regeneration.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from serving_runtime.enforcement.cache import check_cache
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.mvp.assets import snapshot, check, _safe_relative
from serving_runtime.mvp.policy_bundle import _strict_json

SCHEMA = "selected-asset-boundaries/1.0"
FIXTURE = b"BYUL_PUBLIC_INNOCUOUS_CACHE_FIXTURE_V1\n"
PROBE = r'''
import errno,hashlib,json,os,sys
p=json.loads(sys.argv[1]);out={'requested_uid':p['uid'],'requested_gid':p['gid'],'action':p['action']}
try:
 os.setgroups([]);os.setgid(p['gid']);os.setuid(p['uid'])
 out.update(uid=os.geteuid(),gid=os.getegid(),groups=os.getgroups())
 action=p['action'];directory=p.get('directory_fd');path=os.path.basename(p['path']) if directory is not None else p['path']
 if action=='access':
  out.update(exists=os.path.isdir(path),write_access=os.access(path,os.W_OK,effective_ids=True,follow_symlinks=False,dir_fd=directory),search_access=os.access(path,os.X_OK,effective_ids=True,follow_symlinks=False,dir_fd=directory))
 elif action=='read':
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
  try: raw=os.read(fd,1025)
  finally: os.close(fd)
  out.update(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
 elif action in ('write_new','overwrite'):
  flags=os.O_WRONLY|os.O_NOFOLLOW|(os.O_CREAT|os.O_EXCL if action=='write_new' else os.O_TRUNC)
  fd=os.open(path,flags,0o600,dir_fd=directory)
  try: os.write(fd,b'BYUL_PUBLIC_INNOCUOUS_CACHE_FIXTURE_V1\n')
  finally: os.close(fd)
 elif action=='replace': os.replace(os.path.basename(p['source']),path,src_dir_fd=p['source_directory_fd'],dst_dir_fd=directory)
 elif action=='symlink': os.symlink('not-an-approved-artifact',path,dir_fd=directory)
 elif action=='chown': os.chown(path,p['new_uid'],-1,follow_symlinks=False,dir_fd=directory)
 else: raise ValueError('unknown action')
 out['succeeded']=True
except OSError as e: out.update(succeeded=False,errno=e.errno,error_type=type(e).__name__)
except Exception as e: out.update(succeeded=None,error_type=type(e).__name__)
print(json.dumps(out,sort_keys=True))
'''


def frozen_json(path):
    raw = Path(path).read_bytes()
    document = _strict_json(raw, 'selected asset input')
    if not isinstance(document, dict): raise ValueError('input object required')
    return document, hashlib.sha256(raw).hexdigest()


def absolute(path):
    value = Path(path)
    if not value.is_absolute() or '..' in value.parts:
        raise ValueError("absolute paths without parent traversal required")
    return value


def metadata_chain(path, trusted_writers=(0,)):
    """lstat each ancestor, stop before traversing a symlink; never chmod/chown."""
    path = absolute(path)
    rows = []
    for part in reversed((path, *path.parents)):
        try:
            st = part.lstat()
        except OSError as error:
            return {"state": "unknown", "entries": rows, "errno": error.errno,
                    "error_type": type(error).__name__, "failed_path": str(part)}
        row = {"path": str(part), "uid": st.st_uid, "gid": st.st_gid,
               "mode": oct(stat.S_IMODE(st.st_mode)), "device": st.st_dev,
               "inode": st.st_ino, "symlink": stat.S_ISLNK(st.st_mode),
               "owner_is_declared_trusted_writer": st.st_uid in trusted_writers,
               "group_writable": bool(st.st_mode & stat.S_IWGRP),
               "other_writable": bool(st.st_mode & stat.S_IWOTH)}
        rows.append(row)
        if row['symlink'] or not stat.S_ISDIR(st.st_mode):
            return {"state": "blocked_symlink_or_non_directory", "entries": rows}
    return {"state": "observed", "entries": rows,
            "trusted_writer_uids": sorted(set(trusted_writers)),
            "access_verdict": "metadata_only; ACL/group membership and container mount enforcement separate"}


def approved_file_metadata(root, asset_check, trusted_writers):
    items = asset_check.get('items')
    total = asset_check.get('total')
    if not isinstance(items,list) or type(total) is not int or total < 1 or len(items) != total:
        return {'state':'not_available','entries':[], 'reason':'complete approved file list required'}
    entries = []
    root_fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for item in items:
            directory = os.dup(root_fd)
            try:
                relative = _safe_relative(item.get('path') if isinstance(item,dict) else None)
                parts = Path(relative).parts
                for part in parts[:-1]:
                    child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    os.close(directory); directory = child
                st = os.stat(parts[-1],dir_fd=directory,follow_symlinks=False)
                regular = stat.S_ISREG(st.st_mode)
                entries.append({'path':relative,'state':'observed' if regular else 'blocked_non_regular',
                    'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode)),
                    'device':st.st_dev,'inode':st.st_ino,'size':st.st_size,
                    'owner_is_declared_trusted_writer':st.st_uid in trusted_writers,
                    'owner_writable':bool(st.st_mode & stat.S_IWUSR),
                    'group_writable':bool(st.st_mode & stat.S_IWGRP),
                    'other_writable':bool(st.st_mode & stat.S_IWOTH)})
            except (OSError,ValueError) as error:
                entries.append({'state':'unknown','error_type':type(error).__name__,'errno':getattr(error,'errno',None)})
            finally: os.close(directory)
    finally: os.close(root_fd)
    return {'state':'observed' if all(x['state']=='observed' for x in entries) else 'unknown',
            'entries':entries,'content_read':False,'limits':'owner/mode observation; ACL and effective reader/writer group membership remain separate'}


def inspect_host(intent, intent_sha, model_host_path, asset_report, asset_sha, trusted_writers, *, engine_gid=0, denied_uid=65534):
    if intent.assets is None:
        raise ValueError("assets intent required")
    binding = asset_report.get('execution_binding')
    if (asset_report.get('schema_version') != 'runtime-update-asset-check/1.0'
            or not isinstance(binding, dict) or not isinstance(asset_report.get('asset_check'),dict)):
        raise ValueError('existing runtime asset report required')
    bound = (asset_report.get('intent_sha256') == intent_sha
             and binding.get('state') == 'match'
             and binding.get('model_host_path') == str(absolute(model_host_path))
             and binding.get('cache_host_path') == intent.assets.generation_path)
    model = metadata_chain(model_host_path, trusted_writers)
    cache = metadata_chain(intent.assets.generation_path, (0, intent.assets.engine_uid))
    cache_result = check_cache(intent) if cache['state'] == 'observed' else None
    files = approved_file_metadata(model_host_path, asset_report['asset_check'], trusted_writers) if model['state']=='observed' else {'state':'unknown','entries':[]}
    role_access = []
    if os.geteuid() == 0 and intent.assets.engine_uid not in (0,denied_uid) and denied_uid != 0:
        for path in (model_host_path, intent.assets.generation_path):
            for uid,gid in ((0,0),(intent.assets.engine_uid,engine_gid),(denied_uid,denied_uid)):
                role_access.append({'path':str(path),**run_probe(uid,gid,'access',path,'observe')})
    return {'schema': SCHEMA, 'mode': 'host_read_only', 'observed_at': now(),
            'intent_sha256': intent_sha, 'asset_report_sha256': asset_sha,
            'source_binding_state': 'match' if bound else 'mismatch',
            'existing_asset_check_intact': asset_report.get('asset_check',{}).get('intact'),
            'existing_asset_report_ready': asset_report.get('ready_for_operator_review'),
            'pod_uid': binding.get('pod_uid'), 'container_id': binding.get('container_id'),
            'model_parent_metadata': model, 'model_file_metadata': files, 'cache_parent_metadata': cache,
            'existing_check_cache': cache_result, 'read_only_role_access': role_access,
            'role_access_scope': 'host namespace effective UID/GID with empty supplementary groups; os.access only, no writes; pod mount RO and actual engine consumption are separate',
            'state': 'observed' if bound and model['state'] == cache['state'] == files['state'] == 'observed' else 'unknown',
            'actual_engine_cache_consumption': 'unverified',
            'actual_condition_change_regeneration': 'unverified',
            'approval_created': False, 'mutated_active_assets': False}


def now():
    return datetime.now(timezone.utc).isoformat()


def run_probe(uid, gid, action, path, expected, **extra):
    payload = {'uid': uid, 'gid': gid, 'action': action, 'path': str(path), **extra}
    try:
        result = subprocess.run([sys.executable, '-I', '-c', PROBE, json.dumps(payload)],
                                env={'PATH': os.defpath, 'LC_ALL': 'C.UTF-8'},
                                capture_output=True, timeout=5, check=False,
                                pass_fds=tuple(extra[k] for k in ('directory_fd','source_directory_fd') if k in extra))
        if result.returncode or len(result.stdout) > 8192:
            return {**payload, 'state': 'unknown', 'reason': 'probe_process_failed'}
        facts = json.loads(result.stdout)
        facts['path'] = str(path)
        if 'source' in extra: facts['source_path'] = str(extra['source'])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {**payload, 'state': 'unknown', 'reason': 'probe_not_available'}
    identity = facts.get('uid') == uid and facts.get('gid') == gid and facts.get('groups') == []
    denied = facts.get('succeeded') is False and facts.get('errno') in (errno.EACCES, errno.EPERM)
    if expected == 'observe':
        observed = identity and facts.get('succeeded') is True and facts.get('exists') is True
        return {**facts,'state':'observed' if observed else 'unknown','expected':expected,'identity_verified':identity}
    matches = facts.get('succeeded') is True if expected == 'allow' else denied
    # ENOENT/unsupported syscall/failed identity never qualify as a denial.
    state = ('passed' if matches else 'failed') if identity and facts.get('succeeded') is not None else 'unknown'
    if expected == 'deny' and facts.get('succeeded') is False and not denied:
        state = 'unknown'
    return {**facts, 'state': state, 'expected': expected, 'identity_verified': identity}


def _new_directory(path, uid, gid, mode):
    path.mkdir()
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _tree(root, generation, uid, gid):
    current = root
    for part in (generation.engine, generation.image_id, generation.model_revision, generation.compile_profile):
        current = current / part
        if not current.exists():
            _new_directory(current, 0, 0, 0o700)  # root-only staging until all files and fds exist
    return current


def prepare_sandbox_root(root, protected):
    root = absolute(root)
    parent = metadata_chain(root.parent)
    if parent['state'] != 'observed':
        raise ValueError('sandbox parent path is not a real directory chain')
    for row in parent['entries']:
        mode = int(row['mode'], 8)
        sticky_root = row['uid'] == 0 and bool(mode & stat.S_ISVTX)
        if row['uid'] != 0 or ((mode & 0o022) and not sticky_root) or not mode & 0o001:
            raise ValueError('sandbox parents require root ownership, safe writes and role traversal')
    for path in protected:
        path = absolute(path)
        resolved_root, resolved_path = root.resolve(), path.resolve()
        if (root == path or root.is_relative_to(path) or path.is_relative_to(root)
                or resolved_root == resolved_path or resolved_root.is_relative_to(resolved_path)
                or resolved_path.is_relative_to(resolved_root)):
            raise ValueError('sandbox overlaps an active asset path')
    _new_directory(root, 0, 0, 0o755)  # fails if it already exists; never reuse/delete
    return root


def isolated_uid_fixture(intent, sandbox_root, model_host_path, *, engine_gid, denied_uid=65534):
    report = {'schema': SCHEMA, 'mode': 'new_isolated_uid_fixture', 'observed_at': now(),
              'state': 'unknown', 'proof_scope': 'disposable filesystem fixture, not native compiler cache',
              'actual_engine_cache_consumption': 'unverified',
              'actual_condition_change_regeneration': 'unverified',
              'mutated_active_assets': False, 'approval_created': False, 'probes': []}
    if intent.assets is None or os.geteuid() != 0:
        return {**report, 'reason': 'root_and_assets_intent_required'}
    uid = intent.assets.engine_uid
    if uid in (0, denied_uid) or not 0 < denied_uid < 2**31 or not 0 <= engine_gid < 2**31:
        raise ValueError('distinct root, engine and denied identities required')
    root = prepare_sandbox_root(sandbox_root, (model_host_path, intent.assets.cache_root))
    report['sandbox_root'] = str(root)
    model = root / 'model-source'; _new_directory(model, 0, 0, 0o755)
    (model / 'required-config.json').write_bytes(FIXTURE)
    cache = root / 'cache'; _new_directory(cache, 0, 0, 0o755)
    leaf = _tree(cache, intent.assets.cache_generation, uid, engine_gid)
    artifact = leaf / 'artifact.bin'; artifact.write_bytes(FIXTURE)
    os.chown(artifact, uid, engine_gid); os.chmod(artifact, 0o600)
    attacker = root / 'untrusted'; _new_directory(attacker, 0, 0, 0o700)
    source = attacker / 'replacement.bin'; source.write_bytes(FIXTURE + b'UNTRUSTED\n')
    os.chown(source, denied_uid, denied_uid)
    manifest = snapshot(leaf)
    # Pin both directories before granting fixture access. UID probes use openat/
    # renameat on these descriptors, so a renamed ancestor cannot redirect root.
    model_fd = os.open(model, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    leaf_fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    attacker_fd = os.open(attacker, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    for directory in (leaf, *list(leaf.parents)[:3]):
        os.chown(directory, uid, engine_gid)
    os.chown(attacker, denied_uid, denied_uid)
    cases = [(0, 0, 'read', artifact, 'allow', {}),
             (0, 0, 'write_new', model / 'trusted-root-only', 'allow', {}),
             (uid, engine_gid, 'read', artifact, 'allow', {}),
             (uid, engine_gid, 'write_new', leaf / 'engine-normal.bin', 'allow', {}),
             (uid, engine_gid, 'overwrite', model / 'required-config.json', 'deny', {}),
             (denied_uid, denied_uid, 'overwrite', model / 'required-config.json', 'deny', {}),
             (denied_uid, denied_uid, 'overwrite', artifact, 'deny', {}),
             (denied_uid, denied_uid, 'replace', artifact, 'deny', {'source': str(source)}),
             (denied_uid, denied_uid, 'symlink', leaf / 'untrusted-link', 'deny', {}),
             (uid, engine_gid, 'chown', artifact, 'deny', {'new_uid': denied_uid}),
             (denied_uid, denied_uid, 'chown', artifact, 'deny', {'new_uid': denied_uid}),
             (uid, engine_gid, 'read', artifact, 'allow', {})]
    try:
        report['probes'] = []
        for a,b,c,d,e,f in cases:
            directory_fd = model_fd if d.parent == model else leaf_fd
            extra = {**f,'directory_fd':directory_fd}
            if c == 'replace': extra['source_directory_fd'] = attacker_fd
            report['probes'].append(run_probe(a,b,c,d,e,**extra))
    finally:
        for descriptor in (model_fd,leaf_fd,attacker_fd): os.close(descriptor)
    # The only extra file is the allowed engine write. Check the original entry
    # with the existing checker, and expose extras rather than calling intact.
    checked = check(manifest)
    original_unchanged = checked['counts']['match'] == 1 and checked['counts']['changed'] == 0 and checked['counts']['missing'] == 0 and checked['counts']['symlink'] == 0
    report['fixture_asset_check'] = checked
    report['original_fixture_unchanged'] = original_unchanged
    report['existing_check_cache'] = check_cache(replace(intent, assets=replace(intent.assets, cache_root=str(cache))))
    before_hash = hashlib.sha256(FIXTURE).hexdigest()
    reads = [p for p in report['probes'] if p.get('action') == 'read']
    read_matches = len(reads) == 3 and all(p.get('sha256') == before_hash for p in reads)
    report['fixture_reads_match'] = read_matches
    states = [p['state'] for p in report['probes']]
    report['state'] = ('passed' if original_unchanged and read_matches and all(s == 'passed' for s in states)
                       and report['existing_check_cache']['state'] == 'ok' else
                       'failed' if 'failed' in states or not original_unchanged else 'unknown')
    report['limits'] = ['No active model/cache write, deletion, chmod or chown.',
                        'DAC fixture only; actual mount RO/ACL/container identity are separate witnesses.',
                        'Warm fixture reads are not engine compiler-cache consumption.',
                        'Actual selected condition-change regeneration requires root-owned new declaration/leaf and actual engine cold/warm receipts.']
    return report


def validate_output(path, protected):
    destination = absolute(path).resolve()
    for source in protected:
        root = absolute(source).resolve()
        if destination == root or destination.is_relative_to(root):
            raise ValueError('report output overlaps active assets')


def write_new(path, report):
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inspect', 'isolated'))
    parser.add_argument('--intent', required=True)
    parser.add_argument('--model-host-path', required=True)
    parser.add_argument('--report-out', required=True)
    parser.add_argument('--asset-report')
    parser.add_argument('--trusted-model-writer-uid', action='append', type=int, default=[0])
    parser.add_argument('--sandbox-root')
    parser.add_argument('--engine-gid', type=int, default=0)
    parser.add_argument('--denied-uid', type=int, default=65534)
    args = parser.parse_args(argv)
    try:
        document, sha = frozen_json(args.intent); intent = parse_intent(document)
        if intent.assets is None: raise ValueError('assets intent required')
        validate_output(args.report_out, (args.model_host_path, intent.assets.cache_root))
        if args.mode == 'inspect':
            if not args.asset_report:
                raise ValueError('asset report required')
            asset, asset_sha = frozen_json(args.asset_report)
            result = inspect_host(intent, sha, args.model_host_path, asset, asset_sha, args.trusted_model_writer_uid, engine_gid=args.engine_gid, denied_uid=args.denied_uid)
        else:
            if not args.sandbox_root:
                raise ValueError('new sandbox root required')
            result = isolated_uid_fixture(intent, args.sandbox_root, args.model_host_path,
                                          engine_gid=args.engine_gid, denied_uid=args.denied_uid)
            result['intent_sha256'] = sha
        write_new(args.report_out, result)
    except (OSError, ValueError) as error:
        print(json.dumps({'schema': SCHEMA, 'state': 'error', 'error_type': type(error).__name__}), file=sys.stderr)
        return 2
    return 0 if result['state'] in ('observed', 'passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
