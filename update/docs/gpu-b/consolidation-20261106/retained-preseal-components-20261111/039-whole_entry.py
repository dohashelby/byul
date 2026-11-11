"""Pinned SUP16 caller composition; no discovery, installation or AWS client.

Static binding and replay are read-only. Explicit execution requires caller-
supplied current root600 proofs, the original active timer and original rc
node/process identity. This creates no current proof or additional allowance.
"""
import argparse
import base64
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import traceback
import types
import zipfile

PHASES=('normal-before','challenge-partial','normal-recovered')
MODULE_PINS={'controller': '02899e3e8d9f46516eb09855f516ae074780c312988571f2f8fba20f15569b52', 'root_contract': '27eef88640bb654236102a9e176d3bc4a3f700caeb40abd952c7dd675825d745', 'outer': '059fb04cc8e2eae69d9633b14c2e6e0b501382aff3241e69ce7b29c4dcd5b988', 'adapter': '7d839a0a0a8385822a9d8262739e0c89093241262c8e638deb3cc425f01876fc', 'actionwise': 'cdeca47d1a7d40f5c0eda0f5e0d208e5c0918c5b00b9b81b86cade6f2684a2ce', 'aggregate': 'c56640251fd2406d3734b8109725d5fe1394965e90b0e7222472b68ea924c3ce', 'supervisor': '6c9584521401b8138f96be2882245b2081cea20b82094a6f2801b408ecf3a48c', 'config_assembly': '76fb2cce4e6e8b00bded26e9284748ebe4ffd6995b16f079bd74f3bb5c5f937e', 'context_binder': '97d0cede6e68eccabd7dafc2a76e85d84ac2b0807969315a87fc894ee4d28359', 'state_factories': 'd04b853567fd02b98e3f522655ec12f0b0af529e3e7adbcf73a488c4a2aaf76c', 'bounded_preflight': 'ff20552ba29efaa4900cdb096d9771b7b82959bed71058e58b59a17918f0b153', 'actual_assembly': '5a4f38bc2dd93a26aca97319f90b1187aea6e096f18dff341c7f124b06472fee', 'deadlines': 'cdfd792252cd3f9f0218bc5a790eb816b3f3974a3e14459a4cd2d17f348f3f17', 'join': 'fb276ca37b703958856886a0819a0755a205859739ea12217eb4e97debaa360a'}
BASE_CONFIG_SHA='3a102855bd3da2313a21406931e8e42a1dd37c0b4029bdf4e4b03f45bd6a09b2'
KINDS={'normal':'observe','preserve':'preserve','quiesce':'quiesce',
       'archive':'archive','aggregate':'archive'}


def require(ok, code):
    if not ok:raise ValueError(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def parse(raw):
    def pairs(rows):
        value={}
        for key,item in rows:
            require(key not in value,'SUP16_ENTRY_DUPLICATE_JSON_KEY')
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:require(False,'SUP16_ENTRY_FINITE_JSON_REQUIRED'))


def pointer(value, owner=None):
    require(type(value) is dict and set(value)=={'path','owner','sha256'},
            'SUP16_ENTRY_EXACT_FILE_POINTER_REQUIRED')
    require(type(value['path']) is str and Path(value['path']).is_absolute()
            and '..' not in Path(value['path']).parts,'SUP16_ENTRY_ABSOLUTE_PATH_REQUIRED')
    require(type(value['owner']) is int and value['owner'] in (0,1000)
            and (owner is None or value['owner']==owner),'SUP16_ENTRY_EXACT_OWNER_REQUIRED')
    require(type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}',value['sha256']),
            'SUP16_ENTRY_EXACT_BYTES_SHA_REQUIRED')


def read_pin(value, maximum=3*1024**2):
    """Bounded bootstrap read; reject a FIFO both before and after open."""
    pointer(value)
    path=Path(value['path'])
    require(not any(p.is_symlink() for p in (path,*path.parents)),
            'SUP16_ENTRY_NO_INPUT_SYMLINK')
    before_path=path.lstat()
    def regular(meta):
        return (stat.S_ISREG(meta.st_mode) and meta.st_uid==value['owner']
            and meta.st_nlink==1 and stat.S_IMODE(meta.st_mode)==0o600
            and 0<meta.st_size<=maximum)
    require(regular(before_path),'SUP16_ENTRY_OWN_REGULAR600_INPUT_REQUIRED')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        require(regular(before),'SUP16_ENTRY_OWN_REGULAR600_FD_REQUIRED')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(maximum+1)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,
                            s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        require(len(raw)==before.st_size and identity(before)==identity(before_path)
            and identity(before)==identity(os.fstat(fd)) and identity(before)==identity(path.lstat())
            and sha(raw)==value['sha256'],'SUP16_ENTRY_INPUT_BYTES_OR_IDENTITY_CHANGED')
        return raw
    finally:os.close(fd)


