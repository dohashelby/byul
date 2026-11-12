"""Real candidate wrapper binding and driver/Popen/helper-main connections."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

PUBLIC=Path(__file__).resolve().parents[1]
FIX=Path(__file__).resolve().parent/'fixtures'/'gpu-b-sup16'
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

class Connections(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='byul-connections-');self.path=Path(self.temp.name)
        self.old_umask=os.umask(0o077)
        self.boundary=load(FIX/'integration_boundaries.py','actual_connection_fixture')
    def tearDown(self):
        os.umask(self.old_umask);self.temp.cleanup()

    def test_real_wrapper_track_and_rebound_command_reach_original_call_cli(self):
        rc=load(FIX/'original_cli.py','connection_original_cli')
        verify=[];rc.verify_controller=lambda state:verify.append(state)
        self.boundary.track_actual_children(rc)  # Actual candidate function; no manual attach.
        state=dict(output=self.path,update=self.path,environment=dict(os.environ),commands=[])
        namespace=dict(rc=rc,state=state,serial=198,active_command_deadline=time.monotonic()+45,
            require=self.boundary.require,re=re,time=time)
        command=self.boundary._rebind(self.boundary.command,namespace)
        self.assertIs(command.__globals__['rc'],rc)
        argv=[sys.executable,'-I','-S','-B','-c',"import sys;print('connected first error',file=sys.stderr);raise SystemExit(7)"]
        with self.assertRaisesRegex(ValueError,'GB49_CLI_FAILED:199-sup16-resolve-025-capture'):
            command('sup16-resolve-025-capture',argv)
        observer=self.boundary.FIRST_FAILURE
        self.assertIsNotNone(observer.failure)
        self.assertEqual(observer.failure['row'],state['commands'][0])
        self.assertEqual(observer.failure['row']['argv'],argv)
        self.assertTrue(all(p.poll() is not None for p in self.boundary.ACTUAL_CHILDREN))
        cap=self.path/'capsule.json'
        observer.write(cap,binding=dict(boot_id='EXAMPLE',configuration_sha256='e'*64),owner=os.geteuid())
        value=self.boundary._capsule.consume(cap.read_bytes())
        self.assertEqual(value['channels']['stderr']['sha256'],state['commands'][0]['stderr_sha256'])

    def launcher(self,actual_main):
        path=self.path/('getter.py' if actual_main else 'put-argv.py')
        if not actual_main:
            path.write_text("import json,sys;print(json.dumps(sys.argv[1:]))\n")
            return path
        def loader(variable,path,name):
            return f"s=importlib.util.spec_from_file_location({name!r},{str(path)!r});{variable}=importlib.util.module_from_spec(s);sys.modules[{name!r}]={variable};s.loader.exec_module({variable})\n"
        source='import sys;sys.dont_write_bytecode=True\nimport importlib.util,types\n'
        source+=loader('original',FIX/'original_transport.py','child_original_transport')
        source+=loader('contract',PUBLIC/'tools/gpu_b_transfer_contract.py','child_transfer_contract')
        source+=loader('entry',FIX/'integration_boundaries.py','child_actual_main')
        source+=loader('sdk_module',PUBLIC/'tests/test_gpu_b_capsule_transfer.py','child_fake_sdk_fixture')
        source+="for k,v in vars(original).items():\n    if not k.startswith('__'):setattr(entry,k,v)\n"
        source+="entry.bind_transfer=contract.bind_transfer\nentry.REGION='EXAMPLE-not-a-real-region'\n"
        source+="sdk=sdk_module.SDK(b'EXAMPLE actual-main fixture bytes')\nsys.modules['aws_cli_adapter_v2']=types.SimpleNamespace(client=lambda *a,**k:sdk)\nentry.main(sys.argv[1:])\n"
        path.write_text(source)
        return path

    def driver(self,helper,*,fail_handoff=False):
        b=self.boundary;b.OUT=self.path;b.PACKET={'copy_helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}
        b.now=lambda:'EXAMPLE-CPU-time';b.uncertainty_barrier=lambda *a,**k:None
        events=[];offset=[0.0]
        b.time=SimpleNamespace(monotonic=lambda:time.monotonic()+offset[0],sleep=time.sleep)
        original=subprocess
        def popen(argv,**options):
            events.append(('Popen',list(argv)));return original.Popen(argv,**options)
        b.subprocess=SimpleNamespace(**vars(original));b.subprocess.Popen=popen
        actual_wait=b.wait_child
        def wait(proc,seconds):
            events.append(('wait',seconds));return actual_wait(proc,seconds)
        b.wait_child=wait
        error=OSError('EXAMPLE metadata handoff failure')
        def put(name,value):
            events.append(('put',name,value))
            if name.endswith('-group-PID.json'):
                offset[0]+=5
                if fail_handoff:raise error
            with (self.path/name).open('x') as stream:json.dump(value,stream)
        b.put=put
        return b,events,error

    def test_real_driver_GET_Popen_actual_helper_main_and_parent_wait_share_deadline(self):
        helper=self.launcher(True);b,events,_=self.driver(helper)
        data=b'EXAMPLE actual-main fixture bytes'
        argv=[sys.executable,str(helper),'get','GB99-original-evidence.zip',str(self.path/'received.zip'),
            '--expected-size',str(len(data)),'--expected-sha256',hashlib.sha256(data).hexdigest(),
            '--log',str(self.path/'copy.jsonl')]
        result=b.run_transfer_group('get-connection',argv)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        actual=next(e[1] for e in events if e[0]=='Popen')
        self.assertEqual(actual.count('--deadline-monotonic'),1)
        deadline=float(actual[actual.index('--deadline-monotonic')+1])
        wait=next(e[1] for e in events if e[0]=='wait')
        self.assertGreater(deadline,time.monotonic())
        self.assertGreater(wait,890);self.assertLess(wait,896)
        self.assertNotIn('--deadline-monotonic',argv)
        self.assertEqual((self.path/'received.zip').read_bytes(),data)
        complete=json.loads((self.path/'copy.jsonl.records/get-complete.json').read_bytes())
        self.assertTrue(complete['complete'])

    def test_real_driver_PUT_keeps_argv_and_original_wait_contract(self):
        helper=self.launcher(False);b,events,_=self.driver(helper)
        argv=[sys.executable,str(helper),'put','EXAMPLE-input','EXAMPLE.zip']
        result=b.run_transfer_group('put-connection',argv)
        actual=next(e[1] for e in events if e[0]=='Popen')
        self.assertEqual(actual,argv)
        self.assertEqual(next(e[1] for e in events if e[0]=='wait'),900)
        self.assertEqual(result.returncode,0)

    def test_real_driver_handoff_failure_reaps_child_and_preserves_original_error(self):
        helper=self.launcher(True);b,events,error=self.driver(helper,fail_handoff=True)
        argv=[sys.executable,str(helper),'get','GB99-original-evidence.zip',str(self.path/'received.zip'),
              '--expected-size','0','--expected-sha256','0'*64,'--log',str(self.path/'copy.jsonl')]
        with self.assertRaises(OSError) as caught:b.run_transfer_group('handoff-failure',argv)
        self.assertIs(caught.exception,error)
        terminal=next(e[2] for e in events if e[0]=='put' and e[1].endswith('-group-terminal.json'))
        self.assertTrue(terminal['child_reaped'] and terminal['group_terminal'])
        self.assertFalse(terminal['cleanup_errors'])
        self.assertIn('local_metadata_error',terminal)

    def test_generation_rejects_zero_or_ambiguous_CRLF_replacement(self):
        source='def run_transfer_group(label,argv):\r\n    pass\r\n'
        with self.assertRaisesRegex(ValueError,'EXACTLY_ONCE'):
            self.boundary.once(source,'def run_transfer_group(label,argv):\n','changed','old LF-only bug')
        with self.assertRaisesRegex(ValueError,'EXACTLY_ONCE'):
            self.boundary.once(source+source,'def run_transfer_group(label,argv):','changed','ambiguous source')
        changed=self.boundary.once(source,'def run_transfer_group(label,argv):','def changed(label,argv):','unique CRLF header')
        self.assertIn('def changed(label,argv):\r\n',changed)

if __name__=='__main__':unittest.main(verbosity=2)
