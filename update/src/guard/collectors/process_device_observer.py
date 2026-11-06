"""PLT-01·04 공통 입력 관측기(개발 담당, 2단계 P1 process-device-provider).

컨테이너 안에서는 NVML의 host PID와 컨테이너 PID를 잇는 고리(NSpid)가 보이지 않는다. 그래서 두 관측을 합친다.

1. 노드 관측(`NODE_OBSERVER`): 노드 PID 이름공간의 `/proc`를 읽는다. 소유 Pod UID와 container ID가 cgroup 경로에
   함께 있는 프로세스만 고른다. 프로세스마다 NSpid 사슬·시작 ticks·실효 UID·cgroup 해시·열린 GPU device fd·
   후보 마운트 아래 열린 파일 fd·후보 환경 변수 **이름**의 존재를 읽고, 읽기 앞뒤의 신원이 같은지 확인한다.
   노드의 `nvidia-smi`로 NVML compute 프로세스(host PID·GPU UUID)를, 드라이버 정보로 device minor↔GPU UUID를 읽는다.
   GPU lab에서는 노드의 root(`sudo -n`)로 돈다. 제품 경로에서는 노드 관측 주체(DaemonSet 등)가 필요하며 이 모듈 범위 밖이다.
2. 컨테이너 안 열기 시험(`DEVICE_OPEN_TEST`): 엔진 컨테이너에서 보이는 GPU device node를 O_RDWR로 열었다 바로 닫는다.
   ioctl을 보내지 않는다. 결과는 그 컨테이너 cgroup의 device 집행(허용/거부)이다.

비밀 원문은 읽어 남기지 않는다. 환경 변수는 후보 이름의 존재만 남기고 값은 버린다. 파일은 열지 않고 fd 링크와 메타데이터만 본다.
"""
from __future__ import annotations

import hashlib
import re

NODE_SCHEMA = 'guard-process-device-node/1'
OPEN_SCHEMA = 'guard-process-device-open/1'
DEVICE_PATTERN = r'/dev/(?:nvidia(?:[0-9]+|ctl|-uvm(?:-tools)?|-modeset|-caps/nvidia-cap[0-9]+)|dri/renderD[0-9]+)'
GPU_NODE = re.compile(r'/dev/nvidia[0-9]+')
MAX_PROCESSES = 64
MAX_CANDIDATES = 32
_ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')

