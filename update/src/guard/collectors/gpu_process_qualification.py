"""Exact-source GPU census qualification; never whole GPU release or recovery."""
from pathlib import Path
import hashlib,json,re
from guard.adapters import gpu_process_probe,resource_recovery_probe
from . import qualification_document

ASSETS=Path(gpu_process_probe.__file__).with_name('assets')
REGISTRY=ASSETS/'gpu_process_qualifications.json'
SCOPE='owned-host-gpu-process-and-device-reference-census'


def producer():
    root=Path(gpu_process_probe.__file__).parents[1]
    paths=[Path(__file__),Path(gpu_process_probe.__file__),Path(resource_recovery_probe.__file__),
        root/'runtime/gpu_process_source.py',root/'runtime/source_assets.py',root/'runtime/bounded.py',
        Path(qualification_document.__file__),root/'executors/node_gpu_process.py',
        root/'executors/node_image_proof.py']
    digest=hashlib.sha256()
    for path in paths:digest.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    return {'name':'guard-owned-host-gpu-process-census','version':'1','source_sha256':digest.hexdigest()}


def status(engine,version,image):
    result={'registered':False,'producer':producer(),'qualified_scope':SCOPE,
        'whole_gpu_release_qualified':False,'whole_group_recovery_qualified':False,'whole69_complete':False}
    try:
        from guard.adapters.vllm_rank_ipc_capture import _read
        data=qualification_document.read(_read(REGISTRY,32768))
        if(type(data)is not dict or set(data)!={'schema','qualifications'}or data['schema']!='guard-owned-host-gpu-process-qualifications/1'
                or type(data['qualifications'])is not list or len(data['qualifications'])>64):raise ValueError
        keys={'engine','version','image','producer','scope','evidence_file','evidence_bytes','evidence_sha256','verified_export_sha256'}
        if any(type(r)is not dict or set(r)!=keys for r in data['qualifications']):raise ValueError
        rows=[r for r in data['qualifications']if(r['engine'],r['version'],r['image'],r['producer'])==(engine,version,image,result['producer'])]
        if not rows:return result
        if len(rows)!=1:raise ValueError
        row,=rows
        sha=lambda v:type(v)is str and re.fullmatch('[0-9a-f]{64}',v)is not None
        if(row['scope']!=SCOPE or type(row['evidence_file'])is not str
                or re.fullmatch(r'[A-Za-z0-9_-]+\.json',row['evidence_file'])is None
                or type(row['evidence_bytes'])is not int or not 0<row['evidence_bytes']<=32768
                or not sha(row['evidence_sha256'])or not sha(row['verified_export_sha256'])):raise ValueError
        from guard.adapters.vllm_rank_ipc_sources import _bytes
        raw=_bytes(ASSETS/row['evidence_file'],{'bytes':row['evidence_bytes'],'sha256':row['evidence_sha256']})
        evidence=qualification_document.read(raw)
        required=('actual_gpu_source_qualified','exact_node_task_and_root_mount_verified',
            'normal_repeated_host_worker_gpu_links_verified','source_or_packet_tamper_rejected',
            'stale_anchor_or_owner_rejected','partial_query_not_complete')
        if(evidence.get('schema')!='byul-owned-host-gpu-process-actual-qualification/1'
                or evidence.get('producer')!=result['producer']or evidence.get('scope')!=SCOPE or evidence.get('image')!=image
                or any(evidence.get(k)is not True for k in required)
                or any(evidence.get(k)is not False for k in('whole_gpu_release_qualified','whole_group_recovery_qualified','whole69_complete'))):raise ValueError
        return {**result,'registered':True,'evidence_sha256':row['evidence_sha256'],'verified_export_sha256':row['verified_export_sha256']}
    except(OSError,ValueError,KeyError,TypeError):raise ValueError('GPU_PROCESS_QUALIFICATION_DATA_INVALID')from None
