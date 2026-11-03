"""REQ-17 재시험 사례 목록의 설계 조건. EXAMPLE 입력 계약이며 GPU 실측이 아니다."""
import json

import pytest

from guard.executors import engine_callback_projection as projection
from tools import gpu_a_req17_cohort as cohort

SAMPLED = ('control-present', 'control-absent', 'present', 'absent')


def test_cohort_keeps_roles_order_and_isolated_salts():
    cases = cohort.build(7)['cases']
    projection.validate_cases('REQ-17', cases)
    assert len(cases) == 4 + 24 * 5 <= projection.MAX_CASES
    assert {role: sum(c['role'] == role for c in cases) for role in SAMPLED} == dict.fromkeys(SAMPLED, 24)
    index = {c['case_id']: i for i, c in enumerate(cases)}
    by_id = {c['case_id']: c for c in cases}
    for n in range(24):
        prime = by_id[f'prime-{n}']
        assert index[f'prime-{n}'] < min(index[f'{r}-{n}'] for r in ('cp', 'ca', 'p', 'a'))
        assert by_id[f'cp-{n}']['prompt'] == by_id[f'p-{n}']['prompt'] == prime['prompt']
        assert by_id[f'cp-{n}']['cache_salt'] == prime['cache_salt'] == cohort.OWNER_SALT
    others = [c['cache_salt'] for c in cases if c['cache_salt'] != cohort.OWNER_SALT and not c['case_id'].startswith('w')]
    assert len(others) == len(set(others)) == 24 * 3
    assert {len(c['prompt']) for c in cases} == {1500} and all(c['prompt'].isdigit() for c in cases)
    fresh = [c['prompt'] for c in cases if c['role'] in ('control-absent', 'absent')]
    secrets = {c['prompt'] for c in cases if c['role'] == 'control-present'}
    assert len(set(fresh)) == len(fresh) and not set(fresh) & secrets


def test_seed_fixes_the_cohort():
    first, again, other = cohort.build(11), cohort.build(11), cohort.build(12)
    assert first == again and first['cases_sha256'] != other['cases_sha256']
    assert [c['role'] for c in first['cases']] != [c['role'] for c in other['cases']]


@pytest.mark.parametrize('options', [{'prompt_chars': 4097}, {'prompt_chars': 0}, {'block_size': 4}])
def test_limits_reject_unsupported_cohorts(options):
    with pytest.raises(ValueError):
        cohort.build(1, **options)


def test_cli_writes_recorded_cohort(tmp_path, capsys):
    out = tmp_path / 'cohort.json'
    cohort.main(['--seed', '3', '--quartets', '2', '--out', str(out)])
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written == cohort.build(3, quartets=2) and written['data_origin'] == 'EXAMPLE'
    assert json.loads(capsys.readouterr().out) == {'cases': 14, 'cases_sha256': written['cases_sha256']}
