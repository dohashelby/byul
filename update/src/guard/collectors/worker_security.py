"""Prepare and validate a separate owned worker probe; never start an engine."""
from pathlib import Path
import hashlib
import json
import os
import re
import uuid

from guard.adapters import vllm_worker_security_sensor as sensor
from guard.models import canonical_json,sha256_hex
from guard.update_validation import reserve_output

BOOTSTRAP = b'''"""Preserve image startup before installing a private owned-worker observer."""
def _byul_worker_start():
    import importlib.machinery
    import importlib.util
    from pathlib import Path
    import sys
    current=sys.modules[__name__]
    current.__dict__.pop('_byul_worker_start',None)
    own=Path(__file__).resolve().parent
    search=[]
    for entry in sys.path:
        if type(entry) is str:
            try:
                if Path(entry or '.').resolve()==own:continue
            except OSError:pass
        search.append(entry)
    sys.path[:]=search
    original=importlib.machinery.PathFinder.find_spec('sitecustomize',search)
    if original is not None and original.loader is not None:
        current.__file__,current.__loader__,current.__spec__=original.origin,original.loader,original
        current.__package__,current.__cached__=original.parent,original.cached
        original.loader.exec_module(current)
    try:
        name='_byul_worker_security'
        if name in sys.modules:raise ValueError('WORKER_SECURITY_NAMESPACE_IN_USE')
        spec=importlib.util.spec_from_file_location(name,own/'guard_worker_security.py')
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        spec.loader.exec_module(module)
        if module.install() is not True:sys.stderr.write('WORKER_SECURITY_OBSERVER_UNAVAILABLE\\n')
    except Exception:sys.stderr.write('WORKER_SECURITY_OBSERVER_UNAVAILABLE\\n')
_byul_worker_start()
'''


def prepare_assets(output,repository,*,credential_paths,sentinel_paths,expected_ranks,permission_paths=None):
    cfg=sensor.settings({'schema':'guard-worker-security-settings/1','session':uuid.uuid4().hex,
        'expected_ranks':expected_ranks,'credential_paths':credential_paths,'sentinel_paths':sentinel_paths,
        'permission_paths':sorted(sensor.PERMISSION_PATHS) if permission_paths is None else permission_paths})
    output=Path(output).absolute()
    if any(parent.is_symlink() or parent.is_junction() or (parent/'.git').exists() for parent in output.parents):
        raise ValueError('WORKER_SECURITY_OUTPUT_IN_REPOSITORY')
    output=reserve_output(output,protected=[Path(repository)])
    raw=canonical_json(cfg);digest=sha256_hex(raw);source=sensor.render_source(digest)
    values={'settings.json':raw,'guard_worker_security.py':source,
        'sitecustomize.py':BOOTSTRAP}
    for name,value in values.items():
        fd=os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
        with os.fdopen(fd,'wb') as stream:stream.write(value)
    manifest={'schema':'guard-worker-security-assets/1','profile':sensor.PROFILE,'session':cfg['session'],
        'expected_ranks':expected_ranks,'assets':{name:{'sha256':sha256_hex(value),'bytes':len(value)} for name,value in values.items()},
        'producer_authenticated':False,'gpu_qualified':False}
    (output/'manifest.json').write_bytes(canonical_json(manifest))
    return manifest


def load_prepared(path):
    path=Path(path)
    manifest=json.loads(sensor.source_bytes(path,16384))
    if (type(manifest) is not dict or set(manifest)!={'schema','profile','session','expected_ranks','assets','producer_authenticated','gpu_qualified'}
            or manifest['schema']!='guard-worker-security-assets/1' or manifest['profile']!=sensor.PROFILE
            or manifest['producer_authenticated'] is not False or manifest['gpu_qualified'] is not False
            or set(manifest['assets'])!={'settings.json','guard_worker_security.py','sitecustomize.py'}):
        raise ValueError('WORKER_SECURITY_ASSETS_INVALID')
    raw={}
    for name,row in manifest['assets'].items():
        raw[name]=sensor.source_bytes(path.parent/name)
        if row!={'sha256':sha256_hex(raw[name]),'bytes':len(raw[name])}:raise ValueError('WORKER_SECURITY_ASSETS_CHANGED')
    cfg=sensor.settings(json.loads(raw['settings.json']))
    if (cfg['session']!=manifest['session'] or cfg['expected_ranks']!=manifest['expected_ranks']
            or raw['guard_worker_security.py']!=sensor.render_source(sha256_hex(raw['settings.json']))
            or raw['sitecustomize.py']!=BOOTSTRAP):
        raise ValueError('WORKER_SECURITY_ASSETS_CHANGED')
    return manifest,cfg


