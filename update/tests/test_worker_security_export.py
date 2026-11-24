"""Round-trip the exported probe against owned files and real CPU /proc only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from types import SimpleNamespace as NS
import pytest

from guard.collectors import worker_security as collector
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_worker_security import REPO


def test_exported_probe_record_crosses_real_owned_sink_and_closed_decoder(external,monkeypatch):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    prepared=collector.prepare_assets(external/'probe',REPO,credential_paths=[],sentinel_paths=[],
        permission_paths=[],expected_ranks=[0])
    prepared,cfg=collector.load_prepared(external/'probe/manifest.json')
    root=external/'probe';directory=root/'data'/cfg['session'];directory.mkdir(parents=True,mode=0o700);directory.chmod(0o700)
    module=types.ModuleType('_exported_worker_probe_example');module.__file__=str(root/'guard_worker_security.py')
    exec(compile((root/'guard_worker_security.py').read_bytes(),module.__file__,'exec',dont_inherit=True,optimize=0),vars(module))
    module.ROOT=str(root)
    monkeypatch.setattr(module.os,'environ',{'EXAMPLE_NAME':'EXAMPLE_VALUE_MUST_NOT_LEAK'})
    owner=type('ExampleWorker',(),{});worker=owner();worker.rank=0
    worker.parallel_config=NS(tensor_parallel_size=1,pipeline_parallel_size=1,data_parallel_size=1)
    module.probe(worker,owner,cfg,collector.BODY_CODES.get(runtime,'0'*64))
    raw=(directory/'rank-0.json').read_bytes();row=json.loads(raw)
    if runtime in collector.BODY_CODES:collector.validate_record(row,prepared,cfg)
    else:
        with pytest.raises(ValueError,match='WORKER_SECURITY_RECORD_INVALID'):collector.validate_record(row,prepared,cfg)
    assert row['environment_names']==['EXAMPLE_NAME'] and b'EXAMPLE_VALUE_MUST_NOT_LEAK' not in raw
    assert row['producer_authenticated'] is False and prepared['gpu_qualified'] is False
    assert row['write_results']==row['credential_results']==row['sentinel_results']=={}
    assert not collector.qualification('mock','EXAMPLE','example.invalid/unqualified',[row])


@pytest.mark.parametrize('mode',['normal','original-error','namespace-collision'])
def test_worker_bootstrap_preserves_original_startup_and_public_imports(external,mode):
    own=external/'own';original=external/'original';own.mkdir();original.mkdir()
    trace=external/'startup-order.txt'
    (own/'sitecustomize.py').write_bytes(collector.BOOTSTRAP)
    (original/'guard_worker_security.py').write_text("VALUE='ORIGINAL_PUBLIC_MODULE'\n")
    original_body="import os,sys\nfrom pathlib import Path\nPath(os.environ['EXAMPLE_STARTUP_TRACE']).write_text('original\\n')\nORIGINAL_MARKER=True\n"
    if mode=='original-error':original_body+="raise RuntimeError('EXAMPLE_ORIGINAL_STARTUP_ERROR')\n"
    if mode=='namespace-collision':original_body+="import types\nsys.modules['_byul_worker_security']=types.ModuleType('EXAMPLE_preexisting')\n"
    (original/'sitecustomize.py').write_text(original_body)
    (own/'guard_worker_security.py').write_text('''def install():
    import os,sys
    from pathlib import Path
    assert sys.modules['sitecustomize'].ORIGINAL_MARKER
    assert Path(sys.modules['sitecustomize'].__file__).parent.name=='original'
    assert str(Path(__file__).parent) not in sys.path
    path=Path(os.environ['EXAMPLE_STARTUP_TRACE'])
    path.write_text(path.read_text()+'observer\\n')
    return True
''')
    env=os.environ|{'PYTHONPATH':str(own)+os.pathsep+str(original),'EXAMPLE_STARTUP_TRACE':str(trace)}
    program="import guard_worker_security,json,sys;print(json.dumps({'public':guard_worker_security.VALUE,'file':getattr(sys.modules.get('sitecustomize'),'__file__',None)}))"
    baseline=subprocess.run([sys.executable,'-c',program],cwd=external,
        env=env|{'PYTHONPATH':str(original)},capture_output=True,text=True)
    assert baseline.returncode==0,baseline.stderr
    result=subprocess.run([sys.executable,'-c',program],cwd=external,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    observed=json.loads(result.stdout)
    assert observed==json.loads(baseline.stdout)
    assert observed['public']=='ORIGINAL_PUBLIC_MODULE'
    assert trace.read_text()==('original\nobserver\n' if mode=='normal' else 'original\n')
    if mode=='original-error':assert 'EXAMPLE_ORIGINAL_STARTUP_ERROR' in result.stderr
    elif mode=='namespace-collision':assert 'WORKER_SECURITY_OBSERVER_UNAVAILABLE' in result.stderr
    else:assert not result.stderr
