"""기존 LIF-08 실행기의 파일 소비 경로. 네트워크·장애 주입·판정 의미를 추가하지 않는다."""
from pathlib import Path

from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from .base import EvidenceDraft
from .rank_failure_contract import normalize_policy
from .lifecycle_common_input_consumption import _read, attach_inputs


def recipe_source_files(cfg):
    """Bind the passive source paths and their exact bytes before consumption."""
    if (type(cfg) is not dict or set(cfg) != {'input_mode', 'target', 'prepared', 'common_inputs'}
            or cfg['input_mode'] != 'common-records'):
        return None
    try:
        descriptor = cfg['common_inputs']
        if (type(descriptor) is not dict or descriptor['prepared'] != cfg['prepared']
                or type(descriptor['records']) is not dict or set(descriptor['records']) != {'rank-fault'}):
            return None
        paths = {'prepared': cfg['prepared'], 'record': descriptor['records']['rank-fault']}
        files = {}
        for key, path in paths.items():
            path = str(Path(path).absolute())
            try:
                _, digest = _read(path)
            except (OSError, ValueError):
                digest = None
            files[key] = {'path': path, 'sha256': digest}
        return files
    except (KeyError, TypeError):
        return None


def run_records(check, env, values, cfg, workdir, *, expected_target_sha256=None, expected_files=None):
    started = utcnow()
    start, end = normalize_policy(values)
    prepared_reads = {}
    try:
        if (type(cfg) is not dict or set(cfg) != {'input_mode', 'target', 'prepared', 'common_inputs'}
                or cfg['input_mode'] != 'common-records' or not env.hash_ok()):
            raise ValueError
        target, _ = _read(cfg['target'])
        expected = expected_files['prepared'] if expected_files is not None else None
        prepared, _ = _read(cfg['prepared'], reads=prepared_reads, expected_file=expected)
        descriptor = cfg['common_inputs']
        if (type(target) is not dict or target.get('environment_ref') != env.ref().model_dump(mode='json')
                or type(prepared) is not dict or type(descriptor) is not dict
                or type(descriptor.get('records')) is not dict or set(descriptor['records']) != {'rank-fault'}
                or descriptor.get('prepared') != cfg['prepared']
                or descriptor.get('session') != prepared.get('session')
                or sha256_hex(canonical_json(target)) != values['rank_target_sha256']
                or env.ref().sha256 != values['rank_environment_sha256']
                or sha256_hex(canonical_json(prepared)) != values['rank_prepared_sha256']
                or expected_target_sha256 is not None and values['rank_target_sha256'] != expected_target_sha256
                or not start <= utcnow() <= end):
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('RANK_RECORD_INPUT_CONFIGURATION_INVALID') from None
    missing = ['RANK_RECORD_INPUT_FILE_ONLY', 'RANK_RECORD_INPUT_SOURCE_TIME_UNVERIFIED',
        'RANK_NORMAL_ALL_RANKS_UNVERIFIED', 'RANK_UPPER_RECEIPT_UNVERIFIED']
    origin = min(env.data_origin, DataOrigin.DECLARED, key=lambda v: ORIGIN_STRENGTH[v])
    draft = EvidenceDraft(status=ExecStatus.ABORTED, data_origin=origin,
        normal_control={'attempted': False, 'succeeded': None,
            'detail': '외부 공통 파일의 수집이며 실제 엔진 요청을 실행하지 않았다.'},
        observations={
            'rank_record_input': {'value': {'input_mode': 'common-records', 'engine_executed': False}, 'collected': True},
            'activation_steps': {'value': [], 'collected': True},
            'activation_missing': {'value': missing, 'collected': True},
            'activation_violations': {'value': [], 'collected': True},
            'activation_scope_complete': {'value': False, 'collected': True}},
        config={'input_mode': 'common-records', 'target_sha256': values['rank_target_sha256'],
            'prepared_sha256': values['rank_prepared_sha256'], 'engine_executed': False},
        started_at=started, abort_reason='RANK_RECORD_INPUT_QUESTION_SCOPE_INCOMPLETE')
    # 선언된 준비물을 runtime 자격으로 넘기지 않는다. 현재 owner는 재관측하지 않았다.
    draft = attach_inputs(draft, check, env, target, descriptor, Path(workdir), prepared_reads=prepared_reads, expected_files=expected_files)
    draft.finished_at = utcnow()
    return draft
