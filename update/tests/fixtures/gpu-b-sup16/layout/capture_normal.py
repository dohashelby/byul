"""S2-only normal native17 sealing/capture via existing bounded reader.

No engine creation/restart, product verdict, paired policy or AWS management.
"""
from pathlib import Path
import argparse,hashlib,json,sys,traceback
def verify_source(repo,commit,manifest):
    helper=Path(__file__).with_name('source_owner_verify.py');raw=helper.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9':raise ValueError('SUP16_SOURCE_OWNER_HELPER_CHANGED')
    namespace={'__name__':'_owned_sup16_source_verify','__file__':str(helper)}
    exec(compile(raw,str(helper),'exec',dont_inherit=True),namespace)
    return namespace['verify_source_owned'](repo,commit,manifest)

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['source-root','environment','target','prepared','output']:p.add_argument('--'+n,type=Path,required=True)
 for n in ['source-commit','manifest-sha256','tool-sha256']:p.add_argument('--'+n,required=True)
 p.add_argument('--kubectl-binary',default='kubectl');p.add_argument('--execute',action='store_true')
 a=p.parse_args();sys.path[:0]=[str(a.source_root/'update/src'),str(a.source_root/'update')]
 verify_source(a.source_root,a.source_commit,a.manifest_sha256)
 from guard.collectors.kubernetes_lifecycle import load_inputs
 from guard.collectors.model_read_scope import validate_binding
 from guard.collectors.native_model_file_reads import analyze
 from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
 from guard.executors.lifecycle_session import capture_session
 from guard.models import canonical_json
 from guard.update_validation import reserve_output
 if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=a.tool_sha256:raise ValueError('SUP16_NORMAL_CAPTURE_SOURCE_CHANGED')
 env,target,prepared=load_inputs(a.environment,a.target,a.prepared);validate_binding(prepared,env,target)
 if (env.engine.name,env.engine.version,env.engine.image_digest)!=('vllm','0.31.0',
  'vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b'):
  raise ValueError('SUP16_NORMAL_CAPTURE_EXISTING_GPU_SUPPORT_MISMATCH')
 if prepared['profile']!='vllm-native-lifecycle-metadata/17' or target.get('lab') is not True:
  raise ValueError('SUP16_NORMAL_CAPTURE_NATIVE17_LAB_REQUIRED')
 if not a.execute:
  print(json.dumps({'validated_inputs':True,'capture_executed':False,'engine_or_AWS_started':False}));return
 out=reserve_output(a.output,protected=[a.source_root,a.environment,a.target,a.prepared])
 report={'capture_executed':False,'source':a.source_commit,'errors':[],'security_verdict_generated':False,
  'scope':'normal live native diagnostic; paired SUP16 product execution not performed'}
 reader=None
 try:
  # One reader keeps one original120s budget across seal, capture and report.
  reader=KubernetesLifecycleReader(target,env,prepared,binary=a.kubectl_binary,duration=120)
  reader.snapshot()
  report['seal']=reader.seal_session()
  engine=reader.binding['engine']
  packet=capture_session(reader,target,container=engine['container'],image=engine['image'],prepared=prepared,
   owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
  (out/'capture.json').write_bytes(canonical_json(packet))
  report['capture_executed']=True
  analysis=analyze(packet,prepared)
  (out/'native-analysis.json').write_bytes(canonical_json(analysis))
  report['model_root_read_candidates']=len(analysis['candidates']);report['missing']=analysis['missing']
 except BaseException as exc:
  report['errors'].append({'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
  traceback.print_exc()
 finally:
  if reader is not None:
   try:report['transport']=reader.report()
   except BaseException as exc:report['errors'].append({'phase':'report','type':type(exc).__name__,'message':str(exc)})
  (out/'capture-receipt.json').write_bytes(canonical_json(report))
 print(json.dumps(report,sort_keys=True))
 if report['errors']:raise SystemExit(1)
if __name__=='__main__':main()
