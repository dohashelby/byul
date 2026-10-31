"""Closed envelope checks use immutable reviewed Git bytes, never packet hashes."""
from copy import deepcopy
from pathlib import Path
import subprocess
import pytest

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_worker_readiness import KubernetesWorkerReadinessHTTP, validate_snapshot, FLAGS
from tests.test_a1_worker_readiness_http import inputs, assert_safe
from tools.verify_req10_consumption import REVIEWED_COMMON_COMMIT


@pytest.fixture(scope='module')
def reviewed_sources():
    repo = Path(__file__).resolve().parents[2]
    def reviewed(path):
        return subprocess.check_output(['git', 'show', REVIEWED_COMMON_COMMIT + ':update/src/guard/' + path], cwd=repo)
    probe = reviewed('executors/stream_probe.py').decode('utf-8').replace('\r\n', '\n')
    wrapper = ("import math,os,sys,threading\nlimit=float(sys.argv[1])\n"
        "if not math.isfinite(limit) or not 0 < limit <= 120: sys.exit(124)\n"
        "watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n"
        + "exec(compile(" + repr(probe) + ",'guard-stream-probe','exec'),{'__name__':'__main__'})\n")
    return {'collector':sha256_hex(reviewed('runtime/kubernetes_worker_readiness.py')),
            'binding':sha256_hex(reviewed('runtime/kubernetes_startup_binding.py')), 'probe':sha256_hex(wrapper.encode())}


def captured(sources, mode='normal'):
    environment, world, policy = inputs(mode)
    collector = KubernetesWorkerReadinessHTTP(world.target, environment, runner=world.runner)
    expected = {'sample_id':'d'*32, 'environment_ref':environment.ref().model_dump(mode='json'),
        'target_sha256':policy['readiness_target_sha256'], 'session':policy['readiness_session'],
        'cohort_sha256':policy['readiness_cohort_sha256'], 'run_id':world.target['kubernetes']['run_id'],
        'binding_sha256':sha256_hex(canonical_json(world.target['kubernetes'])), 'expected_sources':deepcopy(sources)}
    return collector.sample(policy, sample_id='d'*32), expected


@pytest.mark.parametrize('mode',['normal','unready','transition','unknown-ready','ambiguous-ready','probe-not-ready',
    'engine-replaced-late','probe-replaced-late','namespace-late','canary-timeout','health-refused','health503',
    'canary-bad-json','canary-duplicate-model','partial-body','body-count','outer-returncode',
    'outer-truncated','cleanup-after-canary','late-final-api','wrong-response-id'])
def test_fixed_source_parser_roundtrip_preserves_safe_complete_and_partial_facts(reviewed_sources, mode):
    packet, expected = captured(reviewed_sources, mode)
    parsed = validate_snapshot(packet, **expected)
    assert parsed == packet
    assert_safe(parsed)
    parsed['requests'].clear()
    assert packet['requests']


@pytest.mark.parametrize('change',['raw','measured','trust','numeric-trust','source','unknown-source','sample',
    'environment','revision-bool','target','session','cohort','run','binding','request-id','rid-extra','slot',
    'status-bool','status-unknown','body-bytes-negative','body-bytes-large','normal-shape-type','missing-row',
    'duplicate-row','api-duplicate','api-other-stage','api-private','readiness-type','request-before',
    'missing-final','final-uncollected','false-collection','clock-unmarked','unknown-diagnostic','cleanup-raw',
    'limitations','clock-domain','reply-complete','health-claim','canary-health','missing-field'])