# 노드 root로 `python3 -I -S -`에 표준 입력으로 넣는다. 입력 JSON은 argv[1].
NODE_OBSERVER = r'''
import json,os,re,shutil,stat,subprocess,sys,hashlib
spec=json.loads(sys.argv[1]);root=spec['proc_root'];dev=spec['dev_root'];drv=spec['driver_root']
for p in (root,dev,drv):
 if type(p) is not str or not os.path.isabs(p) or '..' in p.split('/') or len(p)>256:raise SystemExit(64)
uid=spec['pod_uid'];cid=spec['container_id']
if re.fullmatch('[0-9a-f-]{36}',uid) is None or re.fullmatch('[0-9a-f]{64}',cid) is None:raise SystemExit(65)
names=spec['env_names'];mounts=spec['mount_paths']
if len(names)>32 or len(mounts)>32 or any(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',n) is None for n in names) or any(type(m) is not str or not m.startswith('/') or len(m)>256 for m in mounts):raise SystemExit(66)
PAT=re.compile(spec['device_pattern']);codes=set()
def ticks(pid):
 raw=open(root+'/'+str(pid)+'/stat','rb').read(8192).decode('utf-8','replace');return int(raw[raw.rfind(')')+2:].split()[19])
def ident(pid):
 vals={}
 for line in open(root+'/'+str(pid)+'/status','rb').read(16384).decode('utf-8','replace').splitlines():
  if line.startswith(('NSpid:','Uid:')):k,v=line.split(':',1);vals[k]=[int(x) for x in v.split()]
 return {'start_ticks':ticks(pid),'nspid':vals.get('NSpid',[])[:8],'uid_effective':(vals.get('Uid') or [None,None])[1]}
def under(path,base):return path==base or path.startswith(base.rstrip('/')+'/')
selected=[];scanned=0
for entry in os.listdir(root):
 if not entry.isdecimal():continue
 scanned+=1
 try:raw=open(root+'/'+entry+'/cgroup','rb').read(8192)
 except OSError:continue
 text=raw.decode('utf-8','replace')
 if cid in text and (uid in text or uid.replace('-','_') in text):selected.append((int(entry),raw))
selected.sort()
if len(selected)>64:codes.add('PROCESS_SET_BOUND');selected=selected[:64]
rows=[]
for pid,raw in selected:
 row={'pid':pid,'stable':False,'nspid':[],'start_ticks':None,'uid_effective':None,'cgroup_sha256':hashlib.sha256(raw).hexdigest(),
      'device_fds':[],'env_present':[],'mounts_present':[],'mount_fds':[],'codes':[]}
 try:
  before=ident(pid);base=root+'/'+str(pid)
  fds=os.listdir(base+'/fd')
  if len(fds)>4096:row['codes'].append('FD_SET_BOUND');fds=sorted(fds,key=lambda x:int(x) if x.isdecimal() else 1<<62)[:4096]
  for name in fds:
   if not name.isdecimal():continue
   try:
    link=os.readlink(base+'/fd/'+name);st=os.stat(base+'/fd/'+name)
    info=open(base+'/fdinfo/'+name,'rb').read(4096).decode('ascii','replace')
    flags=next(int(x.split()[1],8) for x in info.splitlines() if x.startswith('flags:'))
   except (OSError,StopIteration,ValueError):continue
   mode=flags&os.O_ACCMODE;meta=bool(flags&getattr(os,'O_PATH',0))
   if stat.S_ISCHR(st.st_mode) and PAT.fullmatch(link):
    item={'path':link,'read_capable':not meta and mode in (os.O_RDONLY,os.O_RDWR),'write_capable':not meta and mode in (os.O_WRONLY,os.O_RDWR)}
    if item not in row['device_fds']:row['device_fds'].append(item)
   elif stat.S_ISREG(st.st_mode):
    for i,m in enumerate(mounts):
     if under(link,m) and i not in row['mount_fds']:row['mount_fds'].append(i)
  try:
   env=open(base+'/environ','rb').read((1<<20)+1)
   if len(env)>1<<20:row['codes'].append('ENVIRON_TRUNCATED');env=env[:1<<20].rsplit(b'\0',1)[0]
   present={chunk.split(b'=',1)[0].decode('ascii','replace') for chunk in env.split(b'\0') if b'=' in chunk}
   del env
   row['env_present']=sorted(n for n in names if n in present)
  except OSError:row['codes'].append('ENVIRON_UNAVAILABLE')
  try:
   points=set()
   raw_mounts=open(base+'/mountinfo','rb').read((1<<20)+1)
   if len(raw_mounts)>1<<20:row['codes'].append('MOUNTINFO_TRUNCATED');raw_mounts=raw_mounts[:1<<20].rsplit(b'\n',1)[0]
   for line in raw_mounts.decode('utf-8','replace').splitlines():
    parts=line.split(' ')
    if len(parts)>4:points.add(parts[4].replace('\\040',' '))
   row['mounts_present']=[i for i,m in enumerate(mounts) if m in points]
  except OSError:row['codes'].append('MOUNTINFO_UNAVAILABLE')
  after=ident(pid)
  cgroup_after=open(base+'/cgroup','rb').read(8192)
  row.update(before);row['stable']=before==after and raw==cgroup_after and before['uid_effective'] is not None
  if not row['stable']:
   row['codes'].append('PID_IDENTITY_CHANGED')
   for k in ('device_fds','env_present','mounts_present','mount_fds'):row[k]=[]
 except Exception:
  row['codes'].append('PID_READ_UNAVAILABLE')
  for k in ('device_fds','env_present','mounts_present','mount_fds'):row[k]=[]
 row['mount_fds'].sort();rows.append(row)
uuids={}
try:
 for bus in sorted(os.listdir(drv+'/gpus'))[:64]:
  info=open(drv+'/gpus/'+bus+'/information','rb').read(8192).decode('ascii','replace')
  minor=re.search(r'Device Minor:\s*([0-9]+)',info);gu=re.search(r'GPU UUID:\s*(GPU-[0-9a-fA-F-]{8,80})',info)
  if minor and gu:uuids[int(minor.group(1))]=gu.group(1)
except OSError:codes.add('DRIVER_GPU_INFO_UNAVAILABLE')
devices=[]
def walk(d,prefix):
 try:items=sorted(os.listdir(d))
 except OSError:return
 for n in items:
  p=d+'/'+n;logical=prefix+'/'+n
  try:st=os.lstat(p)
  except OSError:continue
  if stat.S_ISDIR(st.st_mode) and logical in ('/dev/nvidia-caps','/dev/dri'):walk(p,logical)
  elif stat.S_ISCHR(st.st_mode) and PAT.fullmatch(logical):
   minor=os.minor(st.st_rdev);gpu=re.fullmatch('/dev/nvidia[0-9]+',logical) is not None
   devices.append({'path':logical,'minor':minor,'gpu_uuid':uuids.get(minor) if gpu else None})
walk(dev,'/dev')
if len(devices)>64:codes.add('DEVICE_SET_BOUND');devices=devices[:64]
nvml={'available':False,'rows':[]}
tool=shutil.which('nvidia-smi') if spec['nvidia_smi'] else None
if tool:
 try:
  r=subprocess.run([tool,'--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=False)
  if r.returncode==0 and len(r.stdout)<=65536:
   parsed=[]
   for line in r.stdout.decode('ascii').splitlines():
    if not line.strip() or 'No running' in line:continue
    f=[x.strip() for x in line.split(',')]
    if len(f)!=2 or re.fullmatch('[0-9]{1,10}',f[0]) is None or re.fullmatch('GPU-[0-9a-fA-F-]{8,80}',f[1]) is None:raise ValueError()
    parsed.append({'pid':int(f[0]),'gpu_uuid':f[1]})
   if len(parsed)<=64:nvml={'available':True,'rows':parsed}
 except (OSError,ValueError,UnicodeError,subprocess.TimeoutExpired):pass
print(json.dumps({'schema':'guard-process-device-node/1','scanned':scanned,'processes':rows,'devices':devices,'nvml':nvml,
 'codes':sorted(codes)},sort_keys=True,separators=(',',':')))
'''.strip()

