"""Native source fixtures exercise the normal projection without GPU or requests."""

from copy import deepcopy
from pathlib import Path

import pytest

from guard.adapters import vllm_ready_endpoint_metadata, vllm_worker_generation_metadata
from guard.executors import initialization_normal_worker as subject
from guard.executors.lifecycle_capture import parse_recording
from guard.models import load_pack, sha256_hex
from tests import test_a1_ready_endpoint_product as endpoint, test_a1_worker_generation_product as generation
from tests.builders import environment
from tests.test_kubernetes_lifecycle import group, target as native_target
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory


@pytest.mark.parametrize(('producer', 'metadata'), [
    (generation, vllm_worker_generation_metadata),
    (endpoint, vllm_ready_endpoint_metadata),
])
def test_normal_worker_projection_keeps_only_qualified_local_facts(
        tmp_path, monkeypatch, group, native_target, writer_factory, producer, metadata):
    world, _, _, _ = producer.inputs(tmp_path, group, native_target, 'normal', monkeypatch, 'normal')
    prepared = group.prepared
    assert not prepared['engine_started']
    context = {'declared_phase': 'normal', 'expected_ranks': ['0'], 'sealed': True}
    assert subject.normal_worker_steps(None, prepared, {}, **context) == []
    world.add_native()
    raw = group.raw_by_pid[42]
    records, summary = parse_recording(raw, prepared)
    # The source bytes were emitted by the fixed sensor. The envelope models
    # the owner's verified collector response and remains an EXAMPLE fixture.
    recording = {'session_owner_match': True, 'listed_file_identity_match': True,
                 'capture': {'records': records, 'summary': summary,
                             'provenance': {'owner_binding_verified': True, 'capture_sha256': sha256_hex(raw)},
                             'process_context_verified': True, 'image_digest_verified': True}}
    packet = {'session': prepared['session'], 'recorded_files_covered': True, 'recordings': [recording]}
    projected = metadata.project(packet, prepared)
    step, = subject.normal_worker_steps(packet, prepared, projected, **context)
    assert step['step'] == 'native-normal-worker-load-return-and-ready-send'
    assert step['source_recording_sha256'] == sha256_hex(raw)
    assert step['image_identity_basis'] == 'node-verified-image-digest'
    assert step['session'] == prepared['session'] and step['rank'] == 0
    start, end = step['worker_load_call_and_return_seq']
    send_start, send_end = step['ready_send_call_and_return_seq']
    assert start < step['model_root_event_seq'] < end < send_start < send_end
    assert not world.sent

    for changed in ({'sealed': False}, {'declared_phase': 'partial-failure'}, {'expected_ranks': ['99']}):
        assert subject.normal_worker_steps(packet, prepared, projected, **{**context, **changed}) == []
    for changed in ({'session': 'f' * 32}, {'recorded_files_covered': False}, {'recordings': []}):
        assert subject.normal_worker_steps({**packet, **changed}, prepared, projected, **context) == []
    for key in ('process_context_verified', 'image_digest_verified'):
        damaged = deepcopy(packet)
        damaged['recordings'][0]['capture'][key] = False
        assert subject.normal_worker_steps(damaged, prepared, projected, **context) == []
    damaged = deepcopy(packet)
    damaged['recordings'][0]['capture']['summary']['incomplete_final_row'] = True
    assert subject.normal_worker_steps(damaged, prepared, projected, **context) == []
    assert subject.normal_worker_steps(packet, prepared, {**projected, 'ready_sends': []}, **context) == []
    api_only = deepcopy(packet)
    capture = api_only['recordings'][0]['capture']
    capture['image_digest_verified'] = False
    image = group.env.engine.image_digest
    capture['provenance']['native_image_reference'] = {
        'declared_image': image, 'reported_image_id': image,
        'api_binding_verified_before': True, 'api_binding_verified_after': True,
    }
    api_projected = metadata.project(api_only, prepared)
    result = subject.normal_worker_steps(api_only, prepared, api_projected, **context)
    if producer is generation:
        api_step, = result
        assert api_step['image_identity_basis'] == 'api-reported-exact-image-digest'
    else:
        assert not result


@pytest.mark.parametrize('check_id', ['LIF-07', 'LIF-09'])
def test_normal_worker_projection_requires_all_typed_activation_observations(check_id):
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs' / 'PACK-BASELINE')
    check = pack.checks[check_id]
    assert subject.has_activation_contract(check)
    for key in subject.ACTIVATION_TYPES:
        required = [item for item in check.required_observations if item.key != key]
        assert not subject.has_activation_contract(check.model_copy(update={'required_observations': required}))
    wrong = [item.model_copy(update={'type': 'list'}) if item.key == 'activation_scope_complete' else item
             for item in check.required_observations]
    assert not subject.has_activation_contract(check.model_copy(update={'required_observations': wrong}))


@pytest.mark.parametrize('profile', sorted(subject.GENERATION_PROFILES))
def test_normal_generation_profile_rejects_cross_release_or_foreign_image(profile):
    valid = subject.release_profile('0.31.0')
    other = subject.release_profile('0.30.0')
    for release, image, expected in [
        (valid, valid['images'][0], True),
        (valid, other['images'][0], False),
        (other, other['images'][0], False),
        (other, valid['images'][0], False),
    ]:
        env = environment(engine={'name': 'vllm', 'version': release['version'], 'image_digest': image})
        assert subject.generation_release_supported(env, profile) is expected
