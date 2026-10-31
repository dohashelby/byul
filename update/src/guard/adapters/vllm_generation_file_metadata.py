"""Exact native20/21/22 file facts; admitted packets only, no profile relabeling.

This public projection consumes capture_session output and load_prepared output.
It shares journals with native19 while preserving that public contract's defaults.
It does not authenticate a worker, authorize a model, or prove tensor use.
"""
from collections import Counter
from guard.models import Ref
from . import vllm_worker_startup_metadata as startup
from . import vllm_worker_initialization_metadata as joint
from . import vllm_worker_generation_metadata as generation
from . import vllm_ready_endpoint_metadata as endpoint

VERSION='0.1.0'
SCHEMA='guard-native-generation-file-contexts/1'
MAX_RECORDINGS=32  # Existing lifecycle_session.MAX_FILES admission limit.
PROFILES={joint.sensor.PROFILE:(joint.sensor.SCHEMA,joint.project),
          generation.sensor.PROFILE:(generation.sensor.SCHEMA,generation.project),
          endpoint.sensor.PROFILE:(endpoint.sensor.SCHEMA,endpoint.project)}
TRUTH=frozenset((*startup.sensor.TRUTH,*startup.weights.TRUTH,*startup.mapping.sensor.TRUTH,
                 'cross_process_lineage_verified','ready_repair_lineage_verified','scope_complete'))
ERROR='GENERATION_FILE_CONTEXT_RECORDING_UNAVAILABLE'


def _header(recording,prepared):
    capture=recording.get('capture')
    if capture is None:
        if recording.get('session_owner_match') is not False or recording.get('listed_file_identity_match') is not False:
            raise ValueError(ERROR)
        return None
    records=capture['records'];header=records[0]['payload'];context=header['context']
    if (records[0]['event']!='START' or records[0]['seq']!=0
            or header.get('profile')!=prepared['profile'] or header.get('schema')!=PROFILES[prepared['profile']][0]
            or header.get('session')!=prepared['session']
            or any(type(context.get(k)) is not int or context[k]<1 for k in ('pid','start_ticks'))):
        raise ValueError(ERROR)
    return header


def _restrict(rows,bound,image):
    for row in rows:
        row['process_binding_verified']=bool(row.get('process_binding_verified') and bound)
        row['image_binding_verified']=image
        for key in ('same_process_iteration_observed','loader_call_model_link_observed','same_loader_model_candidate',
                    'local_worker_generation_model_candidate','local_object_generation_candidate'):
            if key in row:row[key]=bool(row[key] and bound)
        if type(row.get('worker_generation_candidates')) is list:
            _restrict(row['worker_generation_candidates'],bound,image)