# 엔진 컨테이너 안 `python3 -I -S -c`. 보이는 GPU device node를 O_RDWR로 열고 바로 닫는다(ioctl 없음).
DEVICE_OPEN_TEST = r'''
import errno,json,os,re,stat,sys
PAT=re.compile(sys.argv[1]);rows=[]
def walk(d):
 try:items=sorted(os.listdir(d))
 except OSError:return
 for n in items:
  p=d+'/'+n
  try:st=os.lstat(p)
  except OSError:continue
  if stat.S_ISDIR(st.st_mode) and p in ('/dev/nvidia-caps','/dev/dri'):walk(p)
  elif stat.S_ISCHR(st.st_mode) and PAT.fullmatch(p):
   try:
    fd=os.open(p,os.O_RDWR|os.O_NONBLOCK|os.O_CLOEXEC|getattr(os,'O_NOCTTY',0));os.close(fd);rows.append({'path':p,'result':'allowed','errno':None})
   except OSError as e:
    rows.append({'path':p,'result':'denied' if e.errno in (errno.EPERM,errno.EACCES) else 'unknown','errno':errno.errorcode.get(e.errno,'EUNKNOWN')})
walk('/dev')
print(json.dumps({'schema':'guard-process-device-open/1','uid_effective':os.geteuid(),'devices':rows[:64]},sort_keys=True,separators=(',',':')))
'''.strip()


class ObserverOutputError(ValueError):
    pass


