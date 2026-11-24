"""Current owned Pod generation plus closed worker access records."""
from pathlib import Path

from guard.models import canonical_json,sha256_hex,utcnow

ASSET=Path(__file__).parent/'assets/worker_security_read_v1.py.txt'
SOURCE_SHA256='4677074e1d291cd9c8731ebeb9002d51db7f9f2973b9d4ffef8fcb77cb54d989'  # Fixed source pin; never operator input.


def source():
    raw=ASSET.read_bytes()
    if sha256_hex(raw)!=SOURCE_SHA256:raise ValueError('WORKER_SECURITY_READER_SOURCE_UNAVAILABLE')
    return raw.decode('utf-8')


def capture(transport,prepared_path):
    from guard.collectors.worker_security import load_prepared,validate_packet,qualification
    result={'profile':'kubernetes-owned-worker-security-v1','available':False,'records':[],
        'whole_selected_tp_roster_verified':False,'qualified_registered':False,
        'target_sha256':transport.target_sha256,'data_origin':transport.process_observation_origin,
        'started_at':utcnow().isoformat(),'finished_at':None,'diagnostic_codes':[]}
    try:
        prepared,cfg=load_prepared(prepared_path)
        if transport.profile!='kubernetes-owned-request-binding-http-v1':raise ValueError()
        engine=transport.binding['engine']
        spec={'session':prepared['session'],'ranks':prepared['expected_ranks'],
            'sensor_sha256':prepared['assets']['guard_worker_security.py']['sha256'],
            'settings_sha256':prepared['assets']['settings.json']['sha256']}
        transport._verify()
        packet=transport._call([f"--namespace={engine['namespace']}",'exec',engine['pod'],'-c',engine['container'],
            '--','python3','-I','-S','-B','-c',source(),canonical_json(spec).decode()],cap=300000,budget=8)
        transport._verify()
        records,complete=validate_packet(packet,prepared,cfg)
        registered=qualification(transport.engine_name,transport.engine_version,engine['image'],records)
        result.update(available=bool(records),records=records,whole_selected_tp_roster_verified=complete,
                      qualified_registered=registered,prepared_sha256=sha256_hex(canonical_json(prepared)),
                      sensor_sha256=spec['sensor_sha256'],settings_sha256=spec['settings_sha256'])
        if not complete:result['diagnostic_codes'].append('WORKER_SECURITY_ROSTER_INCOMPLETE')
        if not registered:result['diagnostic_codes'].append('WORKER_SECURITY_PRODUCER_UNREGISTERED')
    except Exception:result['diagnostic_codes'].append('WORKER_SECURITY_CAPTURE_UNAVAILABLE')
    result['finished_at']=utcnow().isoformat()
    return result