def producer():
    # Registry data is excluded from this source identity. A changed collector,
    # observer or wire reader must undergo a new GPU qualification.
    digest=hashlib.sha256()
    root=Path(__file__).parents[1]
    paths=(Path(sensor.__file__),Path(__file__),root/'runtime/kubernetes_worker_security.py',
        root/'runtime/assets/worker_security_read_v1.py.txt',
        *(root/'executors'/name for name in ('worker_security_projection.py','kubernetes_container_config.py',
                                           'container_config.py','credential_exposure.py')),
        root/'runtime/kubernetes_http.py')
    for path in paths:
        digest.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    return {'name':'guard-vllm-owned-worker-security','version':'1','source_sha256':digest.hexdigest()}


BODY_CODES={
    'cpython-3.12.3':'6287f56ed63d404529e3e055330c3eb78a99d22b8c98022526981a9a2652315a',
    'cpython-3.12.8':'eb036567c51d25e523f17cfb49bc7ce597d2986ec6a5e217e90edd366a885246',
}  # Fresh compilation of the pinned function body; no engine import or GPU claim.
QUALIFICATIONS=Path(sensor.__file__).parent/'assets/worker_security_qualifications.json'
FIELDS=frozenset({'schema','profile','session','rank','source_module','source_sha256','body_code_sha256',
    'sensor_sha256','settings_sha256','observer_runtime','context','started_ns','finished_ns','environment_names',
    'credential_results','sentinel_results','write_results','mounts','device_fds','source_verified','producer_authenticated','scope'})
