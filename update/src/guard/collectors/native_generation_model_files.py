"""A2 exact native20/21/22 local startup/root/scoped file candidates.

Only the admitted A1 public projection is consumed. Shared journals, collection,
profile admission and transport stay with A1. Python boundary facts never prove
worker identity, model approval, tensor application or endpoint readiness.
"""
from pathlib import Path
from guard.adapters import vllm_generation_file_metadata as common
from guard.collectors import native_worker_model_files as legacy
from guard.models import sha256_hex

VERSION='0.1.0'
SCHEMA='guard-native-generation-scoped-file-candidates/1'
PROFILES=frozenset('vllm-native-lifecycle-metadata/'+str(v) for v in (20,21,22))
SOURCE_PINS={
    'vllm_generation_file_metadata.py':'82391549d2767f76b1e550d2a8fd467e82a253795b9dc34ef6eab906e39b3b1f',
    'vllm_worker_startup_metadata.py':'b5d0be2de94fe9453a5f68b58e019b62a2f454bd59718fb43512a321755e651a',
    'vllm_worker_startup_sensor.py':'2fb8b6386bc59e236c9227251da05f8e8704e08a935dcff27a8c18bc57ed91f4',
    'vllm_worker_startup_contract.py':'6788df2f4c6f04f46e681eb5eab48631e2ba037754d26166bb02341e4b592950',
    'vllm_worker_initialization_metadata.py':'7070261a91e4ff0754a4612b2253af89ebe896dd2b9aa47c0e98d086a07680d2',
    'vllm_worker_initialization_sensor.py':'b7447db52f40b61b20361703ce786d591ada767602e6ec5ea38404e90e559cf1',
    'vllm_worker_generation_metadata.py':'db9b79d796e954acee143be45b825109beff332fa86a5e7ede4b4c5087360623',
    'vllm_worker_generation_sensor.py':'186ee38bcc29da4ffa4cc88c6b386f60f67d343cbc58613cf925213611ab18d2',
    'vllm_worker_generation_contract.py':'f0c37215577292ce7b89b535cf147553ac22f5ae6ef6a08708cf055f4d4eb27a',
    'vllm_ready_endpoint_metadata.py':'c722aec3fa4a5eafee9ed478d970f5b82e26335b2788ec4cbb2d714128007c8e',
    'vllm_ready_endpoint_sensor.py':'e2d35ed9730df734dac6e62b0ab0c6a7cf3e2f10728509b069cb8721e64c4fe3',
    'vllm_ready_endpoint_contract.py':'ddae1078d9807910c9f79a815f2abeab6b1c340e8122eb4c3bca9a7b71ef3d7d',
    'vllm_weight_iteration_metadata.py':'f8ced901b1b1c7d918f96c9ef8ec0899696c2d161efbaefdd43dbedd340f04c0',
    'vllm_weight_iteration_sensor.py':'6048767399a893605c693c49f89a3f97360ec081b59169b7ac29fb08ac9e7648',
    'vllm_safe_open_mapping_metadata.py':'833030d5d24aad97bc7ec0050ef105488daeb0f2023987d75bb1d8ffdbc0be29',
    'vllm_safe_open_mapping_sensor.py':'dd9e75f2752e042b43527ddd9cf13cc06a3c82fd4c5ba73cfe7114df7dcafaf1',
    'vllm_file_read_sensor.py':'e32c0a53e810735f9eaca1a648397af49ca259d264020fd0f99193c401b2697d',
}
TRUTH=tuple(sorted(set(legacy.TRUTH)|set(common.TRUTH)))
MAX_LOCAL_LINKS=64
STAGES=frozenset(('header','binding','startup_generation_full','startup_generation',
                  'worker_file_roots','weight_iteration','safe_open_mappings'))
CODES=frozenset((common.ERROR,'GENERATION_FILE_CONTEXT_BINDING_UNAVAILABLE','generation_file_capture_missing'))


def _require(value):
    if not value:raise ValueError('NATIVE_GENERATION_FILE_INPUT_INVALID')


def _source_gate():
    root=Path(__file__).resolve().parents[1]/'adapters'
    try:matched=all(sha256_hex((root/name).read_bytes())==expected for name,expected in SOURCE_PINS.items())
    except OSError:matched=False
    if not matched:raise ValueError('NATIVE_GENERATION_FILE_COMMON_SOURCE_MISMATCH')