def node_spec(*, pod_uid, container_id, candidates, proc_root='/proc', dev_root='/dev', driver_root='/proc/driver/nvidia',
              nvidia_smi=True):
    """노드 관측 입력. container ID는 `containerd://` 접두를 뗀 64 hex여야 한다."""
    cid = container_id.split('://', 1)[-1]
    return {'proc_root': proc_root, 'dev_root': dev_root, 'driver_root': driver_root, 'pod_uid': pod_uid, 'container_id': cid,
            'env_names': sorted({c['env_name'] for c in candidates if c.get('env_name')}),
            'mount_paths': [c['mount_path'] for c in candidates if c.get('mount_path')],
            'device_pattern': DEVICE_PATTERN, 'nvidia_smi': nvidia_smi}


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def provider_candidates(pod, container_name):
    """Pod 명세에서 자격 공급 후보를 고른다. 이름·참조만 쓰고 Secret 본문은 읽지 않는다.

    종류: env_secret(secretKeyRef), env_from_secret(envFrom.secretRef, 이름 미상), volume_secret, projected_sa_token,
    projected_secret, csi. 비밀 아닌 식별자는 참조 문자열의 SHA-256이다.
    """
    spec = pod['spec']
    container = next(c for c in spec.get('containers') or [] if c.get('name') == container_name)
    volumes = {v.get('name'): v for v in spec.get('volumes') or []}
    out = []
    for env in container.get('env') or []:
        ref = (env.get('valueFrom') or {}).get('secretKeyRef')
        if ref and type(env.get('name')) is str and _ENV_NAME.fullmatch(env['name']):
            out.append({'kind': 'env_secret', 'env_name': env['name'], 'mount_path': None,
                        'nonsecret_id_sha256': _sha(f"secret:{ref.get('name')}:{ref.get('key')}")})
    for source in container.get('envFrom') or []:
        ref = source.get('secretRef')
        if ref:
            out.append({'kind': 'env_from_secret', 'env_name': None, 'mount_path': None,
                        'nonsecret_id_sha256': _sha(f"secret:{ref.get('name')}")})
    for mount in container.get('volumeMounts') or []:
        volume = volumes.get(mount.get('name')) or {}
        path = mount.get('mountPath')
        if type(path) is not str or not path.startswith('/'):
            continue
        if 'secret' in volume:
            kinds = [('volume_secret', f"secret:{volume['secret'].get('secretName')}")]
        elif 'projected' in volume:
            kinds = []
            for item in volume['projected'].get('sources') or []:
                if 'serviceAccountToken' in item:
                    kinds.append(('projected_sa_token', f"serviceaccount:{spec.get('serviceAccountName') or 'default'}"))
                if 'secret' in item:
                    kinds.append(('projected_secret', f"secret:{item['secret'].get('name')}"))
        elif 'csi' in volume:
            kinds = [('csi', f"csi:{volume['csi'].get('driver')}")]
        else:
            kinds = []
        for kind, ref in kinds:
            out.append({'kind': kind, 'env_name': None, 'mount_path': path, 'nonsecret_id_sha256': _sha(ref)})
    if len(out) > MAX_CANDIDATES:
        raise ObserverOutputError('PROVIDER_CANDIDATES_BOUND')
    return out


def _bool(value):
    return type(value) is bool


def _int(value, low=0):
    return type(value) is int and low <= value <= 2**63 - 1


