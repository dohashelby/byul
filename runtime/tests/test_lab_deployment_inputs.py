"""Deployment uses the actual immutable refs and original Lab report, never substitute summaries."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from serving_runtime.mvp.lab_report import LabReportSource,REPORT_SCHEMA
from serving_runtime.mvp.management_listener import ManagementListenerConfig
from serving_runtime.mvp.policy_bundle import verify_policy_bundle
from tools.aws_lab_preflight import bind_lab_management,bind_lab_report,lab_management_scope

ROOT=Path(__file__).resolve().parents[1]
SCOPE={'target_ref':'target-approved','policy_ref':'a'*64,'run_id':'byul-run','environment':'aws-lab'}


@pytest.mark.parametrize('path',['deploy/gpu-lab/engine-management-sidecar.patch.yaml','deploy/gpu-lab-sglang/engine.yaml'])
def test_existing_sidecar_templates_receive_all_four_explicit_scope_flags(path):
    original=yaml.safe_load((ROOT/path).read_text());engine=bind_lab_management(original,SCOPE)
    args=next(c['args'] for c in engine['spec']['template']['spec']['containers'] if c['name']=='management-listener')
    for name,value in SCOPE.items():
        flag='--'+name.replace('_','-');assert args.count(flag)==1 and args[args.index(flag)+1]==value
    assert engine['spec']['template']['metadata']['labels']['guard.run']==SCOPE['run_id']
    config=ManagementListenerConfig.build(engine_url='http://127.0.0.1:8000',**SCOPE)
    assert config.target_ref=='target-approved' and config.policy_ref=='a'*64
    assert 'REPLACE_TARGET_REF' in str(original) and 'REPLACE_TARGET_REF' not in str(engine)


@pytest.mark.parametrize('arguments',[
    ['management-listener','--run-id','other'],
    ['management-listener','--run-id','byul-run','--run-id','byul-run'],
    ['management-listener','--run-id=byul-run','--run-id','byul-run'],
    ['management-listener','--run-id'],
])
def test_duplicate_or_different_scope_cannot_silently_rebind(arguments):
    engine={'spec':{'template':{'spec':{'containers':[{'name':'management-listener','args':arguments}]}}}}
    with pytest.raises(ValueError):bind_lab_management(engine,SCOPE)


def test_engine_run_and_scope_input_are_the_same():
    engine={'spec':{'template':{'metadata':{'labels':{'guard.run':'other'}},
        'spec':{'containers':[{'name':'management-listener','args':[]}]}}}}
    with pytest.raises(ValueError,match='guard.run'):bind_lab_management(engine,SCOPE)


def test_scope_is_derived_from_existing_bundle_verifier_not_live_actual_copy():
    path=ROOT.parent/'contracts/examples/bundle';now=datetime(2026,10,26,tzinfo=timezone.utc)
    original=verify_policy_bundle
    with patch('serving_runtime.mvp.policy_bundle.verify_policy_bundle',side_effect=lambda p:original(p,now=now)):
        value=lab_management_scope(path,run_id='byul-run',environment='aws-lab')
    approved=original(path,now=now)
    assert value['target_ref']==approved.target_ref and value['policy_ref']==approved.policy_ref['sha256']


def report():
    return {'schema':REPORT_SCHEMA,'run_id':'actual-k8s-lab-run','started_at':'2026-11-06T00:00:00Z',
        'finished_at':'2026-11-06T00:00:10Z','context':'byul-aws-lab','namespace':'isolated-lab',
        'overall':'unknown','cases':[{'id':case,'status':'unknown' if case=='L-05b' else 'failed' if case=='L-05c' else 'passed',
            'title':case,'summary':'original measured outcome','evidence':['raw-'+case+'.json'],
            'limitation':'observed unsupported capability' if case=='L-05b' else None}
            for case in ['L-01','L-02','L-03','L-04','L-05','L-06','L-05b','L-05c']]}


def gateway():return {'spec':{'template':{'spec':{'containers':[{'name':'gateway','args':['mvp-serve']}],
    'volumes':[]}}}}


def test_actual_lab_report_is_read_only_mounted_and_outcomes_are_preserved(tmp_path):
    path=tmp_path/'report.json';raw=json.dumps(report()).encode();path.write_bytes(raw)
    before=path.read_bytes();deployment,receipt=bind_lab_report(gateway(),host_directory=tmp_path)
    spec=deployment['spec']['template']['spec'];container=spec['containers'][0]
    assert container['args']==['mvp-serve','--lab-report','/runtime-lab/report.json']
    assert container['volumeMounts']==[{'name':'runtime-lab-report','mountPath':'/runtime-lab','readOnly':True}]
    assert spec['volumes'][0]['hostPath']['path']==str(tmp_path)
    assert receipt['source_sha256']==hashlib.sha256(before).hexdigest() and path.read_bytes()==before
    view=LabReportSource(path).read();assert view['state']=='ok'
    cases={c['id']:c for c in view['report']['cases']}
    assert cases['L-05b']['status']=='unknown' and cases['L-05c']['status']=='failed'
    assert receipt['report_run_id']=='actual-k8s-lab-run'


@pytest.mark.parametrize('document',[
    {'schema_version':'runtime-tetragon-verification/1.0'},
    {'schema':'byul-report/1.0','security':'UNKNOWN'},
    {**report(),'cases':[c for c in report()['cases'] if c['id']!='L-05c']},
])
def test_other_verifications_or_missing_optional_case_are_not_lab_report_substitutes(tmp_path,document):
    (tmp_path/'report.json').write_text(json.dumps(document))
    with pytest.raises(ValueError):bind_lab_report(gateway(),host_directory=tmp_path)


def test_conflicting_mount_or_flag_is_rejected_without_mutating_input(tmp_path):
    (tmp_path/'report.json').write_text(json.dumps(report()))
    original=gateway();original['spec']['template']['spec']['containers'][0]['volumeMounts']=[
        {'name':'other','mountPath':'/runtime-lab','readOnly':False}]
    before=deepcopy(original)
    with pytest.raises(ValueError):bind_lab_report(original,host_directory=tmp_path)
    assert original==before


@pytest.mark.parametrize('path,container,readonly',[
    ('deploy/gpu-lab/engine-management-sidecar.patch.yaml','management-listener',False),
    ('deploy/gpu-lab-sglang/engine.yaml','management-listener',False),
    ('deploy/gpu-lab/gateway.yaml','gateway',True),
])
def test_management_journal_is_connected_with_separate_writer_and_reader_mounts(path,container,readonly):
    spec=yaml.safe_load((ROOT/path).read_text())['spec']['template']['spec']
    target=next(c for c in spec['containers']if c['name']==container)
    mount=next(m for m in target['volumeMounts']if m['name']=='management-access')
    assert mount.get('readOnly',False) is readonly
    volume=next(v for v in spec['volumes']if v['name']=='management-access')
    assert volume['hostPath']=={'path':'/srv/serving/runtime/management-access','type':'Directory'}
    assert target['securityContext']['readOnlyRootFilesystem'] is True
    args=target['args']
    if container=='management-listener':
        assert args[args.index('--record-file')+1]==mount['mountPath']+'/access.jsonl'
        assert args[args.index('--heartbeat-file')+1]==mount['mountPath']+'/heartbeat.json'
        engine=next(c for c in spec['containers']if c['name']=='engine')
        assert all(m['name']!='management-access' for m in engine.get('volumeMounts',[]))
    else:
        assert 'management_listener='+mount['mountPath']+'/access.jsonl' in args
        assert 'management_listener='+mount['mountPath']+'/heartbeat.json' in args


@pytest.mark.parametrize('path', ['deploy/gpu-lab/gateway.yaml', 'deploy/lab/manifests/lab.yaml'])
def test_gateway_readiness_waits_for_the_configured_listener_without_auth_credentials(path):
    documents = list(yaml.safe_load_all((ROOT / path).read_text()))
    deployment = next(d for d in documents if d.get('kind') == 'Deployment'
                      and d.get('metadata', {}).get('name') == 'serving-gateway')
    gateway = next(c for c in deployment['spec']['template']['spec']['containers']
                   if c['name'] == 'gateway')
    port = int(gateway['args'][gateway['args'].index('--gateway-port') + 1])
    probe = gateway['readinessProbe']
    assert port == 8080
    command = probe['exec']['command']
    assert command[:4] == ['python3', '-I', '-S', '-c']
    assert len(command) == 5
    assert command[4] == "import socket; socket.create_connection(('127.0.0.1', 8080), timeout=0.5).close()"
    assert probe['periodSeconds'] == probe['timeoutSeconds'] == probe['failureThreshold'] == 1
    assert 'httpGet' not in probe and 'tcpSocket' not in probe
    assert 'livenessProbe' not in gateway


def test_gateway_loopback_probe_fails_before_listen_and_succeeds_after_listen():
    document = yaml.safe_load((ROOT / 'deploy/gpu-lab/gateway.yaml').read_text())
    command = next(c for c in document['spec']['template']['spec']['containers']
                   if c['name'] == 'gateway')['readinessProbe']['exec']['command']
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        invocation = [sys.executable, *command[1:4], command[4].replace('8080', str(port))]
        before = subprocess.run(invocation, capture_output=True, timeout=2)
        assert before.returncode != 0
        listener.listen(1)
        after = subprocess.run(invocation, capture_output=True, timeout=2)
        assert after.returncode == 0
