"""Materialize pinned B inputs through existing preparers; never execute engines."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tomllib

import yaml

from guard.executors import registry, template_validation, template_sandbox
from guard.models import canonical_json, load_pack
from guard.runtime.bounded import strict_json
from tools import gpu_a_run_inputs, prepare_checklist_cases

SELECTED=('SUP-03','SUP-08','REQ-08','REQ-09','REQ-10','REQ-07','SUP-13')


def pinned(pointer, base):
    if type(pointer) is not dict or set(pointer)!={'path','sha256'}:
        raise ValueError('B03_EXACT_INPUT_POINTER_REQUIRED')
    from guard.adapters.vllm_file_read_sensor import dependency
    path=Path(pointer['path'])
    path=path if path.is_absolute() else base/path
    raw=dependency(path,pointer['sha256'])
    return raw,dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def write(path,value):
    raw=value if isinstance(value,bytes) else canonical_json(value)
    with Path(path).open('xb') as stream:stream.write(raw)
    Path(path).chmod(0o600)
    return dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def materialize_plt01(spec,base,root,output):
    """Explicit EXAMPLE preparation only; retain original seven-check selection."""
    import ast
    from guard.models import Environment,sha256_hex
    from guard.executors.http_common import fill_model,request_model
    pins={}
    for relative,sha in spec['source_files'].items():
        _,pins[relative]=pinned({'path':str(root/relative),'sha256':sha},base)
    pack=load_pack(root/'update/packs/PACK-BASELINE')
    if pack.ref().model_dump(mode='json')!=spec['pack_ref']:
        raise ValueError('B03_PACK_INPUT_MISMATCH')
    env_raw,env_pin=pinned(spec['environment'],base)
    target_raw,target_pin=pinned(spec['target'],base)
    policy_raw,policy_pin=pinned(spec['permission_policy_source'],base)
    env=Environment.model_validate(strict_json(env_raw))
    target=strict_json(target_raw)
    if (not env.hash_ok() or env.data_origin.value!='EXAMPLE' or target.get('lab') is not True
            or target.get('environment_ref')!=env.ref().model_dump(mode='json')):
        raise ValueError('B03_EXISTING_PLT01_EXAMPLE_INPUT_REQUIRED')
    assignments=[n.value for n in ast.parse(policy_raw).body if isinstance(n,ast.Assign)
        and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='PERMISSION_VALUES']
    if len(assignments)!=1:raise ValueError('B03_EXISTING_PERMISSION_LITERAL_REQUIRED')
    values=ast.literal_eval(assignments[0])
    check=pack.checks['PLT-01']
    if type(values) is not dict or set(values)!={p.key for p in check.policy_params}:
        raise ValueError('B03_PERMISSION_FIELDS_REQUIRED')
    config={'_kubernetes_target_sha256':sha256_hex(canonical_json(target)),
        'adapter':{'name':env.engine.name},'executors':{check.executor:'kubernetes'},
        check.executor:{'target':target_pin['path']}}
    executor=registry.build_executor(check.executor,'kubernetes',config,
        engine=env.engine.name,version=env.engine.version)
    executor.validate_policy(check,values)
    alias=request_model(env,target)
    if output.is_relative_to(root):raise ValueError('B03_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    # Exact input, policy and factory checks precede all output writes.
    plan=prepare_checklist_cases.prepare_cases(pack,output/'definition-plan',['PLT-01'])
    folder=output/'PLT-01';folder.mkdir(mode=0o700)
    policy=yaml.safe_load((output/'definition-plan/PLT-01/policy-template.yaml').read_bytes())
    policy.update(data_origin='EXAMPLE',status='DRAFT',values={'PLT-01':deepcopy(values)})
    normal={'schema':'b03-request-input/1','check_id':'PLT-01','role':'normal',
        'functional_probes':[p.model_dump(mode='json') for p in check.functional_probes],
        'bodies':[fill_model(deepcopy(p.body),alias) for p in check.functional_probes],
        'planned_model_alias':alias,'planned_alias_is_current_observation':False,
        'served_model_name':None,'runtime_target':target_pin['path']}
    challenge={'schema':'b03-request-input/1','check_id':'PLT-01','role':'challenge',
        'steps':list(check.challenge_steps),'policy_values':deepcopy(values),
        'runtime_target':target_pin['path'],'native_record':None}
    outputs={name:write(folder/name,value) for name,value in (
        ('policy.existing-lab.json',policy),('config.json',config),('normal.json',normal),('challenge.json',challenge))}
    toml='_kubernetes_target_sha256 = '+json.dumps(config['_kubernetes_target_sha256'])+'\n'
    toml+='\n'.join('['+name+']\n'+'\n'.join(k+' = '+json.dumps(v) for k,v in table.items())
        for name,table in config.items() if type(table) is dict)+'\n'
    if tomllib.loads(toml)!=config:raise ValueError('B03_CONFIG_TOML_ROUNDTRIP_MISMATCH')
    outputs['config.toml']=write(folder/'config.toml',toml.encode())
    row={'check_id':'PLT-01','executor':check.executor,'backend':'kubernetes',
        'factory_type':type(executor).__module__+'.'+type(executor).__name__,
        'policy_source':policy_pin,'policy_data_origin':'EXAMPLE','policy_is_enterprise_approval':False,
        'policy_input_keys_missing':[],'existing_policy_validator_accepted':True,'policy_diagnostic':None,
        'ready_to_execute':False,'filled_static_inputs':True,
        'normal':outputs['normal.json'],'challenge':outputs['challenge.json'],'config':outputs['config.toml'],
        'config_inspection_json':outputs['config.json'],'policy':outputs['policy.existing-lab.json'],
        'target':target_pin,'environment':env_pin,
        'pending':[{'field':f,'owner':o} for f,o in (
            ('actual_owned_environment_target_worker_identity','root/S2'),
            ('enterprise_policy_authority_and_existing_canonical_POL','권한자/root'),
            ('actual_mount_device_access_and_whole_worker_observations','해당producer/root'),
            ('normal_inference_GPU_TP1_product_path','실행담당/root'))],
        'prepare_command':['guard','prepare-validation','--pack',str(root/'update/packs/PACK-BASELINE'),
            '--env',env_pin['path'],'--policy',outputs['policy.existing-lab.json']['path'],
            '--config',outputs['config.toml']['path']],
        'prepare_command_executed':False,'full_policy_loader_would_issue_POL':True}
    result={'schema':'b03-materialized-existing-inputs/1','source_commit':spec['source_commit'],
        'source_files':pins,'pack_ref':pack.ref().model_dump(mode='json'),'rows':[row],
        'definition_plan_cases':len(plan['cases']),'engine_GPU_AWS_executed':False,
        'EVD_RES_RPT_Refs_created':False,'POL_ENV_current_target_created':False,
        'producer_authenticated':False,'ready_for_GPU':False}
    write(output/'materialized-inputs.json',result)
    return result


def materialize(spec_path,source_root,output):
    """No POL/ENV/PLAN/Ref issuance; unknown runtime identity stays null."""
    spec_path=Path(spec_path).absolute()
    spec=strict_json(spec_path.read_bytes(),limit=262144)
    if spec.get('schema')=='b03-supply-pair-inputs/1':
        return preflight_supply_pair(spec,spec_path.parent,Path(source_root).absolute(),Path(output).absolute())
    if spec.get('schema')=='b03-static-input-selection/1' and spec.get('check_ids')==['PLT-01']:
        return materialize_plt01(spec,spec_path.parent,Path(source_root).absolute(),Path(output).absolute())
    if spec.get('schema')!='b03-static-input-selection/1' or spec.get('check_ids')!=list(SELECTED):
        raise ValueError('B03_SELECTION_INVALID')
    root=Path(source_root).absolute()
    source_pins={}
    for relative,pin in spec['source_files'].items():
        _,source_pins[relative]=pinned({'path':str(root/relative),'sha256':pin},spec_path.parent)
    model_raw,model_pin=pinned(spec['model_config'],spec_path.parent)
    model=strict_json(model_raw)
    if type(model.get('vocab_size')) is not int or not 0<model['vocab_size']<2**31:
        raise ValueError('B03_MODEL_VOCAB_INVALID')
    pack=load_pack(root/'update/packs/PACK-BASELINE')
    if pack.ref().model_dump(mode='json')!=spec['pack_ref']:
        raise ValueError('B03_PACK_INPUT_MISMATCH')
    policy_raw,policy_pin=pinned(spec['http_policy_source'],spec_path.parent)
    draft=yaml.safe_load(policy_raw)
    if draft['data_origin']!='EXAMPLE' or draft['status']!='DRAFT':
        raise ValueError('B03_EXISTING_LAB_POLICY_PROVENANCE_REQUIRED')
    chat_raw,chat_pin=pinned(spec['chat_policy_source'],spec_path.parent)
    chat=yaml.safe_load(chat_raw)
    if chat['data_origin']!='EXAMPLE' or chat['status']!='DRAFT':
        raise ValueError('B03_EXISTING_CHAT_POLICY_PROVENANCE_REQUIRED')
    output=Path(output).absolute()
    if output.is_relative_to(root):raise ValueError('B03_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    # All immutable input checks precede the first output mutation.
    plan=prepare_checklist_cases.prepare_cases(pack,output/'definition-plan',SELECTED)
    rows=[]
    for cid in SELECTED:
        check=pack.checks[cid]
        folder=output/cid;folder.mkdir(mode=0o700)
        target={'schema':'b03-unbound-runtime-input/1','check_id':cid,
            'image_digest':spec['image_digest'],'model':deepcopy(spec['model']),
            'environment_ref':None,'target_sha256':None,'prepared_sha256':None,
            'boot_id':None,'namespace':None,'namespace_uid':None,'pod_uid':None,
            'container_id':None,'pid':None,'start_time':None,'uid':None,'gid':None,
            'mount':None,'subPath':None,'FD_identity':None}
        target_pin=write(folder/'target.unbound.json',target)
        if cid in gpu_a_run_inputs.HTTP_CHECKS:
            policy,toml=gpu_a_run_inputs.http_inputs(cid,target,draft,root=folder)
            config=tomllib.loads(toml)
            executor_name,settings=gpu_a_run_inputs.HTTP_CHECKS[cid]
            cfg=config[executor_name]
            if executor_name=='input_validation':
                # The original helper explicitly pins Qwen. This selection is a
                # different real pinned model; keep that substitution explicit.
                cfg['vocab_size']=model['vocab_size']
                cfg['vocab_source']='pinned selected model config.json sha256:'+model_pin['sha256']
            config['adapter']={'name':spec['adapter']}
            policy_source=policy_pin
            backend='kubernetes'
        else:
            values=deepcopy(chat.get('values',{}).get(cid,{}))
            policy={'data_origin':chat['data_origin'],'status':'DRAFT','pack':deepcopy(chat['pack']),
                'scope':{'include':[cid],'exclude':[]},'values':{cid:values},'exceptions':[],
                'confirmed_by':None,'confirmed_at':None}
            config={'adapter':{'name':spec['adapter']},'executors':{check.executor:'real'},check.executor:{
                **deepcopy(template_validation.DEFAULTS if cid=='REQ-07' else template_sandbox.DEFAULTS),
                'target':str(folder/'target.unbound.json')}}
            policy_source=chat_pin
            backend='real'
        config[check.executor]['target']=str(folder/'target.unbound.json')
        policy['pack']={'id':pack.ref().id,'revision':pack.ref().revision}
        # Root-only storage/adapter metadata is excluded from executor settings.
        cfg=config[check.executor]
        factory=registry.resolve_executor(check.executor,backend,engine=spec['engine_name'],version=spec['engine_version'])
        executor=registry.build_executor(check.executor,backend,config,engine=spec['engine_name'],version=spec['engine_version'])
        values=policy['values'][cid]
        try:
            executor.validate_policy(check,values)
            diagnostic=None
        except ValueError as error:
            diagnostic={'type':type(error).__name__,'message':str(error)}
        normal={'schema':'b03-request-input/1','check_id':cid,'role':'normal',
            'functional_probes':[p.model_dump(mode='json') for p in check.functional_probes],
            'served_model_name':None,'runtime_target':str(folder/'target.unbound.json')}
        challenge={'schema':'b03-request-input/1','check_id':cid,'role':'challenge',
            'steps':list(check.challenge_steps),'policy_values':deepcopy(values),
            'runtime_target':str(folder/'target.unbound.json'),'native_record':None}
        from guard.executors.http_common import fill_model
        normal['planned_model_alias']=spec['model']['id']
        normal['planned_alias_is_current_observation']=False
        normal['bodies']=[fill_model(deepcopy(p.body),spec['model']['id']) for p in check.functional_probes]
        if diagnostic is None and check.executor=='input_validation':
            from guard.executors.kubernetes_input_validation import _contract
            contract=_contract(check,values,cfg,spec['model']['id'])
            normal['transport_contract']=contract
            challenge['transport_contract']=contract
            # These are actual declared transport inputs, not positive records.
            body=deepcopy(contract['control'])
            if contract['fields']:
                body.update(deepcopy(contract['fields']))
                challenge['bodies']=[body]
            else:
                bounds=contract['prompt_tokens']
                challenge['bodies']=[{**body,'prompt':[bounds['max_value']]},
                    {**body,'prompt':[0]*bounds['max_length']}]
        pins={name:write(folder/name,value) for name,value in (
            ('policy.existing-lab.json',policy),('config.json',config),('normal.json',normal),('challenge.json',challenge))}
        # guard CLI always reads TOML, irrespective of the filename suffix.
        scalars='\n'.join(name+' = '+json.dumps(value) for name,value in config.items() if type(value) is not dict)
        tables='\n'.join('['+name+']\n'+'\n'.join(key+' = '+json.dumps(value) for key,value in table.items())
            for name,table in config.items() if type(table) is dict)
        toml=scalars+'\n'+tables+'\n'
        pins['config.toml']=write(folder/'config.toml',toml.encode())
        missing=[p.key for p in check.policy_params if p.required and p.key not in values]
        rows.append({'check_id':cid,'executor':check.executor,'backend':backend,
            'collection_profile':getattr(check,'collection_profile',None),'factory_module':factory.__module__,
            'factory_type':type(executor).__module__+'.'+type(executor).__name__,
            'policy_source':policy_source,'policy_data_origin':policy['data_origin'],
            'policy_is_enterprise_approval':False,'policy_input_keys_missing':missing,
            'existing_policy_validator_accepted':diagnostic is None,'policy_diagnostic':diagnostic,
            'filled_static_inputs':True,'ready_to_execute':False,
            'pending':[{'field':f,'owner':o} for f,o in (
                ('current_environment_target_identity','S2'),('new_native_target_prepared_and_observation','S2'),
                ('case_policy_authority_and_validity','권한자/root'),('PACK34_canonical_run_only','A04'),
                *([('missing_policy_values:'+','.join(missing),'권한자/root')] if missing else []))],
            'normal':pins['normal.json'],'challenge':pins['challenge.json'],'config':pins['config.toml'],
            'config_inspection_json':pins['config.json'],
            'policy':pins['policy.existing-lab.json'],'target':target_pin,
            'prepare_command':['guard','validate','--pack',str(root/'update/packs/PACK-BASELINE'),
                '--env',None,'--policy',str(folder/'policy.existing-lab.json'),'--config',str(folder/'config.toml'),
                '--store',None,'--output',None]})
    origins={}
    for name,module in tuple(sys.modules.items()):
        if name=='guard' or name.startswith('guard.'):
            path=Path(module.__file__).resolve()
            if not path.is_relative_to((root/'update/src/guard').resolve()):
                raise ValueError('B03_IMPORTED_SOURCE_ORIGIN_MISMATCH')
            origins[name]=str(path)
    result={'schema':'b03-materialized-existing-inputs/1','source_commit':spec['source_commit'],
        'guard_import_origins':origins,
        'source_files':source_pins,'model_config':model_pin,'policy_source':policy_pin,'chat_policy_source':chat_pin,
        'pack_ref':pack.ref().model_dump(mode='json'),'rows':rows,'definition_plan_cases':len(plan['cases']),
        'engine_GPU_AWS_executed':False,'EVD_RES_RPT_Refs_created':False,
        'current_runtime_IDs_created':False,'producer_authenticated':False,'ready_for_GPU':False}
    write(output/'materialized-inputs.json',result)
    return result


def preflight_supply_pair(spec,base,root,output):
    """Read actual paired local inputs through the existing boot validator only.

    A passing receipt covers files/policy/binding, never live worker identity,
    producer qualification, complete collection or execution authorization.
    """
    from guard.executors import supply_boot_contract as contract
    from guard.executors.kubernetes_supply_boot import CONFIG_KEYS, KubernetesSupplyBootExecutor
    from guard.models import Environment, utcnow, sha256_hex
    import re
    if (set(spec)!={'schema','check_id','pack_ref','environment','policy','config','roles'}
            or spec['check_id'] not in contract.CHECK_PROFILES
            or type(spec['roles']) is not dict or set(spec['roles'])!=set(contract.ROLES)):
        raise ValueError('B03_SUPPLY_PAIR_SPEC_INVALID')
    def required_pointer(value):
        if (type(value) is not dict or set(value)!={'path','sha256'}
                or type(value['path']) is not str or not value['path'].strip()
                or '\x00' in value['path'] or type(value['sha256']) is not str
                or re.fullmatch('[0-9a-f]{64}',value['sha256']) is None):
            raise ValueError('B03_SUPPLY_EXACT_FILE_POINTER_REQUIRED')
    # dependency(None) deliberately means an unpinned read in legacy code.
    # This new exact-pointer mode must reject it before opening any input.
    for name in ('environment','policy','config'):
        required_pointer(spec[name])
    for role in contract.ROLES:
        pointers=spec['roles'][role]
        if type(pointers) is not dict or set(pointers)!={'target','prepared'}:
            raise ValueError('B03_SUPPLY_ROLE_POINTERS_REQUIRED')
        for value in pointers.values():required_pointer(value)
    pack=load_pack(root/'update/packs/PACK-BASELINE')
    if spec['pack_ref']!=pack.ref().model_dump(mode='json'):
        raise ValueError('B03_PACK_INPUT_MISMATCH')
    raw,p_env=pinned(spec['environment'],base)
    env=Environment.model_validate(strict_json(raw))
    if not env.hash_ok():raise ValueError('B03_ENV_HASH_INVALID')
    raw,p_policy=pinned(spec['policy'],base)
    policy=strict_json(raw)
    cid=spec['check_id']
    if (type(policy) is not dict or policy.get('data_origin') not in {'EXAMPLE','DECLARED'}
            or policy.get('status') not in {'DRAFT','CONFIRMED'}
            or policy.get('pack')!={'id':pack.ref().id,'revision':pack.ref().revision}
            or type(policy.get('values')) is not dict or set(policy['values'])!={cid}):
        raise ValueError('B03_SUPPLY_POLICY_INPUT_INVALID')
    values=policy['values'][cid]
    raw,p_cfg=pinned(spec['config'],base)
    config=strict_json(raw)
    if (type(config) is not dict or set(config)!={'adapter','executors','supply_boot'}
            or config['executors']!={'supply_boot':'kubernetes'}
            or config['adapter']!={'name':env.engine.name}
            or type(config['supply_boot']) is not dict
            or set(config['supply_boot'])-CONFIG_KEYS
            or not set(contract.ROLES)<=set(config['supply_boot'])):
        raise ValueError('B03_SUPPLY_CONFIG_INPUT_INVALID')
    executor=KubernetesSupplyBootExecutor(config['supply_boot'])
    executor.validate_policy(pack.checks[cid],values)
    if env.ref().sha256!=values['supply_environment_sha256']:
        raise ValueError('B03_SUPPLY_ENV_BINDING_MISMATCH')
    begin,end=contract.normalize_policy(cid,values)
    observed=utcnow()
    if not begin<=observed<=end:raise ValueError('SUPPLY_BOOT_POLICY_WINDOW_EXPIRED')
    roles=[]
    for role in contract.ROLES:
        pointers=spec['roles'][role]
        if type(pointers) is not dict or set(pointers)!={'target','prepared'}:
            raise ValueError('B03_SUPPLY_ROLE_POINTERS_REQUIRED')
        pins={name:pinned(pointers[name],base)[1] for name in ('target','prepared')}
        section=config['supply_boot'][role]
        if (type(section) is not dict or set(section)!={'target','prepared'}
                or any(not Path(section[name]).is_absolute()
                       or Path(section[name])!=Path(pins[name]['path']) for name in pins)):
            raise ValueError('B03_SUPPLY_ROLE_PATH_MISMATCH')
        target,prepared,target_sha=executor._load(env,values,role)
        if target.get('environment_ref')!=env.ref().model_dump(mode='json'):
            raise ValueError('B03_SUPPLY_TARGET_ENV_MISMATCH')
        # Recheck the original exact pointers after the existing file consumer.
        for name in pins:
            if pinned(pointers[name],base)[1]!=pins[name]:
                raise ValueError('B03_SUPPLY_INPUT_CHANGED')
        roles.append({'role':role,'target':pins['target'],'prepared':pins['prepared'],
            'target_object_sha256':target_sha,'prepared_object_sha256':sha256_hex(canonical_json(prepared)),
            'session':prepared['session']})
    if (roles[0]['session']==roles[1]['session']
            or roles[0]['target_object_sha256']==roles[1]['target_object_sha256']):
        raise ValueError('B03_SUPPLY_DISTINCT_ROLE_INPUTS_REQUIRED')
    if output.resolve().is_relative_to(root.resolve()):raise ValueError('B03_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    result={'schema':'b03-supply-pair-preflight/1','check_id':cid,
        'pack_ref':pack.ref().model_dump(mode='json'),'observed_at':observed.isoformat(),
        'environment':p_env,'policy':p_policy,'config':p_cfg,'roles':roles,
        'policy_data_origin':policy['data_origin'],'environment_data_origin':env.data_origin.value,
        'local_input_checks_passed':True,'ready_for_GPU':False,
        'engine_GPU_AWS_executed':False,'EVD_RES_RPT_Refs_created':False,
        'remaining':['current worker/boot identity','collection and producer qualification',
                     'execution authorization and resource cleanup','canonical pack/policy authority'],
        'rows':[{'check_id':cid,'existing_policy_validator_accepted':True}]}
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    write(output/'supply-pair-preflight.json',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('spec','source-root','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args(argv)
    result=materialize(args.spec,args.source_root,args.output)
    print(json.dumps({'materialized_checks':len(result['rows']),
        'policy_validated':sum(r['existing_policy_validator_accepted'] for r in result['rows']),
        'ready_for_GPU':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())
