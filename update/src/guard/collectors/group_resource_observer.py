"""LIF-11 공통 입력 관측기: 엔진 소유 그룹의 자원 inventory (개발 담당, B 의존 반환 7).

엔진 컨테이너 안에서 `python3 -I -S -c OBSERVER`로 실패 전·후(다음 세대)에 한 번씩 돈다. 읽기만 한다.

- 세대: PID 1의 시작 ticks와 수집 시각(CLOCK_REALTIME ns). 컨테이너 ID·재시작 횟수는 전송이 Pod 상태에서 따로 준다.
- 프로세스: PID·시작 ticks(앞뒤 동일만 안정)·역할(vLLM 제목으로만)·연 unix socket inode·공유 메모리 참조·GPU 장치 fd.
- 공유 메모리: `/dev/shm` 항목의 (dev, ino)·크기와 이름 SHA-256. 프로세스가 fd 또는 매핑으로 잡고 있는 항목을 참조로 남긴다.
  `/dev/shm`이 Pod 볼륨이면 컨테이너 재시작 뒤에도 남으므로, 실패 뒤 반환되지 않은 공유 메모리를 다음 세대에서 볼 수 있다.

GPU 메모리(NVML host PID)는 컨테이너 안에서 이 그룹의 프로세스와 잇지 못하므로 여기서 관측하지 않는다(생산자가 공백으로 남긴다).
"""
from __future__ import annotations

import json

SCHEMA = 'guard-group-resources/1'
ROLES = ('api_server', 'engine_core', 'worker', 'utility', 'other')
MAX_PROCESSES = 64
MAX_ITEMS = 1024

OBSERVER = r'''
import hashlib,json,os,stat,time
codes=set();me=os.getpid()
def ticks(pid):
 raw=open('/proc/%d/stat'%pid,'rb').read(8192).decode('utf-8','replace');return int(raw[raw.rfind(')')+2:].split()[19])
def role(argv):
 title=argv[0] if argv else '';base=[os.path.basename(a) for a in argv[:3]]
 if title.startswith('VLLM::EngineCore'):return 'engine_core'
 if title.startswith('VLLM::Worker'):return 'worker'
 if title.startswith('VLLM::APIServer') or ('vllm' in base[:2] and 'serve' in argv[:3]):return 'api_server'
 if any('multiprocessing.' in a for a in argv[:4]):return 'utility'
 return 'other'
shm=[]
try:
 for name in sorted(os.listdir('/dev/shm'))[:1024]:
  try:st=os.lstat('/dev/shm/'+name)
  except OSError:continue
  if stat.S_ISREG(st.st_mode):shm.append({'name_sha256':hashlib.sha256(name.encode()).hexdigest(),'dev':st.st_dev,'ino':st.st_ino,'size':st.st_size})
except OSError:codes.add('SHM_DIRECTORY_UNAVAILABLE')
known={(s['dev'],s['ino']) for s in shm}
processes=[]
for pid in sorted(int(p) for p in os.listdir('/proc') if p.isdecimal()):
 if pid==me:continue
 if len(processes)>=64:codes.add('PROCESS_SET_BOUND');break
 try:
  before=ticks(pid)
  argv=[a.decode('utf-8','replace') for a in open('/proc/%d/cmdline'%pid,'rb').read(65536).split(b'\0') if a]
  sockets=set();refs=set();devices=set()
  for name in os.listdir('/proc/%d/fd'%pid):
   if not name.isdecimal():continue
   path='/proc/%d/fd/%s'%(pid,name)
   try:link=os.readlink(path)
   except OSError:continue
   if link.startswith('socket:['):sockets.add(int(link[8:-1]))
   elif link.startswith('/dev/shm/'):
    try:st=os.stat(path);refs.add((st.st_dev,st.st_ino))
    except OSError:pass
   elif link.startswith('/dev/nvidia') or link.startswith('/dev/dri/'):devices.add(hashlib.sha256(link.encode()).hexdigest())
  for line in open('/proc/%d/maps'%pid,'rb').read(1<<22).decode('utf-8','replace').splitlines():
   parts=line.split()
   if len(parts)>=6 and parts[5].startswith('/dev/shm/'):
    dev=parts[3].split(':');key=(os.makedev(int(dev[0],16),int(dev[1],16)),int(parts[4]))
    if key in known:refs.add(key)
  processes.append({'pid':pid,'start_ticks':before,'role':role(argv),'stable':ticks(pid)==before,'unix_inodes':sorted(sockets)[:1024],
                    'shm_refs':[list(k) for k in sorted(refs)][:1024],'device_fds':sorted(devices)[:64]})
 except (OSError,ValueError,IndexError):codes.add('PROCESS_READ_FAILED')
generation={'pid1_start_ticks':ticks(1)}
print(json.dumps({'schema':'guard-group-resources/1','generation':generation,'realtime_ns':time.time_ns(),'shm':shm,
                  'processes':processes,'codes':sorted(codes)}))
'''


def decode(value):
    """관측기 출력의 닫힌 형식 검사. 맞지 않으면 ValueError."""
    def need(ok):
        if not ok:
            raise ValueError('GROUP_RESOURCE_OBSERVATION_INVALID')
    nat = lambda v: type(v) is int and 0 <= v < 2**63
    sha = lambda v: type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
    need(type(value) is dict and set(value) == {'schema', 'generation', 'realtime_ns', 'shm', 'processes', 'codes'}
         and value['schema'] == SCHEMA and nat(value['realtime_ns']))
    need(type(value['generation']) is dict and set(value['generation']) == {'pid1_start_ticks'} and nat(value['generation']['pid1_start_ticks']))
    need(type(value['codes']) is list and all(type(c) is str and c.isupper() for c in value['codes']))
    need(type(value['shm']) is list and len(value['shm']) <= MAX_ITEMS)
    for s in value['shm']:
        need(type(s) is dict and set(s) == {'name_sha256', 'dev', 'ino', 'size'} and sha(s['name_sha256'])
             and nat(s['dev']) and nat(s['ino']) and nat(s['size']))
    need(type(value['processes']) is list and len(value['processes']) <= MAX_PROCESSES)
    for p in value['processes']:
        need(type(p) is dict and set(p) == {'pid', 'start_ticks', 'role', 'stable', 'unix_inodes', 'shm_refs', 'device_fds'})
        need(nat(p['pid']) and p['pid'] >= 1 and nat(p['start_ticks']) and p['role'] in ROLES and type(p['stable']) is bool)
        need(type(p['unix_inodes']) is list and len(p['unix_inodes']) <= MAX_ITEMS and all(nat(i) for i in p['unix_inodes']))
        need(type(p['shm_refs']) is list and len(p['shm_refs']) <= MAX_ITEMS
             and all(type(r) is list and len(r) == 2 and all(nat(x) for x in r) for r in p['shm_refs']))
        need(type(p['device_fds']) is list and len(p['device_fds']) <= 64 and all(sha(d) for d in p['device_fds']))
    need(len({p['pid'] for p in value['processes']}) == len(value['processes']))
    return value


def parse(stdout_bytes):
    return decode(json.loads(stdout_bytes.decode('utf-8')))
