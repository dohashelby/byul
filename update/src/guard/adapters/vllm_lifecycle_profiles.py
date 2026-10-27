"""Known private recording versions; old assets are read, never executed here."""
from __future__ import annotations

import hashlib
from pathlib import Path
from . import vllm_lifecycle_sensor as sensor

LEGACY_PROFILE = "vllm-native-lifecycle-metadata/1"
LEGACY_SCHEMA = "guard-native-lifecycle-events/1"
V2_PROFILE = "vllm-native-lifecycle-metadata/2"
V2_SCHEMA = "guard-native-lifecycle-events/2"
SCHEMAS = {sensor.PROFILE: sensor.SCHEMA, LEGACY_PROFILE: LEGACY_SCHEMA, V2_PROFILE: V2_SCHEMA}
# Exact previously shipped LF/CRLF source spellings; no normalization on input.
LEGACY_ASSETS = {
    (31143, "50143b5cf54aa8f5bbcfef769c286f65c971e0acd2caa111c625583f4baec3de"),
    (31825, "812d512c75c15fb369e2546ef0a01c858e872ad0aaf033e146b3b36b5473d4c8"),
}
V2_ASSETS = {
    (31774, "ebaab06ea8388de1a3a70fa9321f44ea7472032c9aa5227a5854f3f406d1801e"),
    (32467, "88e3f03cb65c0b33d795281d37610f6ed7cbf53a98ae4818805584f625a9a88e"),
}

LEGACY_SOURCE_SHA = {'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
 'vllm.v1.engine.output_processor': 'c72d0149e18d6da71d53e23b6550d09310af6917523fef6e282b0907d36b8d54',
 'vllm.v1.engine.async_llm': 'ff933ed78aa3195782cf1c6d18c53897adfa5bf920421bd2bcc330f2adc84d1e',
 'vllm.v1.engine.core': 'f116962c35d9d312b20b0b76c34a8cdaa6d3f1d31f2036db4e123957d2d1555b',
 'vllm.v1.engine.core_client': '24eecc78e8fc4849d4eac948c7586ee349ebfd503d07e80f05ec6c859da06fdd',
 'vllm.v1.core.sched.scheduler': 'f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a',
 'vllm.v1.core.sched.output': '972e0bc8e7af1f4262ac73f660f6b8f798ba780f86c031a9c8a7f10cbfaad3a8',
 'vllm.v1.core.kv_cache_manager': 'bd9bf46a2101f9368d4941867415a239d18d95e0b956f82f885ce25e10e69f3b',
 'vllm.v1.core.single_type_kv_cache_manager': '3a3cb20bd6aa1e0ad46471fc9e5e01ee5797ab4bb759e00ef5fb309d2830db13',
 'vllm.v1.core.kv_cache_coordinator': '6a1e00227b981a7bb6108a42db0e446c716f789274f40c63eb9a6ce4d699ddcb',
 'vllm.v1.core.block_pool': '05c73052934a9d4664ccd5df562ce71011278d9a511932da102ef2efff00e934',
 'vllm.v1.worker.gpu_model_runner': 'bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a',
 'vllm.v1.worker.gpu_input_batch': 'e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d',
 'vllm.v1.worker.cpu_model_runner': '69cd626ddbc3b1693e026fda99018201d8c9f4dd559540970e8ff265b7a97093',
 'vllm.v1.request': 'dd7c30bf8b0894dc123a739c299c0133566f8cc075b081e45905f1c29506e69d',
 'vllm.entrypoints.serve.utils.api_utils': 'ffb5e08c2b6cf6d0402c6ba31d0d05e53eb7e9e8c13b29700e84a4bfada420ba',
 'vllm.entrypoints.openai.completion.serving': 'aac889978efb034b95ab81ec59695d83ba7c879ab48cd5be2448c980f109a3bd'}

LEGACY_POINTS = {'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request',
                                                         'abort_requests',
                                                         '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free',
                                                                            'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}


def asset_supported(profile, raw):
    if profile == LEGACY_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in LEGACY_ASSETS
    if profile == V2_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V2_ASSETS
    if profile == sensor.PROFILE:
        return raw == Path(sensor.__file__).read_bytes()
    return False


def source_hashes(profile):
    if profile == sensor.PROFILE:
        return sensor.SOURCE_SHA
    if profile in {LEGACY_PROFILE, V2_PROFILE}:
        return LEGACY_SOURCE_SHA
    raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")


def points(profile):
    if profile == sensor.PROFILE:
        return sensor.POINTS
    if profile in {LEGACY_PROFILE, V2_PROFILE}:
        return LEGACY_POINTS
    raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")
