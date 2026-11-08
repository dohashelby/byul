"""PLT-02 공통 입력 관측기: 엔진 컨테이너 안 unix socket의 실제 소유·상대 (개발 담당, B 의존 반환 6).

엔진 컨테이너 안에서 `python3 -I -S -c OBSERVER`로 한 번 돈다. 읽기만 하며 socket을 열거나 연결하지 않는다.

- 프로세스: 컨테이너 PID 이름공간의 `/proc`. PID·시작 ticks·실효 UID·net 이름공간 inode·역할을 읽고, 읽기 앞뒤 시작
  ticks가 같은 프로세스만 안정(stable)으로 둔다. 역할은 vLLM 프로세스 제목으로만 정한다(api_server·engine_core·worker·
  utility·other). 제목·명령줄 원문은 남기지 않고 SHA-256만 남긴다.
- socket 소유: 각 프로세스의 `fd` 링크 `socket:[inode]`. 같은 커널 socket inode를 여러 프로세스가 가질 수 있다(상속).
- unix socket 사실: 커널 sock_diag(NETLINK_SOCK_DIAG, AF_UNIX)의 상태·이름·VFS·상대 inode. 이름(경로)은 SHA-256만,
  경로 파일의 (dev, ino)는 **파일** 신원으로 따로 둔다. socket 커널 inode와 섞지 않는다.

listen 상태(TCP_LISTEN=10)는 listener, 이름이 있는 연결 상태(1)는 그 이름의 listener가 accept한 socket, 이름이 없는
연결 상태는 연결한 쪽 또는 socketpair다. 상대 inode가 컨테이너 안 어느 프로세스에도 없으면 상대 미해결이다.
"""
from __future__ import annotations

import json

SCHEMA = 'guard-ipc-ownership/1'
ROLES = ('api_server', 'engine_core', 'worker', 'utility', 'other')
MAX_PROCESSES = 64
MAX_SOCKETS = 4096

OBSERVER = r'''
import hashlib,json,os,socket,struct,sys
codes=set()
def ticks(pid):
 raw=open('/proc/%d/stat'%pid,'rb').read(8192).decode('utf-8','replace');return int(raw[raw.rfind(')')+2:].split()[19])
def uid(pid):
 for line in open('/proc/%d/status'%pid,'rb').read(16384).decode('utf-8','replace').splitlines():
  if line.startswith('Uid:'):return int(line.split()[2])
def role(pid,argv):
 title=argv[0] if argv else ''
 base=[os.path.basename(a) for a in argv[:3]]
 if title.startswith('VLLM::EngineCore'):return 'engine_core'
 if title.startswith('VLLM::Worker'):return 'worker'
 if title.startswith('VLLM::APIServer') or (('vllm' in base[:2]) and 'serve' in argv[:3]):return 'api_server'
 if any('multiprocessing.resource_tracker' in a or 'multiprocessing.spawn' in a or 'multiprocessing.forkserver' in a for a in argv[:4]):return 'utility'
 return 'other'
def rank(argv):
 title=argv[0] if argv else ''
 for mark in ('_TP','_PP','_DP'):
  if mark in title:
   tail=title.split(mark,1)[1];digits=''
   for ch in tail:
    if not ch.isdigit():break
    digits+=ch
   if digits:return int(digits)
 return None
processes=[];me=os.getpid()
for entry in sorted((int(p) for p in os.listdir('/proc') if p.isdecimal())):
 if entry==me:continue
 if len(processes)>=64:codes.add('PROCESS_SET_BOUND');break
 try:
  before=ticks(entry)
  argv=[a.decode('utf-8','replace') for a in open('/proc/%d/cmdline'%entry,'rb').read(65536).split(b'\0') if a]
  sockets=[]
  for name in os.listdir('/proc/%d/fd'%entry):
   if not name.isdecimal():continue
   try:link=os.readlink('/proc/%d/fd/%s'%(entry,name))
   except OSError:continue
   if link.startswith('socket:[') and link.endswith(']'):sockets.append({'fd':int(name),'inode':int(link[8:-1])})
  net=os.stat('/proc/%d/ns/net'%entry).st_ino
  item={'pid':entry,'start_ticks':before,'uid_effective':uid(entry),'net_ns':net,'role':role(entry,argv),'rank':rank(argv),
        'title_sha256':hashlib.sha256((argv[0] if argv else '').encode()).hexdigest(),'sockets':sorted(sockets,key=lambda s:s['fd'])[:4096],
        'stable':ticks(entry)==before}
  if len(sockets)>4096:codes.add('SOCKET_SET_BOUND')
  processes.append(item)
 except (OSError,ValueError,IndexError):
  codes.add('PROCESS_READ_FAILED')
unix={};ok=False
try:
 s=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,4)
 req=struct.pack('=BBHIIIII',1,0,0,0xffffffff,0,1|2|4,0xffffffff,0xffffffff)
 s.send(struct.pack('=IHHII',16+len(req),20,0x301,1,0)+req)
 done=False
 while not done:
  data=s.recv(1<<17);off=0
  if not data:break
  while off+16<=len(data):
   ln,typ=struct.unpack_from('=IH',data,off)
   if ln<16:done=True;codes.add('SOCK_DIAG_MALFORMED');break
   if typ==3:done=True;ok=True;break
   if typ==2:done=True;codes.add('SOCK_DIAG_ERROR');break
   fam,styp,state,_,ino=struct.unpack_from('=BBBBI',data,off+16)
   item={'type':styp,'state':state,'path_sha256':None,'abstract':False,'vfs':None,'peer':None};a=off+32
   while a+4<=off+ln:
    alen,atype=struct.unpack_from('=HH',data,a);body=data[a+4:a+alen]
    if alen<4:break
    if atype==0:
     if body[:1]==b'\0':item['abstract']=True;item['path_sha256']=hashlib.sha256(body).hexdigest()
     else:item['path_sha256']=hashlib.sha256(body.split(b'\0')[0]).hexdigest()
    elif atype==1 and len(body)>=8:item['vfs']=list(struct.unpack('=II',body[:8]))
    elif atype==2 and len(body)>=4:item['peer']=struct.unpack('=I',body[:4])[0]
    a+=(alen+3)&~3
   if len(unix)>=4096:codes.add('SOCK_DIAG_BOUND');done=True;break
   unix[str(ino)]=item
   off+=(ln+3)&~3
 s.close()
except OSError:
 codes.add('SOCK_DIAG_UNAVAILABLE')
print(json.dumps({'schema':'guard-ipc-ownership/1','processes':processes,'unix':unix,'sock_diag_complete':ok,'codes':sorted(codes)}))
'''