def project_file_contexts(packet,prepared):
    """Preserve indexed partial facts; exact profile/session, no new trust input.

    A missing or rejected recording keeps its original index. Later projection
    errors do not erase earlier recordings or another independently valid stage.
    Callers must retain capture_session/prepared provenance and original gates.
    """
    if type(prepared) is not dict or type(prepared.get('profile')) is not str or prepared['profile'] not in PROFILES:
        raise ValueError('GENERATION_FILE_CONTEXT_PROFILE_UNSUPPORTED')
    if (type(packet) is not dict or type(packet.get('recordings')) is not list or len(packet['recordings'])>MAX_RECORDINGS
            or type(prepared.get('session')) is not str or len(prepared['session'])!=32
            or any(char not in '0123456789abcdef' for char in prepared['session'])):
        raise ValueError('GENERATION_FILE_CONTEXT_INPUT_INVALID')
    scope=prepared.get('model_read_scope')
    try:
        reference=Ref.model_validate(scope['environment_ref'])
        if (type(scope) is not dict or scope.get('nonsecret_model_weights') is not True
                or scope.get('data_origin') not in {'EXAMPLE','DECLARED','MEASURED'}
                or not all(startup.contract.fingerprint(scope.get(k)) for k in (
                    'target_sha256','model_sha256','revision_sha256','inventory_sha256','manifest_bytes_sha256'))
                or reference.kind!='ENV' or reference.revision<1 or not reference.id
                or not startup.contract.fingerprint(reference.sha256)):raise ValueError
    except (KeyError,ValueError,TypeError,AttributeError):
        raise ValueError('GENERATION_FILE_CONTEXT_SCOPE_INVALID') from None
    profile=prepared['profile'];recordings=packet['recordings'];headers=[]
    for recording in recordings:
        try:headers.append(_header(recording,prepared))
        except (KeyError,TypeError,ValueError,IndexError,AttributeError):headers.append(False)
    counts=Counter((h['context']['pid'],h['context']['start_ticks']) for h in headers if h)
    result={'schema':SCHEMA,'version':VERSION,'recording_profile':profile,'recordings':[],
            'scope_binding':{key:scope.get(key) for key in (
                'environment_ref','target_sha256','model_sha256','revision_sha256','inventory_sha256',
                'manifest_bytes_sha256','data_origin','nonsecret_model_weights')},
            'model_candidates':[],'weight_iteration':{'schema':'guard-weight-iteration-projection/1',
                'samples':[],'model_candidates':[],'missing':[],**{key:False for key in startup.weights.TRUTH},'scope_complete':False},
            'safe_open_mappings':{'schema':'guard-safe-open-mapping-projection/1','samples':[],'contexts':[],
                'missing':[],**{key:False for key in startup.mapping.sensor.TRUTH}},
            'startup_generation':None,'missing':[],'diagnostics':[],**{key:False for key in TRUTH}}
    try:
        # Keep the existing projection's full cross-recording endpoint semantics.
        # The indexed file views below additionally require image/unique-generation binding.
        result['startup_generation']=PROFILES[profile][1](packet,prepared)
    except (KeyError,ValueError,TypeError,IndexError,OSError,RuntimeError,AttributeError):
        result['missing'].append(ERROR)
        result['diagnostics'].append({'recording':None,'stage':'startup_generation_full','code':ERROR})
    for index,(recording,header) in enumerate(zip(recordings,headers)):
        entry={'recording':index,'capture_available':bool(header),'process_binding_verified':False,
               'image_binding_verified':False,'duplicate_process_generation':False,'status':'unavailable'}
        result['recordings'].append(entry)
        if not header:
            code='generation_file_capture_missing' if header is None else ERROR
            result['missing'].append(code);result['diagnostics'].append({'recording':index,'stage':'header','code':code})
            for key in ('weight_iteration','safe_open_mappings'):result[key]['missing'].append(code)
            continue
        capture=recording['capture'];context=header['context'];duplicate=counts[(context['pid'],context['start_ticks'])]!=1
        image=capture.get('image_digest_verified') is True
        bound=bool(not duplicate and image and capture.get('process_context_verified') is True
                   and recording.get('session_owner_match') is True and recording.get('listed_file_identity_match') is True)
        entry.update(pid=context['pid'],start_ticks=context['start_ticks'],process_binding_verified=bound,
                     image_binding_verified=image,duplicate_process_generation=duplicate,status='projected')
        if duplicate:result['missing'].append('generation_file_duplicate_process_generation')
        if not bound:
            entry['status']='partial';result['missing'].append('generation_file_process_or_image_binding_unavailable')
            result['diagnostics'].append({'recording':index,'stage':'binding','code':'GENERATION_FILE_CONTEXT_BINDING_UNAVAILABLE'})
        # Padding preserves original Ref recording indices without rewriting any capture/header.
        selected={'recordings':[{'capture':None,'session_owner_match':False,'listed_file_identity_match':False}
                                for _ in range(index)]+[recording]}
        for stage in ('startup_generation','weight_iteration','safe_open_mappings'):
            try:
                if stage=='startup_generation':
                    value=PROFILES[profile][1](selected,prepared)
                    for key,rows in value.items():
                        if type(rows) is list and all(type(row) is dict for row in rows):_restrict(rows,bound,image)
                    result['model_candidates'].extend(value['model_candidates']);entry[stage]=value
                else:
                    value=(startup._project_iterations(selected,profile,upper_anchors=True) if stage=='weight_iteration'
                           else startup._project_mappings(selected,prepared,profile))
                    for key in ('samples','model_candidates'):
                        if key in value:_restrict(value[key],bound,image)
                    if stage=='safe_open_mappings':
                        for row in value['contexts']:
                            samples=[sample for sample in value['samples'] if sample['context_sha256']==row['context_sha256']]
                            row.update(pid=context['pid'],start_ticks=context['start_ticks'],
                                process_binding_verified=bound,image_binding_verified=image,
                                first_mapping_event=[index,samples[0]['event_seq']] if samples else None,
                                last_mapping_event=[index,samples[-1]['event_seq']] if samples else None)
                    for key in ('samples','model_candidates','contexts','missing'):
                        if key in value:result[stage][key].extend(value[key])
                result['missing'].extend(value['missing'])
            except (KeyError,ValueError,TypeError,IndexError,OSError,RuntimeError,AttributeError):
                entry['status']='partial';result['missing'].append(ERROR)
                result['diagnostics'].append({'recording':index,'stage':stage,'code':ERROR})
                if stage in ('weight_iteration','safe_open_mappings'):result[stage]['missing'].append(ERROR)
    full=result['startup_generation']
    if full is not None:
        binding={row['recording']:row for row in result['recordings']}
        for key,rows in full.items():
            if type(rows) is not list:continue
            for row in rows:
                if type(row) is not dict:continue
                if row.get('recording') in binding:
                    bound=binding[row['recording']]
                    _restrict([row],bound['process_binding_verified'],bound['image_binding_verified'])
                elif key=='endpoint_candidates':
                    bound=all(binding.get(row.get(k),{}).get('process_binding_verified') is True
                              for k in ('parent_recording','child_recording'))
                    row['same_held_fifo_candidate']=bool(row['same_held_fifo_candidate'] and bound)
    result['missing']=sorted(set(result['missing']+['worker_role_approval_and_final_tensor_consumption_unverified']))
    for key in ('weight_iteration','safe_open_mappings'):result[key]['missing']=sorted(set(result[key]['missing']))
    return result


