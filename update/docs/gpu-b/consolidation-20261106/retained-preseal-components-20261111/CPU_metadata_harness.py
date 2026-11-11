"""Consume the NEW S2 metadata via existing B entry/assembly data functions.

Only CPU path translation substitutes deployment; no ZIP/main/controller run.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def main():
    d,s2,receipt=map(lambda s:Path(s).absolute(),sys.argv[1:4]);f=s2/'fixed43/files'
    raw=(s2/'metadata-source-map-build-receipt-v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='671b30ebbabe801e34df8a528d8431cd33b8068a576fc5973d3603d735cba558'
    record=json.loads(raw);plan=json.loads((s2/'fixed43/file-install-plan.json').read_bytes())
    request=json.loads((f/'040-whole-fixed-request.json').read_bytes())
    inputs=json.loads((f/'015-assembly-inputs.json').read_bytes())
    assert record['helper_sha256']=='f868ee1d9c4bbb0c56c981691d08b91476408aa93c34239193846eaeeed6df96'
    assert not record['actual_ENV_target_generated'] and record['old_R8_paths_reference_only']
    assembly=load(f/'007-config_assembly.py','s5_actual_S2_assembly')
    entry=load(f/'039-whole_entry.py','s5_actual_S2_entry')
    tree=ast.parse((f/'039-whole_entry.py').read_bytes());cl=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FrozenCaller')
    init=next(n for n in cl.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    skip=next(i for i,n in enumerate(init.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=='modules' for t in n.targets))
    stop=next(i for i,n in enumerate(init.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='built' for t in n.targets))
    before=compile(ast.Module(body=init.body[:skip],type_ignores=[]),str(f/'039-whole_entry.py'),'exec')
    after=compile(ast.Module(body=init.body[skip+1:stop+1],type_ignores=[]),str(f/'039-whole_entry.py'),'exec')
    native={'argv':sys.orig_argv,'uid':os.getuid(),'gid':os.getgid(),'python':sys.version.split()[0],
            'flags':{'I':sys.flags.isolated,'S':sys.flags.no_site,'B':sys.dont_write_bytecode},
            'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'S2_metadata_sha256':hashlib.sha256(raw).hexdigest(),'entry_sha256':hashlib.sha256((f/'039-whole_entry.py').read_bytes()).hexdigest(),
            'assembly_sha256':hashlib.sha256((f/'007-config_assembly.py').read_bytes()).hexdigest()}
    assert os.geteuid()==0
    cases=[];fd_before=len(os.listdir('/proc/self/fd'))
    with tempfile.TemporaryDirectory(prefix='s5-preseal-metadata-edge-') as tmp:
        root=Path(tmp);mapping={};reads=[]
        for row in plan['files']:
            source=s2/'fixed43'/row['local_file'];blob=source.read_bytes()
            assert len(blob)==row['bytes'] and hashlib.sha256(blob).hexdigest()==row['sha256']
            mapped=record['source_map'][row['local_file']]['output']
            assert mapped['bytes']==len(blob) and mapped['sha256']==row['sha256']
            target=root/row['local_file'];target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(blob);target.chmod(0o600);os.chown(target,row['owner'],1000 if row['owner']==1000 else 0)
            mapping[row['guest']]=target
        actual_read=entry.read_pin
        def cpu_read(ref,maximum=3*1024**2):
            # Values/owner/SHA are the actual S2 refs; only destination path is translated.
            rebound=dict(ref,path=str(mapping[ref['path']]))
            data=actual_read(rebound,maximum)
            reads.append({'guest_reference':ref,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
            return data
        entry.read_pin=cpu_read
        def consume(value):
            local=dict(entry.__dict__,request=value,self=SimpleNamespace())
            exec(before,local)
            local['self'].modules={'config_assembly':assembly}  # No controller/module fanout execution.
            exec(after,local)
            return local
        result=consume(request);built=result['built'];H=record['helper_sha256'];helper=inputs['shared']['capture_helper']
        assert built['asset_pins'][helper]==inputs['asset_pins'][helper]
        assert built['asset_pins'][helper]['sha256']==H and built['asset_pins'][helper]['bytes']==20279
        assert built['source_binding']['commit']=='2cb01e9f08f9a123b8fffff00f9e798060115db9'
        assert built['source_binding']['manifest_sha256']=='ac423c8243af7b824bd66f7dadc5bd4ef5d827026a86d66f0c32dd28f3b4cf8b'
        assert built['gpu_ready'] is False and built['product_complete'] is False
        cases.append({'name':'actual_S2_040_to_015_assets_to_existing_assemble','passed':True,
                      'helper_asset':built['asset_pins'][helper],'source_binding':built['source_binding'],
                      'gpu_ready':False,'read_count':len(reads)})
        base=result['base'];asset_bytes=result['assets']
        broken=copy.deepcopy(inputs);broken['asset_pins'][helper]['sha256']='7ed0879319b77895234108418fe536d9d9d3c5155f7f03660b1129d3cda5faf8'
        try:assembly.assemble(base_config_bytes=base,base_config_sha256=entry.BASE_CONFIG_SHA,
                asset_bytes=asset_bytes,**{k:v for k,v in broken.items() if k!='base_config_sha256'})
        except ValueError as e:cases.append({'name':'S2_asset_pin_stale_hash_counterfactual_refused','passed':str(e).startswith('SUP16_CFG_ASSET_BYTES_CHANGED:'),'error':str(e)})
        else:cases.append({'name':'S2_asset_pin_stale_hash_counterfactual_refused','passed':False})
        stale=copy.deepcopy(request);stale['assembly_inputs']['sha256']=record['source_map']['files/015-assembly-inputs.json']['origin']['input']['sha256']
        try:consume(stale)
        except ValueError as e:cases.append({'name':'S2_040_stale_015_reference_counterfactual_refused','passed':str(e)=='SUP16_ENTRY_INPUT_BYTES_OR_IDENTITY_CHANGED','error':str(e)})
        else:cases.append({'name':'S2_040_stale_015_reference_counterfactual_refused','passed':False})
    cases.append({'name':'all_FDs_restored','passed':fd_before==len(os.listdir('/proc/self/fd'))})
    out={'scope':'EXPLICIT_SYNTHETIC_CPU_NEW_S2_METADATA_EDGE_ONLY','native':native,'cases':cases,
         'checks':len(cases),'failures':[c['name'] for c in cases if not c['passed']],
         'original_S2_source_map':str(s2/'metadata-source-map-build-receipt-v1.json'),'actual_read_refs':reads,
         'path_translation':'CPU substitute for deployment only; logical R8 paths remain reference-only',
         'old_main_or_controller_or_module_fanout_executed':False,'S2_files_modified':False,
         'archive_opened_or_CRC_repeated':False,'actual_ENV_target_generated':False,'new_GPU_AWS':0,'operation_ready':False}
    with receipt.open('x',encoding='utf-8') as stream:json.dump(out,stream,indent=2);stream.write('\n')
    print(json.dumps({'receipt':str(receipt),'checks':len(cases),'failures':out['failures']}))
    return bool(out['failures'])


if __name__=='__main__':raise SystemExit(main())
