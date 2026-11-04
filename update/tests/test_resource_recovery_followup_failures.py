"""Final reports cannot erase a previously confirmed EXAMPLE deadline FAIL."""
import pytest
from guard.models import canonical_json,Policy,new_id,utcnow
from guard.core.select import select
from guard.core.run import RunConfig,run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_recovery_http import KubernetesRecoveryHTTP
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.store import Store
from guard.store.packs import put_pack,get_pack
from guard.store.bundle import export_bundle,restore_bundle,verify_complete
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_a1_resource_recovery_product import inputs


def product(tmp_path,group,native_target,monkeypatch,mode,branch):
    world,selection,values,pack=inputs(tmp_path,group,native_target,mode,branch)
    for name,value in [('target',world.target),('selection',selection)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    store=Store(tmp_path/'store');put_pack(store,pack.path)
    policy=Policy(id=new_id('POL'),data_origin='EXAMPLE',status='DRAFT',pack=pack.ref(),
        scope={'include':['LIF-11']},values={'LIF-11':values}).seal()
    store.put_many([group.env,policy]);plan=select(pack,[group.env],policy);store.put(plan)
    cfg={'resource_recovery':{'target':str(tmp_path/'target.json'),'selection':str(tmp_path/'selection.json'),
                             'prepared':str(group.manifest_path)}}
    evd,=run_plan(store,pack,plan,RunConfig(executors={'resource_recovery':'kubernetes'},adapter='vllm',raw=cfg))
    asof=utcnow();result,=judge_plan(store,pack,plan,asof);report=build_report(store,pack,plan,asof);store.put(report)
    export_bundle(store.root,tmp_path/'bundle.zip');restore_bundle(tmp_path/'bundle.zip',tmp_path/'restored')
    restored=Store(tmp_path/'restored');verify_complete(restored)
    again,=judge_plan(restored,get_pack(restored,pack.ref()),restored.resolve(plan.ref()),asof)
    assert again.ref()==result.ref()
    return evd,result


@pytest.mark.parametrize('boundary,code',[
    ('reader','RESOURCE_RECOVERY_READER_REPORT_UNAVAILABLE'),
    ('http','RESOURCE_RECOVERY_HTTP_REPORT_UNAVAILABLE')])
@pytest.mark.parametrize('branch',['runtime-failure','partial-initialization'])
def test_report_error_preserves_deadline_violation_and_saved_res(tmp_path,group,native_target,monkeypatch,boundary,code,branch):
    cls=KubernetesLifecycleReader if boundary=='reader' else KubernetesRecoveryHTTP
    original=cls.report
    calls=[]
    def failure(self):
        calls.append(True)
        original(self)
        raise OSError('EXAMPLE_PRIVATE_RECOVERY_REPORT')
    monkeypatch.setattr(cls,'report',failure)
    evd,result=product(tmp_path,group,native_target,monkeypatch,'retained',branch)
    assert calls
    assert result.verdicts.functional.value=='FAIL'
    assert evd.observations['recovery_absolute_deadline_violated'].value is True
    assert evd.execution.status.value=='ABORTED' and evd.execution.cleanup_errors
    assert code in evd.execution.abort_reason
    assert 'EXAMPLE_PRIVATE_RECOVERY_REPORT' not in canonical_json(evd.to_dict()).decode()


def test_actual_census_failure_diagnostic_reaches_abort_reason(tmp_path,group,native_target,monkeypatch):
    evd,result=product(tmp_path,group,native_target,monkeypatch,'census-failure','runtime-failure')
    assert result.verdicts.functional.value=='UNKNOWN'
    assert 'RESOURCE_RECOVERY_CENSUS_BINDING_UNAVAILABLE' in evd.execution.abort_reason
