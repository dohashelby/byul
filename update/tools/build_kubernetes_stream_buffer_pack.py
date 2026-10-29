"""Preserve PACK31/full LIF04 SSE and debug branches in a DRAFT product pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.stream_buffer_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF
PACK_ID='PACK-KUBERNETES-STREAM-BUFFER'
LIMITATIONS=[
 'LIF04의SC07-03 느린SSE와SC07-04 실제debug buffering 두분기·원질문/기준을유지한다. 현재는닫힌수신제어와선택된실제transport단계의정제관측제품연결이며전체buffer/두분기완료가아니다.',
 '기존source/lab/image gate와default native8/기존9~11 bytes를유지하고12를명시선택한다. lab=true/vLLM0.31과고정Uvicorn0.54 source3파일의정확코드에만hook하며현재이미지포함/ancestry/worker실증은별도다. native0.32미지원이다.',
 '같은UUID의전후정상JSON과normal/slow/slow-close 고정hello8token SSE를선택engine/probe세대에서수행한다. 기존slow-reader-v1 chunk1..4096/간격1..250ms/최대32reads만지원하며임계close/EOF/DONE/완전포획을구분한다.',
 'REQUEST/SEND/실제FlowControl.drain/connection_lost를source/code·PID/start·요청/연결/부모call로묶는다. 선택asyncio-selector-TCP의실제write buffer·Linuxown socket TIOCOUTQ·전체process RSS와body bytes를구분하며프로브RSS·파일bytes·수신지연을서버buffer비용으로올리지않는다.',
 'operation시작/끝은기존await/call직전/직후에서기록하고관측I/O시간과구분한다. drain부모send의실제구간과귀속이유일하지않으면누락이다. 지원되지않은event loop/transport는null이며0bytes나안전PASS로채우지않는다.',
 '정확ENV/target/prepared·모델/alias·aware600초창과명시메모리/send대기/회수예산·수신프로필을요구한다. 실행중발견한hash·임의예산을자동승인하지않으며native생산자/정책/전체buffer인벤토리/클라이언트close시계·회수기한은독립근거가없어미완료다.',
 '관측된버퍼/전체process RSS/drain시간의예산초과는confirmed_violation=false후보다. process RSS는한연결의배타비용이아니다. 전축UNKNOWN·normal_control.succeeded=null·producer/inventory/release/scopefalse를유지한다.',
 '실제debug route/옵션/활성/관측주체·소스·정책은unverified/missing이다. 미확정debug payload나profiler제어를만들지않고실제비사용근거없이비적용으로올리지않는다. B OBS03전용허용필드판정은B가유지한다.',
 '선택한native session만seal/capture한다. worker/GPU/다른세션종료·임의권한확대는하지않는다. 누락/교체/restart/후속수집·첨부·부모정리실패의앞선안전관측과독립FAIL을보존하고원문응답·헤더·자격·socket주소·예외를저장하지않는다.',
 '실제준비에는현재ENV/POL/source/image/model전체inventory·실제부하/역압력·buffer종류별생산자·시계/소유권/정리책임이필요하다. 원GPU창종료/추가Start0/마지막확인stopped·기존비용/승인경계·CI결제차단과부모A39/56/4·A119/27/2를유지한다.',
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/LIF-04.yaml'].decode('utf-8'))
 check.update(revision=4,executor='stream_buffer',collection_profile=PROFILE,
  policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
  judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/LIF-04.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
  files_sha256=[{'path':n,'sha256':sha256_hex(v)} for n,v in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
 output.mkdir(parents=True)
 for name,data in changed.items():
  path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
 if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
 return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,'policy_values_created':False,'full_LIF04_complete':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
