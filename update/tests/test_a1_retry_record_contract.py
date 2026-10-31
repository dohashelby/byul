"""LIF12 metadata binding/ambiguity/cost boundaries, all producer inputs EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import pytest

from guard.executors import retry_record_contract as contract
from guard.models import canonical_json, sha256_hex, utcnow


def example():
    now = utcnow()
    scope = {'schema': contract.SCOPE_SCHEMA, 'policy_sha256': 'c'*64, 'run_id': 'guard-fixture',
        'producer_id': 'd'*32, 'producer_source_sha256': 'd'*64, 'producer_code_sha256': 'f'*64,
        'configuration_sha256': 'b'*64, 'native_source_sha256': None,
        'image_digest': 'sha256:'+'a'*64, 'model_revision': 'EXAMPLE-revision', 'model_inventory_sha256': 'c'*64,
        'subjects': [], 'limits': {'max_attempts': 4, 'max_overlap_ms': 10, 'max_remaining_ms': 20,
            'remaining_anchor': 'NEW_ATTEMPT_START', 'max_return_ms': 50, 'return_anchor': 'CALL_START',
            'max_cost': {'unit': 'attempt', 'amount': 100, 'counter_source_sha256': 'e'*64, 'epoch_id': 'e'*32}}}
    values = {'retry_environment_sha256': 'a'*64, 'retry_target_sha256': 'b'*64,
        'retry_scope_sha256': '0'*64, 'retry_policy_sha256': scope['policy_sha256'],
        'retry_record_source_sha256': scope['producer_source_sha256'], 'retry_session': '5'*32,
        'retry_validity_start': (now-timedelta(seconds=30)).isoformat(),
        'retry_validity_end': (now+timedelta(minutes=5)).isoformat(), 'retry_policy_mode': 'ALLOW',
        'retry_max_record_delay_ms': 300000}
    ref = {'kind': 'ENV', 'id': 'ENV-RETRY-EXAMPLE', 'revision': 1, 'sha256': values['retry_environment_sha256']}
    events = []

    def add(subject, kind, data, *, attempt=None, parent=None, initiator='UNKNOWN'):
        row = {k: v for k, v in subject.items() if k != 'scenario'}
        row.update(event_id=format(4096+len(events), '032x'), seq=len(events), type=kind,
            attempt_id=attempt, parent_attempt_id=parent, initiator=initiator,
            monotonic_ns=(len(events)+1)*1_000_000, at=now.isoformat(), data=data)
        events.append(row)

    for index, scenario in enumerate(('NORMAL', 'MANUAL_CONTROL', 'AUTOMATIC_CHALLENGE')):
        subject = {'subject_id': format(100+index, '032x'), 'actor_id': '6'*32,
            'request_family_id': format(200+index, '032x'), 'scenario': scenario,
            'pid': 42, 'process_start_ticks': 123, 'worker_generation': '7'*32, 'clock_domain_sha256': '8'*64}
        scope['subjects'].append(subject)
        old, new = format(1000+10*index, '032x'), format(1001+10*index, '032x')
        initiator = 'MANUAL' if scenario == 'MANUAL_CONTROL' else 'INITIAL'
        add(subject, 'CONFIGURATION_CLAIM', {'mode': 'ALLOW', 'config_sha256': scope['configuration_sha256'], 'trigger_kind': 'TIMEOUT'})
        add(subject, 'CALL_CLAIM', {'phase': 'START', 'outcome': 'STARTED'}, initiator=initiator)
        add(subject, 'ATTEMPT_START_CLAIM', {'native_event_ref': None}, attempt=old, initiator=initiator)
        if scenario == 'AUTOMATIC_CHALLENGE':
            add(subject, 'TRIGGER_CLAIM', {'trigger_id': '9'*32, 'kind': 'TIMEOUT', 'native_event_ref': None}, attempt=old)
            add(subject, 'RETRY_DECISION_CLAIM', {'trigger_id': '9'*32, 'decision': 'ALLOWED', 'next_attempt_id': new}, attempt=old)
            add(subject, 'ATTEMPT_START_CLAIM', {'native_event_ref': None}, attempt=new, parent=old, initiator='AUTOMATIC')
            add(subject, 'PREVIOUS_WORK_CLAIM', {'previous_attempt_id': old, 'state': 'REMAINING', 'resource_scope': 'ENGINE_TASK', 'native_event_ref': None},
                attempt=new, parent=old, initiator='AUTOMATIC')
        add(subject, 'ATTEMPT_END_CLAIM', {'outcome': 'RETURNED', 'native_event_ref': None}, attempt=old, initiator=initiator)
        if scenario == 'AUTOMATIC_CHALLENGE':
            add(subject, 'ATTEMPT_END_CLAIM', {'outcome': 'RETURNED', 'native_event_ref': None}, attempt=new, parent=old, initiator='AUTOMATIC')
        add(subject, 'COST_SAMPLE_CLAIM', {'cost_subject': 'FAMILY', 'unit': 'attempt', 'epoch_id': 'e'*32,
            'cumulative_amount': 10, 'counter_source_sha256': 'e'*64})
        add(subject, 'CALL_CLAIM', {'phase': 'END', 'outcome': 'RETURNED'}, initiator=initiator)
        add(subject, 'RETURN_CLAIM', {'outcome': 'RETURNED', 'native_event_ref': None}, initiator=initiator)
    packet = {'schema': contract.RECORD_SCHEMA, 'environment_ref': ref,
        'target_sha256': values['retry_target_sha256'], 'session': values['retry_session'], 'run_id': scope['run_id'],
        'source_sha256': values['retry_record_source_sha256'], 'code_sha256': scope['producer_code_sha256'],
        'scope_sha256': '', 'policy_sha256': values['retry_policy_sha256'], 'producer_id': scope['producer_id'],
        'snapshot_generation': 'a'*32, 'recorded_at': now.isoformat(), 'declared_complete': True, 'events': events}
    bind(scope, values, packet)
    return scope, values, packet, ref


def bind(scope, values, packet):
    # Explicit EXAMPLE inputs are fixed before any collection. No runtime target hash approval.
    values['retry_scope_sha256'] = sha256_hex(canonical_json(scope))
    packet['scope_sha256'] = values['retry_scope_sha256']


def validate(scope, values, packet, ref):
    contract.validate_scope(scope, policy_sha256=values['retry_policy_sha256'],
        source_sha256=values['retry_record_source_sha256'], image_digest='sha256:'+'a'*64,
        model_revision='EXAMPLE-revision', model_inventory_sha256='c'*64)
    contract.validate_record(packet, environment_ref=ref, target_sha256=values['retry_target_sha256'], values=values, scope=scope)


def rows_of(packet, kind):
    return [r for r in packet['events'] if r['type'] == kind]


def automatic(packet):
    return next(r for r in rows_of(packet, 'ATTEMPT_START_CLAIM') if r['initiator'] == 'AUTOMATIC')


def append_row(packet, original, **updates):
    row = deepcopy(original)
    row.update(event_id=format(8192+len(packet['events']), '032x'), seq=len(packet['events']),
        monotonic_ns=(len(packet['events'])+1)*1_000_000, **updates)
    packet['events'].append(row)
    return row


@pytest.mark.parametrize('change', [
    'normal', 'unresolved', 'forbidden', 'manual-is-not-auto', 'no-parent', 'foreign-parent',
    'wrong-actor', 'worker-generation', 'missing-end', 'partial', 'empty', 'duplicate-event',
    'event-conflict', 'seq-conflict', 'seq-gap', 'duplicate-attempt', 'duplicate-call', 'orphan-end',
    'snapshot-generation', 'record-clock', 'event-window', 'delay', 'unknown-clock', 'different-process-same-domain',
    'cost-exceeded', 'cost-source', 'cost-unit', 'cost-epoch', 'cost-reset', 'attempt-limit',
    'overlap-limit', 'remaining-limit', 'return-limit', 'configuration', 'parent-cycle',
    'normal-manual-only', 'manual-baseline-missing', 'no-overlap', 'native-ref'])
def test_declarations_preserve_ambiguity_and_never_authenticate_retry_or_cost(change):
    scope, values, packet, ref = example()
    auto = automatic(packet)
    snapshots = []
    if change in {'unresolved', 'forbidden'}:
        values['retry_policy_mode'] = 'UNRESOLVED' if change == 'unresolved' else 'FORBID'
        for row in rows_of(packet, 'CONFIGURATION_CLAIM'):row['data']['mode'] = values['retry_policy_mode']
    elif change == 'manual-is-not-auto':
        for row in packet['events']:
            if row['attempt_id'] == auto['attempt_id'] and row['type'] in {'ATTEMPT_START_CLAIM','ATTEMPT_END_CLAIM'}:row['initiator']='MANUAL'
    elif change in {'no-parent','foreign-parent','parent-cycle'}:
        parent = None if change=='no-parent' else rows_of(packet,'ATTEMPT_START_CLAIM')[0]['attempt_id'] if change=='foreign-parent' else auto['attempt_id']
        for row in packet['events']:
            if row['attempt_id']==auto['attempt_id'] and row['type'] in {'ATTEMPT_START_CLAIM','ATTEMPT_END_CLAIM'}:row['parent_attempt_id']=parent
    elif change=='wrong-actor':auto['actor_id']='f'*32
    elif change=='worker-generation':auto['worker_generation']='f'*32
    elif change=='missing-end':packet['events']=[r for r in packet['events'] if r['type']!='ATTEMPT_END_CLAIM']
    elif change=='partial':packet['declared_complete']=False
    elif change=='empty':packet['events']=[]
    elif change=='duplicate-event':packet['events'].append(deepcopy(auto))
    elif change=='event-conflict':
        row=deepcopy(auto);row['initiator']='MANUAL';packet['events'].append(row)
    elif change=='seq-conflict':packet['events'][1]['seq']=packet['events'][0]['seq']
    elif change=='seq-gap':packet['events'][-1]['seq']+=1
    elif change=='duplicate-attempt':append_row(packet,auto)
    elif change=='duplicate-call':append_row(packet,rows_of(packet,'CALL_CLAIM')[0])
    elif change=='orphan-end':append_row(packet,rows_of(packet,'ATTEMPT_END_CLAIM')[0],attempt_id='f'*32)
    elif change=='record-clock':packet['recorded_at']=(contract.stamp(packet['recorded_at'])-timedelta(seconds=1)).isoformat()
    elif change=='event-window':packet['events'][0]['at']='2020-01-01T00:00:00+00:00'
    elif change=='delay':values['retry_max_record_delay_ms']=0;packet['recorded_at']=(contract.stamp(packet['recorded_at'])+timedelta(seconds=1)).isoformat()
    elif change=='unknown-clock':
        for row in packet['events']:
            if row['attempt_id']==auto['attempt_id']:row['monotonic_ns']=None
    elif change=='different-process-same-domain':
        subject=deepcopy(scope['subjects'][-1]);subject.update(subject_id='f'*32,pid=43,process_start_ticks=456,worker_generation='f'*32)
        scope['subjects'].append(subject)
        for row in packet['events']:
            if row['attempt_id']==auto['attempt_id']:
                row.update({k:v for k,v in subject.items() if k!='scenario'})
    elif change.startswith('cost-'):
        cost=rows_of(packet,'COST_SAMPLE_CLAIM')[-1]
        if change=='cost-exceeded':cost['data']['cumulative_amount']=101
        elif change=='cost-source':cost['data']['counter_source_sha256']='f'*64
        elif change=='cost-unit':cost['data']['unit']='token'
        elif change=='cost-epoch':append_row(packet,cost)['data']['epoch_id']='f'*32
        elif change=='cost-reset':
            cost['data']['cumulative_amount']=101
            append_row(packet,cost)['data']['cumulative_amount']=1
            append_row(packet,cost)['data']['cumulative_amount']=102
    elif change=='attempt-limit':scope['limits']['max_attempts']=1
    elif change=='overlap-limit':scope['limits']['max_overlap_ms']=0
    elif change=='remaining-limit':scope['limits']['max_remaining_ms']=0
    elif change=='return-limit':scope['limits']['max_return_ms']=0
    elif change=='configuration':rows_of(packet,'CONFIGURATION_CLAIM')[0]['data']['config_sha256']='f'*64
    elif change=='normal-manual-only':
        packet['events']=[r for r in packet['events'] if r['request_family_id']!=scope['subjects'][-1]['request_family_id']]
        scope['subjects']=scope['subjects'][:-1]
    elif change=='manual-baseline-missing':
        packet['events']=[r for r in packet['events'] if r['request_family_id']!=scope['subjects'][1]['request_family_id']]
        scope['subjects'].pop(1)
    elif change=='no-overlap':
        end=next(r for r in rows_of(packet,'ATTEMPT_END_CLAIM') if r['attempt_id']==auto['parent_attempt_id'])
        packet['events'].remove(end);packet['events'].insert(packet['events'].index(auto),end)
        for index,row in enumerate(packet['events']):row.update(seq=index,monotonic_ns=(index+1)*1_000_000)
    elif change=='native-ref':
        scope['native_source_sha256']='f'*64
        auto['data']['native_event_ref']={'recording_sha256':'a'*64,'event_seq':3,'request_id_sha256':None,
            'session':values['retry_session'],'run_id':scope['run_id'],'source_sha256':'f'*64,
            'pid':auto['pid'],'process_start_ticks':auto['process_start_ticks']}
    bind(scope,values,packet);validate(scope,values,packet,ref)
    snapshots.append({'stage':'before','packet':packet})
    after=deepcopy(packet)
    if change=='snapshot-generation':after['snapshot_generation']='f'*32
    snapshots.append({'stage':'after','packet':after})
    result=contract.project(scope,snapshots,values)
    assert all(result[k] is False for k in ('producer_authenticated','automatic_retry_verified','nonoccurrence_verified','previous_work_termination_verified','cost_enforcement_verified','whole_scope_complete'))
    assert all(r['confirmed_violation'] is False for k in ('attempt_candidates','family_candidates','cost_candidates') for r in result[k])
    if change=='normal':assert not result['missing'] and not result['conflicts'] and len(result['events'])==len(packet['events'])
    if change=='duplicate-event':assert len(result['events'])==len(packet['events'])-1
    if change=='unresolved':assert 'enterprise_retry_policy_unresolved' in result['unverified']
    if change=='forbidden':assert any(r['declared_forbidden_automatic_attempt'] for r in result['family_candidates'])
    if change=='manual-is-not-auto':assert not result['attempt_candidates'] and result['missing']
    if change in {'no-parent','foreign-parent'}:assert not result['attempt_candidates'][0]['declared_parent_trigger_decision_link']
    if change in {'wrong-actor','worker-generation','event-conflict','seq-conflict','duplicate-attempt','snapshot-generation','configuration','parent-cycle'}:assert result['conflicts']
    if change in {'missing-end','partial','empty','seq-gap','duplicate-call','orphan-end','record-clock','event-window','delay','normal-manual-only','manual-baseline-missing'}:assert result['missing']
    if change in {'unknown-clock','different-process-same-domain'}:assert result['attempt_candidates'][0]['declaration_internal_overlap_ms'] is None and result['unverified']
    if change=='cost-exceeded':assert any(r['declared_limit_exceeded'] for r in result['cost_candidates'])
    if change in {'cost-source','cost-unit','cost-epoch'}:assert result['cost_candidates'][-1]['declared_limit_exceeded'] is None
    if change=='cost-reset':
        assert result['missing'] and result['cost_candidates'][-1]['declared_limit_exceeded'] is None
        assert any(r['declared_limit_exceeded'] is True for r in result['cost_candidates'])
    if change=='attempt-limit':assert any(r['declared_attempt_limit_exceeded'] for r in result['family_candidates'])
    if change=='overlap-limit':assert result['attempt_candidates'][0]['declared_overlap_limit_exceeded']
    if change=='remaining-limit':assert result['attempt_candidates'][0]['declared_remaining_limit_exceeded']
    if change=='return-limit':assert any(r['declared_return_limit_exceeded'] for r in result['family_candidates'])
    if change=='no-overlap':assert result['attempt_candidates'][0]['declaration_internal_overlap_ms']==0
    if change=='native-ref':assert result['attempt_candidates'][0]['actual_automatic_causation_verified'] is False


@pytest.mark.parametrize('change',['schema','extra','raw','trust','bool-seq','bool-pid','naive-time','env','bool-revision',
    'target','session','run','source','code','scope','policy','producer','overflow','event-data','cost-bool','native-no-scope',
    'native-session','native-run','native-source','native-pid','native-start'])
def test_record_rejects_open_payloads_and_foreign_bindings(change):
    scope,values,packet,ref=example();auto=automatic(packet)
    if change=='schema':packet['schema']='guard-required-event-records/1'
    elif change=='extra':packet['unknown']=None
    elif change=='raw':auto['data']['prompt']='EXAMPLE_PRIVATE'
    elif change=='trust':packet['producer_authenticated']=True
    elif change=='bool-seq':auto['seq']=True
    elif change=='bool-pid':auto['pid']=True
    elif change=='naive-time':auto['at']='2026-10-31T00:00:00'
    elif change=='env':packet['environment_ref']={**ref,'sha256':'f'*64}
    elif change=='bool-revision':packet['environment_ref']={**ref,'revision':True}
    elif change=='target':packet['target_sha256']='f'*64
    elif change=='session':packet['session']='../unsupported'
    elif change=='run':packet['run_id']='another-run'
    elif change=='source':packet['source_sha256']='f'*64
    elif change=='code':packet['code_sha256']='e'*64
    elif change=='scope':packet['scope_sha256']='f'*64
    elif change=='policy':packet['policy_sha256']='f'*64
    elif change=='producer':packet['producer_id']='f'*32
    elif change=='overflow':packet['events']=[deepcopy(auto)]*257
    elif change=='event-data':auto['data']['actual_retry_verified']=False
    elif change=='cost-bool':rows_of(packet,'COST_SAMPLE_CLAIM')[-1]['data']['cumulative_amount']=True
    elif change.startswith('native-'):
        if change!='native-no-scope':scope['native_source_sha256']='f'*64
        native={'recording_sha256':'a'*64,'event_seq':3,'request_id_sha256':None,
            'session':values['retry_session'],'run_id':scope['run_id'],'source_sha256':'f'*64,'pid':42,'process_start_ticks':123}
        key={'native-session':'session','native-run':'run_id','native-source':'source_sha256','native-pid':'pid','native-start':'process_start_ticks'}.get(change)
        if key:native[key]='f'*32 if key=='session' else 'different-run' if key=='run_id' else 'e'*64 if key=='source_sha256' else 43 if key=='pid' else 124
        auto['data']['native_event_ref']=native
        bind(scope,values,packet)
    with pytest.raises(ValueError,match='RETRY_RECORD_INVALID'):
        contract.validate_record(packet,environment_ref=ref,target_sha256=values['retry_target_sha256'],values=values,scope=scope)


@pytest.mark.parametrize('change',['extra','duplicate-subject','bool-pid','bool-start','empty-producer','image','model','inventory','scope-raw',
    'limit-bool','limit-negative','anchor-missing','anchor-without-limit','cost-unit','cost-epoch','cost-source','too-many-subjects'])
def test_scope_rejects_unreviewed_bindings_and_implicit_limits(change):
    scope,values,packet,ref=example()
    if change=='extra':scope['unknown']=False
    elif change=='duplicate-subject':scope['subjects'].append(deepcopy(scope['subjects'][0]))
    elif change=='bool-pid':scope['subjects'][0]['pid']=True
    elif change=='bool-start':scope['subjects'][0]['process_start_ticks']=True
    elif change=='empty-producer':scope['producer_id']=''
    elif change=='image':scope['image_digest']='image:latest'
    elif change=='model':scope['model_revision']='other-revision'
    elif change=='inventory':scope['model_inventory_sha256']='f'*64
    elif change=='scope-raw':scope['subjects'][0]['argv']=['EXAMPLE_PRIVATE']
    elif change=='limit-bool':scope['limits']['max_attempts']=True
    elif change=='limit-negative':scope['limits']['max_overlap_ms']=-1
    elif change=='anchor-missing':scope['limits']['remaining_anchor']=None
    elif change=='anchor-without-limit':scope['limits']['max_remaining_ms']=None
    elif change=='cost-unit':scope['limits']['max_cost']['unit']='unknown'
    elif change=='cost-epoch':scope['limits']['max_cost']['epoch_id']=None
    elif change=='cost-source':scope['limits']['max_cost']['counter_source_sha256']=True
    elif change=='too-many-subjects':scope['subjects']=[deepcopy(scope['subjects'][0])]*65
    with pytest.raises(ValueError,match='RETRY_SCOPE_INVALID'):
        contract.validate_scope(scope,policy_sha256=values['retry_policy_sha256'],source_sha256=values['retry_record_source_sha256'],
            image_digest='sha256:'+'a'*64,model_revision='EXAMPLE-revision',model_inventory_sha256='c'*64)


@pytest.mark.parametrize('key,value',[('retry_unknown',None),('retry_policy_mode','implicit-allow'),('retry_session','../unsupported'),
    ('retry_record_source_sha256',True),('retry_max_record_delay_ms',True),('retry_max_record_delay_ms',600001),
    ('retry_validity_start','2026-10-31T00:00:00'),('retry_validity_end','2020-01-01T00:00:00+00:00')])
def test_policy_rejects_defaults_extra_fields_and_invalid_windows(key,value):
    _,values,_,_=example();values[key]=value
    with pytest.raises(ValueError,match='RETRY_POLICY_INVALID'):contract.normalize_policy(values)
