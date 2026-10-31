"""Closed nonsecret PLT12 declarations and policy binding, no credential I/O."""
from copy import deepcopy
from datetime import timedelta
import pytest
from guard.executors import credential_copy_contract as contract
from guard.models import canonical_json,sha256_hex,utcnow


def example():
    now=utcnow();policy_sha='c'*64
    inventory={'schema':contract.INVENTORY_SCHEMA,'credential_family_id':'1'*32,'policy_sha256':policy_sha,'copies':[
        {'copy_id':'2'*32,'generation':1,'location_id':'3'*32,'location_kind':'FILE','recipient_id':'4'*32,
         'required_action':'delete','deadline_at':(now+timedelta(seconds=20)).isoformat(),'retention_exception':None}]}
    raw=canonical_json(inventory)
    values={'copy_environment_sha256':'a'*64,'copy_target_sha256':'b'*64,'copy_inventory_sha256':sha256_hex(raw),
        'copy_policy_sha256':policy_sha,'copy_record_source_sha256':'d'*64,'copy_record_session':'5'*32,
        'copy_validity_start':(now-timedelta(minutes=1)).isoformat(),'copy_validity_end':(now+timedelta(minutes=1)).isoformat(),
        'copy_max_record_delay_ms':300000}
    env={'kind':'ENV','id':'ENV-COPY-EXAMPLE','revision':1,'sha256':'a'*64}
    events=[]
    for index,(kind,method,outcome) in enumerate([('COPY_INVENTORY_CLAIM',None,'present'),('TREATMENT_CLAIM','delete','completed'),
        ('REUSE_CLAIM',None,'denied'),('ISSUER_CLAIM',None,'revoked')]):
        events.append({'event_id':format(index+16,'032x'),'seq':index,'type':kind,'copy_id':'2'*32,'copy_generation':1,
            'location_id':'3'*32,'recipient_id':'4'*32,'actor_id':'6'*32,'at':now.isoformat(),'method':method,'outcome':outcome,
            'restriction_claims':[],'request_id_sha256':'e'*64 if kind=='REUSE_CLAIM' else None,
            'enforcer_id':'7'*32 if kind in {'REUSE_CLAIM','ISSUER_CLAIM'} else None})
    packet={'schema':contract.RECORD_SCHEMA,'environment_ref':env,'target_sha256':values['copy_target_sha256'],
        'session':values['copy_record_session'],'source_sha256':values['copy_record_source_sha256'],
        'inventory_sha256':values['copy_inventory_sha256'],'policy_sha256':policy_sha,'producer_id':'8'*32,
        'snapshot_generation':'9'*32,'recorded_at':now.isoformat(),'declared_complete':True,'events':events}
    return inventory,values,packet,env


def validate(packet,values,env):
    return contract.validate_record(packet,environment_ref=env,target_sha256=values['copy_target_sha256'],session=values['copy_record_session'],
        source_sha256=values['copy_record_source_sha256'],inventory_sha256=values['copy_inventory_sha256'],policy_sha256=values['copy_policy_sha256'])