def load_module(name, raw, path):
    module=types.ModuleType('_sup16_fixed_'+name)
    module.__file__=str(path)
    exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
    return module


class FrozenCaller:
    def __init__(self, request):
        require(type(request) is dict and set(request)=={'module_files','base_config',
            'assembly_inputs','outer_static','role_configs','aggregate_config','aggregate_frame'},
            'SUP16_ENTRY_COMPLETE_FIXED_REQUEST_REQUIRED')
        require(set(request['module_files'])==set(MODULE_PINS),
                'SUP16_ENTRY_EXACT_FROZEN_COMPONENT_SET_REQUIRED')
        self.request=parse(canonical(request))
        raw_modules={}
        for name,pin in MODULE_PINS.items():
            ref=self.request['module_files'][name]
            pointer(ref,0)
            require(ref['sha256']==pin,'SUP16_ENTRY_FROZEN_COMPONENT_CHANGED:'+name)
            raw_modules[name]=read_pin(ref)
        # Read every declared code byte before executing any selected module.
        self.modules={name:load_module(name,raw_modules[name],self.request['module_files'][name]['path'])
                      for name in MODULE_PINS}
        self.raw_modules=raw_modules
        m=self.modules
        pointer(self.request['base_config'],0)
        require(self.request['base_config']['sha256']==BASE_CONFIG_SHA,
                'SUP16_ENTRY_ORIGINAL_BASE_CONFIG_PIN_REQUIRED')
        base=read_pin(self.request['base_config'])
        for key in ('assembly_inputs','outer_static','aggregate_config'):
            pointer(self.request[key],0)
        inputs=parse(read_pin(self.request['assembly_inputs']))
        require(set(inputs)=={'base_config_sha256','source_binding','roles','shared','aggregate','asset_pins'}
            and inputs['base_config_sha256']==BASE_CONFIG_SHA,'SUP16_ENTRY_FIXED_ASSEMBLY_INPUTS_REQUIRED')
        assets={}
        for path,pin in inputs['asset_pins'].items():
            assets[path]=read_pin({'path':path,'owner':pin['owner'],'sha256':pin['sha256']})
            require(len(assets[path])==pin['bytes'],'SUP16_ENTRY_ASSET_EXACT_SIZE_REQUIRED')
        built=m['config_assembly'].assemble(base_config_bytes=base,
            base_config_sha256=BASE_CONFIG_SHA,asset_bytes=assets,**{k:v for k,v in inputs.items()
                if k not in ('base_config_sha256','asset_pins')},asset_pins=inputs['asset_pins'])
        require(read_pin(self.request['outer_static'])==canonical(built['outer_kwargs']),
                'SUP16_ENTRY_OUTER_STATIC_BYTES_MISMATCH')
        require(set(self.request['role_configs'])==set(PHASES),
                'SUP16_ENTRY_EXACT_ROLE_CONFIG_FILES_REQUIRED')
        for phase in PHASES:
            ref=self.request['role_configs'][phase]
            pointer(ref,0)
            require(read_pin(ref)==built['role_config_bytes'][phase],
                    'SUP16_ENTRY_ROLE_CONFIG_BYTES_MISMATCH:'+phase)
        require(read_pin(self.request['aggregate_config'])==built['aggregate_config_bytes'],
                'SUP16_ENTRY_AGGREGATE_CONFIG_BYTES_MISMATCH')
        self.static=built['outer_kwargs']
        frame=self.request['aggregate_frame']
        require(type(frame) is dict and set(frame)==m['aggregate'].FRAME_KEYS,
                'SUP16_ENTRY_EXACT_AGGREGATE_FRAME_REQUIRED')
        require(frame['ROOT']==self.static['aggregate_root']
            and frame['ARCHIVE']==self.static['aggregate_alias']
            and frame['RUN']==self.static['aggregate_config']['run'],
                'SUP16_ENTRY_SAME_AGGREGATE_IDENTITY_REQUIRED')
        require(all(str(Path(role['code']).parent)==frame['CODE']
            and str(Path(role['runtime']).parent)==frame['RUNTIME']
            for role in self.static['roles'].values()),'SUP16_ENTRY_SAME_PARENT_SNAPSHOT_ROOTS_REQUIRED')
        self.current=None
        self.started=False
        original=m['actual_assembly'].load_original_api_data(
            assets[self.static['shared']['ready_controller']],
            stdlib_namespace=dict(Path=Path,base64=base64,datetime=datetime,hashlib=hashlib,
                json=json,os=os,re=re,stat=stat,sys=sys,time=time,zipfile=zipfile,traceback=traceback))
        binder_inputs={'binder_module':m['context_binder'],'state_module':m['state_factories'],
            'bounded_module':m['bounded_preflight'],'current_controller_module':m['controller'],
            'root_contract':m['root_contract'],'original_api_module':original,
            'model_proof_pointers':None,'global_preflight_pointer':None}
        def additional(role,kind):
            require(self.current is not None,'SUP16_ENTRY_CURRENT_PROOFS_REQUIRED_BEFORE_PHASE')
            return self.current['additional'][role['phase']][kind]
        def serial(role,kind):
            context=self.outer.contexts.get(role['phase'])
            return 0 if context is None else context.serial
        self.supervisor=m['supervisor'].Supervisor(root_contract=m['root_contract'],
            write_new=m['controller'].write_new,owner=0,original_budget_keys=KINDS,
            additional_deadlines=additional,serial=serial)
        Outer=m['actionwise'].actionwise_outer_class(m['outer'],outer_raw=raw_modules['outer'],
            adapter_raw=raw_modules['adapter'])
        def archive():
            require(self.current is not None,'SUP16_ENTRY_CURRENT_AGGREGATE_PROOF_REQUIRED')
            return self.current['aggregate_archive']()
        self.outer=Outer(join_module=m['join'],adapter_module=m['adapter'],
            assembly_module=m['actual_assembly'],deadlines_module=m['deadlines'],
            **self.static,read_regular=m['controller'].read_regular,write_new=m['controller'].write_new,
            binder_inputs=binder_inputs,phase_guard_factory=self.supervisor.phase_guard_factory,
            assert_phase_terminal=self.supervisor.assert_phase_terminal,
            stop_deadlines=additional,aggregate_archive=archive)

    def describe(self):
        return {'fixed_components':dict(MODULE_PINS),'configuration_identity':self.outer.identity,
            'role_configuration_sha256':{k:v['configuration_sha256'] for k,v in self.outer.roles.items()},
            'current_proofs_bound':self.current is not None,'runtime_started':self.started,
            'actual_GPU_or_AWS_result_claimed':False,'full_product_pair_connected':False}

    def replay_only(self):
        # Original completed-receipt reader: no guard entry, write, timer,
        # source import, namespace delete, archive submission or current proof.
        require(not self.started,'SUP16_ENTRY_NO_REPLAY_DURING_ACTIVE_RUN')
        return self.outer._replay()

    def execute(self, current_inputs):
        require(not self.started,'SUP16_ENTRY_ALREADY_STARTED_NO_RESUBMISSION')
        require(type(current_inputs) is dict and set(current_inputs)==
            {'health','positive_preflight','model_proofs','original_additional_deadlines'},
            'SUP16_ENTRY_COMPLETE_CURRENT_PROOF_INPUTS_REQUIRED')
        require(type(current_inputs['model_proofs']) is dict
            and set(current_inputs['model_proofs'])==set(PHASES),
            'SUP16_ENTRY_CURRENT_MODEL_PROOFS_REQUIRED')
        refs=[current_inputs['health'],current_inputs['positive_preflight'],
              *[current_inputs['model_proofs'][phase] for phase in PHASES]]
        for ref in refs:pointer(ref,0)
        # Missing/changed files stop here: no identity command/guard/mutation.
        health,preflight,*models=[parse(read_pin(ref)) for ref in refs]
        additional=current_inputs['original_additional_deadlines']
        require(type(additional) is dict and set(additional)==set((*PHASES,'aggregate')),
                'SUP16_ENTRY_EXPLICIT_ORIGINAL_DEADLINES_REQUIRED')
        frozen={}
        for phase in (*PHASES,'aggregate'):
            kinds=('aggregate',) if phase=='aggregate' else ('normal','preserve','quiesce','archive')
            require(type(additional[phase]) is dict and set(additional[phase])==set(kinds),
                    'SUP16_ENTRY_EXACT_ORIGINAL_DEADLINE_KEYS_REQUIRED')
            frozen[phase]={}
            for kind in kinds:
                values=additional[phase][kind]
                require(type(values) is list and all(type(v) in (int,float) and math.isfinite(v)
                    for v in values),'SUP16_ENTRY_ORIGINAL_FINITE_DEADLINES_REQUIRED')
                frozen[phase][kind]=tuple(values)
        config=self.static['aggregate_config']
        require(preflight['source']==config['fixed_config']['source']
            and preflight['configuration_sha256']==config['configuration_sha256']
            and preflight['no_timer_reset'] is True,'SUP16_ENTRY_SAME_CONFIG_CURRENT_PREFLIGHT_REQUIRED')
        m=self.modules
        require(sys.platform=='linux' and sys.version_info[:2]==(3,12)
            and not sys.flags.optimize and 'PYTHONOPTIMIZE' not in os.environ
            and Path(sys.executable)==m['controller'].SYSTEM_PY,
            'SUP16_ENTRY_ORIGINAL_SYSTEM_PYTHON_REQUIRED')
        m['controller'].validate_cleanup_python_flags({'isolated':sys.flags.isolated,
            'no_site':sys.flags.no_site,'dont_write_bytecode':sys.flags.dont_write_bytecode})
        total=m['deadlines'].original_total_deadline(health,preflight)
        # Reuse original immutable data-pin and model/prefix predicates early;
        # current role preflight still re-observes sameboot/node/source later.
        for phase,value in zip(PHASES,models):
            role=self.outer.roles[phase]
            if phase=='challenge-partial':
                require(value['destination']==role['model_root'] and value['source_modified'] is False
                    and value['new_SDK_interruption_or_recovery_experiment'] is False
                    and value['original_challenge_receipt_sha256']=='17236294c8ce581c3aca501ffd365ea660a2cd6e82689580138d510cafecfca9',
                    'SUP16_ENTRY_CURRENT_FIXED_CHALLENGE_PROOF_REQUIRED')
                row,=[r for r in value['files'] if r['name']=='model.safetensors']
                require(row['bytes']==10485760 and row['sha256']=='d72424507d4abf34517c53c61c31946db15f60de13eb6d0ec106c04b9c052af9',
                        'SUP16_ENTRY_CURRENT_FIXED_CHALLENGE_PIN_REQUIRED')
            else:
                require(value['model_root']==role['model_root']
                    and value['existing_exact_model_reused'] is True and value['model_input_modified'] is False,
                    'SUP16_ENTRY_CURRENT_NORMAL_MODEL_PROOF_REQUIRED')
        expected={key:preflight[key] for key in m['aggregate'].PROOF_KEYS}
        aggregate=m['aggregate'].make_aggregate_archive(controller_raw=self.raw_modules['controller'],
            frame=self.request['aggregate_frame'],configuration_pointer=self.request['aggregate_config'],
            preflight_pointer=current_inputs['positive_preflight'],expected_preflight=expected,
            selected_system_python=m['controller'].SYSTEM_PY)
        # The original already-active timer and exact selected node/process
        # guard are prerequisites, not newly minted approvals or clocks.
        m['root_contract'].identity()
        self.current={'additional':frozen,'aggregate_archive':aggregate,'original_total_deadline':total}
        self.outer.binder_inputs['global_preflight_pointer']=dict(current_inputs['positive_preflight'])
        self.outer.binder_inputs['model_proof_pointers']={k:dict(v) for k,v in current_inputs['model_proofs'].items()}
        self.started=True
        return self.outer.run(health=dict(current_inputs['health']),
                              positive_preflight=dict(current_inputs['positive_preflight']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixed-request',required=True)
    p.add_argument('--fixed-request-sha256',required=True)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true')
    mode.add_argument('--replay-only',action='store_true')
    p.add_argument('--current-inputs')
    p.add_argument('--current-inputs-sha256')
    args=p.parse_args()
    if args.execute:
        require(args.current_inputs is not None and args.current_inputs_sha256 is not None,
                'SUP16_ENTRY_EXECUTE_REQUIRES_CURRENT_INPUT_FILE_AND_SHA')
        current=parse(read_pin({'path':args.current_inputs,'owner':0,'sha256':args.current_inputs_sha256}))
    else:
        require(args.current_inputs is None and args.current_inputs_sha256 is None,
                'SUP16_ENTRY_CURRENT_INPUTS_ONLY_FOR_EXPLICIT_EXECUTE')
        current=None
    request=parse(read_pin({'path':args.fixed_request,'owner':0,'sha256':args.fixed_request_sha256}))
    caller=FrozenCaller(request)
    result=caller.execute(current) if args.execute else caller.replay_only() if args.replay_only else caller.describe()
    print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)


if __name__=='__main__':main()
