"""기존 native 파일 수집의 후속 transport report·정리 실패 보존 회귀."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import native_weight_files as collector
from guard.executors import weight_file_capture as files
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory
from tests.test_native_weight_files import inputs, Reader, CANARY


@pytest.mark.parametrize('report', [
    {'profile':'EXAMPLE-weight-read','collection_gap':True},
    {'profile':'EXAMPLE-weight-read','collection_gap':False,'parent_cleanup_unverified':True},
])
@pytest.mark.parametrize('file_mode', [None,'mismatch'])
def test_default_reader_report_gap_preserves_independent_file_packets(inputs,monkeypatch,report,file_mode):
    value=deepcopy(inputs.value);value['context']['transport']='kubernetes'
    reader=Reader(inputs.owner,file_mode)
    monkeypatch.setattr(files,'KubernetesWeightReader',lambda *a,**kw:
        SimpleNamespace(read=reader.read,report=lambda:deepcopy(report)))
    result=collector.collect(value,max_file_bytes=1024)
    assert result['collection_gap'] and 'WEIGHT_FILE_TRANSPORT_INCOMPLETE' in result['diagnostic_codes']
    assert len(result['files'])==2 and all(r['capture_verified'] and r['capture'] for r in result['files'])
    assert all(r['content_matches_reference'] is (file_mode!='mismatch') for r in result['files'])
    assert result['transport']==report and result['security']=='UNKNOWN' and result['data_origin']=='EXAMPLE'
    assert not result['producer_authenticated'] and not result['actual_consumption_verified']


@pytest.mark.parametrize('damage', ['exception','non-callable','null','list','text','int-gap','null-cleanup'])
def test_report_failure_retains_packets_and_only_fixed_diagnostic(inputs,monkeypatch,damage):
    value=deepcopy(inputs.value);value['context']['transport']='kubernetes'
    reader=Reader(inputs.owner)
    def report():
        if damage=='exception':raise OSError(CANARY)
        return {'null':None,'list':[],'text':CANARY,'int-gap':{'collection_gap':1},
                'null-cleanup':{'parent_cleanup_unverified':None}}[damage]
    monkeypatch.setattr(files,'KubernetesWeightReader',lambda *a,**kw:
        SimpleNamespace(read=reader.read,report=True if damage=='non-callable' else report))
    result=collector.collect(value,max_file_bytes=1024)
    assert result['collection_gap'] and result['diagnostic_codes']==['WEIGHT_FILE_TRANSPORT_REPORT_UNAVAILABLE']
    assert len(result['files'])==2 and all(r['capture_verified'] and r['capture'] for r in result['files'])
    assert result['transport']=={'profile':'kubernetes-owned-weight-file-read-v1','collection_gap':True}
    assert CANARY not in json.dumps(result) and result['security']=='UNKNOWN'
    assert not result['final_model_verified'] and not result['whole_run_coverage_verified']


@pytest.mark.parametrize('report', [None,{}, {'collection_gap':False,'parent_cleanup_unverified':False}])
def test_absent_or_complete_report_preserves_legacy_capture_contract(inputs,monkeypatch,report):
    reader=Reader(inputs.owner)
    def factory(*a,**kw):
        return SimpleNamespace(read=reader.read,**({} if report is None else {'report':lambda:deepcopy(report)}))
    monkeypatch.setattr(files,'DockerWeightReader',factory)
    from guard.executors import owned_capture
    monkeypatch.setattr(owned_capture,'create_capture_client',lambda *a:object())
    result=collector.collect(inputs.value,max_file_bytes=1024)
    assert not result['collection_gap'] and not result['diagnostic_codes']
    assert all(r['capture_verified'] for r in result['files'])
    assert result['transport']==({'profile':'docker-owned-weight-file-read-v1'} if report is None else report)


def test_injected_reader_report_remains_unconsumed(inputs):
    result=collector.collect(inputs.value,reader=Reader(inputs.owner),max_file_bytes=1024)
    assert result['transport']=={'profile':'injected-owned-weight-read-v1'}
    assert not result['collection_gap'] and result['data_origin']=='EXAMPLE'


@pytest.mark.parametrize('mode',['gap','report-error','artifact-error'])
def test_public_capture_CLI_preserves_packets_and_incomplete_metadata(inputs,monkeypatch,tmp_path,mode):
    from guard.cli import app
    from guard.executors import owned_capture
    from typer.testing import CliRunner
    reader=Reader(inputs.owner,'mismatch' if mode!='gap' else None)
    def report():
        if mode=='report-error':raise OSError(CANARY)
        return {'profile':'EXAMPLE-owned-transport-report','collection_gap':True,'parent_cleanup_unverified':True}
    monkeypatch.setattr(owned_capture,'create_capture_client',lambda *a:object())
    monkeypatch.setattr(files,'DockerWeightReader',lambda *a,**kw:SimpleNamespace(read=reader.read,report=report))
    output=tmp_path/'transport-capture.json'
    if mode=='artifact-error':
        original=Path.open
        def open_file(path,*a,**kw):
            if path==output and a and a[0]=='x':raise OSError(CANARY)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    flags=('--env','--target','--prepared','--context','--model-manifest','--native-session')
    argv=['capture-native-weight-files',*[v for pair in zip(flags,map(str,inputs.paths)) for v in pair],
          '--output',str(output),'--max-file-bytes','1024','--execute']
    result=CliRunner().invoke(app,argv)
    assert result.exit_code==(1 if mode=='artifact-error' else 0)
    receipt=json.loads(result.stdout)
    if mode=='artifact-error':
        assert not output.exists() and receipt['artifact_saved'] is False
        assert receipt['diagnostic_codes']==['WEIGHT_FILE_ARTIFACT_UNAVAILABLE']
        value=receipt['capture']
    else:
        assert receipt['artifact_saved'] is True
        value=json.loads(output.read_text(encoding='utf-8'))
    assert value['collection_gap'] and value['data_origin']=='EXAMPLE' and value['security']=='UNKNOWN'
    assert len(value['files'])==2 and all(r['capture_verified'] and r['capture'] for r in value['files'])
    assert all(r['content_matches_reference'] is (mode=='gap') for r in value['files'])
    assert not value['actual_consumption_verified'] and not value['final_model_verified']
    assert CANARY not in result.output and CANARY not in json.dumps(value)
    if mode=='gap':
        fixture=tmp_path/'installed-inputs';fixture.mkdir()
        for name,path in zip(('environment','target','prepared','context','manifest','native'),inputs.paths):
            (fixture/(name+'.json')).write_bytes(path.read_bytes())
        for name in inputs.case.prepared['assets']:
            (fixture/name).write_bytes((inputs.paths[2].parent/name).read_bytes())
        (fixture/'owner.json').write_text(json.dumps(inputs.owner),encoding='utf-8')
