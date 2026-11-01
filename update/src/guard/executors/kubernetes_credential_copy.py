"""PLT12 read-only declared copy records through the existing OBS01 boundary.

No credential contents, copy mutation, issuer call or reuse request is made.
Collection is separate from actual copy membership, treatment and enforcement.
"""
from pathlib import Path
import os,stat
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import capture_pipes,strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP,REQUIRED_EVENT_PROFILE
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_observation import RequiredEventReader
from .owned_capture import MAX_BYTES
from . import credential_copy_contract as contract


def load_inventory(path,expected_hash,expected_policy):
    selected=os.lstat(path)
    if not stat.S_ISREG(selected.st_mode):raise ValueError('COPY_INVENTORY_FILE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=MAX_BYTES:raise ValueError('COPY_INVENTORY_FILE_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(MAX_BYTES+1)
        after=os.fstat(fd);current=os.lstat(path);identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns)
        if (len(raw)!=before.st_size or len({identity(v) for v in (selected,before,after,current)})!=1
                or before.st_ctime_ns!=after.st_ctime_ns or selected.st_ctime_ns!=current.st_ctime_ns
                or sha256_hex(raw)!=expected_hash):raise ValueError('COPY_INVENTORY_FILE_UNAVAILABLE')
        return contract.validate_inventory(strict_json(raw,limit=MAX_BYTES),expected_policy_sha256=expected_policy)
    finally:os.close(fd)


class KubernetesCredentialCopyRecordExecutor(HttpCheckExecutor):
    name='required_event_record';version='0.2.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='PLT-12' or getattr(check,'collection_profile',None)!=contract.PROFILE:raise ValueError('COPY_PROFILE_UNSUPPORTED')
        contract.normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','copy_inventory','kubectl_binary','max_duration_seconds'} or adapter is None:raise ValueError
            start,end=contract.normalize_policy(policy_values)
            if env.ref().sha256!=policy_values['copy_environment_sha256'] or not start<=utcnow()<=end:raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            target_hash=sha256_hex(canonical_json(target))
            if (target.get('lab') is not True or target_hash!=policy_values['copy_target_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target):raise ValueError
            inventory=load_inventory(self.cfg['copy_inventory'],policy_values['copy_inventory_sha256'],policy_values['copy_policy_sha256'])
        except (KeyError,TypeError,ValueError,OSError,RuntimeError):raise ValueError('COPY_CONFIGURATION_INVALID') from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get('max_duration_seconds',120),
            binary=self.cfg.get('kubectl_binary','kubectl'),runner=self.runner,profile=REQUIRED_EVENT_PROFILE)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,
            allow_mutating=False,keys={},secrets=[],cprobe=transport,workdir=workdir,started=utcnow())
        ctx._copy_inventory=inventory;return self._finish(ctx)

    def collect(self,ctx):
        values=ctx.policy_values;start,end=contract.normalize_policy(values);reader=None;snapshots=[];collection=[];diagnostics=[]
        def collect_stage(stage):
            row={'stage':stage,'collected':False,'file_bytes_sha256':None,'file_bytes':None,'owner_binding_verified':False,'collected_at':utcnow().isoformat()}
            try:
                if not start<=utcnow()<=end:raise ValueError
                role=reader.binding['engine']
                raw,provenance=reader.read_file(reader,ctx.target,container=role['container'],image=role['image'],
                    path='/guard-observation/data/'+values['copy_record_session']+'/required-events.json')
                packet=contract.validate_record(strict_json(raw,limit=MAX_BYTES),environment_ref=ctx.env.ref().model_dump(mode='json'),
                    target_sha256=values['copy_target_sha256'],session=values['copy_record_session'],
                    source_sha256=values['copy_record_source_sha256'],inventory_sha256=values['copy_inventory_sha256'],policy_sha256=values['copy_policy_sha256'])
                if provenance.get('owner_binding_verified') is not True or not start<=utcnow()<=end:raise ValueError
                row.update(collected=True,file_bytes_sha256=sha256_hex(raw),file_bytes=len(raw),
                    owner_binding_verified=provenance.get('owner_binding_verified') is True)
                snapshots.append({'stage':stage,'collected':True,'packet':packet})
            except Exception:diagnostics.append('COPY_RECORD_UNAVAILABLE')
            collection.append(row)
        try:
            reader=RequiredEventReader(ctx.cprobe,ctx.target,values['copy_record_session'],
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120),runner=self.runner or capture_pipes)
            reader.snapshot();collect_stage('before');reader.snapshot();collect_stage('after');reader.snapshot()
        except Exception:diagnostics.append('COPY_PREFLIGHT_OR_GENERATION_UNAVAILABLE')
        projected=contract.project(ctx._copy_inventory,snapshots,values)
        report=None;transport=None;cleanup=[]
        try:transport=ctx.cprobe.report();cleanup.extend(transport.get('cleanup_errors',[]))
        except Exception:diagnostics.append('COPY_TRANSPORT_REPORT_UNAVAILABLE')
        if reader is not None:
            try:
                report=reader.report()
                if reader.gap:diagnostics.append('COPY_FILE_COLLECTION_GAP')
                if reader._parent_cleanup_failures:cleanup.append('COPY_RECORD_PARENT_CLEANUP_UNVERIFIED')
            except Exception:diagnostics.append('COPY_READER_REPORT_UNAVAILABLE')
        semantic='actual_copy_membership_treatment_reuse_and_authority_unverified'
        ctx._copy_gap=bool(diagnostics or projected['conflicts'] or any(code!=semantic for code in projected['missing']) or len(snapshots)!=2)
        ctx._copy_cleanup=cleanup
        summary={'profile':contract.PROFILE,'inventory':ctx._copy_inventory,'snapshots':snapshots,'collection':collection,
            'projection':projected,'reader':report,'transport':transport,'diagnostic_codes':sorted(set(diagnostics)),
            'fixture':{'profile':contract.PROFILE},'producer_authenticated':False,'actual_copy_membership_verified':False,
            'actual_treatment_verified':False,'reuse_enforcer_verified':False,'issuer_verified':False,'whole_scope_complete':False}
        observations={'copy_inventory_declarations':ctx._copy_inventory['copies'],'copy_record_snapshots':snapshots,
            'copy_event_declarations':projected['events'],'copy_treatment_candidates':projected['treatment_candidates'],
            'copy_reuse_candidates':projected['reuse_candidates'],'copy_issuer_declarations':projected['issuer_declarations'],
            'copy_missing':projected['missing'],'copy_conflicts':projected['conflicts'],'copy_collection':collection,
            'copy_records_collected':bool(snapshots),'copy_producer_authenticated':False,'copy_membership_verified':False,
            'copy_treatment_verified':False,'copy_reuse_enforcer_verified':False,'copy_issuer_verified':False,'copy_scope_complete':False}
        normal={'attempted':False,'succeeded':None,'detail':'Read-only declarations; actual copy membership/treatment/reuse and normal credential control are independently unverified.'}
        challenges=[{'step':step,'attempted':False,'observations':{'copy_scope_complete':False}} for step in ctx.check.challenge_steps]
        return {key:{'value':value,'collected':True} for key,value in observations.items()},normal,challenges,summary

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        ceiling=DataOrigin.EXAMPLE if self.runner is not None or ctx.target['lab'] or ctx.env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin=min(draft.data_origin,ceiling,key=lambda value:ORIGIN_STRENGTH[value])
        if ctx._copy_gap or ctx._copy_cleanup:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._copy_cleanup)
        return draft
