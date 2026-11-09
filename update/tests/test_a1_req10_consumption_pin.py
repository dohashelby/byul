"""Literal reviewed consumer pin reuses the full unchanged B verification scope."""
from pathlib import Path
import hashlib
import json
import subprocess
import pytest
from tools import verify_req10_consumption as consumer
from tools import b1_integration_cpu as original
from tests.test_b1_integration_pin import pinned_repo,overlay_repo,git,commit_fixture


def test_inventory_check_is_bytes_only_and_original_B_pin_stays_fixed():
    # 정본 저장소의 승인 유무(검토 기록이 있는지)는 이 시험의 조건이 아니다. 바이트 무결성만 본다.
    repo=Path(__file__).resolve().parents[2]
    assert consumer.manifest_id(repo) is not None
    assert consumer.PINNED_PATHS==original.PINNED_PATHS
    assert original.BASE_SHA=='c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
    with pytest.raises(ValueError,match='outside selected overlay'):
        original.verify_baseline(repo,selected_overlay=original.SELECTED_PROFILE)
    manifest=consumer.verify_inventory(repo)
    assert len(manifest['files'])>200 and manifest['paths']==list(original.PINNED_PATHS)


@pytest.mark.parametrize('state,code',[((None,None),'SOURCE_REVIEW_RECEIPT_REQUIRED'),
                                       ((None,'SOURCE_REVIEW_RECEIPTS_INVALID'),'SOURCE_REVIEW_RECEIPTS_INVALID')])
def test_unapproved_gate_state_rejects_verify(monkeypatch,state,code):
    # 격리된 gate 상태: 미승인 또는 잘못된 기록 문서이면 바이트가 맞아도 검증하지 않는다.
    repo=Path(__file__).resolve().parents[2]
    monkeypatch.setattr(consumer,'REVIEWED_COMMON_COMMIT',state[0])
    monkeypatch.setattr(consumer,'RECEIPTS_ERROR',state[1])
    with pytest.raises(ValueError,match=code):
        consumer.verify(repo)


def _receipt_repo(tmp_path,manifest=b'{"files":{}}\n'):
    repo=tmp_path/'repo';path=repo/consumer.MANIFEST_PATH;path.parent.mkdir(parents=True);path.write_bytes(manifest)
    return repo,hashlib.sha256(manifest).hexdigest()


def _receipts(tmp_path,rows,**top):
    path=tmp_path/'receipts.json'
    path.write_text(json.dumps({'schema':consumer.RECEIPTS_SCHEMA,'receipts':rows,**top}),encoding='utf-8')
    return path


def _row(digest,**changes):
    return {'manifest_sha256':digest,'reviewed_by':'B 팩 검토자','reviewed_at':'2026-11-09T18:00:00+09:00',**changes}


def test_receipt_boundaries_none_other_one_duplicate_and_new_sha_after_write(tmp_path):
    repo,digest=_receipt_repo(tmp_path)
    assert consumer.approved_manifest(repo,_receipts(tmp_path,[])) is None
    assert consumer.approved_manifest(repo,_receipts(tmp_path,[_row('0'*64)])) is None
    assert consumer.approved_manifest(repo,_receipts(tmp_path,[_row(digest)]))==digest
    assert consumer.approved_manifest(repo,_receipts(tmp_path,[_row(digest,reviewed_at='2026-11-09T09:00:00Z')]))==digest
    assert consumer.approved_manifest(repo,_receipts(tmp_path,[_row(digest),_row(digest)])) is None
    approved=_receipts(tmp_path,[_row(digest)])
    # --write로 목록이 다시 만들어지면 새 SHA는 이전 기록으로 승인되지 않는다.
    (repo/consumer.MANIFEST_PATH).write_bytes(b'{"files":{"changed":"x"}}\n')
    assert consumer.approved_manifest(repo,approved) is None
    assert consumer.receipt_state(repo,approved)==(None,None)


@pytest.mark.parametrize('case',['schema','extra-top','receipts-not-list','row-not-dict','extra-key','missing-key',
    'sha-upper','sha-short','reviewer-number','reviewer-blank','reviewer-padded','reviewer-control','time-text',
    'time-naive','time-date-only','time-number','incomplete-same-sha','duplicate-json-key','not-json'])
