"""Closed passive input captures. Partial source facts never confer a verdict."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

from guard.adapters import vllm_native_input_runtime as runtime
from guard.adapters import vllm_native_input_profiles as profiles
from guard.adapters.vllm_rank_ipc_capture import _read
from guard.adapters.vllm_rank_private_bootstrap import render as render_bootstrap
from guard.update_validation import reserve_output

BOOTSTRAP=render_bootstrap('vllm_rank_ipc_runtime').replace('vllm_rank_ipc_runtime','vllm_native_input_runtime').replace(
    '_byul_rank_ipc','_byul_native_input').replace('RANK_IPC_','NATIVE_INPUT_').encode()
FLAGS={'observer_installed':False,'runtime_qualified':False,'producer_registered':False,'scope_complete':False}
ROW_FIELDS=frozenset({'schema','event','point','source_module','source_sha256','body_code_sha256','context',
    'call_hmac_sha256','root_call_hmac_sha256','parent_root_call_hmac_sha256','object_hmac_sha256',
    'request_hmac_sha256','method','path','operation_started_ns','observation_ns','exception_kind',
    'exception_site','original_invoked','source_authenticated','producer_registered','scope_complete'})
CONTEXT=frozenset({'pid','start_ticks','uid','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256'})


def _sha(value):return type(value)is str and re.fullmatch('[0-9a-f]{64}',value)is not None


def _context(value):
    return(type(value)is dict and set(value)==CONTEXT
        and all(type(value[k])is int and 0<=value[k]<2**63 for k in CONTEXT-{'boot_sha256','time_offsets_sha256'})
        and value['pid']>0 and value['start_ticks']>0
        and all(_sha(value[k])for k in('boot_sha256','time_offsets_sha256')))


def producer():
    """Exact observer, wire reader and consumer bytes; no registration implied."""
    source_root=Path(runtime.__file__).parents[2]
    paths={name:(BOOTSTRAP if name=='sitecustomize.py'else(source_root/name).read_bytes())for name in runtime.ASSETS}
    root=source_root/'guard'
    for name in('collectors/native_input.py','runtime/kubernetes_native_input.py',
            'runtime/assets/native_input_read_v1.py.txt','runtime/kubernetes_http.py',
            'executors/native_input_link.py','executors/input_validation.py',
            'executors/kubernetes_input_validation.py','executors/request_field_probe.py'):
        paths['guard/'+name]=(root/name).read_bytes()
    digest=sha256()
    for name,raw in sorted(paths.items()):digest.update(name.encode()+b'\0'+raw+b'\0')
    return {'name':'guard-vllm-native-input-observation','version':'1','source_sha256':digest.hexdigest()}


def prepare(output, *, image, repository):
    """Freeze current reviewed bytes; a fresh directory and new private key only."""
    from tools.verify_req10_consumption import verify
    reviewed=verify(Path(repository))
    profile=profiles.select(image,runtime='cpython-3.12.3')
    source_root=Path(runtime.__file__).parents[2]
    values={name:(BOOTSTRAP if name=='sitecustomize.py'else(source_root/name).read_bytes())for name in runtime.ASSETS}
    assets={name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}for name,raw in sorted(values.items())}
    session=secrets.token_hex(16)
    cfg={'schema':'guard-native-input-settings/1','session':session,'image':image,'key':secrets.token_hex(32),
        'output_dir':str(runtime.ROOT/'data'/session),'assets':assets}
    raw=runtime.canonical(cfg)
    manifest={'schema':'guard-native-input-prepared-candidate/1','session':session,'image':image,
        'settings_sha256':sha256(raw).hexdigest(),'assets':assets,'profile':profile['id'],
        'source_manifest_sha256':reviewed['reviewed_common_commit'],**FLAGS}
    values.update({'settings.json':raw,'manifest.json':runtime.canonical(manifest)})
    output=reserve_output(Path(output).absolute(),protected=[Path(repository)])
    for name,value in values.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb')as stream:stream.write(value)
    (output/'data'/session).mkdir(parents=True,mode=0o700)
    return manifest


def load_prepared(path, *, expected_uid=None):
    prepared=Path(path)
    manifest=json.loads(_read(prepared/'manifest.json',32768),object_pairs_hook=runtime.shared._pairs)
    expected_uid=os.geteuid()if expected_uid is None else expected_uid
    if type(expected_uid)is not int or not 0<=expected_uid<2**31:raise ValueError('NATIVE_INPUT_PREPARED_OWNER_INVALID')
    path=prepared/'settings.json';before=os.stat(path,follow_symlinks=False)
    raw=_read(path,32768);after=os.stat(path,follow_symlinks=False)
    if(before.st_uid!=expected_uid or stat.S_IMODE(before.st_mode)not in{0o400,0o600}
            or runtime.shared.sources._identity(before)!=runtime.shared.sources._identity(after)):
        raise ValueError('NATIVE_INPUT_PREPARED_OWNER_INVALID')
    cfg=json.loads(raw,object_pairs_hook=runtime.shared._pairs);digest=sha256(raw).hexdigest()
    # This is host verification against an explicit target UID. The SDK's
    # process-local load_settings still requires its own effective UID.
    if(type(cfg)is not dict or set(cfg)!={'schema','session','image','key','output_dir','assets'}
            or cfg['schema']!='guard-native-input-settings/1'
            or type(cfg['session'])is not str or not re.fullmatch('[0-9a-f]{32}',cfg['session'])
            or type(cfg['key'])is not str or not re.fullmatch('[0-9a-f]{64}',cfg['key'])
            or cfg['output_dir']!=str(runtime.ROOT/'data'/cfg['session'])
            or type(cfg['assets'])is not dict or set(cfg['assets'])!=runtime.ASSETS):
        raise ValueError('NATIVE_INPUT_SETTINGS_INVALID')
    profile=profiles.select(cfg['image'],runtime='cpython-3.12.3')
    if (type(manifest)is not dict or set(manifest)!={'schema','session','image','settings_sha256','assets','profile',
            'source_manifest_sha256',*FLAGS}
            or manifest['schema']!='guard-native-input-prepared-candidate/1'
            or any(manifest[k]is not v for k,v in FLAGS.items())
            or not _sha(manifest['source_manifest_sha256'])
            or manifest['session']!=cfg['session']or manifest['image']!=cfg['image']
            or manifest['settings_sha256']!=digest or manifest['assets']!=cfg['assets']or manifest['profile']!=profile['id']):
        raise ValueError('NATIVE_INPUT_PREPARED_INVALID')
    source_root=Path(runtime.__file__).parents[2]
    for name,pin in cfg['assets'].items():
        raw=runtime.shared._asset(prepared/name,pin)
        expected=BOOTSTRAP if name=='sitecustomize.py'else(source_root/name).read_bytes()
        if raw!=expected:raise ValueError('NATIVE_INPUT_OBSERVER_ASSETS_CHANGED')
    return manifest,cfg,profile


def _row(row,profile,context):
    if (type(row)is not dict or set(row)!=ROW_FIELDS or row['schema']!='guard-native-input-hook-candidate/1'
            or row['source_authenticated']is not False or row['producer_registered']is not False or row['scope_complete']is not False
            or row['context']!=context or row['method']!='POST' or row['path']not in{'/v1/completions','/v1/chat/completions'}
            or row['event']not in{'CALL','RETURN','RAISE'}
            or any(not _sha(row[k])for k in('call_hmac_sha256','root_call_hmac_sha256','object_hmac_sha256','request_hmac_sha256'))
            or row['parent_root_call_hmac_sha256']is not None and not _sha(row['parent_root_call_hmac_sha256'])
            or any(type(row[k])is not int or not 0<=row[k]<2**63 for k in('operation_started_ns','observation_ns'))
            or row['operation_started_ns']>row['observation_ns']
            or row['original_invoked']is not(row['event']!='CALL')):
        raise ValueError('NATIVE_INPUT_ROW_INVALID')
    pin=profile['sources'][row['source_module']];hook=pin['hooks'][row['point']]
    if row['source_sha256']!=pin['sha256']or row['body_code_sha256']!=pin['body_code_sha256'][row['point']]:
        raise ValueError('NATIVE_INPUT_ROW_SOURCE_INVALID')
    if hook['kind']=='http-handler'and row['path']not in hook['paths']:raise ValueError('NATIVE_INPUT_ROW_ROUTE_INVALID')
    kind=row['exception_kind'];site=row['exception_site']
    native_error=hook['kind']=='input-validator'and row['event']=='RAISE'
    if (native_error and kind not in{'native-validation-error','other-error'}
            or not native_error and kind is not None or kind!='native-validation-error'and site is not None):
        raise ValueError('NATIVE_INPUT_ROW_EXCEPTION_INVALID')
    if site is not None and(type(site)is not dict or set(site)!={'code_offset','source_line','direct_raise'}
            or type(site['direct_raise'])is not bool
            or any(type(site[k])is not int or not 0<=site[k]<2**31 for k in('code_offset','source_line'))):
        raise ValueError('NATIVE_INPUT_ROW_EXCEPTION_INVALID')
    return row


def verify_capture(prepared,capture, *, requests=None,expected_uid=None):
    """Check original bytes and causal pairs. No engine/GPU qualification occurs."""
    paths=list(Path(capture).glob('pid-*.json'))
    if not 1<=len(paths)<=4096:raise ValueError('NATIVE_INPUT_CAPTURE_BOUND')
    return verify_records(prepared,[(path.name,_read(path,131072))for path in paths],requests=requests,expected_uid=expected_uid)


def verify_records(prepared,files, *, requests=None,expected_uid=None):
    """Validate bounded original records before any local persistence."""
    expected_uid=os.geteuid()if expected_uid is None else expected_uid
    manifest,cfg,profile=load_prepared(prepared,expected_uid=expected_uid)
    key=bytes.fromhex(cfg['key']);snapshots=[];seen=set();gaps=[]
    if type(files)is not list or not 1<=len(files)<=4096:raise ValueError('NATIVE_INPUT_CAPTURE_BOUND')
    for name,raw in files:
        if type(raw)is not bytes or not 0<len(raw)<=131072:raise ValueError('NATIVE_INPUT_CAPTURE_BOUND')
        record=json.loads(raw,object_pairs_hook=runtime.shared._pairs)
        if type(record)is not dict or set(record)!={'snapshot','hmac_sha256'}or not _sha(record['hmac_sha256']):
            raise ValueError('NATIVE_INPUT_SNAPSHOT_INVALID')
        value=record['snapshot']
        if (type(value)is not dict or set(value)!={'schema','session','image','settings_sha256','observer_assets','context','ordinal','payload'}
                or value['schema']!='guard-native-input-snapshot-candidate/1'
                or value['session']!=manifest['session']or value['image']!=manifest['image']
                or value['settings_sha256']!=manifest['settings_sha256']or value['observer_assets']!=manifest['assets']
                or not hmac.compare_digest(hmac.new(key,runtime.canonical(value),sha256).hexdigest(),record['hmac_sha256'])
                or not _context(value['context'])or value['context']['uid']!=expected_uid
                or type(value['ordinal'])is not int or not 1<=value['ordinal']<=4096):
            raise ValueError('NATIVE_INPUT_SNAPSHOT_INVALID')
        context=value['context'];identity=(context['pid'],context['start_ticks'],value['ordinal'])
        if identity in seen or name!='pid-{}-{}-{}.json'.format(*identity):raise ValueError('NATIVE_INPUT_SNAPSHOT_REPLAY')
        seen.add(identity);snapshots.append(value)
    snapshots.sort(key=lambda r:(r['context']['pid'],r['context']['start_ticks'],r['ordinal']))
    rows=[];calls={};roots={};last={};closed=set()
    for value in snapshots:
        context=value['context'];process=runtime.canonical(context);ordinal=value['ordinal'];row=value['payload']
        if ordinal!=last.get(process,0)+1:gaps.append('NATIVE_INPUT_SNAPSHOT_SEQUENCE_GAP')
        last[process]=ordinal
        if type(row)is dict and row.get('schema')=='guard-native-input-hook-gap/1':
            if (set(row)!={'schema','diagnostic','source_authenticated','producer_registered','scope_complete'}
                    or row['diagnostic']not in{'NATIVE_INPUT_PROOF_UNAVAILABLE','NATIVE_INPUT_SOURCE_UNAVAILABLE'}
                    or any(row[k]is not False for k in('source_authenticated','producer_registered','scope_complete'))):
                raise ValueError('NATIVE_INPUT_GAP_INVALID')
            gaps.append(row['diagnostic']);continue
        try:row=_row(row,profile,context)
        except (KeyError,TypeError,ValueError):raise ValueError('NATIVE_INPUT_ROW_INVALID')from None
        call=(process,row['call_hmac_sha256']);root=(process,row['root_call_hmac_sha256'])
        if row['event']=='CALL':
            if call in calls or call in closed:raise ValueError('NATIVE_INPUT_CALL_REPLAY')
            hook=profile['sources'][row['source_module']]['hooks'][row['point']]
            if hook['kind']=='http-handler':
                if call!=root:raise ValueError('NATIVE_INPUT_ROOT_INVALID')
                parent=row['parent_root_call_hmac_sha256']
                if parent is not None:
                    outer=roots.get((process,parent))
                    if outer is None or any(outer[k]!=row[k]for k in('request_hmac_sha256','method','path')):
                        gaps.append('NATIVE_INPUT_PARENT_ROOT_UNAVAILABLE')
                roots[call]=row
            elif root not in roots:gaps.append('NATIVE_INPUT_ROOT_UNAVAILABLE')
            else:
                outer=roots[root]
                if any(outer[k]!=row[k]for k in('request_hmac_sha256','method','path')):
                    raise ValueError('NATIVE_INPUT_REQUEST_MISMATCH')
            calls[call]=row
        else:
            original=calls.pop(call,None)
            stable=ROW_FIELDS-{'event','observation_ns','original_invoked','exception_kind','exception_site'}
            if (original is None or any(original[k]!=row[k]for k in stable)
                    or row['observation_ns']<original['observation_ns']):
                raise ValueError('NATIVE_INPUT_TERMINAL_MISMATCH')
            roots.pop(call,None);closed.add(call)
        rows.append(row)
    if calls:gaps.append('NATIVE_INPUT_CALL_NOT_CLOSED')
    selected={}
    if requests is not None:
        if (type(requests)is not dict or not requests or len(requests)>256
                or any(type(k)is not str or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',k)for k in requests)
                or any(type(v)is not str or not 1<=len(v)<=256 or any(ord(c)<33 or ord(c)>126 for c in v)for v in requests.values())
                or len(set(requests.values()))!=len(requests)):raise ValueError('NATIVE_INPUT_EXPECTED_REQUESTS_INVALID')
        for case,identifier in requests.items():
            digest=hmac.new(key,runtime.canonical(['request',identifier]),sha256).hexdigest()
            selected[case]=[r for r in rows if r['request_hmac_sha256']==digest]
            if not selected[case]:gaps.append('NATIVE_INPUT_EXPECTED_REQUEST_UNAVAILABLE:'+case)
    return {'schema':'guard-native-input-capture-review/1','session':manifest['session'],
        'profile':profile['id'],'image':manifest['image'],'settings_sha256':manifest['settings_sha256'],
        'snapshots':len(snapshots),'rows':rows,'requests':selected,'gaps':sorted(set(gaps)),
        'source_authenticated':False,'producer_registered':False,'runtime_qualified':False,
        'scope_complete':False,'gpu_executed':False,
        'limitations':['Source call/return/rejection sites only; no token contents, parsing coverage or native consumer proof.',
            'Fresh target/Pod/container generation binding, actual GPU qualification and registration remain required.']}
