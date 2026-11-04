"""Prepare LIF14's existing pinned single-GPU fixture; never deploy or run it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from guard.adapters import vllm_profiler_sensor
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.models import canonical_json
from guard.update_validation import reserve_output
from tools.verify_req10_consumption import verify
from tools.build_kubernetes_profiler_control_pack import derive

TEMPLATE_SHA='787d4d447d807146d7c2f445e6f25248b721f20df2ec5a631dda16cfeab37ae7'
MODEL_MANIFEST_SHA='31bcec24aac23f235ee23292934bd20f1acf0abb2c10068820684052218fd7c5'
CHAT_SHA='e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e'


def reserve_preparation_output(output,*,protected):
    candidate=Path(output).expanduser().absolute()
    canonical=candidate.resolve()
    if any((parent/'.git').exists() for parent in (canonical,*canonical.parents)):
        raise ValueError('PREPARATION_OUTPUT_IN_GIT_WORKSPACE')
    # Existing helper rejects links before resolving '..' and checks both
    # directions against every source/model/input path before making a directory.
    result=reserve_output(candidate,protected=protected)
    result.chmod(0o700)
    return result


def digest_file(path):
    """No model copies, symlink following, unbounded RAM or silent replacement."""
    path=Path(path)
    if any(parent.is_symlink() for parent in (path,*path.parents)):
        raise ValueError('PREPARATION_INPUT_SYMLINK')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):raise ValueError('PREPARATION_INPUT_NOT_REGULAR')
        digest=hashlib.sha256();size=0
        while chunk:=os.read(fd,2*1024**2):size+=len(chunk);digest.update(chunk)
        key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if size!=before.st_size or key(before)!=key(os.fstat(fd)) or key(before)!=key(path.lstat()):
            raise ValueError('PREPARATION_INPUT_CHANGED')
        return size,digest.hexdigest()
    finally:os.close(fd)


def render(template,prepared,output,*,namespace,run_id,node,model,chat):
    """Only declared paths and the new native15 session replace old bindings."""
    if (re.fullmatch('[a-z0-9][a-z0-9-]{0,62}',namespace) is None
            or re.fullmatch('[a-z0-9][a-z0-9-]{0,62}',run_id) is None
            or re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}',node) is None):
        raise ValueError('PREPARATION_BINDING_INVALID')
    doc=json.loads(json.dumps(template))
    doc['metadata']['namespace']=namespace;doc['metadata']['labels']['guard.run']=run_id
    doc['spec']['template']['metadata']['labels']['guard.run']=run_id
    spec=doc['spec']['template']['spec'];spec['nodeName']=node;spec.pop('nodeSelector',None)
    engine,=spec['containers']
    args=engine['args'];position=args.index('--profiler-config')+1
    profiler=json.loads(args[position])
    if profiler!={'profiler':'torch','torch_profiler_dir':'/tmp/trace','torch_profiler_with_stack':False,'ignore_frontend':True}:
        raise ValueError('PREPARATION_PROFILER_TEMPLATE_CHANGED')
    profiler['torch_profiler_dir']='/guard-profiler-traces/'+prepared['session']
    args[position]=json.dumps(profiler,separators=(',',':'))
    trace,=[m for m in engine['volumeMounts'] if m['name']=='trace']
    trace.update(mountPath='/guard-profiler-traces',readOnly=False)
    paths={'sensor':str(output/'prepared15'),'data':str(output/'data'),'trace':str(output/'trace'),
        'model':str(model),'template':str(chat)}
    for volume in spec['volumes']:
        if volume['name'] in paths:volume['hostPath']['path']=paths[volume['name']]
    return doc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('template','model-root','model-manifest','chat-template','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('source-commit','manifest-sha256','namespace','run-id','node'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[2]
    if os.geteuid()!=1000:raise ValueError('PREPARATION_REQUIRES_EXISTING_FIXTURE_UID1000')
    if re.fullmatch('[0-9a-f]{40}',args.source_commit) is None or re.fullmatch('[0-9a-f]{64}',args.manifest_sha256) is None:
        raise ValueError('FIXED_SOURCE_REQUIRED')
    actual=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    if actual!=args.source_commit:raise ValueError('FIXED_SOURCE_MISMATCH')
    subprocess.run(['git','-C',str(repo),'diff','--exit-code','HEAD','--','update/src','update/release',
        'update/tools/lif14_stop_restart_prepare.py','update/tools/build_kubernetes_profiler_control_pack.py'],check=True)
    if digest_file(repo/'update/release/reviewed-source.json')[1]!=args.manifest_sha256:
        raise ValueError('FIXED_MANIFEST_MISMATCH')
    verify(repo)
    if digest_file(args.template)[1]!=TEMPLATE_SHA or digest_file(args.model_manifest)[1]!=MODEL_MANIFEST_SHA or digest_file(args.chat_template)[1]!=CHAT_SHA:
        raise ValueError('REVIEWED_FIXTURE_INPUT_CHANGED')
    model=args.model_root.resolve(strict=True)
    inventory=json.loads(args.model_manifest.read_bytes())
    for row in inventory['files']:
        path=model/row['path']
        if not path.resolve(strict=True).is_relative_to(model) or digest_file(path)!=(row['bytes'],row['sha256']):
            raise ValueError('REVIEWED_MODEL_CHANGED')
    output=reserve_preparation_output(args.output,protected=[repo,model,args.model_manifest,args.chat_template,args.template])
    prepared=prepare_assets(output/'prepared15',repo,profile=vllm_profiler_sensor.PROFILE)
    for name in prepared['assets']:(output/'prepared15'/name).chmod(0o600)
    if load_prepared(output/'prepared15/manifest.json')!=prepared:raise ValueError('PREPARED15_MISMATCH')
    (output/'data'/prepared['session']).mkdir(mode=0o700,parents=True)
    (output/'trace').mkdir(mode=0o700)
    doc=render(json.loads(args.template.read_bytes()),prepared,output,namespace=args.namespace,run_id=args.run_id,
        node=args.node,model=model,chat=args.chat_template.absolute())
    (output/'engine.json').write_bytes(canonical_json(doc))
    pack_receipt=derive(repo/'update/packs/PACK-BASELINE',output/'pack')
    receipt={'source_commit':actual,'source_manifest_sha256':args.manifest_sha256,'template_sha256':TEMPLATE_SHA,
        'prepared_manifest':str(output/'prepared15/manifest.json'),'prepared_sha256':hashlib.sha256(canonical_json(prepared)).hexdigest(),
        'pack':pack_receipt,'trace_root':'/guard-profiler-traces','trace_target':'/guard-profiler-traces/'+prepared['session'],
        'trace_target_initially_absent':True,'engine_started':False,'ENV_target_POLICY_created':False,
        'runtime_UID_PID_Pod_GPU_identity_bound':False,'factory_executed':False,'GPU_executed':False}
    (output/'preparation-receipt.json').write_bytes(canonical_json(receipt))
    print(json.dumps(receipt))


if __name__=='__main__':main()
