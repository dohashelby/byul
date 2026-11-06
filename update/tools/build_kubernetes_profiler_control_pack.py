"""Existing derived-DRAFT procedure for LIF14 direct control observations only."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import yaml
from guard.executors.kubernetes_profiler_control import OBSERVATIONS
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack

SEED_SHA='1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd'
# Integration main has baseline33; its LIF14 question/judge is unchanged.
# Keep exact historical32 support and reject any other revision/content pair.
SUPPORTED_SEEDS=frozenset({
    (32,SEED_SHA),
    (33,'74219b48b241775e4c4fa153a950b2a94029147f512c731f055e91cf945cd5b6'),
})


def derive(seed,output):
    seed,output=Path(seed),Path(output)
    original=load_pack(seed)
    ref=original.ref()
    if ref.id!='PACK-BASELINE' or (ref.revision,ref.sha256) not in SUPPORTED_SEEDS:
        raise ValueError('PROFILER_CONTROL_SEED_UNSUPPORTED')
    if output.exists() or output.is_symlink():raise ValueError('FRESH_DRAFT_OUTPUT_REQUIRED')
    manifest=yaml.safe_load((seed/'pack.yaml').read_bytes())
    files={row['path']:(seed/row['path']).read_bytes() for row in manifest['files_sha256']}
    check=yaml.safe_load(files['checks/LIF-14.yaml'])
    check['required_observations']=[{'key':key,'type':kind} for key,kind in OBSERVATIONS.items()]
    files['checks/LIF-14.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode()
    derived=deepcopy(manifest)
    derived.update(pack_id='PACK-KUBERNETES-PROFILER-CONTROL',revision=1,
        review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
        files_sha256=[{'path':name,'sha256':sha256_hex(raw)} for name,raw in sorted(files.items())])
    result=build_pack(derived,files)
    before=original.checks['LIF-14'].model_dump(mode='json');after=result.checks['LIF-14'].model_dump(mode='json')
    assert {k:v for k,v in before.items() if k!='required_observations'}=={k:v for k,v in after.items() if k!='required_observations'}
    output.mkdir(parents=True)
    for name,raw in files.items():
        destination=output/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
    (output/'pack.yaml').write_bytes(yaml.safe_dump(derived,allow_unicode=True,sort_keys=False).encode())
    if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref':original.ref().model_dump(mode='json'),'pack_ref':result.ref().model_dump(mode='json'),
        'unchanged_definition_bytes':76,'original_question_judge_criteria_preserved':True,
        'policy_values_created':False,'whole_LIF14_complete':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output)))


if __name__=='__main__':main()