def decode_node(raw, spec):
    """노드 관측 출력의 닫힌 형식 확인. 어긋나면 ObserverOutputError."""
    pattern = re.compile(DEVICE_PATTERN)
    keys = {'schema', 'scanned', 'processes', 'devices', 'nvml', 'codes'}
    if type(raw) is not dict or set(raw) != keys or raw['schema'] != NODE_SCHEMA or not _int(raw['scanned']):
        raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    row_keys = {'pid', 'stable', 'nspid', 'start_ticks', 'uid_effective', 'cgroup_sha256', 'device_fds', 'env_present',
                'mounts_present', 'mount_fds', 'codes'}
    names, mounts = set(spec['env_names']), len(spec['mount_paths'])
    if type(raw['processes']) is not list or len(raw['processes']) > MAX_PROCESSES:
        raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    for row in raw['processes']:
        if (type(row) is not dict or set(row) != row_keys or not _int(row['pid'], 1) or not _bool(row['stable'])
                or type(row['nspid']) is not list or len(row['nspid']) > 8 or not all(_int(p, 1) for p in row['nspid'])
                or row['start_ticks'] is not None and not _int(row['start_ticks'])
                or row['uid_effective'] is not None and not _int(row['uid_effective'])
                or type(row['cgroup_sha256']) is not str or re.fullmatch('[0-9a-f]{64}', row['cgroup_sha256']) is None
                or type(row['device_fds']) is not list or type(row['env_present']) is not list
                or type(row['mounts_present']) is not list or type(row['mount_fds']) is not list
                or type(row['codes']) is not list or not all(type(c) is str for c in row['codes'])
                or not set(row['env_present']) <= names
                or not all(_int(i) and i < mounts for i in row['mounts_present'] + row['mount_fds'])):
            raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
        if row['stable'] and (not row['nspid'] or row['nspid'][0] != row['pid'] or row['start_ticks'] is None):
            raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
        for fd in row['device_fds']:
            if (type(fd) is not dict or set(fd) != {'path', 'read_capable', 'write_capable'} or type(fd['path']) is not str
                    or pattern.fullmatch(fd['path']) is None or not _bool(fd['read_capable']) or not _bool(fd['write_capable'])):
                raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    if type(raw['devices']) is not list or len(raw['devices']) > 64:
        raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    for item in raw['devices']:
        if (type(item) is not dict or set(item) != {'path', 'minor', 'gpu_uuid'} or type(item['path']) is not str
                or pattern.fullmatch(item['path']) is None or not _int(item['minor'])
                or item['gpu_uuid'] is not None and (type(item['gpu_uuid']) is not str
                                                    or re.fullmatch(r'GPU-[0-9a-fA-F-]{8,80}', item['gpu_uuid']) is None)):
            raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    nvml = raw['nvml']
    if (type(nvml) is not dict or set(nvml) != {'available', 'rows'} or not _bool(nvml['available'])
            or type(nvml['rows']) is not list or len(nvml['rows']) > 64 or not nvml['available'] and nvml['rows']):
        raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    for item in nvml['rows']:
        if (type(item) is not dict or set(item) != {'pid', 'gpu_uuid'} or not _int(item['pid'], 1)
                or type(item['gpu_uuid']) is not str or re.fullmatch(r'GPU-[0-9a-fA-F-]{8,80}', item['gpu_uuid']) is None):
            raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    if type(raw['codes']) is not list or not all(type(c) is str for c in raw['codes']):
        raise ObserverOutputError('PROCESS_DEVICE_NODE_OUTPUT_INVALID')
    return raw


def decode_open(raw):
    pattern = re.compile(DEVICE_PATTERN)
    if (type(raw) is not dict or set(raw) != {'schema', 'uid_effective', 'devices'} or raw['schema'] != OPEN_SCHEMA
            or not _int(raw['uid_effective']) or type(raw['devices']) is not list or len(raw['devices']) > 64):
        raise ObserverOutputError('PROCESS_DEVICE_OPEN_OUTPUT_INVALID')
    for item in raw['devices']:
        if (type(item) is not dict or set(item) != {'path', 'result', 'errno'} or type(item['path']) is not str
                or pattern.fullmatch(item['path']) is None or item['result'] not in ('allowed', 'denied', 'unknown')
                or (item['result'] == 'allowed') != (item['errno'] is None)
                or item['errno'] is not None and (type(item['errno']) is not str or re.fullmatch('E[A-Z0-9]{1,30}', item['errno']) is None)):
            raise ObserverOutputError('PROCESS_DEVICE_OPEN_OUTPUT_INVALID')
    return raw