def decode(value):
    """관측기 출력의 닫힌 형식 검사. 맞지 않으면 ValueError."""
    def need(ok):
        if not ok:
            raise ValueError('IPC_OWNERSHIP_OBSERVATION_INVALID')
    sha = lambda v: type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
    nat = lambda v: type(v) is int and 0 <= v < 2**63
    need(type(value) is dict and set(value) == {'schema', 'processes', 'unix', 'sock_diag_complete', 'codes'}
         and value['schema'] == SCHEMA and type(value['sock_diag_complete']) is bool)
    need(type(value['codes']) is list and all(type(c) is str and c.isupper() for c in value['codes']))
    need(type(value['processes']) is list and len(value['processes']) <= MAX_PROCESSES)
    for p in value['processes']:
        need(type(p) is dict and set(p) == {'pid', 'start_ticks', 'uid_effective', 'net_ns', 'role', 'rank', 'title_sha256',
                                            'sockets', 'stable'})
        need(nat(p['pid']) and p['pid'] >= 1 and nat(p['start_ticks']) and nat(p['net_ns']) and sha(p['title_sha256'])
             and (p['uid_effective'] is None or nat(p['uid_effective'])) and p['role'] in ROLES
             and (p['rank'] is None or nat(p['rank'])) and type(p['stable']) is bool)
        need(type(p['sockets']) is list and len(p['sockets']) <= MAX_SOCKETS
             and all(type(s) is dict and set(s) == {'fd', 'inode'} and nat(s['fd']) and nat(s['inode']) for s in p['sockets']))
    need(len({p['pid'] for p in value['processes']}) == len(value['processes']))
    need(type(value['unix']) is dict and len(value['unix']) <= MAX_SOCKETS)
    for key, u in value['unix'].items():
        need(type(key) is str and key.isdecimal() and type(u) is dict
             and set(u) == {'type', 'state', 'path_sha256', 'abstract', 'vfs', 'peer'})
        need(nat(u['type']) and nat(u['state']) and type(u['abstract']) is bool
             and (u['path_sha256'] is None or sha(u['path_sha256'])) and (u['peer'] is None or nat(u['peer']))
             and (u['vfs'] is None or type(u['vfs']) is list and len(u['vfs']) == 2 and all(nat(v) for v in u['vfs'])))
    return value


def parse(stdout_bytes):
    return decode(json.loads(stdout_bytes.decode('utf-8')))
