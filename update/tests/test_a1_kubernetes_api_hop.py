"""Policy/source/binding boundary for the requested existing PLT08 consumer."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime import kubernetes_api_hop as hop
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE, PROFILE
from tests.test_kubernetes_functional_control import World, response
from tests.test_kubernetes_request_binding import env
from tests.test_kubernetes_sampling import PRIVATE
from tests.builders import baseline_pack


def setup():
    world=World()
    transport=KubernetesHTTP(world.target,env(),duration=30,runner=world.runner,profile=REQUEST_BINDING_PROFILE)
    now=utcnow()
    policy={'api_hop_scope':hop.SCOPE,'api_environment_sha256':env().ref().sha256,
        'api_target_sha256':transport.target_sha256,'api_binding_sha256':sha256_hex(canonical_json(transport.binding)),
        'api_ip':transport.endpoint.hostname,'api_port':transport.endpoint.port,'plaintext_forbidden':True,
        'validity_start':(now-timedelta(minutes=1)).isoformat(),'validity_end':(now+timedelta(minutes=1)).isoformat()}
    return world,transport,policy


@pytest.mark.parametrize('forbidden',[False,True])
def test_same_live_normal_entry_returns_only_bound_hop_metadata(forbidden):
    world,transport,policy=setup();policy['plaintext_forbidden']=forbidden;before=deepcopy(policy)
    value=transport.api_hop_snapshot(policy,3)
    assert policy==before and value['available'] and value['capture_executed']
    assert value['normal_response_schema_verified'] and value['data_origin']=='EXAMPLE'
    assert value['policy_binding_verified'] and value['policy_window_verified']
    assert value['binding_verified_before_after'] and value['parent_cleanup_verified']
    assert value['plaintext_forbidden'] is forbidden
    assert value['binding']==transport.binding and value['environment_ref']==env().ref().model_dump(mode='json')
    assert value['policy_sha256']==sha256_hex(canonical_json(policy))
    assert value['probe_source_sha256']==transport.source_sha256
    assert world.execs[0]['url'].endswith('/v1/completions')
    assert world.execs[0]['body']=={'model':'fixture-model','prompt':'ping','max_tokens':1}
    assert value['initial_response']['capture_complete'] is True
    assert not any(value[k] for k in ['cryptographic_peer_identity_verified','deployed_caller_path_verified',
        'api_transport_scope_complete','internal_IPC_TP_scope_complete'])
    assert 'security' not in value and 'verdict' not in value
    assert PRIVATE not in json.dumps([value,transport.report()])
    assert 'body' not in value['initial_response'] and not value['diagnostic_codes']


@pytest.mark.parametrize('damage',['missing','extra','docker-fields','scope','environment','target','binding',
    'ip','port','bool-port','no-prohibition','expired','future','naive','reversed','timeout','profile'])
def test_unmatched_or_missing_explicit_policy_rejected_before_remote_io(damage):
    world,transport,policy=setup();timeout=3
    if damage=='missing':policy.pop('plaintext_forbidden')
    elif damage=='extra':policy['verified_capture']=PRIVATE
    elif damage=='docker-fields':
        for old,new in [('api_container_id','api_environment_sha256'),('api_network_id','api_target_sha256'),('api_endpoint_id','api_binding_sha256')]:policy[old]=policy.pop(new)
    elif damage=='scope':policy['api_hop_scope']='guard_probe_to_owned_direct_api'
    elif damage in {'environment','target','binding'}:policy['api_'+damage+'_sha256']='0'*64
    elif damage=='ip':policy['api_ip']='192.0.2.20'
    elif damage=='port':policy['api_port']+=1
    elif damage=='bool-port':policy['api_port']=True
    elif damage=='no-prohibition':policy['plaintext_forbidden']=None
    elif damage=='expired':policy['validity_end']=(utcnow()-timedelta(seconds=1)).isoformat()
    elif damage=='future':policy['validity_start']=(utcnow()+timedelta(seconds=1)).isoformat()
    elif damage=='naive':policy['validity_end']='2026-10-29T12:00:00'
    elif damage=='reversed':policy['validity_start'],policy['validity_end']=policy['validity_end'],policy['validity_start']
    elif damage=='timeout':timeout=float('inf')
    else:transport=KubernetesHTTP(world.target,env(),duration=30,runner=world.runner,profile=PROFILE)
    value=transport.api_hop_snapshot(policy,timeout)
    assert not value['available'] and not value['capture_executed'] and not value['initial_response']
    assert value['diagnostic_codes']==['KUBERNETES_API_HOP_UNAVAILABLE']
    assert not world.calls and not world.execs and PRIVATE not in json.dumps(value)


@pytest.mark.parametrize('damage',['denied','truncated','wrong-model','invalid-json','incomplete-usage'])
def test_known_status_without_complete_structural_response_is_not_normal_success(damage):
    world,transport,policy=setup()
    def mutate(rows):
        row=rows[0]
        if damage=='denied':row['outcome']=403
        elif damage=='truncated':row['capture_complete']=False
        else:
            body=json.loads(response())
            if damage=='wrong-model':body['model']='other'
            elif damage=='incomplete-usage':body['usage'].pop('total_tokens')
            text=PRIVATE if damage=='invalid-json' else json.dumps(body)
            row.update(body=text,body_bytes=len(text.encode()),body_sha256=sha256_hex(text.encode()))
    world.damage=mutate
    value=transport.api_hop_snapshot(policy,3)
    assert value['available'] and value['binding_verified_before_after']
    assert value['normal_response_schema_verified'] is False
    assert value['initial_response']['outcome']==(403 if damage=='denied' else 200)
    assert PRIVATE not in json.dumps(value)


def test_policy_window_after_capture_preserves_response_fact_without_bound_trial(monkeypatch):
    _,transport,policy=setup();first=utcnow();late=first+timedelta(minutes=2)
    times=iter([first,late]);monkeypatch.setattr(hop,'utcnow',lambda:next(times))
    value=transport.api_hop_snapshot(policy,3)
    assert value['normal_response_schema_verified'] and value['initial_response']['outcome']==200
    assert value['binding_verified_before_after'] and not value['policy_window_verified'] and not value['available']
    assert value['diagnostic_codes']==['KUBERNETES_API_HOP_WINDOW_UNVERIFIED']
    assert value['finished_at']==late.isoformat()


@pytest.mark.parametrize('role',['engine','probe'])
def test_later_generation_failure_does_not_rewrite_first_bound_snapshot(role):
    world,transport,policy=setup();first=transport.api_hop_snapshot(policy,3);original=deepcopy(first)
    def mutate(which,value,after):
        if which==role and after and len(world.execs)==2:value['metadata']['uid']='replacement'
    world.mutate=mutate
    later=transport.api_hop_snapshot(policy,3)
    assert first==original and first['available'] and first['normal_response_schema_verified']
    assert not later['available'] and not later['binding_verified_before_after'] and later['initial_response'] is None
    assert PRIVATE not in json.dumps([first,later])


def test_known_complete_hop_and_parent_cleanup_gap_are_distinct():
    world,transport,policy=setup();original=world.runner
    def runner(argv,**kwargs):
        value=original(argv,**kwargs)
        if 'exec' in argv:value['capture'].update(pipes_closed=False,read_errors=['child_reap'])
        return value
    transport.runner=runner
    value=transport.api_hop_snapshot(policy,3)
    assert value['available'] and value['normal_response_schema_verified']
    assert not value['parent_cleanup_verified']
    assert value['cleanup_errors']==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']
    assert not value['api_transport_scope_complete']


def test_original_pack_and_docker_policy_admission_are_not_relabelled():
    from guard.executors.api_transport import normalize_policy as docker_policy
    from guard.core.select import select
    from guard.models import Gap
    from tests.builders import policy as make_policy
    _,_,values=setup();pack=baseline_pack();check=pack.checks['PLT-08']
    assert pack.manifest.revision==31 and check.revision==5
    assert {'api_container_id','api_network_id','api_endpoint_id'}<={p.key for p in check.policy_params if p.required}
    assert not set(hop.POLICY_TYPES)&{'api_container_id','api_network_id','api_endpoint_id'}
    with pytest.raises(ValueError):docker_policy(values)
    pol=make_policy(pack.ref(),scope={'include':['PLT-08']},values={'PLT-08':values})
    item=next(x for x in select(pack,[env()],pol).items if x.check_id=='PLT-08')
    assert item.selected and Gap.NO_POLICY_VALUE in item.gaps
    # This common API cannot bypass the unchanged formal selection boundary.


def test_exact_normal_source_on_real_local_http_socket():
    from tests.test_stream_probe import _server,_response
    def handle(conn,request,index,stop):
        assert b'POST /v1/completions ' in request
        _response(conn,response().encode())
    with _server(handle) as (url,accepted):
        world,_,_=setup();world.target['engine']['trusted_url']=url;world.actual_probe=True
        transport=KubernetesHTTP(world.target,env(),duration=15,runner=world.runner,profile=REQUEST_BINDING_PROFILE)
        _,_,values=setup();values.update(api_target_sha256=transport.target_sha256,
            api_binding_sha256=sha256_hex(canonical_json(transport.binding)),api_port=transport.endpoint.port)
        value=transport.api_hop_snapshot(values,3)
        assert value['available'] and value['normal_response_schema_verified'] and len(accepted)==1
        assert value['data_origin']=='EXAMPLE' and PRIVATE not in json.dumps(value)
