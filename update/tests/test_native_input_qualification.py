"""Qualification lookup uses verified actual source receipts, never fake runs."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from guard.collectors import native_input_qualification as qualification


def actual_row():
    data=json.loads(qualification.QUALIFICATIONS.read_bytes())
    row,=[row for row in data['qualifications']if row['producer']==qualification.native_input.producer()]
    return row


def test_exact_gpu_source_qualification_is_registered_only_for_its_selected_scope():
    row=actual_row();result=qualification.status(row['engine'],row['version'],row['image'])
    assert result['registered']is True and result['producer']==row['producer']
    assert result['qualified_scope']=='selected-original-http-input-validation-source-calls'
    assert all(result[k]is False for k in('producer_authenticated','full_check_scope_qualified',
        'fresh_runtime_bindings_verified','whole69_complete'))


@pytest.mark.parametrize('field',['engine','version','image','source'])
def test_qualification_is_not_inherited_by_another_engine_version_image_or_source(monkeypatch,field):
    row=actual_row();args={k:row[k]for k in('engine','version','image')}
    if field=='source':
        monkeypatch.setattr(qualification.native_input,'producer',lambda:{**row['producer'],'source_sha256':'0'*64})
    else:args[field]='EXAMPLE-unqualified'
    result=qualification.status(**args)
    assert result['registered']is result['full_check_scope_qualified']is result['whole69_complete']is False


@pytest.mark.parametrize('damage',['missing-positive','new-source','whole-scope','evidence-hash','extra-registry-field'])
def test_bad_or_expanded_qualification_evidence_cannot_create_registration(tmp_path,monkeypatch,damage):
    row=actual_row();evidence=json.loads((qualification.ASSETS/row['evidence_file']).read_bytes())
    if damage=='missing-positive':evidence['current_product_request_link_verified']=False
    elif damage=='new-source':evidence['producer']['source_sha256']='0'*64
    elif damage=='whole-scope':evidence['full_req08_or_req09_scope_complete']=True
    raw=json.dumps(evidence).encode();(tmp_path/row['evidence_file']).write_bytes(raw)
    row['evidence_bytes']=len(raw);row['evidence_sha256']=sha256(raw).hexdigest()
    if damage=='evidence-hash':row['evidence_sha256']='0'*64
    if damage=='extra-registry-field':row['full_check_scope_qualified']=True
    table=tmp_path/'qualifications.json';table.write_text(json.dumps({'schema':'guard-native-input-qualifications/1','qualifications':[row]}))
    monkeypatch.setattr(qualification,'ASSETS',tmp_path);monkeypatch.setattr(qualification,'QUALIFICATIONS',table)
    with pytest.raises(ValueError,match='NATIVE_INPUT_QUALIFICATION_DATA_INVALID'):
        qualification.status(row['engine'],row['version'],row['image'])