def test_forged_raw_context_sources_and_trust_are_rejected_without_echo(reviewed_sources, change):
    packet, expected = captured(reviewed_sources)
    private = 'EXAMPLE_UNTRUSTED_PRIVATE_VALUE'
    if change=='raw':packet['body']=private
    elif change=='measured':packet['data_origin']='MEASURED'
    elif change=='trust':packet[FLAGS[0]]=True
    elif change=='numeric-trust':packet[FLAGS[0]]=0
    elif change=='source':packet['collector_source_sha256']='f'*64
    elif change=='unknown-source':expected['expected_sources']['unknown']='f'*64
    elif change=='sample':expected['sample_id']='e'*32
    elif change=='environment':packet['environment_ref']['sha256']='f'*64
    elif change=='revision-bool':packet['environment_ref']['revision']=True
    elif change=='target':expected['target_sha256']='f'*64
    elif change=='session':expected['session']='e'*32
    elif change=='cohort':expected['cohort_sha256']='f'*64
    elif change=='run':expected['run_id']='other-run'
    elif change=='binding':expected['binding_sha256']='f'*64
    elif change=='request-id':packet['requests'][0]['request_id_sha256']='f'*64
    elif change=='rid-extra':packet['requests'][0]['request_id']=private
    elif change=='slot':packet['requests'][0]['slot']='lif09-before'
    elif change=='status-bool':packet['requests'][0]['outcome']=True
    elif change=='status-unknown':packet['requests'][0]['outcome']=private
    elif change=='body-bytes-negative':packet['requests'][0]['retained_body_bytes']=-1
    elif change=='body-bytes-large':packet['requests'][0]['retained_body_bytes']=131073
    elif change=='normal-shape-type':packet['requests'][1]['normal_shape_verified']='true'
    elif change=='missing-row':packet['requests'].pop(1)
    elif change=='duplicate-row':packet['requests'].insert(0,deepcopy(packet['requests'][0]))
    elif change=='api-duplicate':packet['api_readiness'].insert(0,deepcopy(packet['api_readiness'][0]))
    elif change=='api-other-stage':packet['api_readiness'][0]['stage']='other-before'
    elif change=='api-private':packet['api_readiness'][0]['env']=private
    elif change=='readiness-type':packet['api_readiness'][0]['engine_readiness']['pod_ready']=1
    elif change=='request-before':packet['requests'][0]['binding_before_verified']=False
    elif change=='missing-final':packet['api_readiness'].pop()
    elif change=='final-uncollected':packet['api_readiness'][-1]['collected']=False
    elif change=='false-collection':packet['sampling_complete']=False
    elif change=='clock-unmarked':packet['api_readiness'][3]['collected_at']='2000-01-01T00:00:00+00:00'
    elif change=='unknown-diagnostic':packet['diagnostic_codes']=[private]
    elif change=='cleanup-raw':packet['cleanup_errors']=[private]
    elif change=='limitations':packet['limitations']=[]
    elif change=='clock-domain':packet['collector_clock_domain']='native worker'
    elif change=='reply-complete':packet['requests'][0]['http_reply_complete']=False
    elif change=='health-claim':packet['requests'][0]['health_status_200']=False
    elif change=='canary-health':packet['requests'][1]['health_status_200']=True
    elif change=='missing-field':del packet['binding_sha256']
    with pytest.raises(ValueError,match='^WORKER_READINESS_SNAPSHOT_INVALID$') as error:
        validate_snapshot(packet, **expected)
    assert private not in str(error.value) and error.value.__cause__ is None


def test_intermediate_collector_clock_regression_keeps_facts_and_marks_gap(reviewed_sources, monkeypatch):
    from datetime import timedelta
    from guard.runtime import kubernetes_worker_readiness as module
    now = module.utcnow(); count = [0]
    def wall():
        count[0]+=1
        return now-timedelta(seconds=2) if count[0]==7 else now
    monkeypatch.setattr(module,'utcnow',wall)
    packet, expected = captured(reviewed_sources)
    assert 'WORKER_READINESS_COLLECTOR_CLOCK_UNVERIFIED' in packet['diagnostic_codes']
    assert packet['collection_gap'] and not packet['sampling_complete']
    assert len(packet['requests'])==3 and all(row['outcome']==200 for row in packet['requests'])
    assert validate_snapshot(packet,**expected)==packet
