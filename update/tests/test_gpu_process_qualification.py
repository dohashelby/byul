"""Qualification data is source/engine/image scoped and never group recovery."""
from copy import deepcopy
from hashlib import sha256
import json
import pytest
from guard.collectors import gpu_process_qualification as qualification


def example(tmp_path,monkeypatch):
    # EXAMPLE receipt only; never written into the production registry.
    evidence={'schema':'byul-owned-host-gpu-process-actual-qualification/1','producer':qualification.producer(),
        'scope':qualification.SCOPE,'image':'example.invalid/unqualified',
        'actual_gpu_source_qualified':True,'exact_node_task_and_root_mount_verified':True,
        'normal_repeated_host_worker_gpu_links_verified':True,'source_or_packet_tamper_rejected':True,
        'stale_anchor_or_owner_rejected':True,'partial_query_not_complete':True,
        'whole_gpu_release_qualified':False,'whole_group_recovery_qualified':False,'whole69_complete':False}
    row={'engine':'mock','version':'EXAMPLE','image':evidence['image'],'producer':evidence['producer'],
        'scope':qualification.SCOPE,'evidence_file':'EXAMPLE-only.json','evidence_bytes':0,'evidence_sha256':'0'*64,'verified_export_sha256':'a'*64}
    monkeypatch.setattr(qualification,'ASSETS',tmp_path);monkeypatch.setattr(qualification,'REGISTRY',tmp_path/'registry.json')
    return evidence,row


def write(tmp_path,evidence,row):
    raw=json.dumps(evidence).encode();row['evidence_bytes']=len(raw);row['evidence_sha256']=sha256(raw).hexdigest()
    (tmp_path/row['evidence_file']).write_bytes(raw)
    qualification.REGISTRY.write_text(json.dumps({'schema':'guard-owned-host-gpu-process-qualifications/1','qualifications':[row]}))


def test_empty_registry_cannot_qualify_a_collector_or_whole_recovery(tmp_path,monkeypatch):
    path=tmp_path/'empty.json';path.write_text('{"schema":"guard-owned-host-gpu-process-qualifications/1","qualifications":[]}')
    monkeypatch.setattr(qualification,'REGISTRY',path)
    value=qualification.status('mock','EXAMPLE','example.invalid/unqualified')
    assert value['registered']is value['whole_gpu_release_qualified']is value['whole_group_recovery_qualified']is value['whole69_complete']is False


def test_exact_example_lookup_does_not_qualify_another_image_engine_version_or_source(tmp_path,monkeypatch):
    evidence,row=example(tmp_path,monkeypatch);write(tmp_path,evidence,row)
    value=qualification.status(row['engine'],row['version'],row['image'])
    assert value['registered']and not value['whole_gpu_release_qualified']and not value['whole_group_recovery_qualified']
    for field in('engine','version','image'):
        args={k:row[k]for k in('engine','version','image')};args[field]='EXAMPLE-other'
        assert qualification.status(**args)['registered']is False
    monkeypatch.setattr(qualification,'producer',lambda:{**row['producer'],'source_sha256':'0'*64})
    assert qualification.status(row['engine'],row['version'],row['image'])['registered']is False


@pytest.mark.parametrize('damage',['missing-positive','whole-gpu','whole-group','whole69','source','scope','bytes','duplicate','path','extra-row'])
def test_missing_tampered_or_expanded_qualification_is_rejected(tmp_path,monkeypatch,damage):
    evidence,row=example(tmp_path,monkeypatch)
    if damage=='missing-positive':evidence['normal_repeated_host_worker_gpu_links_verified']=False
    elif damage=='whole-gpu':evidence['whole_gpu_release_qualified']=True
    elif damage=='whole-group':evidence['whole_group_recovery_qualified']=True
    elif damage=='whole69':evidence['whole69_complete']=True
    elif damage=='source':evidence['producer']={**row['producer'],'source_sha256':'0'*64}
    elif damage=='scope':evidence['scope']='EXAMPLE-full-recovery'
    write(tmp_path,evidence,row)
    data=json.loads(qualification.REGISTRY.read_bytes())
    if damage=='bytes':(tmp_path/row['evidence_file']).write_text('{}')
    elif damage=='duplicate':data['qualifications']*=2
    elif damage=='path':data['qualifications'][0]['evidence_file']='../escape.json'
    elif damage=='extra-row':data['qualifications'][0]['whole_scope_qualified']=True
    qualification.REGISTRY.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='GPU_PROCESS_QUALIFICATION_DATA_INVALID'):
        qualification.status(row['engine'],row['version'],row['image'])