def attach_configured_models(trials,projection):
    """OBS02 common facts only, tied to the same model/root/startup and forward."""
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator'];context=locator['context'];joined=[]
            for worker in projection['model_candidates']:
                anchors=[worker.get(key) for key in ('worker_start_seq','worker_end_seq','root_event_seq','link_event_seq')]
                if (worker['recording']!=locator['recording'] or not worker['process_binding_verified']
                        or not worker['same_startup_model_root_candidate']
                        or (worker['pid'],worker['start_ticks'])!=(context['pid'],context['start_ticks'])
                        or worker['model_object_sha256']!=model['session_model_object_sha256']
                        or any(type(seq) is not int or not 0<=seq<locator['forward_seq'] for seq in anchors)):continue
                roots=[row for row in projection['weight_iteration']['model_candidates']
                       if row['recording']==worker['recording'] and row['process_binding_verified']
                       and row['call_sha256']==worker['root_load_sha256'] and row['model_object_sha256']==worker['model_object_sha256']
                       and row['factory_seq']==worker['root_event_seq'] and row['last_seq']<locator['forward_seq']
                       and row['loader_call_sha256'] is not None and row['loader_model_sha256']==worker['model_object_sha256']
                       and (row['last_source_event'] is None or row['last_source_event'][1]<locator['forward_seq'])]
                if len(roots)!=1:continue
                joined.append({'startup_generation':worker,'weight_root':roots[0],
                               'recording_profile':projection['recording_profile'],'scope_complete':False})
            model['generation_file_context_candidates']=joined
            model['worker_startup_candidates']=[row['startup_generation'] for row in joined]
            model['actual_tensor_consumption_verified']=False
    return trials
