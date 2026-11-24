"""A profile must choose one source/parser/engine contract across merged catalogs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters import vllm_baseline_stream_composition as stream
from guard.adapters import vllm_lifecycle_catalog as configured
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_release_sensor as release
from guard.adapters import vllm_release_observation_sensor as rank
from guard.collectors import lifecycle
from tests.test_lifecycle_capture import external  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('rows', [
    [('existing-profile', 'new-schema')],
    [('new-profile', 'existing-schema')],
    [('new-profile', 'schema-a'), ('new-profile', 'schema-b')],
    [('profile-a', 'new-schema'), ('profile-b', 'new-schema')],
])
def test_profile_or_event_schema_collision_is_rejected(rows):
    target = {'existing-profile': 'existing-schema'}
    with pytest.raises(ValueError, match='LIFECYCLE_PROFILE_IDENTITY_COLLISION'):
        profiles._add_profile_schemas(target, rows)


def test_release_variants_and_configured_rows_have_disjoint_identities():
    release_profiles = [profile for profile, _ in rank.PROFILES.values()]
    release_schemas = [schema for _, schema in rank.PROFILES.values()]
    assert len(set(release_profiles)) == len(release_profiles)
    assert len(set(release_schemas)) == len(release_schemas)
    assert not set(release_profiles).intersection(configured.ROWS)
    assert not set(release_schemas).intersection(row['schema'] for row in configured.ROWS.values())
    assert stream.PROFILE == 'vllm-native-lifecycle-metadata/33'
    assert rank.RANK_PROFILE == 'vllm-native-lifecycle-metadata/34'


def test_both_profiles_prepare_and_reload_their_own_source_and_parser(external):
    baseline = lifecycle.prepare_assets(external / 'baseline-stream', REPO, profile=stream.PROFILE)
    candidate = lifecycle.prepare_assets(external / 'candidate-rank', REPO, profile=rank.RANK_PROFILE)
    for folder, prepared in [('baseline-stream', baseline), ('candidate-rank', candidate)]:
        assert lifecycle.load_prepared(external / folder / 'manifest.json') == prepared
        assert prepared['source_sha256'] == profiles.source_hashes(prepared['profile'])
        assert prepared['public_commit'] == profiles.public_commit(prepared['profile'])
    assert baseline['source_sha256'] != candidate['source_sha256']
    assert baseline['public_commit'] == configured.row(stream.PROFILE)['commit']
    assert candidate['public_commit'] == release.COMMIT
    assert 'guard_stream_buffer_extension.py' in baseline['assets']
    assert 'guard_stream_buffer_extension.py' not in candidate['assets']
    assert 'guard_lifecycle_extension.py' in candidate['assets']
    assert 'guard_lifecycle_extension.py' not in baseline['assets']
    assert profiles.SCHEMAS[stream.PROFILE] == stream.SCHEMA
    assert profiles.SCHEMAS[rank.RANK_PROFILE] == rank.PROFILES['rank'][1]


def test_historical_rank_number_cannot_be_relabelled_as_baseline_stream(external):
    prepared = lifecycle.prepare_assets(external / 'rank', REPO, profile=rank.RANK_PROFILE)
    changed = deepcopy(prepared)
    changed['profile'] = stream.PROFILE
    manifest = external / 'rank' / 'manifest.json'
    manifest.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        lifecycle.load_prepared(manifest)
