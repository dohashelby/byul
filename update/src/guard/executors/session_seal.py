"""선정된 native session의 제어·기록 종결만 확인하는 고정 stdlib 프로그램."""
SESSION_SEALER = r'''
import hashlib,json,os,re,stat,sys,time
session=sys.argv[1]
if re.fullmatch(r"[0-9a-f]{32}",session) is None:raise ValueError()
path='/guard-lifecycle/data/'+session

def directory():
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
 try:
  for part in path.split('/')[1:]:
   child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
   os.close(fd);fd=child
  return fd
 except BaseException:
  os.close(fd);raise

def identity(s):
 return (s.st_dev,s.st_ino,s.st_uid,stat.S_IMODE(s.st_mode))

def private_file(s):
 return stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and not s.st_mode&0o077 and s.st_nlink==1

root=directory()
try:
 selected=identity(os.fstat(root))
 if selected[2]!=os.geteuid() or selected[3]&0o077:raise ValueError()
 try:
  control=os.open('SEALED',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=root)
 except FileExistsError:
  control=os.open('SEALED',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
 try:
  value=os.fstat(control)
  if not private_file(value) or value.st_size!=0:raise ValueError()
  control_identity=identity(value)
  if identity(os.stat('SEALED',dir_fd=root,follow_symlinks=False))!=control_identity:raise ValueError()
 finally:os.close(control)

 def census():
  rows=[]
  entries=os.listdir(root)
  if not 2<=len(entries)<=33 or 'SEALED' not in entries:raise ValueError()
  for name in sorted(entries):
   if name=='SEALED':continue
   if re.fullmatch(r'pid-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}\.jsonl',name) is None:raise ValueError()
   fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
   try:
    first=os.fstat(fd)
    if not private_file(first) or not 0<first.st_size<=262144:raise ValueError()
    os.lseek(fd,max(0,first.st_size-16384),os.SEEK_SET)
    tail=os.read(fd,16385)
    last=os.fstat(fd);named=os.stat(name,dir_fd=root,follow_symlinks=False)
    facts=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_uid,stat.S_IMODE(s.st_mode),s.st_nlink)
    if facts(first)!=facts(last) or facts(first)!=facts(named):return None
    if not tail.endswith(b'\n'):return None
    line=tail.rstrip(b'\n').rsplit(b'\n',1)[-1]
    def pairs(items):
     result={}
     for key,item in items:
      if key in result:raise ValueError()
      result[key]=item
     return result
    end=json.loads(line,object_pairs_hook=pairs)
    if (type(end) is not dict or set(end)!={'seq','event','monotonic_ns','previous_sha256','payload'}
      or end['event']!='END' or type(end['seq']) is not int or end['seq']<1
      or type(end['monotonic_ns']) is not int or end['monotonic_ns']<0
      or type(end['previous_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',end['previous_sha256']) is None
      or type(end['payload']) is not dict or set(end['payload'])!={'reason'}
      or end['payload']['reason'] not in {'SEALED','PROCESS_EXIT','BOUND_REACHED','CONTROL_UNAVAILABLE'}):return None
    rows.append([hashlib.sha256(name.encode()).hexdigest(),list(facts(first)),hashlib.sha256(line).hexdigest()])
   finally:os.close(fd)
  # Reopen the complete path; a renamed/replaced session directory is not selected.
  current=directory()
  try:
   if identity(os.fstat(current))!=selected:raise ValueError()
  finally:os.close(current)
  marker=os.stat('SEALED',dir_fd=root,follow_symlinks=False)
  if not private_file(marker) or marker.st_size!=0 or identity(marker)!=control_identity:raise ValueError()
  return rows

 deadline=time.monotonic()+4
 previous=None;sealed=False;count=0;digest=None
 while time.monotonic()<deadline:
  try:rows=census()
  except (OSError,ValueError,TypeError,KeyError):rows=None
  if rows is not None and rows==previous:
   sealed=True;count=len(rows)
   digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   break
  previous=rows
  time.sleep(0.05)
 print(json.dumps({'schema':'guard-session-seal/1','session':session,'sealed':sealed,
  'recordings_closed':count,'terminal_inventory_sha256':digest},separators=(',',':')))
finally:os.close(root)
'''
