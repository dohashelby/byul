"""Keep PACK31 and the full REQ06 branch in an explicit DRAFT product pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.stored_response_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF
PACK_ID='PACK-KUBERNETES-STORED-RESPONSES'
LIMITATIONS=[
 'REQ06 SC02-03의A/B소유객체·상호조회/취소/previous_response연결/동일request_id덮어쓰기와Gateway/직통경로를유지한다. 선택하지않은범위는missing에남기며전체분기를줄이지않는다.',
 '기존일반adapter의mutating/안전프로브분류는변경하지않는다. lab=true/vLLM0.31명시프로필·두서로다른credential·정확ENV/target/model·기존4Pod세대에서소유실행UUID의닫힌객체만다룬다. rawID/응답/자격은저장하지않는다.',
 '정적responses router/serving/protocol3파일SHA와기업identity policy SHA는명시선고정입력이다. 원보관manifest와의정적일치이며현재이미지ancestry·코드실행·principal/producer/store활성인증이아니다.',
 'samePOL재실행도freshUUID를생성한다. 현재실행의생성응답ID·별칭/상태/모델구조와owner조회대조가없으면외부ID를사용하거나취소하지않는다. 우리프로브가붙인owner별칭은실제owner증명이아니다.',
 '취소정상대조는별도소유background객체의queued/in_progress와자기객체cancel/후속GET을요구한다. 동기completed취소오류·HTTP200·cancelled표시를권한거부나실제worker/GPU회수로취급하지않는다.',
 '자기previous참조와자기overwrite/후속GETmarker를정상대조로구분하고타주체의조회/인가·저장·체인·취소효과는개별HTTP사실/confirmed_violation=false후보로남긴다. 독립source/principal/권한정책/worker효과가없어전축UNKNOWN·normal_control.succeeded=null·producer/owner/worker/scopefalse다.',
 '정확awarePOL창최대600초·요청10초·실행300초·HTTP128회/응답2MiB·본문128KiB·객체20개와비저장continuation8개가상한이다. 누락/불완전/세대교체/restart/후속수집/첨부/부모정리오류에서도앞선유효관측을유지한다.',
 'cleanup은동일세대의소유cancel-target에한정한다. 저장결과의DELETE나공유worker재시작을가정하지않는다. POLretain-owned-lab-objects를명시하고남은객체hash·API취소와worker종료미검증을기록한다. 임의제3자객체·운영서버에는사용하지않는다.',
 '실제두principal/owner·공유·작업권한표·store/background지원·source/image/PID-start·새ENV/POL/소유권·정리책임과새시간승인이없는실제환경은미완료다. 원GPU창종료/추가Start0/마지막확인stopped·CI결제차단과부모A39/56/4·A119/27/2를유지한다.',
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/REQ-06.yaml'].decode('utf-8'))
 check.update(revision=4,executor='stored_responses',collection_profile=PROFILE,
  policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
  judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/REQ-06.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
  files_sha256=[{'path':n,'sha256':sha256_hex(v)} for n,v in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
 output.mkdir(parents=True)
 for name,data in changed.items():
  p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
 if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
 return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,'policy_values_created':False,'full_REQ06_complete':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
