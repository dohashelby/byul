"""LAB ownership preserves ENV origin; reader/transport doubles run no engine."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from guard.models import DataOrigin, Environment, canonical_json, sha256_hex
from guard.collectors import lifecycle
from guard.executors import kubernetes_lifecycle_capture, lifecycle_session
from serving_runtime.mvp import native_receipts


def tool():
    path=Path(__file__).resolve().parents[1]/'tools/update_engine_link.py'
    spec=importlib.util.spec_from_file_location('capture_origin_tool',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def environment(origin):
    return Environment(id='ENV-origin',created_at=datetime.now(timezone.utc),data_origin=DataOrigin(origin),
                       engine={'name':'vllm','version':'0.31.0'},facts={}).seal()


def inputs(tmp_path,origin):
    env=environment(origin)
    target={'lab':True}
    for name,value in [('env',env.model_dump(mode='json')),('target',target)]:
        (tmp_path/name).write_text(json.dumps(value))
    return env,target


def fake_reader(monkeypatch,injected):
    class Reader:
        def __init__(self,*args,**kwargs):
            if injected is not None:self.injected=injected
            self.binding={'engine':{'container':'engine','image':'image'}}
            self.owner_identity=self.read_file=self.image_check=None
        def snapshot(self):pass
        def report(self):return {'collection_gap':False,'injected_runner':getattr(self,'injected',True)}
    monkeypatch.setattr(kubernetes_lifecycle_capture,'KubernetesLifecycleReader',Reader)
    monkeypatch.setattr(lifecycle,'load_prepared',lambda _: {'session':'a'*32})


@pytest.mark.parametrize('origin',['MEASURED','DECLARED','EXAMPLE'])
@pytest.mark.parametrize('injected',[False,True,None],ids=['real-reader','injected-reader','unspecified-test-reader'])
def test_capture_preserves_env_origin_only_for_explicit_real_reader(tmp_path,monkeypatch,origin,injected):
    _,target=inputs(tmp_path,origin)
    fake_reader(monkeypatch,injected)
    monkeypatch.setattr(lifecycle_session,'capture_session',lambda *args,**kwargs:{'session':'a'*32})
    output=tmp_path/'capture.json'
    assert tool().main(['capture','--environment',str(tmp_path/'env'),'--target',str(tmp_path/'target'),
                       '--prepared','prepared','--output',str(output)])==0
    captured=json.loads(output.read_text())
    assert target['lab']is True
    assert captured['data_origin']==(origin if injected is False else 'EXAMPLE')
    assert captured['environment_ref']==environment_ref(json.loads((tmp_path/'env').read_text()))


def environment_ref(raw):
    return Environment.model_validate(raw).ref().model_dump(mode='json')


def watch(tmp_path,monkeypatch,origin,injected,recorded_origin):
    env,target=inputs(tmp_path,origin)
    fake_reader(monkeypatch,False)
    transport={'collection_gap':False}
    if injected is not None:transport['injected_runner']=injected
    source={'schema':'byul-runtime-native-capture/1','data_origin':recorded_origin,
            'environment_ref':env.ref().model_dump(mode='json'),'target_sha256':sha256_hex(canonical_json(target)),
            'transport':transport,'session_sealed':True,
            'packet':{'session':'a'*32,'before':{'metadata':{'sealed':True}},'after':{'metadata':{'sealed':True}},'recordings':[]}}
    packet=tmp_path/'packet';packet.write_text(json.dumps(source))
    records=tmp_path/'records';records.write_bytes(b'')
    archive=tmp_path/'archive';archive.mkdir(mode=0o700)
    monkeypatch.setattr(native_receipts,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    value=tool()
    monkeypatch.setattr(value.os,'geteuid',lambda:0)
    monkeypatch.setattr(value.os,'fchown',lambda *args:None)
    original=Path.lstat
    def owned(self,*args,**kwargs):
        fields=list(original(self,*args,**kwargs));fields[4]=0
        return os.stat_result(fields)
    monkeypatch.setattr(Path,'lstat',owned)
    output=tmp_path/'current.json'
    value.main(['watch','--environment',str(tmp_path/'env'),'--target',str(tmp_path/'target'),'--prepared','prepared',
                '--records',str(records),'--output',str(output),'--capture-dir',str(archive),'--reader-gid','10000',
                '--max-delay-ms','5000','--required-scopes','worker_batch','--packet',str(packet),
                '--packet-sha256',hashlib.sha256(packet.read_bytes()).hexdigest(),'--once'])
    return json.loads(output.read_text())


@pytest.mark.parametrize('origin',['MEASURED','DECLARED','EXAMPLE'])
@pytest.mark.parametrize('injected',[False,True])
def test_watch_accepts_matching_transport_and_env_ceiling(tmp_path,monkeypatch,origin,injected):
    expected='EXAMPLE'if injected else origin
    result=watch(tmp_path,monkeypatch,origin,injected,expected)
    assert result['state']=='ok'
    assert result['source_data_origin']==expected


@pytest.mark.parametrize('origin,injected,recorded',[('DECLARED',False,'MEASURED'),('MEASURED',True,'MEASURED'),
                                                    ('EXAMPLE',False,'MEASURED'),('MEASURED',None,'MEASURED')])
def test_watch_rejects_upgrades_and_missing_transport_provenance(tmp_path,monkeypatch,origin,injected,recorded):
    result=watch(tmp_path,monkeypatch,origin,injected,recorded)
    assert result['state']=='unavailable'
    assert result['entries']==[]


def test_legacy_unspecified_transport_retains_only_example_ceiling(tmp_path,monkeypatch):
    result=watch(tmp_path,monkeypatch,'MEASURED',None,'EXAMPLE')
    assert result['state']=='ok'
    assert result['source_data_origin']=='EXAMPLE'