def _analyze(packet,prepared):
    _require(type(prepared) is dict and type(prepared.get('profile')) is str and prepared['profile'] in PROFILES)
    projection=common.project_file_contexts(packet,prepared)
    _require(projection['schema']==common.SCHEMA and projection['version']==common.VERSION
             and projection['recording_profile']==prepared['profile'])
    allowed=legacy._declared_references(prepared,origins=('EXAMPLE','DECLARED','MEASURED'))
    recordings={row['recording']:row for row in projection['recordings']}
    spans=[]
    for row in recordings.values():
        local=row.get('startup_generation') or {}
        # Profile21/22 currently omit spans. Absence is preserved instead of
        # reproducing A1 callback/phase pairing from its private journal.
        spans.extend(local.get('spans',[]))
    startups={'model_candidates':projection['model_candidates'],'spans':spans,'missing':projection['missing']}
    weights=projection['weight_iteration'];mappings=projection['safe_open_mappings']
    result=legacy._join_candidates(allowed,[],startups,weights,mappings,
        prepared['assets']['model-read-scope.json']['sha256'],profile=prepared['profile'],allow_missing_spans=True)
    roots={(row['recording'],row['call_sha256']):row for row in weights['model_candidates']}
    starts={(row['recording'],row['worker_call_sha256'],row['root_load_sha256']):row for row in projection['model_candidates']}
    paired={(row['recording'],row['call_sha256']) for row in spans}
    for candidate in result['candidates']:
        recording=recordings[candidate['recording']]
        root=roots[(candidate['recording'],candidate['root_load_sha256'])]
        start=starts[(candidate['recording'],candidate['startup_call_sha256'],candidate['root_load_sha256'])]
        links=start.get('worker_generation_candidates',[])
        _require(type(links) is list)
        candidate.update(recording_profile=prepared['profile'],
            process_binding_verified=recording['process_binding_verified'],
            image_binding_verified=recording['image_binding_verified'],
            startup_pair_facts_available=(candidate['recording'],candidate['startup_call_sha256']) in paired,
            root_last_seq=root['last_seq'],first_weight_source_event=root['first_source_event'],
            last_weight_source_event=root['last_source_event'],
            local_generation_link_count=len(links),
            local_generation_link_refs=[[candidate['recording'],link['link_event_seq']] for link in links[:MAX_LOCAL_LINKS]],
            local_generation_link_locators_complete=len(links)<=MAX_LOCAL_LINKS,
            local_worker_generation_model_candidate_observed=start.get('local_worker_generation_model_candidate') is True,
            **{key:False for key in TRUTH})
        if not candidate['startup_pair_facts_available']:
            result['missing'].append('generation_file_startup_pair_facts_unavailable')
        if len(links)>MAX_LOCAL_LINKS:result['missing'].append('generation_file_local_generation_locator_output_bound')
    status_keys=('recording','capture_available','process_binding_verified','image_binding_verified',
                 'duplicate_process_generation','status','pid','start_ticks')
    diagnostics=[]
    for row in projection['diagnostics']:
        _require(set(row)=={'recording','stage','code'} and
                 (row['recording'] is None or type(row['recording']) is int) and row['stage'] in STAGES and row['code'] in CODES)
        diagnostics.append({key:row[key] for key in ('recording','stage','code')})
    result.update(schema=SCHEMA,version=VERSION,public_projection_schema=common.SCHEMA,
        scope_data_origin=prepared['model_read_scope']['data_origin'],
        recording_status=[{key:row[key] for key in status_keys if key in row} for row in projection['recordings']],
        diagnostics=diagnostics,observed_weight_fact_count=len(weights['samples']),
        observed_mapping_fact_count=len(mappings['samples']),missing=sorted(set(result['missing'])),
        security='UNKNOWN',**{key:False for key in TRUTH})
    return result


def analyze(packet,prepared):
    """Consume the exact admitted projection; no collection or approval occurs."""
    _source_gate()
    try:return _analyze(packet,prepared)
    except (ValueError,KeyError,TypeError,UnicodeError,RecursionError,IndexError,AttributeError,OSError,RuntimeError,OverflowError):
        raise ValueError('NATIVE_GENERATION_FILE_INPUT_INVALID') from None


def attach_configured_models(trials,analysis):
    """Consume A1's existing exact model/root join, then require full upper bounds.

    A1 calls its own attach_configured_models first. This function does not
    recreate that common selection or accept a matching time/PID as identity.
    """
    _require(analysis['schema']==SCHEMA and analysis['version']==VERSION and analysis['profile'] in PROFILES)
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator'];context=locator['context'];forward=locator['forward_seq'];selected=[]
            for candidate in analysis['candidates']:
                if (candidate['recording']!=locator['recording'] or not candidate['process_binding_verified']
                        or (candidate['pid'],candidate['start_ticks'])!=(context['pid'],context['start_ticks'])
                        or candidate['observed_model_object_sha256']!=model['session_model_object_sha256']):continue
                common_matches=[value for value in model.get('generation_file_context_candidates',[])
                    if value['recording_profile']==analysis['profile']
                    and value['startup_generation']['worker_call_sha256']==candidate['startup_call_sha256']
                    and value['startup_generation']['root_load_sha256']==candidate['root_load_sha256']
                    and value['weight_root']['loader_call_sha256']==candidate['loader_call_sha256']]
                if len(common_matches)!=1:continue
                last=candidate['last_weight_source_event']
                anchors=[candidate['startup_terminal_event'][1],candidate['root_last_seq'],candidate['last_mapping_event'][1]]
                if (last is None or last[0]!=candidate['recording'] or type(last[1]) is not int
                        or any(type(seq) is not int or not 0<=seq<forward for seq in (*anchors,last[1]))):continue
                selected.append(candidate)
            model['generation_scoped_file_context_candidates']=selected
            model['actual_tensor_consumption_verified']=False
    return trials

