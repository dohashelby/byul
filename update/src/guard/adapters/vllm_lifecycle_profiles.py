"""Known private recording versions; old assets are read, never executed here."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from . import vllm_lifecycle_sensor as sensor
from . import vllm_online_sensor as online
from . import vllm_attempt_sensor as attempt
from . import vllm_request_output_sensor as output_sensor
from . import vllm_stream_buffer_sensor as stream_sensor
from . import vllm_rank_failure_sensor as rank_sensor
from . import vllm_initialization_sensor as init_sensor
from . import vllm_profiler_sensor as profiler_sensor

LEGACY_PROFILE = "vllm-native-lifecycle-metadata/1"
LEGACY_SCHEMA = "guard-native-lifecycle-events/1"
V2_PROFILE = "vllm-native-lifecycle-metadata/2"
V2_SCHEMA = "guard-native-lifecycle-events/2"
V3_PROFILE = "vllm-native-lifecycle-metadata/3"
V3_SCHEMA = "guard-native-lifecycle-events/3"
V4_PROFILE = "vllm-native-lifecycle-metadata/4"
V4_SCHEMA = "guard-native-lifecycle-events/4"
V5_PROFILE = "vllm-native-lifecycle-metadata/5"
V5_SCHEMA = "guard-native-lifecycle-events/5"
V6_PROFILE = "vllm-native-lifecycle-metadata/6"
V6_SCHEMA = "guard-native-lifecycle-events/6"
V7_PROFILE = "vllm-native-lifecycle-metadata/7"
V7_SCHEMA = "guard-native-lifecycle-events/7"
SCHEMAS = {profiler_sensor.PROFILE:profiler_sensor.SCHEMA,init_sensor.PROFILE:init_sensor.SCHEMA,rank_sensor.PROFILE: rank_sensor.SCHEMA, stream_sensor.PROFILE: stream_sensor.SCHEMA, output_sensor.PROFILE: output_sensor.SCHEMA, sensor.PROFILE: sensor.SCHEMA, online.PROFILE: online.SCHEMA, attempt.PROFILE: attempt.SCHEMA, LEGACY_PROFILE: LEGACY_SCHEMA, V2_PROFILE: V2_SCHEMA, V3_PROFILE: V3_SCHEMA, V4_PROFILE: V4_SCHEMA, V5_PROFILE: V5_SCHEMA, V6_PROFILE: V6_SCHEMA, V7_PROFILE: V7_SCHEMA}
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


V3_ASSETS = {(37472, '3cd9bf59b5c9f6e1edfd4c27aef4cdc568ac14db38bc29b4c1afaad629f22624'), (36695, '1f9b69ab3be55e8c5304028825c59fabe71c74bc949b3606fe3bc8effcadbb82')}
V3_SOURCE_SHA = {'vllm.model_executor.model_loader.base_loader': '5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2',
 'vllm.model_executor.model_loader.default_loader': 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
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
V3_POINTS = {'vllm.model_executor.model_loader.base_loader': {'BaseModelLoader': ('load_model',)},
 'vllm.model_executor.model_loader.default_loader': {'DefaultModelLoader': ('load_weights',)},
 'vllm.v1.worker.cpu_model_runner': {'CPUModelRunner': ('load_model', 'get_model')},
 'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request', 'abort_requests', '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens',
                                                        'load_model',
                                                        'get_model',
                                                        '_model_forward'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}


V4_ASSETS = {(41459, '876f92626f4937bb52ecbd1ececa1f35b0a35489f24e945f75ab11149493e264'), (42309, '991e79b27e27ddafb8f785e23893149638891b2d8a3747990cb969b153b274cf')}
V4_SOURCE_SHA = {'vllm.model_executor.model_loader.weight_utils': '7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd',
 'vllm.model_executor.model_loader.base_loader': '5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2',
 'vllm.model_executor.model_loader.default_loader': 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
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
V4_POINTS = {'vllm.model_executor.model_loader.base_loader': {'BaseModelLoader': ('load_model',)},
 'vllm.model_executor.model_loader.default_loader': {'DefaultModelLoader': ('load_weights',
                                                                            '_prepare_weights',
                                                                            '_get_weights_iterator')},
 'vllm.v1.worker.cpu_model_runner': {'CPUModelRunner': ('load_model', 'get_model')},
 'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request', 'abort_requests', '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens',
                                                        'load_model',
                                                        'get_model',
                                                        '_model_forward'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}
V4_FUNCTION_POINTS = {'vllm.model_executor.model_loader.weight_utils': ('safetensors_weights_iterator',
                                                   'pt_weights_iterator',
                                                   'multi_thread_pt_weights_iterator',
                                                   'multi_thread_safetensors_weights_iterator',
                                                   'fastsafetensors_weights_iterator',
                                                   'instanttensor_weights_iterator',
                                                   'np_cache_weights_iterator')}


V5_ASSETS = {(47577, 'f4fe02abd35fb0922c47fa19df611566d5f39ceb288b39d310b3487b9667456b'),
 (48519, '071fbd5be48c55944ae1513d98097e54d7e491644239014a0af3c515fde258ce')}
V5_SOURCE_SHA = {'vllm.entrypoints.serve.middleware.authenticate': 'ceb576aa3c053a5bcf145cfbe3f81def0f7213480c4aa1531fc4c00e32955225',
 'vllm.entrypoints.openai.models.serving': '39ecebf92abec7931b4c953e0d9743320dbd93532a8e3488307398c29dba2553',
 'vllm.entrypoints.openai.models.api_router': '58d7bf90b7dd2a896441d3085b8a4467c12412120f21fe53d838109b7b85f48b',
 'vllm.model_executor.model_loader.weight_utils': '7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd',
 'vllm.model_executor.model_loader.base_loader': '5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2',
 'vllm.model_executor.model_loader.default_loader': 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
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
V5_POINTS = {'vllm.entrypoints.serve.middleware.authenticate': {'AuthenticationMiddleware': ('__call__', 'verify_token')},
 'vllm.entrypoints.openai.models.serving': {'OpenAIServingModels': ('show_available_models',)},
 'vllm.model_executor.model_loader.base_loader': {'BaseModelLoader': ('load_model',)},
 'vllm.model_executor.model_loader.default_loader': {'DefaultModelLoader': ('load_weights',
                                                                            '_prepare_weights',
                                                                            '_get_weights_iterator')},
 'vllm.v1.worker.cpu_model_runner': {'CPUModelRunner': ('load_model', 'get_model')},
 'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request', 'abort_requests', '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free', 'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens',
                                                        'load_model',
                                                        'get_model',
                                                        '_model_forward'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}
V5_FUNCTION_POINTS = {'vllm.model_executor.model_loader.weight_utils': ('safetensors_weights_iterator',
                                                   'pt_weights_iterator',
                                                   'multi_thread_pt_weights_iterator',
                                                   'multi_thread_safetensors_weights_iterator',
                                                   'fastsafetensors_weights_iterator',
                                                   'instanttensor_weights_iterator',
                                                   'np_cache_weights_iterator'),
 'vllm.entrypoints.openai.models.api_router': ('models',)}

V6_ASSETS = {(53372, 'f1780d8b0e25ea695b346cab5adf95c624d039c92ccdb33ecf7221c0957724a4'), (52361, '54c1ac305dc9abfbdfd75eedc90c21a404b3409f33e0b0d6619613add43f31e4')}
V6_SOURCE_SHA = {'vllm.entrypoints.generate.base.serving': '9bffd4141b12973b37c08b19d603f2694c42d6fb41f9d1569c349445dc7d00b2',
 'vllm.entrypoints.serve.engine.serving': 'e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02',
 'vllm.entrypoints.serve.middleware.authenticate': 'ceb576aa3c053a5bcf145cfbe3f81def0f7213480c4aa1531fc4c00e32955225',
 'vllm.entrypoints.openai.models.serving': '39ecebf92abec7931b4c953e0d9743320dbd93532a8e3488307398c29dba2553',
 'vllm.entrypoints.openai.models.api_router': '58d7bf90b7dd2a896441d3085b8a4467c12412120f21fe53d838109b7b85f48b',
 'vllm.model_executor.model_loader.weight_utils': '7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd',
 'vllm.model_executor.model_loader.base_loader': '5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2',
 'vllm.model_executor.model_loader.default_loader': 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
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
V6_POINTS = {'vllm.entrypoints.generate.base.serving': {'GenerateBaseServing': ('_get_data_parallel_rank',
                                                                    '_get_session_id_from_headers',
                                                                    '_get_session_id',
                                                                    '_get_priority')},
 'vllm.entrypoints.serve.engine.serving': {'BaseServing': ('_base_request_id',)},
 'vllm.entrypoints.serve.middleware.authenticate': {'AuthenticationMiddleware': ('__call__',
                                                                                 'verify_token')},
 'vllm.entrypoints.openai.models.serving': {'OpenAIServingModels': ('show_available_models',)},
 'vllm.model_executor.model_loader.base_loader': {'BaseModelLoader': ('load_model',)},
 'vllm.model_executor.model_loader.default_loader': {'DefaultModelLoader': ('load_weights',
                                                                            '_prepare_weights',
                                                                            '_get_weights_iterator')},
 'vllm.v1.worker.cpu_model_runner': {'CPUModelRunner': ('load_model',
                                                        'get_model')},
 'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request',
                                                         'abort_requests',
                                                         '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free',
                                                      'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free',
                                                                            'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens',
                                                        'load_model',
                                                        'get_model',
                                                        '_model_forward'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}
V6_FUNCTION_POINTS = {'vllm.model_executor.model_loader.weight_utils': ('safetensors_weights_iterator',
                                                   'pt_weights_iterator',
                                                   'multi_thread_pt_weights_iterator',
                                                   'multi_thread_safetensors_weights_iterator',
                                                   'fastsafetensors_weights_iterator',
                                                   'instanttensor_weights_iterator',
                                                   'np_cache_weights_iterator'),
 'vllm.entrypoints.openai.models.api_router': ('models',)}


V7_ASSETS = {(55952, 'e981d01d4aa982a975d89a3693aaf774f19e149ec4a660e62b82563d67725656'),
 (57021, '7f656969ec0d873a040d17a5844efa89feb30562cf37da4fa5583cef7ba446a0')}
V7_SOURCE_SHA = {'vllm.entrypoints.generate.base.serving': '9bffd4141b12973b37c08b19d603f2694c42d6fb41f9d1569c349445dc7d00b2',
 'vllm.entrypoints.serve.engine.serving': 'e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02',
 'vllm.entrypoints.serve.middleware.authenticate': 'ceb576aa3c053a5bcf145cfbe3f81def0f7213480c4aa1531fc4c00e32955225',
 'vllm.entrypoints.openai.models.serving': '39ecebf92abec7931b4c953e0d9743320dbd93532a8e3488307398c29dba2553',
 'vllm.entrypoints.openai.models.api_router': '58d7bf90b7dd2a896441d3085b8a4467c12412120f21fe53d838109b7b85f48b',
 'vllm.model_executor.model_loader.weight_utils': '7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd',
 'vllm.model_executor.model_loader.base_loader': '5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2',
 'vllm.model_executor.model_loader.default_loader': 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 'vllm.v1.engine.input_processor': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
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
V7_POINTS = {'vllm.entrypoints.generate.base.serving': {'GenerateBaseServing': ('_get_data_parallel_rank',
                                                                    '_get_session_id_from_headers',
                                                                    '_get_session_id',
                                                                    '_get_priority')},
 'vllm.entrypoints.serve.engine.serving': {'BaseServing': ('_base_request_id',)},
 'vllm.entrypoints.serve.middleware.authenticate': {'AuthenticationMiddleware': ('__call__',
                                                                                 'verify_token')},
 'vllm.entrypoints.openai.models.serving': {'OpenAIServingModels': ('show_available_models',)},
 'vllm.model_executor.model_loader.base_loader': {'BaseModelLoader': ('load_model',)},
 'vllm.model_executor.model_loader.default_loader': {'DefaultModelLoader': ('load_weights',
                                                                            '_prepare_weights',
                                                                            '_get_weights_iterator')},
 'vllm.v1.worker.cpu_model_runner': {'CPUModelRunner': ('load_model',
                                                        'get_model')},
 'vllm.v1.engine.input_processor': {'InputProcessor': ('assign_request_id',)},
 'vllm.v1.engine.output_processor': {'OutputProcessor': ('add_request',
                                                         'abort_requests',
                                                         '_finish_request')},
 'vllm.v1.engine.async_llm': {'AsyncLLM': ('abort',)},
 'vllm.v1.engine.core': {'EngineCore': ('abort_requests',)},
 'vllm.v1.core.sched.scheduler': {'Scheduler': ('finish_requests',
                                                '_free_request_blocks',
                                                '_drain_deferred_frees')},
 'vllm.v1.core.kv_cache_manager': {'KVCacheManager': ('free',
                                                      'pop_blocks_for_free')},
 'vllm.v1.core.single_type_kv_cache_manager': {'SingleTypeKVCacheManager': ('free',
                                                                            'pop_blocks_for_free')},
 'vllm.v1.core.block_pool': {'BlockPool': ('free_blocks',)},
 'vllm.v1.worker.gpu_model_runner': {'GPUModelRunner': ('_update_states',
                                                        'execute_model',
                                                        'sample_tokens',
                                                        'load_model',
                                                        'get_model',
                                                        '_model_forward'),
                                     'AsyncGPUModelRunnerOutput': ('get_output',),
                                     'AsyncGPUPoolingModelRunnerOutput': ('get_output',)},
 'vllm.v1.worker.gpu_input_batch': {'InputBatch': ('remove_request',)}}
V7_FUNCTION_POINTS = {'vllm.model_executor.model_loader.weight_utils': ('safetensors_weights_iterator',
                                                   'pt_weights_iterator',
                                                   'multi_thread_pt_weights_iterator',
                                                   'multi_thread_safetensors_weights_iterator',
                                                   'fastsafetensors_weights_iterator',
                                                   'instanttensor_weights_iterator',
                                                   'np_cache_weights_iterator'),
 'vllm.entrypoints.openai.models.api_router': ('models',
                                               'show_available_models')}


def asset_supported(profile, raw):
    if profile == profiler_sensor.PROFILE:
        return raw == Path(profiler_sensor.__file__).read_bytes()
    if profile == init_sensor.PROFILE:
        return raw == Path(init_sensor.__file__).read_bytes()
    if profile == rank_sensor.PROFILE:
        return raw == Path(rank_sensor.__file__).read_bytes()
    if profile == stream_sensor.PROFILE:
        return raw == Path(stream_sensor.__file__).read_bytes()
    if profile == output_sensor.PROFILE:
        return raw == Path(output_sensor.__file__).read_bytes()
    if profile == attempt.PROFILE:
        return raw == Path(attempt.__file__).read_bytes()
    if profile == online.PROFILE:
        return raw == Path(online.__file__).read_bytes()
    if profile == LEGACY_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in LEGACY_ASSETS
    if profile == V2_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V2_ASSETS
    if profile == V3_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V3_ASSETS
    if profile == V4_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V4_ASSETS
    if profile == V5_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V5_ASSETS
    if profile == V6_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V6_ASSETS
    if profile == V7_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in V7_ASSETS
    if profile == sensor.PROFILE:
        return raw == Path(sensor.__file__).read_bytes()
    return False


def source_hashes(profile):
    if profile == profiler_sensor.PROFILE:
        return {**sensor.SOURCE_SHA,**profiler_sensor.SOURCE_ADDITIONS}
    if profile == init_sensor.PROFILE:
        return {**sensor.SOURCE_SHA,**init_sensor.SOURCE_ADDITIONS}
    if profile == rank_sensor.PROFILE:
        return {**sensor.SOURCE_SHA,**rank_sensor.SOURCE_ADDITIONS}
    if profile == stream_sensor.PROFILE:
        return {**sensor.SOURCE_SHA,**stream_sensor.SOURCE_ADDITIONS}
    if profile == output_sensor.PROFILE:
        return sensor.SOURCE_SHA
    if profile == attempt.PROFILE:
        return sensor.SOURCE_SHA
    if profile == online.PROFILE:
        return {**sensor.SOURCE_SHA, **online.SOURCE_ADDITIONS}
    if profile == V7_PROFILE:
        return V7_SOURCE_SHA
    if profile == sensor.PROFILE:
        return sensor.SOURCE_SHA
    if profile == V3_PROFILE:
        return V3_SOURCE_SHA
    if profile == V4_PROFILE:
        return V4_SOURCE_SHA
    if profile == V5_PROFILE:
        return V5_SOURCE_SHA
    if profile == V6_PROFILE:
        return V6_SOURCE_SHA
    if profile in {LEGACY_PROFILE, V2_PROFILE}:
        return LEGACY_SOURCE_SHA
    raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")


def points(profile):
    if profile == profiler_sensor.PROFILE:return sensor.POINTS
    if profile == init_sensor.PROFILE:return sensor.POINTS
    if profile == rank_sensor.PROFILE:
        return sensor.POINTS
    if profile == stream_sensor.PROFILE:
        return sensor.POINTS
    if profile == output_sensor.PROFILE:
        return output_sensor.points(sensor)
    if profile == attempt.PROFILE:
        return sensor.POINTS
    if profile == online.PROFILE:
        return online.points(sensor)
    if profile == V7_PROFILE:
        return V7_POINTS
    if profile == sensor.PROFILE:
        return sensor.POINTS
    if profile == V3_PROFILE:
        return V3_POINTS
    if profile == V4_PROFILE:
        return V4_POINTS
    if profile == V5_PROFILE:
        return V5_POINTS
    if profile == V6_PROFILE:
        return V6_POINTS
    if profile in {LEGACY_PROFILE, V2_PROFILE}:
        return LEGACY_POINTS
    raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")


def validate_model_selection(payload):
    model = payload["metadata"]["model_selection"]
    hashes = {"owner_model_sha256","argument_model_sha256","returned_model_sha256",
        "configured_model_sha256","configured_revision_sha256","load_format_sha256"}
    flags = {"load_dummy_weights","lora_config_present","speculative_config_present"}
    points = {"BaseModelLoader.load_model","DefaultModelLoader.load_weights","CPUModelRunner.load_model",
        "GPUModelRunner.load_model","CPUModelRunner.get_model","GPUModelRunner.get_model","GPUModelRunner._model_forward"}
    if (type(model) is not dict or set(model) != hashes | flags or payload["point"] not in points
            or any(model[k] is not None and (type(model[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",model[k]) is None) for k in hashes)
            or any(model[k] is not None and type(model[k]) is not bool for k in flags)):
        raise ValueError("LIFECYCLE_RECORDING_MODEL_METADATA_INVALID")
    if (model["returned_model_sha256"] is not None and (payload["phase"]!="RETURN" or payload["point"] not in
            {"BaseModelLoader.load_model","CPUModelRunner.get_model","GPUModelRunner.get_model"})):
        raise ValueError("LIFECYCLE_RECORDING_MODEL_RETURN_INVALID")


ROUTE_KEYS={"route_authentication","asgi_request_sha256","asgi_invocation_sha256","entry_path_sha256",
    "entry_root_path_sha256","entry_method_sha256","auth_call_sha256","observed_path_sha256",
    "observed_root_path_sha256","observed_method_sha256","auth_relative_path_sha256","token_verified",
    "returned_native_coroutine","route_lookup_returned","serving_method_returned"}


def validate_route_authentication(payload):
    value=payload["metadata"]["route_authentication"]
    flags={"token_verified","returned_native_coroutine","route_lookup_returned","serving_method_returned"}
    hashes=ROUTE_KEYS-flags-{"route_authentication"}
    if (type(value) is not dict or set(value)!=hashes|flags or payload["point"] not in sensor.ROUTE_POINTS
            or any(value[k] is not None and (type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",value[k]) is None) for k in hashes)
            or value["asgi_request_sha256"] is None or value["asgi_invocation_sha256"] is None
            or any(value[k] is not None and type(value[k]) is not bool for k in flags)):
        raise ValueError("LIFECYCLE_ROUTE_METADATA_INVALID")
    for key,point in {"token_verified":"AuthenticationMiddleware.verify_token",
            "returned_native_coroutine":"AuthenticationMiddleware.__call__","route_lookup_returned":"api_router.models",
            "serving_method_returned":"OpenAIServingModels.show_available_models"}.items():
        if value[key] is not None and (payload["point"]!=point or payload["phase"]!="RETURN"):
            raise ValueError("LIFECYCLE_ROUTE_RETURN_INVALID")
    if value["route_lookup_returned"] is False or value["serving_method_returned"] is False:
        raise ValueError("LIFECYCLE_ROUTE_COMPLETION_INVALID")
    if (payload["point"]=="OpenAIServingModels.show_available_models" and any(value[k] is not None for k in
            ("observed_path_sha256","observed_root_path_sha256","observed_method_sha256","auth_relative_path_sha256"))
            or payload["point"]=="api_router.models" and value["auth_relative_path_sha256"] is not None):
        raise ValueError("LIFECYCLE_ROUTE_SCOPE_INVALID")


def route_observation(row):
    if row["event"]!="NATIVE":return None
    payload=row["payload"];value=payload["metadata"].get("route_authentication")
    if value is None:return None
    return {"point":payload["point"],"phase":payload["phase"],"metadata":dict(value)}


HANDLER_KEYS={"route_handler","asgi_request_sha256","asgi_invocation_sha256","entry_path_sha256",
    "entry_root_path_sha256","entry_method_sha256","observed_path_sha256","observed_root_path_sha256",
    "observed_method_sha256","registered_route_observed","handler_returned"}


def validate_route_handler(payload):
    value=payload["metadata"]["route_handler"]
    hashes=HANDLER_KEYS-{"route_handler","registered_route_observed","handler_returned"}
    if (type(value) is not dict or set(value)!=hashes|{"registered_route_observed","handler_returned"}
            or payload["point"] not in sensor.HANDLER_POINTS
            or any(value[k] is not None and (type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",value[k]) is None) for k in hashes)
            or value["asgi_request_sha256"] is None or value["asgi_invocation_sha256"] is None
            or type(value["registered_route_observed"]) is not bool
            or value["handler_returned"] is not None and (value["handler_returned"] is not True or payload["phase"]!="RETURN")):
        raise ValueError("LIFECYCLE_ROUTE_HANDLER_INVALID")


def handler_observation(row):
    if row["event"]!="NATIVE":return None
    payload=row["payload"];value=payload["metadata"].get("route_handler")
    if value is None:return None
    return {"point":payload["point"],"phase":payload["phase"],"metadata":dict(value)}


REQUEST_SELECTION_KEYS={"request_selection","priority_observed","session_observed","rank_observed","request_id_observed",
    "selected_priority","selected_session_sha256","selected_rank","selected_request_id_sha256"}


def valid_selected_integer(key,value):
    return key in {"selected_priority","selected_rank"} and type(value) is int and -(2**63)<=value<2**63


def validate_request_selection(payload):
    value=payload["metadata"]["request_selection"]
    context={"asgi_request_sha256","asgi_invocation_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256"}
    keys=(REQUEST_SELECTION_KEYS-{"request_selection"})|context
    if (type(value) is not dict or set(value)!=keys
            or payload["point"] not in sensor.REQUEST_SELECTION_POINTS|{"InputProcessor.assign_request_id"}
            or type(value["asgi_invocation_sha256"]) is not str):
        raise ValueError("LIFECYCLE_REQUEST_SELECTION_INVALID")
    for key in context|{"selected_session_sha256","selected_request_id_sha256"}:
        if value[key] is not None and (type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}",value[key]) is None):
            raise ValueError("LIFECYCLE_REQUEST_SELECTION_HASH_INVALID")
    for name in ("priority","session","rank","request_id"):
        observed=value[name+"_observed"]
        selected=value["selected_"+name+("_sha256" if name in {"session","request_id"} else "")]
        if (type(observed) is not bool or not observed and selected is not None
                or name in {"priority","rank"} and selected is not None
                    and (type(selected) is not int or not -(2**63)<=selected<2**63)
                or name=="priority" and observed and selected is None):
            raise ValueError("LIFECYCLE_REQUEST_SELECTION_VALUE_INVALID")
    if payload["point"]!="InputProcessor.assign_request_id":
        name={"GenerateBaseServing._get_priority":"priority","GenerateBaseServing._get_data_parallel_rank":"rank",
            "GenerateBaseServing._get_session_id_from_headers":"session","GenerateBaseServing._get_session_id":"session",
            "BaseServing._base_request_id":"request_id"}[payload["point"]]
        if any(value[k+"_observed"] for k in ("priority","session","rank","request_id")
                if payload["phase"]!="RETURN" or k!=name):
            raise ValueError("LIFECYCLE_REQUEST_SELECTION_PHASE_INVALID")


def request_selection_observation(row):
    if row["event"] not in {"NATIVE","SELECTION"}:return None
    payload=row["payload"];value=payload["metadata"].get("request_selection")
    if value is None:return None
    return {"point":payload["point"],"phase":payload["phase"],"metadata":dict(value)}


def point_sources(profile):
    result={cls+"."+method:module for module,classes in points(profile).items()
        for cls,methods in classes.items() for method in methods}
    expected=sum(len(methods) for classes in points(profile).values() for methods in classes.values())
    if profile in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,V4_PROFILE,V5_PROFILE,V6_PROFILE,V7_PROFILE}:
        functions=online.functions(sensor) if profile==online.PROFILE else sensor.FUNCTION_POINTS if profile in {sensor.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE} else V7_FUNCTION_POINTS if profile==V7_PROFILE else V6_FUNCTION_POINTS if profile==V6_PROFILE else V5_FUNCTION_POINTS if profile==V5_PROFILE else V4_FUNCTION_POINTS
        result.update({module.rsplit(".",1)[-1]+"."+method:module
            for module,methods in functions.items() for method in methods})
        expected+=sum(len(methods) for methods in functions.values())
    if len(result)!=expected:raise ValueError("LIFECYCLE_POINT_AMBIGUOUS")
    return result


def valid_call_frame(frame,profile):
    if type(frame) is not dict:return False
    base={"point","request_sha256"}
    if set(frame)==base:return True
    extra={"weight_call_sha256","weight_model_sha256"}
    if profile not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,V4_PROFILE,V5_PROFILE,V6_PROFILE,V7_PROFILE} or set(frame)!=base|extra or frame.get("point")!="DefaultModelLoader.load_weights":return False
    return (type(frame["weight_call_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}",frame["weight_call_sha256"]) is not None
        and (frame["weight_model_sha256"] is None or type(frame["weight_model_sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}",frame["weight_model_sha256"]) is not None))


WEIGHT_KEYS={"weight_selection","weight_call_sha256","weight_model_sha256","cwd_sha256",
    "source_model_sha256","source_revision_sha256","normalized_path_sha256","path_count","paths_complete",
    "prepared_folder_sha256","prepared_use_safetensors","prepared_index_name_sha256","factory_returned_generator"}


def validate_weight_selection(payload):
    value=payload["metadata"]["weight_selection"]
    if type(value) is not dict or set(value)!=WEIGHT_KEYS-{"weight_selection"} or payload["point"] not in sensor.WEIGHT_POINTS:
        raise ValueError("LIFECYCLE_WEIGHT_METADATA_INVALID")
    hashes={"weight_call_sha256","weight_model_sha256","cwd_sha256","source_model_sha256",
        "source_revision_sha256","prepared_folder_sha256","prepared_index_name_sha256"}
    if any(value[k] is not None and (type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",value[k]) is None) for k in hashes):
        raise ValueError("LIFECYCLE_WEIGHT_HASH_INVALID")
    paths=value["normalized_path_sha256"];count=value["path_count"]
    if (type(paths) is not list or len(paths)>64 or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}",v) is None for v in paths)
            or count is not None and (type(count) is not int or not len(paths)<=count<2**63)
            or type(value["paths_complete"]) is not bool or value["paths_complete"] and count!=len(paths)
            or count is None and (paths or value["paths_complete"])):
        raise ValueError("LIFECYCLE_WEIGHT_PATHS_INVALID")
    for key in ("prepared_use_safetensors","factory_returned_generator"):
        if value[key] is not None and type(value[key]) is not bool:raise ValueError("LIFECYCLE_WEIGHT_FLAG_INVALID")
    if value["factory_returned_generator"] is not None and (payload["phase"]!="RETURN" or payload["point"]=="DefaultModelLoader._prepare_weights"):
        raise ValueError("LIFECYCLE_WEIGHT_RETURN_INVALID")


def returned_weight_selection(row):
    if row["event"]!="NATIVE":return None
    payload=row["payload"];value=payload["metadata"].get("weight_selection")
    factories={module.rsplit(".",1)[-1]+"."+method for module,methods in sensor.FUNCTION_POINTS.items() for method in methods}
    if payload["phase"]!="RETURN" or payload["point"] not in factories or value is None:return None
    parent=next((f for f in reversed(payload.get("call_chain",[])) if f["point"]=="DefaultModelLoader.load_weights"),{})
    linked=(value["weight_call_sha256"] is not None and value["weight_model_sha256"] is not None
        and value["weight_call_sha256"]==parent.get("weight_call_sha256")
        and value["weight_model_sha256"]==parent.get("weight_model_sha256"))
    return {"point":payload["point"],"weight_call_sha256":value["weight_call_sha256"],
        "weight_model_sha256":value["weight_model_sha256"],"paths":value["normalized_path_sha256"],
        "paths_complete":value["paths_complete"],"path_count":value["path_count"],
        "load_context_linked":linked,"factory_returned_generator":value["factory_returned_generator"]}