CONTEXT=frozenset({'pid','start_ticks','uid_effective','gid_effective','cap_eff','pid_namespace_inode',
    'mount_namespace_inode','time_namespace_inode','time_offsets_sha256','boot_sha256','mountinfo_sha256'})


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_record(row,prepared,cfg):
    try:
        if (type(row) is not dict or set(row)!=FIELDS or row['schema']!=sensor.SCHEMA or row['profile']!=sensor.PROFILE
                or row['session']!=prepared['session'] or type(row['rank']) is not int or row['rank'] not in cfg['expected_ranks']
                or row['source_module']!=sensor.MODULE or row['source_sha256']!=sensor.SOURCE_SHA256
                or row['source_verified'] is not True or row['producer_authenticated'] is not False
                or row['scope']!='selected-owned-tp-worker-after-load-model'
                or type(row['observer_runtime']) is not str or row['observer_runtime'] not in BODY_CODES
                or row['body_code_sha256']!=BODY_CODES[row['observer_runtime']]
                or row['sensor_sha256']!=prepared['assets']['guard_worker_security.py']['sha256']
                or row['settings_sha256']!=prepared['assets']['settings.json']['sha256']):raise ValueError()
        context=row['context']
        if (type(context) is not dict or set(context)!=CONTEXT
                or any(type(context[k]) is not int or not 0<=context[k]<2**63 for k in CONTEXT-{'cap_eff','boot_sha256','time_offsets_sha256','mountinfo_sha256'})
                or not 1<=context['pid']<=2147483647 or context['start_ticks']<=0
                or type(context['cap_eff']) is not str or re.fullmatch('[0-9a-f]{1,16}',context['cap_eff']) is None
                or not all(fingerprint(context[k]) for k in ('boot_sha256','time_offsets_sha256','mountinfo_sha256'))):raise ValueError()
        if any(type(row[k]) is not int or not 0<=row[k]<2**63 for k in ('started_ns','finished_ns')) or row['started_ns']>row['finished_ns']:raise ValueError()
        names=row['environment_names']
        if (type(names) is not list or len(names)>1024 or len(set(map(str,names)))!=len(names)
                or any(type(n) is not str or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',n) is None for n in names)):raise ValueError()
        for key,expected,allowed in (('credential_results',cfg['credential_paths'],{'candidate_readable','absent','denied','unknown'}),
                ('sentinel_results',cfg['sentinel_paths'],{'succeeded','denied','absent','error'}),
                ('write_results',cfg['permission_paths'],{'succeeded','denied','absent','error'})):
            if type(row[key]) is not dict or set(row[key])!=set(expected) or any(type(v) is not str or v not in allowed for v in row[key].values()):raise ValueError()
        mounts=row['mounts']
        if type(mounts) is not list or not 1<=len(mounts)<=256:raise ValueError()
        for mount in mounts:
            if (type(mount) is not dict or set(mount)!={'path','read_only','noexec','filesystem'}
                    or type(mount['path']) is not str or not mount['path'].startswith('/') or len(mount['path'])>512
                    or any(c in mount['path'] for c in ('\0','\n','\r'))
                    or type(mount['read_only']) is not bool or type(mount['noexec']) is not bool
                    or type(mount['filesystem']) is not str or re.fullmatch('[a-zA-Z0-9_.-]{1,64}',mount['filesystem']) is None):raise ValueError()
        if (type(row['device_fds']) is not list or len(row['device_fds'])>64 or len(set(map(str,row['device_fds'])))!=len(row['device_fds'])
                or any(type(p) is not str or re.fullmatch('/dev/(nvidia[0-9]+|nvidiactl|nvidia-uvm(-tools)?|nvidia-caps/nvidia-cap[0-9]+|dri/renderD[0-9]+)',p) is None for p in row['device_fds'])):raise ValueError()
    except (ValueError,KeyError,TypeError):raise ValueError('WORKER_SECURITY_RECORD_INVALID') from None
    return row


def validate_packet(packet,prepared,cfg):
    if (type(packet) is not dict or set(packet)!={'schema','rows'} or packet['schema']!='guard-worker-security-live-read/1'
            or type(packet['rows']) is not list or len(packet['rows'])!=len(cfg['expected_ranks'])):
        raise ValueError('WORKER_SECURITY_PACKET_INVALID')
    records=[]
    for expected,row in zip(cfg['expected_ranks'],packet['rows'],strict=True):
        if (type(row) is not dict or set(row)!={'rank','available','record','record_sha256','diagnostic'}
                or type(row['rank']) is not int or row['rank']!=expected or type(row['available']) is not bool):raise ValueError('WORKER_SECURITY_PACKET_INVALID')
        if row['available']:
            record=validate_record(row['record'],prepared,cfg)
            raw=json.dumps(record,sort_keys=True,separators=(',',':')).encode()
            if sha256_hex(raw)!=row['record_sha256'] or row['diagnostic'] is not None:raise ValueError('WORKER_SECURITY_PACKET_INVALID')
            records.append(record)
        elif row['record'] is not None or row['record_sha256'] is not None or row['diagnostic']!='WORKER_RECORD_UNAVAILABLE':
            raise ValueError('WORKER_SECURITY_PACKET_INVALID')
    identities={(r['context']['pid'],r['context']['start_ticks'],r['context']['pid_namespace_inode']) for r in records}
    if len(identities)!=len(records):raise ValueError('WORKER_SECURITY_DUPLICATE_PROCESS')
    return records,len(records)==len(cfg['expected_ranks'])


def qualification(engine,version,image,records):
    data=json.loads(QUALIFICATIONS.read_bytes())
    if set(data)!={'schema','qualifications'} or data['schema']!='guard-worker-security-qualifications/1':raise ValueError('WORKER_SECURITY_QUALIFICATIONS_INVALID')
    if not records:return False
    expected=producer()
    matches=[r for r in data['qualifications'] if r.get('engine')==engine and r.get('version')==version
        and r.get('image')==image and r.get('producer')==expected]
    if len(matches)!=1:return False
    row=matches[0]
    return (row.get('actual_gpu_qualified') is True and row.get('normal_control_verified') is True
        and row.get('negative_and_missing_cases_verified') is True and fingerprint(row.get('evidence_sha256'))
        and all(r['observer_runtime']==row.get('observer_runtime') for r in records))
