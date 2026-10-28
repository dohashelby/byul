"""REQ-10 EXAMPLE proposal wiring; actual request classes, synthetic inference.

No production registry or published pack changes. All local fixture objects
are EXAMPLE even when their validation input came from actual CPU execution.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import pytest
import yaml
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.structured_tools import SOURCE_COMMIT,StructuredToolsExecutor,judge_params,project_validator,validate_request_class
from guard.models import load_pack,sha256_hex
from guard.store import Store
from guard.store.packs import put_pack,get_pack
from tests.builders import baseline_pack,environment,policy,completion_response

BASE_SHA='876122211fe47d12f8fcbd69dab5883974ace168'
SCENARIOS=('normal','violation_after_failure','missing','unsupported','artifact_failure')
CANARY='B1_PRIVATE_EXCEPTION_DO_NOT_SAVE'


def proposal_fixture(folder):
    data=baseline_pack().checks['REQ-10'].model_dump(mode='json')
    data.update(executor='structured_tools',policy_params=[],functional_probes=[],functional_scope=None,
        judge={'plugin':'rule_table','params':judge_params()},
        limitations=['EXAMPLE scoped proposal; no engine or installed-target measurement.'])
    data['required_observations']=[{'key':k,'type':t} for k,t in (
        ('structured_tools_http_outcomes','dict[str, outcome]'),('structured_tools_controls_planned','list[str]'),
        ('structured_tools_controls','dict[str, outcome]'),('structured_tools_validator_violation','bool'),('runner_consumption_verified','bool'))]
    raw=yaml.safe_dump(data,allow_unicode=True,sort_keys=False).encode()
    path=folder/'checks/REQ-10.yaml';path.parent.mkdir(parents=True);path.write_bytes(raw)
    manifest={'pack_id':'PACK-B1-EXAMPLE-STRUCTURED','revision':1,'engines':['vllm'],'checks':['REQ-10'],
        'files_sha256':[{'path':'checks/REQ-10.yaml','sha256':sha256_hex(raw)}],'review':{'status':'DRAFT'}}
    (folder/'pack.yaml').write_text(yaml.safe_dump(manifest,sort_keys=False))
    return load_pack(folder)


def run_case(folder,scenario,outcomes):
    if scenario not in SCENARIOS:raise ValueError('unsupported scenario')
    folder.mkdir(parents=True,exist_ok=False);pack=proposal_fixture(folder/'EXAMPLE-definition')
    observed=deepcopy(outcomes)
    if scenario in ('violation_after_failure','artifact_failure'):observed['named-json']='accepted'
    if scenario in ('missing','unsupported'):observed={}
    env=environment(engine={'name':'vllm','version':'0.31.0'},model={'id':'fixture-model'})
    target=folder/'target.json';target.write_text(json.dumps({'lab':True,'origins':{'trusted':{'network':'EXAMPLE'}},'engine':{'trusted_url':'http://engine.invalid:8000'}}))
    settings={'target':str(target),'source_commit':SOURCE_COMMIT,'supported':scenario!='unsupported'};calls=[]
    def probe(network,requests,timeout):
        replies=[]
        for request in requests:
            calls.append(request['id'])
            if scenario=='violation_after_failure' and request['id'].endswith('-after'):raise TimeoutError(CANARY)
            replies.append({'id':request['id'],**completion_response(request,400 if request['id'].endswith('-named-json') else 200)})
        return replies
    class ScopedFixture(StructuredToolsExecutor):
        def collect(self,ctx):
            obs,normal,challenges,summary=super().collect(ctx);obs.update(project_validator(observed))
            return obs,normal,challenges,summary
    store=Store(folder/'store');pol=policy(pack.ref(),scope={'include':['REQ-10']},values={})
    store.put_many([env,pol]);plan=select(pack,[env],pol);store.put(plan);put_pack(store,pack.path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry.EXECUTORS,'structured_tools:b1-example',lambda raw:ScopedFixture(settings,container_probe=probe,environ={}))
        if scenario=='artifact_failure':
            def unavailable(*args,**kwargs):raise OSError(CANARY)
            patch.setattr(store,'put_artifact',unavailable)
        evd,=run_plan(store,pack,plan,RunConfig(executors={'structured_tools':'b1-example'},adapter='vllm'))
    result,=judge_plan(store,pack,plan,as_of=env.created_at)
    rpt=build_report(store,pack,plan,as_of=env.created_at);store.put(rpt)
    replay,=judge_plan(store,get_pack(store,plan.inputs.pack),plan,as_of=env.created_at)
    expected='FAIL' if scenario in ('violation_after_failure','artifact_failure') else 'UNKNOWN'
    assert result.verdicts.security.value==expected and replay.verdicts==result.verdicts
    assert evd.observations['runner_consumption_verified'].value is False
    assert evd.data_origin.value=='EXAMPLE' and not rpt.official and store.verify()==[]
    assert all(CANARY not in p.read_text(errors='replace') for p in store.root.rglob('*') if p.is_file() and p.suffix in ('.json','.jsonl','.txt'))
    assert all(CANARY not in (store.root/a.path).read_text() for a in evd.artifacts)
    if scenario=='normal':assert evd.normal_control.succeeded is True and result.verdicts.functional.value=='PASS'
    if scenario=='violation_after_failure':assert evd.normal_control.succeeded is False
    if scenario=='artifact_failure':assert evd.execution.status.value=='ABORTED'
    if scenario=='unsupported':assert not calls
    return {'scenario':scenario,'origin':'EXAMPLE','security':expected,'functional':result.verdicts.functional.value,
        'execution':evd.execution.status.value,'normal_control':evd.normal_control.succeeded,'http_fixture_calls':len(calls),
        'definition':pack.ref().model_dump(mode='json'),'evidence':evd.ref().model_dump(mode='json'),
        'result':result.ref().model_dump(mode='json'),'report':rpt.ref().model_dump(mode='json'),
        'exact_ref_rejudge_preserved':True,'store_verified':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    import sys
    if sys.flags.optimize:raise ValueError("normal Python required for verification assertions")
    repo=Path(__file__).resolve().parents[2];source=args.source.resolve()
    subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',BASE_SHA,'HEAD'],check=True)
    if subprocess.check_output(['git','-C',str(repo),'diff',BASE_SHA,'--','update/src/guard','update/packs','update/uv.lock'],text=True):raise ValueError('pinned common/executor source must remain unchanged')
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=SOURCE_COMMIT:raise ValueError('reference SHA mismatch')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--','vllm'],text=True):raise ValueError('reference modified')
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.exceptions import VLLMValidationError
    if not Path(inspect.getfile(ChatCompletionRequest)).resolve().is_relative_to(source):raise ValueError('reference import mismatch')
    for fn in (run_plan,project_validator,validate_request_class):
        if not Path(inspect.getfile(fn)).resolve().is_relative_to(repo/'update'):raise ValueError('own product import mismatch')
    args.output.mkdir(parents=True,exist_ok=False)
    outcomes=validate_request_class(ChatCompletionRequest,VLLMValidationError,model='fixture-model')
    assert outcomes=={'none':'accepted','auto':'accepted','required':'accepted','named-only':'accepted','named-json':'rejected'}
    summary={'base_sha':BASE_SHA,'source_sha':SOURCE_COMMIT,'actual_request_class_outcomes':outcomes,
        'inference_transport_origin':'EXAMPLE','production_registration_changed':False,'installed_target':None,
        'cases':[run_case(args.output/s,s,outcomes) for s in SCENARIOS]}
    summary['source_file_bytes_sha256']={str(f.relative_to(repo)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (
        Path(__file__),repo/'update/src/guard/executors/structured_tools.py',repo/'update/src/guard/core/run.py',repo/'update/src/guard/core/judge.py')}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2));print('Actual class CPU + five EXAMPLE product-path scenarios passed')


if __name__=='__main__':main()