def test_malformed_receipt_document_is_rejected_not_skipped(tmp_path,case):
    repo,digest=_receipt_repo(tmp_path);rows=[_row(digest)];top={}
    path=tmp_path/'receipts.json'
    if case=='schema':top={'schema':'byul-source-review-receipts/0'}
    elif case=='extra-top':top={'note':'x'}
    elif case=='receipts-not-list':top={'receipts':{'0':_row(digest)}}
    elif case=='row-not-dict':rows=[[digest,'B','2026-11-09T18:00:00+09:00']]
    elif case=='extra-key':rows=[_row(digest,comment='ok')]
    elif case=='missing-key':rows=[{k:v for k,v in _row(digest).items() if k!='reviewed_at'}]
    elif case=='sha-upper':rows=[_row(digest.upper())]
    elif case=='sha-short':rows=[_row(digest[:40])]
    elif case=='reviewer-number':rows=[_row(digest,reviewed_by=1)]
    elif case=='reviewer-blank':rows=[_row(digest,reviewed_by=' ')]
    elif case=='reviewer-padded':rows=[_row(digest,reviewed_by=' B ')]
    elif case=='reviewer-control':rows=[_row(digest,reviewed_by='B\nC')]
    elif case=='time-text':rows=[_row(digest,reviewed_at='x')]
    elif case=='time-naive':rows=[_row(digest,reviewed_at='2026-11-09T18:00:00')]
    elif case=='time-date-only':rows=[_row(digest,reviewed_at='2026-11-09')]
    elif case=='time-number':rows=[_row(digest,reviewed_at=20261109)]
    elif case=='incomplete-same-sha':rows=[_row(digest),{'manifest_sha256':digest}]
    if case=='duplicate-json-key':
        path.write_text('{"schema":"%s","receipts":[],"receipts":[%s]}'%(consumer.RECEIPTS_SCHEMA,json.dumps(_row(digest))),encoding='utf-8')
    elif case=='not-json':
        path.write_text('{"schema":',encoding='utf-8')
    else:
        path=_receipts(tmp_path,rows,**top)
    with pytest.raises(ValueError,match='SOURCE_REVIEW_RECEIPTS_INVALID'):
        consumer.approved_manifest(repo,path)
    assert consumer.receipt_state(repo,path)==(None,'SOURCE_REVIEW_RECEIPTS_INVALID')


def _synthetic(repo,monkeypatch):
    path=repo/consumer.MANIFEST_PATH;path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'schema':consumer.MANIFEST_SCHEMA,'paths':consumer.PINNED_PATHS,'files':consumer.scan(repo)}),encoding='utf-8')
    monkeypatch.setattr(consumer,'REVIEWED_COMMON_COMMIT',consumer.manifest_id(repo))
    monkeypatch.setattr(consumer,'_helper',lambda _:original)
    monkeypatch.setattr(consumer,'_verify_imports',lambda _:None)


@pytest.mark.parametrize('mutation',['dirty','staged','ignored-shadow','cache-source','assume-unchanged'])
def test_same_whole_scope_gate_rejects_changed_source_and_import_shadows(overlay_repo,monkeypatch,mutation):
    repo,_,_=overlay_repo;_synthetic(repo,monkeypatch)
    target=repo/'update/src/guard/executors/common.py'
    if mutation=='ignored-shadow':
        (repo/'.git/info/exclude').write_text('update/src/guard/shadow.py\n');(repo/'update/src/guard/shadow.py').write_text('SHADOW=True\n')
    elif mutation=='cache-source':
        path=target.parent/'__pycache__';path.mkdir();(path/'shadow.py').write_text('SHADOW=True\n')
    else:
        raw=target.read_bytes()
        if mutation=='assume-unchanged':git(repo,'update-index','--assume-unchanged',str(target.relative_to(repo)))
        target.write_bytes(b'COMMON=False\n')
        if mutation in {'staged','index-only'}:git(repo,'add',str(target.relative_to(repo)))
    with pytest.raises(ValueError,match='pinned source'):consumer._verify_manifest(repo.resolve())


def test_fixed_B_helper_bytes_are_checked_before_import(tmp_path):
    path=tmp_path/'update/tools/b1_integration_cpu.py';path.parent.mkdir(parents=True);path.write_text('unreviewed')
    with pytest.raises(ValueError,match='REQ10_FIXED_B_HELPER_MISMATCH'):consumer._helper(tmp_path)


def test_foreign_product_import_rejected(tmp_path):
    with pytest.raises(ValueError,match='REQ10_COMMON_IMPORT_MISMATCH'):consumer._verify_imports(tmp_path)
