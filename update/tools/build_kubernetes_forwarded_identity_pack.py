"""Preserve PACK31 and all REQ04 requirements in an explicit DRAFT product pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.forwarded_identity_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF
PACK_ID='PACK-KUBERNETES-FORWARDED-IDENTITY'
LIMITATIONS=[
 'REQ04 SC01-04의 원입력→정규화/발급→실제소비→인가신원/최종권한 대조와priority/session/rank제어분리를유지한다. 이번은닫힌프로브/양쪽정제기록의제품연결이며전체분기완료가아니다.',
 '정확ENV/target/session·양쪽producer source주장·기업identity policy SHA·aware최대600초창을명시한다. 실행시읽은해시를승인값으로삼지않는다. source SHA일치는생산자실행/인증의증명이아니다.',
 '기존4Pod소유전송에서같은allowed credential의전후정상/대체header 고정hello1token POST/v1/completions를Gateway로만전송한다. Header이름/identity_claim 또는operating_selector종류/값환경변수는명시하며raw값과credential은stdin전용이다.',
 'LAB=true/vLLM0.31의명시프로필만지원하며운영Gateway·IdP·TP/Service/TLS·native0.32·기타신원형식지원은추정하지않는다. 기존REQ01/03/11의입출력과판정의미는보존한다.',
 'gateway와engine의고정소유경로/guard-identity/data/<session>/forwarded-identity.json을기존nofollow/byte/세대/시간reader로별도수집한다. Gateway원문로그·auth설정·Podenv/argv를읽지않고정제기록만받는다.',
 '기록은role/ENV/target/run/source/header-kind/policy/request/call/operation관계와행별누락/중복/교체를검사한다. Source-ended/dropped/후속오류는미완결로남기며앞선관측을지우지않는다.',
 '동일eventID의내용교체와같은request의중복phase는유일귀속을막는다. hash같음/HTTP200/issuer주장·clientheader소비는trusted principal이나권한침해가아니다. producer/principal/scopefalse와전축UNKNOWN·normal_control.succeeded=null을유지한다.',
 '교체header가소비되거나같은transport credential에서principal주장이달라진경우는confirmed_violation=false후보다. 실제issuer/인가정책/배포source와Gateway→engine통신은별도독립근거가필요하다.',
 '원래runtime-mvp참고구현은static bearer LABframe이고실제IdP미결을명시한다. 원본수정·실제배포나새정제producer구현을추정하지않는다. 원GPU창종료/추가Start0·CI결제차단을유지한다.',
]

def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/REQ-04.yaml'].decode('utf-8'))
 check.update(revision=4,executor='forwarded_identity',collection_profile=PROFILE,
  policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
  required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
  judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/REQ-04.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
  files_sha256=[{'path':n,'sha256':sha256_hex(v)} for n,v in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
 output.mkdir(parents=True)
 for name,data in changed.items():
  p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
 if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
 return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,
  'policy_values_created':False,'full_REQ04_complete':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
