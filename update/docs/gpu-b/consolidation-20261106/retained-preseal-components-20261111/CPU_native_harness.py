"""New B observation only: original git-source inventory + native CPU fixture.

No ZIP code is imported/executed. Kubernetes transport/owner are explicit CPU
substitutes; the unmodified inventory program runs in a CPU-only local container.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid


NATIVE_PARENT = r'''
import hashlib,json,os,stat,subprocess,sys
from pathlib import Path
p=json.loads(sys.stdin.buffer.read(131072))
session=p['session'];root=Path('/guard-lifecycle/data')/session
root.mkdir(mode=0o700)
ticks=int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19])
journal=root/('pid-%s-%s.jsonl'%(os.getpid(),ticks))
fd=os.open(journal,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
os.write(fd,b'EXPLICIT_SYNTHETIC_CPU_METADATA_ONLY\n');os.close(fd)
if p['case']=='unsupported-entry':(root/'unsupported.fixture').write_bytes(b'CPU_ONLY')
if p['case']=='native-error':root.chmod(0o777)
facts=os.stat(journal)
native={'uid':os.getuid(),'gid':os.getgid(),'pid':os.getpid(),'start_ticks':ticks,
        'boot_sha256':hashlib.sha256(Path('/proc/sys/kernel/random/boot_id').read_bytes()).hexdigest(),
        'file':{'device':facts.st_dev,'inode':facts.st_ino,'mode':stat.S_IMODE(facts.st_mode),
                'uid':facts.st_uid,'bytes':facts.st_size,'mtime_ns':facts.st_mtime_ns,'ctime_ns':facts.st_ctime_ns},
        'python':sys.version.split()[0],'flags':{'I':sys.flags.isolated,'S':sys.flags.no_site,'B':sys.dont_write_bytecode}}
r=subprocess.run([sys.executable,'-I','-S','-B','-c',p['program'],session],capture_output=True,timeout=5)
assert len(r.stdout)<=65536 and len(r.stderr)<=4096
print(json.dumps({'returncode':r.returncode,'stdout':r.stdout.decode(),'stderr':r.stderr.decode(),'native':native}))
'''


def selected_definitions(path,names,namespace):
    tree=ast.parse(path.read_bytes());nodes=[n for n in tree.body if
        isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names or
        isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    found={n.name if isinstance(n,ast.FunctionDef) else n.targets[0].id for n in nodes}
    assert found==set(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)


def main():
    source,receipt=Path(sys.argv[1]).absolute(),Path(sys.argv[2]).absolute();d=source.parent
    lifecycle=d.parent/'sup16-r8-observation-boundary-trace-20261111-v1/source-refs/2cb01e9f-capture-session.source.txt'
    native={'argv':sys.orig_argv,'executable':sys.executable,'uid':os.getuid(),'gid':os.getgid(),
            'python':sys.version.split()[0],'flags':{'I':sys.flags.isolated,'S':sys.flags.no_site,'B':sys.dont_write_bytecode},
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'original_inventory_module_sha256':hashlib.sha256(lifecycle.read_bytes()).hexdigest()}
    spec=importlib.util.spec_from_file_location('s5_preseal_candidate',source)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    original={'hashlib':hashlib,'json':json,'re':re,'PurePosixPath':PurePosixPath,
              'MAX_FILES':32,'MAX_PROCESSES':128}
    selected_definitions(d/'original-bounded.py.source.txt',{'strict_json'},original)
    selected_definitions(d/'original-owned_capture.py.source.txt',{'validate_owned_source'},original)
    selected_definitions(lifecycle,{'INVENTORY_READER','_integer','_sha','safe_owner','parse_inventory','inventory'},original)
    inventory=original['inventory'];program=original['INVENTORY_READER']
    native['native_inventory_program_sha256']=hashlib.sha256(program.encode()).hexdigest()
    assert native['native_inventory_program_sha256']=='1046b712c6184475fd9020b1c496695cd802e3f9efbea6028d5d4d42ebfffac2'
    image=json.loads(subprocess.check_output(['docker','image','inspect','python:3.12.14-slim']))[0]['Id']
    assert image.startswith('sha256:f77ac9e44ae9')
    native['CPU_image_id']=image
    tree=ast.parse(source.read_bytes());main_node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    capture_try=next(n for n in main_node.body if isinstance(n,ast.Try) and any(
        isinstance(x,ast.Expr) and isinstance(x.value,ast.Call) and isinstance(x.value.func,ast.Attribute) and
        x.value.func.attr=='snapshot' for x in n.body))
    start=next(i for i,n in enumerate(capture_try.body) if isinstance(n,ast.Expr) and
               isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='snapshot')
    end=next(i for i,n in enumerate(capture_try.body) if isinstance(n,ast.Assign) and
             any(isinstance(t,ast.Name) and t.id=='packet' for t in n.targets))
    block=compile(ast.Module(body=capture_try.body[start:end+1],type_ignores=[]),str(source),'exec')
    cases=[];containers=[];fd_before=len(os.listdir('/proc/self/fd'));owner={
        'started_at':'EXPLICIT_SYNTHETIC_CPU_OWNER','identity_scope':'CPU substitute; no Kubernetes/API identity'}
    with tempfile.TemporaryDirectory(prefix='s5-preseal-native-') as tmp:
        root=Path(tmp)
        for kind in ['basic','unsupported-entry','native-error']:
            session=uuid.uuid4().hex if kind!='invalid-session' else 'invalid'
            work=root/kind;work.mkdir(mode=0o700);data=work/'data';data.mkdir(mode=0o700)
            private=work/'private';private.mkdir(mode=0o700)
            preserver=m.FirstClientFailure(private);calls=[];last={};captured=[]
            seal_error=OSError(5,'EXPLICIT_CPU_ORIGINAL_SEAL_ERROR') if kind=='native-error' else None
            capture_error=RuntimeError('EXPLICIT_CPU_ORIGINAL_CAPTURE_ERROR') if kind=='invalid-session' else None
            class Reader:
                binding={'engine':{'container':'engine','image':'CPU-reference@sha256:'+'a'*64}}
                deadline=time.monotonic()+30
                owner_identity=staticmethod(lambda *a:dict(owner))
                read_file=None
                image_check=None
                def snapshot(self):calls.append('snapshot')
                def seal_session(self):
                    calls.append('seal')
                    if seal_error is not None:raise seal_error
                    return {'sealed':False,'scope':'EXPLICIT_SYNTHETIC_CPU_CONTROL'}
                def exec_bounded(self,container,argv,*,timeout,max_bytes):
                    calls.append('native_inventory')
                    assert argv==['python3','-c',program,session] and timeout==5 and max_bytes==65536
                    name='byul-s5-preseal-'+uuid.uuid4().hex
                    command=['docker','run','-i','--rm','--name',name,'--label','byul.s5.cpu=preseal',
                        '--network','none','--read-only','--cap-drop=ALL','--pids-limit','32',
                        '--memory','128m','--cpus','1','--user','1000:1000','--mount',
                        'type=bind,src='+str(data)+',dst=/guard-lifecycle/data',image,
                        'python','-I','-S','-B','-c',NATIVE_PARENT]
                    result=subprocess.run(command,input=json.dumps({'session':session,'case':kind,'program':program}).encode(),
                                          capture_output=True,timeout=20)
                    check=subprocess.run(['docker','container','inspect',name],capture_output=True)
                    absent=check.returncode!=0 and b'No such container' in check.stderr
                    containers.append({'name':name,'argv':command,'returncode':result.returncode,'removed':absent})
                    assert absent and result.returncode==0,result.stderr.decode()[:200]
                    value=json.loads(result.stdout);last.update(value)
                    stdout=value['stdout'].encode();stderr=value['stderr'].encode()
                    pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False,
                          'pipes_closed':True,'read_errors':[],'owner_binding_verified':True,
                          'streams':{k:{'limit':cap,'observed_bytes':len(raw),'retained_bytes':len(raw),
                              'retained_sha256':hashlib.sha256(raw).hexdigest(),'truncated':False}
                              for k,cap,raw in [('stdout',65536,stdout),('stderr',4096,stderr)]}}
                    producer_result={'returncode':value['returncode'],'capture':pipe,'stdout_bytes':stdout,'stderr_bytes':stderr}
                    preserver.save_result(argv,producer_result)
                    return SimpleNamespace(returncode=value['returncode'],stdout=value['stdout'],stderr=value['stderr'],capture=pipe)
            reader=Reader();end_before=reader.deadline;report={};observed=None
            def capture(*args,**kwargs):
                calls.append('capture')
                if capture_error is not None:raise capture_error
                return {'scope':'EXPLICIT_SYNTHETIC_CPU_ONLY'}
            def actual_producer(*args,**kwargs):
                result=inventory(*args,**kwargs);captured.append(result);return result
            namespace={'reader':reader,'target':{},'prepared':{'session':session},'report':report,
                       'observe_pre_seal_inventory':m.observe_pre_seal_inventory,'inventory':actual_producer,
                       'capture_session':capture}
            try:exec(block,namespace)
            except BaseException as exc:observed=exc
            o=report['before_seal'];ok=reader.deadline==end_before and calls[0]=='snapshot' and 'seal' in calls
            detail={'calls':calls,'deadline_unchanged':reader.deadline==end_before,'observation':o,
                    'first_client_failure':preserver.summary(),'native_CPU':last.get('native')}
            if kind in ('basic','unsupported-entry'):
                if not o['producer_returned']:
                    cases.append({'name':kind,'passed':False,'detail':detail});continue
                assert o['producer_result'] is captured[0]
                roundtrip=json.loads(json.dumps(report));snapshot=roundtrip['before_seal']['producer_result']['metadata']
                n=last['native'];f=snapshot['files'][0];proc=next(p for p in snapshot['processes'] if p['pid']==n['pid'])
                ok=ok and observed is None and (f['pid'],f['start_ticks'])==(n['pid'],n['start_ticks']) and \
                   proc['boot_sha256']==n['boot_sha256'] and all(f[k]==v for k,v in n['file'].items()) and \
                   snapshot['sealed'] is False and snapshot['unknown_entries']==(1 if kind=='unsupported-entry' else 0)
            elif kind=='native-error':
                ok=ok and observed is seal_error and o['producer_returned'] is False and \
                   o['error']['message']=='LIFECYCLE_INVENTORY_TRANSPORT_INVALID' and last['returncode']==1
                s=preserver.summary();ok=ok and s.get('saved') is True
                stderr_file=private/'first-client-failure/stderr.raw'
                ok=ok and stderr_file.read_bytes()==last['stderr'].encode() and len(last['stderr'])>0
                detail['original_seal_exception_same_object']=observed is seal_error
            else:
                ok=ok and observed is capture_error and o['producer_returned'] is False and \
                   o['error']['message']=='LIFECYCLE_SESSION_INVALID' and not last
                detail['original_capture_exception_same_object']=observed is capture_error
            cases.append({'name':kind,'passed':ok,'detail':detail})
    cases.append({'name':'FDs_restored','passed':fd_before==len(os.listdir('/proc/self/fd')),
                  'before':fd_before,'after':len(os.listdir('/proc/self/fd'))})
    result={'scope':'EXPLICIT_SYNTHETIC_CPU_NEW_B_OBSERVATION_ONLY','native':native,'cases':cases,
            'checks':len(cases),'failures':[c['name'] for c in cases if not c['passed']],
            'CPU_containers':containers,'actual_R8_new_observations':0,'new_GPU_AWS':0,
            'archive_code_executed':False,'A_source_modified':False,'whole_CRC_repeated':False,'product_complete':False}
    with receipt.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'receipt':str(receipt),'checks':result['checks'],'failures':result['failures']}))
    return bool(result['failures'])


if __name__=='__main__':raise SystemExit(main())
