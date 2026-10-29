"""REQ-10 EXAMPLE proposal wiring; actual request classes, synthetic inference.

No production registry or published pack changes. All local fixture objects
are EXAMPLE even when their validation input came from actual CPU execution.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import inspect
import io
import tarfile
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

BASE_SHA='c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
FRAMEWORK_SHA='91e84c778f4fc8ee1108eaf0551bf51f2f6a2061'
PINNED_PATHS=('update/src/guard','update/packs','update/uv.lock',
              'update/pyproject.toml','update/reference/framework')
SCENARIOS=('normal','violation_after_failure','missing','unsupported','artifact_failure')
CANARY='B1_PRIVATE_EXCEPTION_DO_NOT_SAVE'


def verify_snapshot(repo, commit, paths):
    """Require ancestry, clean index/worktree and exact pinned file bytes.

    Include ignored files: an ignored .py can shadow an otherwise clean import.
    Ordinary Python __pycache__ files are runtime caches, not added source files.
    """
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args])
    subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',commit,'HEAD'],check=True)
    if git('diff','--cached',commit,'--',*paths) or git('diff',commit,'--',*paths):
        raise ValueError('pinned source/index modified')
    with tarfile.open(fileobj=io.BytesIO(git('archive',commit,'--',*paths))) as archive:
        for entry in archive:
            target=repo/entry.name
            if entry.isdir():
                if target.is_symlink() or not target.is_dir():
                    raise ValueError('pinned source directory mismatch: '+entry.name)
            elif entry.issym():
                if not target.is_symlink() or target.readlink().as_posix()!=entry.linkname:
                    raise ValueError('pinned source symlink mismatch: '+entry.name)
            elif entry.isfile():
                if target.is_symlink() or not target.is_file() or target.read_bytes()!=archive.extractfile(entry).read():
                    raise ValueError('pinned source bytes mismatch: '+entry.name)
            else:
                raise ValueError('unsupported pinned source entry: '+entry.name)
    for options in (('--exclude-standard',),('--ignored','--exclude-standard')):
        for raw in git('ls-files','--others','-z',*options,'--',*paths).split(b'\0'):
            if not raw:continue
            path=Path(raw.decode())
            if path.parent.name=='__pycache__' and path.suffix=='.pyc':continue
            raise ValueError('pinned source untracked/ignored file: '+str(path))


def verify_baseline(repo):
    verify_snapshot(repo,BASE_SHA,PINNED_PATHS)
    pack=yaml.safe_load((repo/'update/packs/PACK-BASELINE/pack.yaml').read_text())
    framework=json.loads((repo/'update/reference/framework/UPSTREAM.json').read_text())
    if (pack['pack_id'],pack['revision'])!=('PACK-BASELINE',31):
        raise ValueError('PACK31 baseline mismatch')
    if framework['commit']!=FRAMEWORK_SHA:
        raise ValueError('framework baseline mismatch')


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
    verify_baseline(repo)
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=SOURCE_COMMIT:raise ValueError('reference SHA mismatch')
    verify_snapshot(source,SOURCE_COMMIT,('vllm',))
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.exceptions import VLLMValidationError
    for cls in (ChatCompletionRequest,VLLMValidationError):
        if not Path(inspect.getfile(cls)).resolve().is_relative_to(source/'vllm'):raise ValueError('reference import mismatch')
    for obj in (judge_plan,build_report,RunConfig,run_plan,select,registry,
                StructuredToolsExecutor,judge_params,project_validator,validate_request_class,
                load_pack,sha256_hex,Store,put_pack,get_pack):
        if not Path(inspect.getfile(obj)).resolve().is_relative_to(repo/'update/src/guard'):raise ValueError('own product import mismatch')
    for obj in (baseline_pack,environment,policy,completion_response):
        if not Path(inspect.getfile(obj)).resolve().is_relative_to(repo/'update/tests'):raise ValueError('own fixture import mismatch')
    args.output.mkdir(parents=True,exist_ok=False)
    outcomes=validate_request_class(ChatCompletionRequest,VLLMValidationError,model='fixture-model')
    assert outcomes=={'none':'accepted','auto':'accepted','required':'accepted','named-only':'accepted','named-json':'rejected'}
    summary={'base_sha':BASE_SHA,'pack_revision':31,'framework_sha':FRAMEWORK_SHA,'source_sha':SOURCE_COMMIT,'actual_request_class_outcomes':outcomes,
        'inference_transport_origin':'EXAMPLE','production_registration_changed':False,'installed_target':None,
        'cases':[run_case(args.output/s,s,outcomes) for s in SCENARIOS]}
    summary['source_file_bytes_sha256']={str(f.relative_to(repo)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (
        Path(__file__),repo/'update/src/guard/executors/structured_tools.py',repo/'update/src/guard/core/run.py',repo/'update/src/guard/core/judge.py')}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2));print('Actual class CPU + five EXAMPLE product-path scenarios passed')


if __name__=='__main__':main()
