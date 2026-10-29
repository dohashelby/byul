"""B의 확정 CPU proposal 정의를 같은 Ref로 준비한다. 실행·정책 승인은 하지 않는다."""
from pathlib import Path
import argparse,json
import yaml
from guard.executors.structured_tools import SOURCE_COMMIT,judge_params
from guard.executors.structured_tools_cpu import TARGET031_COMMIT
from guard.models import CheckDef,Environment,Policy,canonical_json,load_pack,sha256_hex

PACK_REFS={
    'reference':{'kind':'PACK','id':'PACK-B-REQ10-CPU-PROPOSAL','revision':1,'sha256':'4157e269c2851e25f6c5b6bbbfcd224db13877053fc2dca7b82e45e0ea765ec6'},
    'vllm-031':{'kind':'PACK','id':'PACK-B-REQ10-031-CPU-PROPOSAL','revision':1,'sha256':'27da82f96dd8b5d5e4f9ccc7efb45a8be9c9a9d118294fddf9f1a7617745f7d1'},
}

def prepare(output,profile,*,origin,model):
    output=Path(output).resolve()
    if profile not in PACK_REFS or origin not in {'EXAMPLE','DECLARED'} or type(model) is not str or not model:
        raise ValueError('REQ10_CPU_INPUT_PROFILE_INVALID')
    if output.exists() or output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('NEW_EXTERNAL_REQ10_CPU_DIRECTORY_REQUIRED')
    target031=profile=='vllm-031';commit=TARGET031_COMMIT if target031 else SOURCE_COMMIT
    executor='structured_tools_target031_cpu' if target031 else 'structured_tools_cpu'
    # Same explicit definition as B fixed57a7537 req10_tools_cpu_pipeline.
    # The fixed Ref assertion below rejects any semantic or byte-format drift.
    specs={'structured_tools_validator_violation':'bool','structured_tools_controls':'dict[str, outcome]',
        'structured_tools_controls_planned':'list[str]','structured_tools_cpu_rows':'list[dict]',
        'runner_consumption_verified':'bool'}
    check=CheckDef(id='REQ-10',revision=1,area='REQ',purpose='최종 입력 검증',
        question='고정 참조 CPU에서 schema/tool 조합의 요청 검증·sampling·xgrammar 변환 경로는?',
        review_order='reference_baseline_first',applies_when={'always':True},
        normal_control='정상 4조합·각 사례 뒤 정상 변환 5건의 경계 수용' if target031 else '고정 4개 정상 조합의 요청·sampling·backend 경계 수용',
        challenge_steps=['named-json 금지 조합의 request validator 경계 검증'],
        required_observations=[{'key':k,'type':v} for k,v in specs.items()],
        satisfied_if='참조 CPU 경계 관측; 전체 보안 PASS 규칙 없음',
        fail_criteria='named-json 금지 조합의 실제 요청 클래스 수용',
        unknown_criteria='미수집·미지원 또는 실제 배포·runner 경계 미확인',
        executor=executor,judge={'plugin':'rule_table','params':judge_params()},
        limitations=['reference CPU 직접 호출. 실제 서버 전처리·배포·runner·서비스 생존 미측정.'])
    raw=yaml.safe_dump(check.model_dump(mode='json'),allow_unicode=True,sort_keys=False).encode()
    manifest={'pack_id':PACK_REFS[profile]['id'],'revision':1,'engines':['vllm'],'checks':['REQ-10'],
        'files_sha256':[{'path':'checks/REQ-10.yaml','sha256':sha256_hex(raw)}],'review':{'status':'DRAFT'}}
    from guard.models.checkdef import build_pack
    built=build_pack(manifest,{'checks/REQ-10.yaml':raw})
    if built.ref().model_dump(mode='json')!=PACK_REFS[profile]:raise ValueError('FIXED_B_REQ10_PROPOSAL_REF_MISMATCH')
    folder=output/'proposal-pack';(folder/'checks').mkdir(parents=True)
    (folder/'checks/REQ-10.yaml').write_bytes(raw);(folder/'pack.yaml').write_bytes(yaml.safe_dump(manifest,sort_keys=False).encode())
    pack=load_pack(folder)
    env=Environment(id='ENV-B-REQ10-031-CPU-REFERENCE' if target031 else 'ENV-B-REQ10-CPU-REFERENCE',data_origin=origin,
        engine={'name':'vllm','version':'reference-'+commit},model={'id':model}).seal()
    pol=Policy(id='POL-B-REQ10-031-CPU-PROPOSAL' if target031 else 'POL-B-REQ10-CPU-PROPOSAL',data_origin=origin,
        status='DRAFT',pack=pack.ref(),scope={'include':['REQ-10']}).seal()
    (output/'environment.json').write_bytes(canonical_json(env.to_dict()));(output/'policy.json').write_bytes(canonical_json(pol.to_dict()))
    return {'pack_ref':pack.ref().model_dump(mode='json'),'environment_ref':env.ref().model_dump(mode='json'),
        'policy_ref':pol.ref().model_dump(mode='json'),'executor':executor,
        'implementation':'source031' if target031 else 'reference-cpu','source_commit':commit,
        'origin':origin,'actual_execution':False,'enterprise_policy_approval':False,
        'source':'B fixed57a7537 existing CPU proposal; PACK31 numeric/Kubernetes path unchanged'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',choices=list(PACK_REFS),required=True);parser.add_argument('--origin',choices=['EXAMPLE','DECLARED'],required=True);parser.add_argument('--model',required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.output,args.profile,origin=args.origin,model=args.model),ensure_ascii=False))
if __name__=='__main__':main()
