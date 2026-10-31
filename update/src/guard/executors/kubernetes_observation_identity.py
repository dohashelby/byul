"""OBS02 reuses closed OBS01 record reads and native8 model attribution facts."""
from guard.models import canonical_json,sha256_hex,utcnow
from guard.adapters import vllm_safe_open_mapping_sensor as mapping_sensor
from guard.adapters import vllm_safe_open_mapping_metadata as mapping_metadata
from guard.adapters import vllm_worker_startup_sensor as startup_sensor,vllm_worker_startup_metadata as startup_metadata
from guard.adapters import vllm_file_read_sensor as read_sensor,vllm_file_read_metadata as read_metadata
from guard.adapters import vllm_weight_iteration_sensor as weight_sensor,vllm_weight_iteration_metadata as weight_metadata
from guard.runtime.bounded import strict_json
from .kubernetes_observation_state import KubernetesObservationStateExecutor
from .kubernetes_observation import RequiredEventReader
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .owned_capture import MAX_BYTES
from .observation_contract import validate_record
from . import observation_identity_contract as contract

PROFILE=contract.PROFILE;POLICY_TYPES=contract.POLICY_TYPES
from .kubernetes_observation_state import OBSERVATIONS as BASE_OBSERVATIONS
OBSERVATIONS={**BASE_OBSERVATIONS,**contract.OBSERVATIONS}


def _attach_scoped_mapping_contexts(trials,projection):
    """Attach bounded candidates to the same admitted root before a forward.

    The untruncated last locator is mandatory: the first 64 source events
    alone cannot establish that the whole context preceded this forward.
    Compute every attachment before mutating models so a consumer error
    cannot leave a partially attached dedicated projection.
    """
    attachments=[]
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator'];context=locator['context'];joined=[]
            for candidate in projection['candidates']:
                if not (model['same_process_loader_object_linked'] and model['source_process_context_verified']
                        and candidate['recording']==locator['recording']
                        and candidate['pid']==context['pid'] and candidate['start_ticks']==context['start_ticks']
                        and candidate['observed_model_object_sha256']==model['session_model_object_sha256']):continue
                events=[candidate['root_event'],candidate['first_source_event'],candidate['last_source_event'],*candidate['source_events']]
                if not all(event[0]==locator['recording'] and event[1]<locator['forward_seq'] for event in events):continue
                if not candidate['root_last_seq']<locator['forward_seq']:continue
                if not any(root['recording']==candidate['recording'] and root['pid']==candidate['pid']
                        and root['start_ticks']==candidate['start_ticks']
                        and root['model_object_sha256']==candidate['observed_model_object_sha256']
                        and root['call_sha256']==candidate['root_load_sha256']
                        and root['loader_call_sha256']==candidate['loader_call_sha256']
                        and root['factory_seq']==candidate['root_event'][1] and root['last_seq']==candidate['root_last_seq']
                        and root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']
                        for root in model.get('weight_iteration_candidates',[])):continue
                joined.append(candidate)
            attachments.append((model,joined))
    for model,candidates in attachments:model['scoped_mapping_context_candidates']=candidates
    return trials


