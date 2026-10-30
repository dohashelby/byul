"""OBS02 reuses closed OBS01 record reads and native8 model attribution facts."""
from guard.models import canonical_json,sha256_hex,utcnow
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


class KubernetesObservationIdentityExecutor(KubernetesObservationStateExecutor):
    name='observation_identity';version='0.2.0';collection_profile=PROFILE
    supported_native_profiles=frozenset({'vllm-native-lifecycle-metadata/8',weight_sensor.PROFILE})
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
        if ctx._termination_prepared['profile']==weight_sensor.PROFILE:
            value['weight_iteration']=weight_metadata.project(packet)
            value['identity_native_models']=weight_metadata.attach_configured_models(value['identity_native_models'],value['weight_iteration'])
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
            scope='OBS01 /1 declarations and source-bound native8/16 configured-object and iterator candidates; approval/inspection authority, approved physical model scope, actual tensor use and enterprise policy remain unverified.')
        if state['reader'] is not None:
            summary['identity_record_transport']=state['reader'].report()
            if summary['identity_record_transport'].get('parent_cleanup_unverified'):
                ctx._termination_cleanup.append('OBSERVATION_IDENTITY_RECORD_PARENT_CLEANUP_UNVERIFIED');state['gap']=True
        if state['gap']:
            summary['diagnostic_codes'].append('OBSERVATION_IDENTITY_RECORD_COLLECTION_INCOMPLETE');ctx._termination_gap=True
        values={'identity_record_collections':state['collections'],'identity_declared_records':projected['records'],
            'identity_native_models':models,'identity_binding_candidates':projected['candidates'],
            'identity_conflict_candidates':projected['conflict_candidates'],'identity_missing':projected['missing'],
            **{key:False for key in contract.TRUTH_FIELDS}}
        observations.update({key:{'value':value,'collected':True} for key,value in values.items()})
        normal['succeeded']=None;normal['detail']='Declared record/native configured-object attribution candidates only; approval, inspection, actual model use and enterprise policy remain unverified.'
        return observations,normal,challenges,summary
