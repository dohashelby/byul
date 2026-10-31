"""Real B source gate + public A HTTP/parser + B main; only transport is EXAMPLE."""
from datetime import timedelta
import json
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import pytest
import yaml

from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime import kubernetes_http
from guard.runtime.kubernetes_worker_readiness import FLAGS
from guard.store import Store
from tools import b_lif07_readiness as tool
from tools import b_lif07_native_capture as helper
from tools import verify_req10_consumption as gate
from tests.test_b_lif07_readiness import case, PRIVATE
from tests.test_b_lif07_http_readiness import World, A_PRIVATE


@pytest.mark.parametrize('mode',['unready','partial-body','cleanup-after-canary','attachment'])
def test_real_B_gate_public_HTTP_main_exact_refs_and_saved_pack(case,tmp_path,monkeypatch,capsys,mode):
    case['manifest']['request']['messages']=[{'role':'user','content':'hello'}]
    case['args']['manifest'].write_bytes(canonical_json(case['manifest']))
    world=World(case['target'],'unready' if mode=='attachment' else mode)
    before=tool._inventory(case['source'].root)
    # No source gate/API/parser replacement: only the original bounded runner
    # is substituted with owned fake API/HTTP metadata.
    monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    processes=[];popen=subprocess.Popen
    def local_git_only(argv,*args,**kwargs):
        assert type(argv) in (list,tuple) and Path(str(argv[0])).name.lower() in {'git','git.exe'}
        processes.append('local Git source metadata')
        return popen(argv,*args,**kwargs)
    def no_socket(*args,**kwargs):
        raise AssertionError('unexpected actual target socket')
    monkeypatch.setattr(subprocess,'Popen',local_git_only)
    monkeypatch.setattr(socket,'socket',no_socket)
    if mode=='attachment':
        original=tool._write
        def unavailable(path,value):
            if path.name=='worker-readiness.json':raise OSError(PRIVATE)
            return original(path,value)
        monkeypatch.setattr(tool,'_write',unavailable)
    # A new common integration is not an implicit B approval. Exercise the
    # real closed gate and exact product refs until B reviews that fixed pair.
    if gate.REVIEWED_COMMON_COMMIT not in helper.APPROVED_REVIEWED_COMMON_COMMITS:
        proof=gate.verify(Path(__file__).resolve().parents[2])
        assert proof['files_verified']==390 and proof['paths_excluded']==[] and not proof['runtime_hash_approval']
        with pytest.raises(ValueError,match='^LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE$'):helper.verify_source()
        now=utcnow();argv=['--collect-http']
        window={'http_validity_start':(now-timedelta(seconds=30)).isoformat(),'http_validity_end':(now+timedelta(minutes=3)).isoformat()}
        for key,value in {**case['args'],**window}.items():argv+=['--'+('plan' if key=='plan_id' else key.replace('_','-')),str(value)]
        tool.main(argv);summary=json.loads(capsys.readouterr().out);out=case['args']['output']
        assert world.execs==[] and summary['requests']==summary['http_requests_attempted']==0
        # B defines this flag as collect_http, even when the source gate stops
        # that invocation before any HTTP request or target exec.
        assert summary['http_observation_invoked'] is True and summary['source_unchanged'] and summary['exact_refs_restored']
        record=summary['safe_facts'] if mode=='attachment' else json.loads((out/'worker-readiness.json').read_text(encoding='utf-8'))
        observation=record['http_observation']
        assert observation['gaps']==['HTTP_SOURCE_UNAVAILABLE'] and observation['snapshot'] is None
        assert observation['source_validation'] is None and observation['sample_attempted'] is False
        assert record['target_io_attempted'] is False and record['http_collection_attempted'] is False
        assert tool._inventory(case['source'].root)==before and processes
        left=Store.read_only(out/'store');right=Store.read_only(out/'restored')
        objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in left.iter_kind(kind)]
        assert len(objects)==6 and left.verify()==right.verify()==[]
        assert all(right.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
        result,=left.iter_kind('RES');report,=left.iter_kind('RPT');plan,=left.iter_kind('PLAN')
        assert result.verdicts.model_dump(mode='json')=={key:'UNKNOWN' for key in ('security','functional','performance')}
        runner=CliRunner();checked=runner.invoke(app,['judge','--plan',plan.id,'--as-of',report.created_at.isoformat(),'--store',str(out/'restored')])
        assert checked.exit_code==0
        checked=runner.invoke(app,['verify','--store',str(out/'restored')]);assert checked.exit_code==0
        final=Store.read_only(out/'restored');assert final.verify()==[]
        assert all(final.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
        assert [obj.ref() for obj in final.iter_kind('RES')]==[result.ref()]
        saved=tmp_path/'product';shutil.copytree(out,saved)
        receipt={'case':mode,'source_gate':proof,'B_pair_approved':False,'requests':0,
            'source_gate_API_parser_mocked':False,'actual_target_worker_GPU':False,
            'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
            'original_export_sha256':sha256_hex((saved/'evidence.zip').read_bytes()),'saved_pack_rejudge_equal':True}
        (tmp_path/'a1-product-receipt.json').write_bytes(canonical_json(receipt))
        return
    proof=helper.verify_source()
    assert proof['files_verified']==387 and proof['reviewed_common_commit']=='f277fecd6df1e59505c0ad731032593f6fb22885'
    assert proof['runtime_hash_approval'] is False and proof['paths_excluded']==[]
    now=utcnow();window={'http_validity_start':(now-timedelta(seconds=30)).isoformat(),
        'http_validity_end':(now+timedelta(minutes=3)).isoformat()}
    argv=['--collect-http']
    for key,value in {**case['args'],**window}.items():
        argv+=['--'+('plan' if key=='plan_id' else key.replace('_','-')),str(value)]
    tool.main(argv);summary=json.loads(capsys.readouterr().out)
    out=case['args']['output']
    record=(summary['safe_facts'] if mode=='attachment' else json.loads((out/'worker-readiness.json').read_text(encoding='utf-8')))
    observation=record['http_observation'];packet=observation['snapshot']
    attempts={'unready':3,'partial-body':1,'cleanup-after-canary':2,'attachment':3}[mode]
    assert len(world.execs)==summary['requests']==summary['http_requests_attempted']==attempts
    assert summary['http_observation_invoked'] and summary['exact_refs_restored'] and summary['source_unchanged']
    assert summary['capture_origin']=='EXAMPLE' and summary['actual_target_executed'] is False
    assert summary['source_authentication_verified'] is False and summary['full_scope_coverage'] is False
    assert observation['source_validation']['reviewed_common_commit']==proof['reviewed_common_commit']
    assert observation['source_validation']['runtime_hash_approval'] is False
    assert observation['report_validated'] is True
    assert packet['collector_source_sha256']==helper.HTTP_EXPECTED_SOURCES['collector']
    assert packet['binding_source_sha256']==helper.HTTP_EXPECTED_SOURCES['binding']
    assert packet['probe_source_sha256']==helper.HTTP_EXPECTED_SOURCES['probe']
    assert all(packet[k] is False for k in FLAGS)
    assert packet['requests'][0]['outcome']==200
    assert packet['collection_gap']==(mode in {'partial-body','cleanup-after-canary'})
    if mode in {'unready','attachment'}:
        assert all(row['engine_readiness_before']=={'pod_ready':False,'container_ready':False} for row in packet['requests'])
    if mode=='cleanup-after-canary':
        assert packet['cleanup_errors'] and packet['requests'][1]['outcome']==200
    if mode=='partial-body':assert packet['requests'][0]['http_reply_complete'] is False
    assert PRIVATE not in json.dumps(record) and A_PRIVATE not in json.dumps(record)
    entry,=yaml.safe_load((out/'manual-evidence.yaml').read_text(encoding='utf-8'))['checks']
    assert entry['normal_control']['result']=='not_attempted' and all(not row['attempted'] for row in entry['challenges'])
    assert 'HTTP 결과' in entry['basis']
    if mode=='attachment':
        assert 'LIF07_SAFE_FACTS=' in entry['observation_notes'][0]['answer'] and not (out/'worker-readiness.json').exists()
    assert tool._inventory(case['source'].root)==before and processes
    left=Store.read_only(out/'store');right=Store.read_only(out/'restored')
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in left.iter_kind(kind)]
    assert len(objects)==6 and left.verify()==right.verify()==[]
    assert all(right.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    result,=left.iter_kind('RES');report,=left.iter_kind('RPT');plan,=left.iter_kind('PLAN');policy,=left.iter_kind('POL')
    assert not policy.exceptions
    assert result.verdicts.model_dump(mode='json')=={key:'UNKNOWN' for key in ('security','functional','performance')}
    # Original B run already restored/rejudged with its exact original pack.
    # Also resolve the saved pack through the existing A CLI, without --pack.
    runner=CliRunner()
    checked=runner.invoke(app,['judge','--plan',plan.id,'--as-of',report.created_at.isoformat(),'--store',str(out/'restored')])
    assert checked.exit_code==0,(checked.output,type(checked.exception).__name__ if checked.exception else None)
    checked=runner.invoke(app,['verify','--store',str(out/'restored')]);assert checked.exit_code==0
    final=Store.read_only(out/'restored');assert final.verify()==[]
    assert all(final.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    assert [r.ref() for r in final.iter_kind('RES')]==[result.ref()]
    assert not any(name=='torch' or name.startswith('torch.') or name=='vllm' or name.startswith('vllm.') for name in sys.modules)
    bundle=out/'evidence.zip'
    saved=tmp_path/'product';shutil.copytree(out,saved)
    assert (saved/'evidence.zip').read_bytes()==bundle.read_bytes()
    receipt={'case':mode,'source_gate':proof,'capture_origin':'EXAMPLE','requests':attempts,
        'actual_target_worker_GPU':False,'source_gate_API_parser_mocked':False,'transport_substituted':True,
        'exact_refs':[o.ref().model_dump(mode='json') for o in objects],
        'original_export_bundle':{'path':str(saved/'evidence.zip'),'original_run_path':str(bundle),'bytes':bundle.stat().st_size,'sha256':sha256_hex(bundle.read_bytes())},
        'saved_pack_rejudge_equal':True,'as_of':report.created_at.isoformat(),
        'as_of_basis':'Stored RPT creation; policy exceptions empty; no original B timestamp equivalence claim'}
    (tmp_path/'a1-product-receipt.json').write_bytes(canonical_json(receipt))