@pytest.mark.parametrize('change',['normal','late','wrong-method','reuse-allowed','failed','retention','empty','partial','missing','duplicate','id-conflict','seq-conflict','wrong-generation','wrong-recipient','other-copy','producer-changed','clock','delay'])
def test_declarations_remain_unverified_and_conflicts_remain_distinct(change):
    inventory,values,packet,env=example();snapshots=[]
    if change=='late':inventory['copies'][0]['deadline_at']=(contract.stamp(packet['recorded_at'])-timedelta(seconds=1)).isoformat()
    elif change=='wrong-method':packet['events'][1]['method']='replace'
    elif change=='reuse-allowed':packet['events'][2]['outcome']='allowed'
    elif change=='failed':packet['events'][1]['outcome']='failed'
    elif change=='retention':
        inventory['copies'][0]['retention_exception']={'exception_id':'f'*32,'expires_at':values['copy_validity_end'],'restrictions_required':['access-denied','audited']}
        packet['events'][1].update(outcome='retained',restriction_claims=['audited','access-denied'])
    elif change=='empty':inventory['copies']=[]
    elif change=='partial':packet['declared_complete']=False
    elif change=='missing':packet['events']=[]
    elif change=='duplicate':packet['events'].append(deepcopy(packet['events'][1]))
    elif change=='id-conflict':
        altered=deepcopy(packet['events'][1]);altered['outcome']='failed';packet['events'].append(altered)
    elif change=='seq-conflict':packet['events'][1]['seq']=packet['events'][0]['seq']
    elif change=='wrong-generation':packet['events'][1]['copy_generation']=2
    elif change=='wrong-recipient':packet['events'][1]['recipient_id']='f'*32
    elif change=='other-copy':packet['events'][1]['copy_id']='f'*32
    elif change=='clock':packet['recorded_at']=(contract.stamp(packet['recorded_at'])-timedelta(seconds=1)).isoformat()
    elif change=='delay':values['copy_max_record_delay_ms']=0;packet['recorded_at']=(contract.stamp(packet['recorded_at'])+timedelta(seconds=1)).isoformat()
    contract.validate_inventory(inventory,expected_policy_sha256=values['copy_policy_sha256'])
    # Inventory changed here is an explicit EXAMPLE test input; policy pins are
    # fixed before collection and never learned from a running target.
    values['copy_inventory_sha256']=sha256_hex(canonical_json(inventory));packet['inventory_sha256']=values['copy_inventory_sha256']
    validate(packet,values,env);snapshots.append({'stage':'before','collected':True,'packet':packet})
    if change=='producer-changed':
        after=deepcopy(packet);after['producer_id']='f'*32;snapshots.append({'stage':'after','collected':True,'packet':after})
    result=contract.project(inventory,snapshots,values)
    assert all(result[key] is False for key in ('producer_authenticated','actual_copy_membership_verified','actual_treatment_verified','reuse_enforcer_verified','issuer_verified','whole_scope_complete'))
    assert all(r['confirmed_violation'] is False for r in result['treatment_candidates']+result['reuse_candidates'])
    if change=='normal':assert len(result['events'])==4 and not result['conflicts']
    if change=='late':assert result['treatment_candidates'][0]['declared_deadline_exceeded']
    if change=='wrong-method':assert result['treatment_candidates'][0]['declared_method_conflict']
    if change=='reuse-allowed':assert result['reuse_candidates'][0]['declared_reuse_allowed']
    if change=='failed':assert result['treatment_candidates'][0]['declared_failure']
    if change=='retention':assert result['treatment_candidates'][0]['declared_restricted_retention_match']
    if change=='duplicate':assert len(result['events'])==4
    if change in {'id-conflict','seq-conflict','wrong-generation','wrong-recipient','other-copy','producer-changed'}:assert result['conflicts']
    if change in {'empty','partial','missing','clock','delay'}:assert len(result['missing'])>1


@pytest.mark.parametrize('change',['field','schema','secret','origin-claim','truth','event-truth','bool-seq','naive-time','wrong-env','bool-env-revision','wrong-target','wrong-session','wrong-source','wrong-policy','wrong-inventory','oversize'])
def test_record_unknown_fields_privilege_claims_and_wrong_bindings_rejected(change):
    _,values,packet,env=example()
    if change=='field':packet['unknown']='not-supported'
    elif change=='schema':packet['schema']='guard-required-event-records/1'
    elif change=='secret':packet['events'][0]['credential_value']='EXAMPLE_PRIVATE_NO_PERSIST'
    elif change=='origin-claim':packet['data_origin']='MEASURED'
    elif change=='truth':packet['producer_authenticated']=True
    elif change=='event-truth':packet['events'][1]['actual_treatment_verified']=True
    elif change=='bool-seq':packet['events'][0]['seq']=True
    elif change=='naive-time':packet['events'][0]['at']='2026-10-31T00:00:00'
    elif change=='wrong-env':packet['environment_ref']={**env,'sha256':'f'*64}
    elif change=='bool-env-revision':packet['environment_ref']={**env,'revision':True}
    elif change=='wrong-target':packet['target_sha256']='f'*64
    elif change=='wrong-session':packet['session']='f'*32
    elif change=='wrong-source':packet['source_sha256']='f'*64
    elif change=='wrong-policy':packet['policy_sha256']='f'*64
    elif change=='wrong-inventory':packet['inventory_sha256']='f'*64
    else:packet['events']*=contract.MAX_EVENTS
    with pytest.raises(ValueError,match='COPY_RECORD_INVALID'):validate(packet,values,env)


@pytest.mark.parametrize('change',['unknown','raw-path','duplicate-copy','bad-id','bool-generation','restrict-no-exception','bad-restriction','naive-deadline','oversize'])
def test_declared_inventory_closed_nonsecret_policy_fields(change):
    inventory,values,_,_=example();row=inventory['copies'][0]
    if change=='unknown':inventory['unknown']=None
    elif change=='raw-path':row['path']='/private/unsupported'
    elif change=='duplicate-copy':inventory['copies'].append(deepcopy(row))
    elif change=='bad-id':row['copy_id']='EXAMPLE_NOT_UUID'
    elif change=='bool-generation':row['generation']=True
    elif change=='restrict-no-exception':row['required_action']='restrict'
    elif change=='bad-restriction':row['retention_exception']={'exception_id':'f'*32,'expires_at':values['copy_validity_end'],'restrictions_required':['arbitrary']}
    elif change=='naive-deadline':row['deadline_at']='2026-10-31T00:00:00'
    else:inventory['copies']*=contract.MAX_COPIES+1
    with pytest.raises(ValueError,match='COPY_INVENTORY_INVALID'):contract.validate_inventory(inventory,expected_policy_sha256=values['copy_policy_sha256'])
