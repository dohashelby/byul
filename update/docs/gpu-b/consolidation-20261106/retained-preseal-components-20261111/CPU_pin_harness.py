"""New pin boundaries only. No old main, source dispatch, AWS or model run."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace


def load_functions(path,names,namespace):
    nodes=[]
    for n in ast.parse(path.read_bytes()).body:
        if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(
                isinstance(t,ast.Name) and t.id in names for t in n.targets):nodes.append(n)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)


def main():
    d,baseline,receipt=map(lambda x:Path(x).absolute(),sys.argv[1:4]);f=d/'pin-closure-v1'
    manifest=json.loads((f/'code-pin-closure-v1.json').read_bytes())
    actual=json.loads((d.parent/'sup16-gb12-causal-readonly-20261111-v1/step06-actual-ENV-target-Pod-role-bindings.json').read_bytes())['challenge-partial']['inputs']
    assert actual['source_commit']=='2cb01e9f08f9a123b8fffff00f9e798060115db9'
    assert actual['source_manifest_sha256']=='ac423c8243af7b824bd66f7dadc5bd4ef5d827026a86d66f0c32dd28f3b4cf8b'
    native={'argv':sys.orig_argv,'uid':os.getuid(),'gid':os.getgid(),'python':sys.version.split()[0],
            'flags':{'I':sys.flags.isolated,'S':sys.flags.no_site,'B':sys.dont_write_bytecode},
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'code_pins':{k:v['candidate'] for k,v in manifest['code'].items()},'helper':manifest['helper']}
    assert os.geteuid()==0, 'root only for new local entry-owner fixtures'
    entry={};load_functions(f/'039-whole_entry.py',{'MODULE_PINS','require','sha','canonical','parse','pointer','read_pin'},entry)
    entry.update({'Path':Path,'os':os,'stat':stat,'hashlib':hashlib,'json':json,'re':re})
    tree=ast.parse((f/'039-whole_entry.py').read_bytes());cl=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FrozenCaller')
    init=next(n for n in cl.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    stop=next(i for i,n in enumerate(init.body) if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Attribute) and t.attr=='modules' for t in n.targets))
    entry_prefix=compile(ast.Module(body=init.body[:stop],type_ignores=[]),str(f/'039-whole_entry.py'),'exec')
    cases=[];fd_before=len(os.listdir('/proc/self/fd'))
    with tempfile.TemporaryDirectory(prefix='s5-preseal-pin-') as tmp:
        root=Path(tmp);root.chmod(0o755)
        assets=root/'assets';assets.mkdir(mode=0o700);os.chown(assets,1000,1000)
        runtime=root/'runtime';runtime.mkdir(mode=0o700);os.chown(runtime,1000,1000)
        helper=assets/'capture_normal.py';helper.write_bytes((d/'capture_normal.py').read_bytes());helper.chmod(0o600);os.chown(helper,1000,1000)
        verify=assets/'source_owner_verify.py';verify.write_bytes((baseline/'041-source_owner_verify.py').read_bytes());verify.chmod(0o600);os.chown(verify,1000,1000)
        child_out=root/'child-output';child_out.mkdir(mode=0o700);os.chown(child_out,1000,1000)
        pid=os.fork();reaped=False
        if pid==0:
            code=1
            try:
                os.setgroups([]);os.setgid(1000);os.setuid(1000)
                a_tree=ast.parse((f/'003-adapter.py').read_bytes())
                producer=next(n for n in ast.walk(a_tree) if isinstance(n,ast.Assign) and any(
                    isinstance(t,ast.Name) and t.id=='inputs' for t in n.targets) and isinstance(n.value,ast.Dict))
                shared=copy.deepcopy(actual);shared['capture_helper']=str(helper)
                cfg={'runtime':str(runtime),'staging_receipt_sha256':actual['staging_receipt_sha256']}
                ns={'shared':shared,'cfg':cfg,'phase':'challenge-partial','previous':actual['previous_roles'],
                    'private_root':str(assets/'client-private-challenge-partial'),'selected_client':[actual['kubectl_binary'],'--kubeconfig',actual['kubeconfig']],
                    'context':actual['context'],'Path':Path}
                exec(compile(ast.Module(body=[producer],type_ignores=[]),str(f/'003-adapter.py'),'exec'),ns)
                s=ns['inputs'];assert s['capture_helper']['file_sha256']==manifest['helper']['sha256']
                # Replay the unchanged downstream MAKE_INPUTS context from actual R8.
                # Fresh ENV/Pod generation is S2 scope, not a claim of this CPU.
                for key in ['environment','target','pod','namespace']:
                    s[key]=copy.deepcopy(actual[key])
                resolver={'Path':Path,'os':os,'stat':stat,'hashlib':hashlib,'re':re,'sys':sys}
                load_functions(f/'021-resolver.py',{'PINS','sha','read','command'},resolver)
                rtree=ast.parse((f/'021-resolver.py').read_bytes());capture_branch=next(n for n in ast.walk(rtree) if isinstance(n,ast.If) and isinstance(n.test,ast.Compare) and
                    isinstance(n.test.left,ast.Name) and n.test.left.id=='stage' and any(isinstance(c,ast.Constant) and c.value=='capture' for c in n.test.comparators))
                branch=compile(ast.Module(body=capture_branch.body,type_ignores=[]),str(f/'021-resolver.py'),'exec')
                def consume(document):
                    scope=dict(resolver,s=document,commands=[],root=Path(actual['source_root']),phase='challenge-partial',directory=runtime/'role')
                    exec(branch,scope);return scope['commands'][0]
                command=consume(s);argv=command['argv'];H=manifest['helper']['sha256']
                assert argv[argv.index('--tool-sha256')+1]==H and argv[argv.index('--source-commit')+1]==actual['source_commit']
                assert argv[argv.index('--manifest-sha256')+1]==actual['source_manifest_sha256'] and argv[1:3]==['-I','-B']
                tests=[{'name':'adapter_actual_inputs_to_resolver_actual_capture_argv','passed':True,'argv':argv,
                        'helper_pin':s['capture_helper'],'native_UID':os.getuid(),'source_guard_read':True}]
                old=copy.deepcopy(s);old['capture_helper']['file_sha256']='7ed0879319b77895234108418fe536d9d9d3c5155f7f03660b1129d3cda5faf8'
                try:consume(old)
                except ValueError as e:tests.append({'name':'stale_helper_pin_refused','passed':str(e)=='SUP16_DELTA_CAPTURE_SOURCE_PIN_REQUIRED','error':str(e)})
                else:tests.append({'name':'stale_helper_pin_refused','passed':False})
                original=helper.read_bytes();helper.write_bytes(original+b'\n# CPU corruption\n')
                try:consume(s)
                except ValueError as e:tests.append({'name':'helper_actual_bytes_mismatch_refused','passed':str(e)=='SUP16_DELTA_FILE_BYTES_CHANGED','error':str(e)})
                else:tests.append({'name':'helper_actual_bytes_mismatch_refused','passed':False})
                finally:helper.write_bytes(original)
                out={'native_uid':os.getuid(),'cases':tests,'input_producer':'exact adapter Assign node',
                     'consumer':'exact resolver capture branch/read/command; broader inputs/role_result/runtime not executed',
                     'original_source_values_from_actual_R8_inputs':{'commit':actual['source_commit'],'manifest':actual['source_manifest_sha256'],'root':actual['source_root']}}
                with (child_out/'producer-consumer.json').open('x',encoding='utf-8') as stream:json.dump(out,stream,indent=2)
                code=0
            except BaseException as exc:
                with (child_out/'child-error.json').open('x',encoding='utf-8') as stream:
                    json.dump({'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()},stream)
            finally:os._exit(code)
        try:
            end=time.monotonic()+15
            while True:
                found,status=os.waitpid(pid,os.WNOHANG)
                if found:reaped=True;break
                assert time.monotonic()<end;time.sleep(.005)
            if os.waitstatus_to_exitcode(status)!=0:
                diagnostic=(child_out/'child-error.json').read_bytes()
                with (d/'cpu-pin-v1_1-child-error.json').open('xb') as saved:saved.write(diagnostic)
                raise RuntimeError(diagnostic.decode())
            child=json.loads((child_out/'producer-consumer.json').read_bytes());cases.extend(child['cases'])
        finally:
            if not reaped:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(pid,0)
        request=json.loads((baseline/'040-whole-fixed-request.json').read_bytes());module_dir=root/'entry-modules';module_dir.mkdir(mode=0o700)
        for name,pin in entry['MODULE_PINS'].items():
            basename=Path(request['module_files'][name]['path']).name
            selected=list(f.glob('*-'+basename)) or list(baseline.glob('*-'+basename));assert len(selected)==1,(name,selected)
            raw=selected[0].read_bytes();assert hashlib.sha256(raw).hexdigest()==pin
            dest=module_dir/basename;dest.write_bytes(raw);dest.chmod(0o600)
            request['module_files'][name]={'path':str(dest),'owner':0,'sha256':pin}
        def consume_entry(document):
            scope=dict(entry,request=document,self=SimpleNamespace());exec(entry_prefix,scope);return scope['raw_modules']
        raw_modules=consume_entry(request)
        cases.append({'name':'entry_real_pointer_read_pin_consumes_new_modules','passed':len(raw_modules)==len(entry['MODULE_PINS']),
                      'changed_pins':{k:hashlib.sha256(raw_modules[k]).hexdigest() for k in ['adapter','actionwise','join']},
                      'owner':0,'loaded_or_executed_operator_modules':False})
        stale=copy.deepcopy(request);stale['module_files']['adapter']['sha256']=manifest['code']['003-adapter.py']['baseline']['sha256']
        try:consume_entry(stale)
        except ValueError as e:cases.append({'name':'entry_stale_adapter_reference_refused','passed':str(e)=='SUP16_ENTRY_FROZEN_COMPONENT_CHANGED:adapter','error':str(e)})
        else:cases.append({'name':'entry_stale_adapter_reference_refused','passed':False})
        adapter=Path(request['module_files']['adapter']['path']);new=adapter.read_bytes();adapter.write_bytes((baseline/'003-adapter.py').read_bytes())
        try:consume_entry(request)
        except ValueError as e:cases.append({'name':'entry_old_actual_adapter_bytes_refused','passed':str(e)=='SUP16_ENTRY_INPUT_BYTES_OR_IDENTITY_CHANGED','error':str(e)})
        else:cases.append({'name':'entry_old_actual_adapter_bytes_refused','passed':False})
        finally:adapter.write_bytes(new)
    cases.append({'name':'all_FDs_restored','passed':fd_before==len(os.listdir('/proc/self/fd'))})
    result={'scope':'EXPLICIT_SYNTHETIC_CPU_NEW_PIN_BOUNDARIES_ONLY','native':native,'child_UID1000':child,
            'cases':cases,'checks':len(cases),'failures':[c['name'] for c in cases if not c['passed']],
            'old_main_executed':False,'whole_module_load_or_controller_init':False,'new_GPU_AWS':0,
            'A_source_modified':False,'S2_operating_metadata_changed':False,'operation_ready':False}
    with receipt.open('x',encoding='utf-8') as out:json.dump(result,out,indent=2);out.write('\n')
    print(json.dumps({'receipt':str(receipt),'checks':len(cases),'failures':result['failures']}))
    return bool(result['failures'])


if __name__=='__main__':raise SystemExit(main())