def _attach_startup_file_contexts(trials,projection):
    """Match both admitted startup and loader root, including terminal bounds."""
    attachments=[]
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator'];context=locator['context'];joined=[]
            for candidate in projection['candidates']:
                if not (model['same_process_loader_object_linked'] and model['source_process_context_verified']
                        and candidate['recording']==locator['recording']
                        and candidate['pid']==context['pid'] and candidate['start_ticks']==context['start_ticks']
                        and candidate['observed_model_object_sha256']==model['session_model_object_sha256']):continue
                events=[candidate[key] for key in ('startup_call_event','startup_terminal_event','root_event','root_link_event','first_mapping_event','last_mapping_event')]
                if not all(event[0]==locator['recording'] and event[1]<locator['forward_seq']
                        for event in [*events,*candidate['mapping_source_events']]):continue
                roots=[root for root in model.get('weight_iteration_candidates',[])
                    if root['recording']==candidate['recording'] and root['pid']==candidate['pid'] and root['start_ticks']==candidate['start_ticks']
                    and root['model_object_sha256']==candidate['observed_model_object_sha256']
                    and root['call_sha256']==candidate['root_load_sha256'] and root['loader_call_sha256']==candidate['loader_call_sha256']
                    and root['factory_seq']==candidate['root_event'][1] and root['last_seq']<locator['forward_seq']
                    and root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']]
                if not roots:continue
                if not any(start['recording']==candidate['recording'] and start['pid']==candidate['pid'] and start['start_ticks']==candidate['start_ticks']
                        and start['model_object_sha256']==candidate['observed_model_object_sha256'] and start['root_load_sha256']==candidate['root_load_sha256']
                        and start['worker_call_sha256']==candidate['startup_call_sha256'] and start['worker_point']==candidate['startup_point']
                        and start['worker_start_seq']==candidate['startup_call_event'][1] and start['worker_end_seq']==candidate['startup_terminal_event'][1]
                        and start['root_event_seq']==candidate['root_event'][1] and start['link_event_seq']==candidate['root_link_event'][1]
                        and start['same_startup_model_root_candidate'] and start['process_binding_verified']
                        for start in model.get('worker_startup_candidates',[])):continue
                joined.append(candidate)
            attachments.append((model,joined))
    for model,candidates in attachments:model['scoped_startup_file_context_candidates']=candidates
    return trials


class KubernetesObservationIdentityExecutor(KubernetesObservationStateExecutor):
    name='observation_identity';version='0.4.0';collection_profile=PROFILE
    supported_native_profiles=frozenset({'vllm-native-lifecycle-metadata/8',weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE})
    policy_normalizer=staticmethod(contract.normalize_policy)

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        from guard.collectors.lifecycle import load_prepared
        try:
            if env.engine.name!='vllm' or env.engine.version!='0.31.0':raise ValueError
            prepared=load_prepared(self.cfg['prepared'])
            if sha256_hex(canonical_json(prepared))!=policy_values['identity_prepared_sha256']:raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('OBSERVATION_IDENTITY_PREPARED_UNAVAILABLE') from None
        return super().run(check,env,policy_values,adapter,workdir)

    def _read_records(self,ctx,stage):
        start,end=self.policy_normalizer(ctx.policy_values);state=ctx._identity_records
        row={'stage':stage,'collected':False,'source_bytes_sha256':None,'source_bytes':None,'owner_binding_verified':False}
        try:
            reader=state['reader']
            if reader is None or not start<=utcnow()<=end:raise ValueError
            role=reader.binding['engine']
            raw,provenance=reader.read_file(reader,ctx.target,container=role['container'],image=role['image'],
                path='/guard-observation/data/'+ctx.policy_values['identity_record_session']+'/required-events.json')
            packet=validate_record(strict_json(raw,limit=MAX_BYTES),environment_ref=ctx.env.ref().model_dump(mode='json'),
                target_sha256=ctx.policy_values['observation_state_target_sha256'],session=ctx.policy_values['identity_record_session'],
                expected_source_sha256=ctx.policy_values['identity_record_source_sha256'],forbidden_values=ctx.secrets)
            if provenance.get('owner_binding_verified') is not True or not start<=utcnow()<=end:raise ValueError
            row.update(collected=True,source_bytes_sha256=provenance['capture_sha256'],source_bytes=provenance['bytes'],
                file_identity=provenance['file_identity'],owner_binding_verified=True)
            state['snapshots'].append({'stage':stage,'packet':packet,'source_bytes_sha256':provenance['capture_sha256']})
            if not packet['source_ended'] or packet['dropped_events']:state['gap']=True
        except Exception:state['gap']=True
        state['collections'].append(row)

    def after_trial(self,ctx,row):
        if row['case_id']=='obs02-normal-before':self._read_records(ctx,'normal-before')

    def project_native(self,ctx,packet,trials):
        value=super().project_native(ctx,packet,trials)
        value['identity_native_models']=contract.native_models(packet,trials)
        if ctx._termination_prepared['profile'] in {weight_sensor.PROFILE,read_sensor.PROFILE}:
            value['weight_iteration']=read_metadata.project_iterations(packet) if ctx._termination_prepared['profile']==read_sensor.PROFILE else weight_metadata.project(packet)
            value['identity_native_models']=weight_metadata.attach_configured_models(value['identity_native_models'],value['weight_iteration'])
        if ctx._termination_prepared['profile']==weight_sensor.PROFILE:
            from guard.collectors import native_weight_iteration as dedicated
            try:
                value['weight_selection_iteration']=dedicated.analyze(packet)
                for trial in value['identity_native_models']:
                    for model in trial['configured_model_candidates']:
                        locator=model['native_locator']
                        model['weight_selection_iteration_candidates']=[candidate for candidate in value['weight_selection_iteration']['candidates']
                            if candidate['root_event'][0]==locator['recording'] and candidate['model_object_sha256']==model['session_model_object_sha256']
                            and all(event[1]<locator['forward_seq'] for event in candidate['source_events'])]
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                value['weight_selection_iteration']=None
                value['weight_selection_iteration_diagnostic']='NATIVE_WEIGHT_ITERATION_CONSUMER_UNAVAILABLE'
                ctx._termination_gap=True
        if ctx._termination_prepared['profile']==read_sensor.PROFILE:
            value['eager_file_reads']=read_metadata.project(packet,ctx._termination_prepared)
            value['identity_native_models']=read_metadata.attach_configured_models(value['identity_native_models'],value['eager_file_reads'])
            try:
                from guard.collectors import native_model_file_reads as dedicated_reads
                value['scoped_file_read_candidates']=dedicated_reads.analyze(packet,ctx._termination_prepared)
                for trial in value['identity_native_models']:
                    for model in trial['configured_model_candidates']:
                        locator=model['native_locator']
                        model['scoped_file_read_candidates']=[candidate for candidate in value['scoped_file_read_candidates']['candidates']
                            if candidate['recording']==locator['recording']
                            and candidate['observed_model_object_sha256']==model['session_model_object_sha256']
                            and all(event[0]==locator['recording'] and event[1]<locator['forward_seq']
                                for event in [candidate['root_event'],*candidate['source_events']])]
            except (ImportError,ValueError,KeyError,TypeError,OSError,RuntimeError):
                value['scoped_file_read_candidates']=None
                value['scoped_file_read_candidates_diagnostic']='NATIVE_MODEL_FILE_READ_CONSUMER_UNAVAILABLE'
                ctx._termination_gap=True
        if ctx._termination_prepared['profile']==mapping_sensor.PROFILE:
            value['weight_iteration']=mapping_metadata.project_iterations(packet)
            value['identity_native_models']=weight_metadata.attach_configured_models(value['identity_native_models'],value['weight_iteration'])
            try:
                value['safe_open_mappings']=mapping_metadata.project(packet,ctx._termination_prepared)
                value['identity_native_models']=mapping_metadata.attach_configured_models(value['identity_native_models'],value['safe_open_mappings'])
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                value['safe_open_mappings']=None;value['safe_open_mapping_diagnostic']='SAFE_OPEN_MAPPING_CONSUMER_UNAVAILABLE';ctx._termination_gap=True
            # Dedicated consumption is isolated from the already-admitted
            # common facts. Never re-project after a common consumer failure.
            if value['safe_open_mappings'] is not None:
                try:
                    from guard.collectors import native_model_mappings as dedicated_mappings
                    value['scoped_mapping_contexts']=dedicated_mappings.analyze(packet,ctx._termination_prepared)
                    _attach_scoped_mapping_contexts(value['identity_native_models'],value['scoped_mapping_contexts'])
                except (ImportError,ValueError,KeyError,TypeError,OSError,RuntimeError):
                    value['scoped_mapping_contexts']=None
                    value['scoped_mapping_context_diagnostic']='NATIVE_MODEL_MAPPING_CONSUMER_UNAVAILABLE'
                    ctx._termination_gap=True
        if ctx._termination_prepared['profile']==startup_sensor.PROFILE:
            value['weight_iteration']=startup_metadata.project_iterations(packet)
            value['identity_native_models']=weight_metadata.attach_configured_models(value['identity_native_models'],value['weight_iteration'])
            try:
                value['safe_open_mappings']=startup_metadata.project_mappings(packet,ctx._termination_prepared)
                value['identity_native_models']=mapping_metadata.attach_configured_models(value['identity_native_models'],value['safe_open_mappings'])
                value['worker_startup']=startup_metadata.project(packet,ctx._termination_prepared)
                value['identity_native_models']=startup_metadata.attach_configured_models(value['identity_native_models'],value['worker_startup'])
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                value['worker_startup']=None;value['worker_startup_diagnostic']='WORKER_STARTUP_CONSUMER_UNAVAILABLE';ctx._termination_gap=True
            if value['worker_startup'] is not None:
                try:
                    from guard.collectors import native_worker_model_files as dedicated_startup_files
                    value['scoped_startup_file_contexts']=dedicated_startup_files.analyze(packet,ctx._termination_prepared)
                    _attach_startup_file_contexts(value['identity_native_models'],value['scoped_startup_file_contexts'])
                except (ImportError,ValueError,KeyError,TypeError,OSError,RuntimeError):
                    value['scoped_startup_file_contexts']=None
                    value['scoped_startup_file_context_diagnostic']='NATIVE_WORKER_FILE_CONSUMER_UNAVAILABLE'
                    ctx._termination_gap=True
        return value

    def collect(self,ctx):
        state={'reader':None,'snapshots':[],'collections':[],'gap':False};ctx._identity_records=state
        try:
            target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
                'environment_ref':ctx.env.ref().model_dump(mode='json'),
                'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
            # Apply the unchanged native source/image/lab/version preflight
            # before the additional record read, even on unsupported targets.
            KubernetesLifecycleReader(target,ctx.env,ctx._termination_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120))
            state['reader']=RequiredEventReader(ctx.cprobe,ctx.target,ctx.policy_values['identity_record_session'],
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120),runner=self.runner)
            state['reader'].snapshot()
        except Exception:state['gap']=True
        self._read_records(ctx,'before')
        observations,normal,challenges,summary=super().collect(ctx)
        self._read_records(ctx,'after')
        models=summary['projection'].pop('identity_native_models')
        projected=contract.correlate(state['snapshots'],models,run_sha256=sha256_hex(ctx.cprobe.binding['run_id'].encode()),
            target_sha256=ctx.policy_values['observation_state_target_sha256'],source_sha256=ctx.policy_values['identity_record_source_sha256'],
            model_sha256=sha256_hex(ctx.env.model.id.encode()),revision_sha256=sha256_hex(ctx.env.model.revision.encode()),
            inventory_sha256=ctx.env.model.inventory_sha256)
        summary.update(profile=PROFILE,identity_projection=projected,identity_record_collections=state['collections'],
            scope='OBS01 /1 declarations and source-bound native8/16/17/18/19 configured-object, iterator, scoped eager FD/read, safe_open and startup/file context candidates; approval/inspection authority, approved physical model scope, actual tensor use and enterprise policy remain unverified.')
        if state['reader'] is not None:
            summary['identity_record_transport']=state['reader'].report()
            if summary['identity_record_transport'].get('parent_cleanup_unverified'):
                ctx._termination_cleanup.append('OBSERVATION_IDENTITY_RECORD_PARENT_CLEANUP_UNVERIFIED');state['gap']=True
        if state['gap']:
            summary['diagnostic_codes'].append('OBSERVATION_IDENTITY_RECORD_COLLECTION_INCOMPLETE');ctx._termination_gap=True
        if summary['projection'].get('weight_selection_iteration_diagnostic')=='NATIVE_WEIGHT_ITERATION_CONSUMER_UNAVAILABLE':
            # Parent collection computes its gap after project_native. Preserve
            # this local consumer failure after that calculation, without losing
            # the already-admitted common projection or independent facts.
            summary['diagnostic_codes'].append('NATIVE_WEIGHT_ITERATION_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        if summary['projection'].get('scoped_file_read_candidates_diagnostic')=='NATIVE_MODEL_FILE_READ_CONSUMER_UNAVAILABLE':
            summary['diagnostic_codes'].append('NATIVE_MODEL_FILE_READ_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        if summary['projection'].get('safe_open_mapping_diagnostic')=='SAFE_OPEN_MAPPING_CONSUMER_UNAVAILABLE':
            summary['diagnostic_codes'].append('SAFE_OPEN_MAPPING_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        if summary['projection'].get('scoped_mapping_context_diagnostic')=='NATIVE_MODEL_MAPPING_CONSUMER_UNAVAILABLE':
            summary['diagnostic_codes'].append('NATIVE_MODEL_MAPPING_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        if summary['projection'].get('worker_startup_diagnostic')=='WORKER_STARTUP_CONSUMER_UNAVAILABLE':
            summary['diagnostic_codes'].append('WORKER_STARTUP_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        if summary['projection'].get('scoped_startup_file_context_diagnostic')=='NATIVE_WORKER_FILE_CONSUMER_UNAVAILABLE':
            summary['diagnostic_codes'].append('NATIVE_WORKER_FILE_CONSUMER_UNAVAILABLE');ctx._termination_gap=True
        values={'identity_record_collections':state['collections'],'identity_declared_records':projected['records'],
            'identity_native_models':models,'identity_binding_candidates':projected['candidates'],
            'identity_conflict_candidates':projected['conflict_candidates'],'identity_missing':projected['missing'],
            **{key:False for key in contract.TRUTH_FIELDS}}
        observations.update({key:{'value':value,'collected':True} for key,value in values.items()})
        normal['succeeded']=None;normal['detail']='Declared record/native configured-object attribution candidates only; approval, inspection, actual model use and enterprise policy remain unverified.'
        return observations,normal,challenges,summary
