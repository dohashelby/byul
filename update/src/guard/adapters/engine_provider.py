"""Engine provider boundary for launch and engine-specific evidence contracts."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re

from .base import LaunchSpec
from .model_inventory import inventory
from .vllm import VllmAdapter, BOUNDED_LAUNCH_KEYS, BOUNDED_LAUNCH_PROFILE, GpuLaunchOptions, CONTAINER_PORT
from .openai_engine import SGLangAdapter, TensorRTAdapter
from guard.runtime import generation_readiness

ENGINE_PORT = CONTAINER_PORT
CACHE_ENV = {'VLLM_CACHE_ROOT': '/guard-cache/vllm', 'TORCHINDUCTOR_CACHE_DIR': '/guard-cache/torchinductor'}


class EngineProvider:
    def __init__(self, name, adapter_factory):
        self.name, self.adapter_factory = name, adapter_factory
        self.executor_factories = {}
        self.source_analysis = None
        self.artifact_roles = frozenset()

    def validate_environment(self, env, executor, implementation):
        if implementation == 'source-cpu' or executor == 'engine_sensor':
            if any(c.role not in self.artifact_roles for c in env.model.components or ()):
                raise ValueError('ENGINE_ARTIFACT_CONFIGURATION_UNSUPPORTED')

    def bind_executor(self, executor, implementation, factory, *, profile=None):
        if implementation == 'kubernetes' and profile is None:
            raise ValueError('NATIVE_EXECUTOR_PROFILE_BINDING_REQUIRED')
        key = (executor, implementation, profile)
        if key in self.executor_factories or not callable(factory):
            raise ValueError('ENGINE_EXECUTOR_DUPLICATE_OR_INVALID')
        self.executor_factories[key] = factory

    def executor(self, executor, implementation, version=None):
        from .engine_profiles import resolve
        selected = resolve(self.name, version, 'native_lifecycle' if executor == 'engine_sensor' else executor)
        matches = [f for (e, i, p), f in self.executor_factories.items()
            if (e, i) == (executor, implementation) and (p is None or p == selected.profile)]
        if len(matches) > 1: raise ValueError('ENGINE_EXECUTOR_AMBIGUOUS')
        return matches[0] if matches else None

    def allows_legacy_native(self, version, *, executor=None):
        return False

    def sandbox_arguments(self, options):
        return {'engine_options': options}

    def resource_lease_keys(self, options):
        if not options.get('gpu_devices'): return []
        import os
        return [{'kind': 'gpu-daemon', 'daemon': os.environ.get('DOCKER_HOST'),
            'context': os.environ.get('DOCKER_CONTEXT')}]

    def make_resource_lease(self, root, identity):
        from guard.runtime.resource_lease import ResourceLease
        return ResourceLease(root, identity)

    def adapter(self):
        return self.adapter_factory()

    def execution_recipe_files(self, factory):
        """Extension packages override this with their reviewed code closure."""
        import inspect
        files = set()
        for item in (type(self), self.adapter_factory, factory):
            try: path = inspect.getsourcefile(item)
            except (TypeError, OSError): path = None
            if path is not None: files.add(Path(path))
        return files

    @property
    def managed_available(self):
        return False

    def prepare_managed(self, env, cfg):
        raise ValueError('ENGINE_MANAGED_LAUNCH_UNSUPPORTED:' + self.name)

    def launch_plan(self, options):
        raise ValueError('ENGINE_MANAGED_LAUNCH_UNSUPPORTED:' + self.name)

    def runtime_profile(self, options, profile_name, profile):
        return deepcopy(profile)

    def launch_conditions(self, options, profile_name, credential_canary=False):
        return {k: v for k, v in options.items() if k in {'model', 'revision', 'gpu', 'cpu_kv_cache_gib'}}

    def generation_profile(self, options):
        return None

    def version_request(self, base_url):
        return None

    def template_profile(self):
        return {}

    def template_rejection_supported(self, image, version):
        return False

    def bounded_launch_evidence(self, options, env):
        return False

    def expected_image_entrypoint(self, options):
        return None

    def resource_limits(self, options):
        return self.launch_plan(options).limits if self.managed_available else None

    def evidence_contract(self, name, *args):
        method = getattr(self.adapter(), name, None)
        return method(*args) if callable(method) else None


class VllmProvider(EngineProvider):
    def __init__(self):
        super().__init__('vllm', VllmAdapter)

    def release_profile(self, version):
        from .vllm_release_profiles import release_profile
        return release_profile(version)

    def sandbox_arguments(self, options):
        # Published callers/fixtures retain their legacy keyword contract.
        return {'vllm': options}

    def allows_legacy_native(self, version, *, executor=None):
        from .engine_profiles import resolve
        if executor == 'input_validation' and resolve(self.name, version, 'native_input').profile == 'vllm-input-validation-http/1':
            return True
        return resolve(self.name, version, 'native_lifecycle').profile == 'vllm-native-source/1'

    def resource_lease_keys(self, options):
        if not options.get('gpu_devices'): return []
        import os
        # Conservatively serialize GPU allocations per daemon. Numeric indices
        # and UUID aliases cannot bypass a per-device lock with different names.
        return [{'kind': 'gpu-daemon', 'daemon': os.environ.get('DOCKER_HOST'),
            'context': os.environ.get('DOCKER_CONTEXT')}]

    @property
    def managed_available(self):
        return True

    def template_profile(self):
        from .vllm import CHAT_TEMPLATE_SHA256, CHAT_TEMPLATE_CONTAINER_PATH
        return {'sha256': CHAT_TEMPLATE_SHA256, 'container_path': CHAT_TEMPLATE_CONTAINER_PATH}

    def template_rejection_supported(self, image, version):
        from .vllm import CHAT_REVIEWED_IMAGE
        return image == CHAT_REVIEWED_IMAGE and version == '0.31.0'

    def bounded_launch_evidence(self, options, env):
        devices = options.get('gpu_devices')
        return (options.get('launch_profile') == BOUNDED_LAUNCH_PROFILE and type(env.runtime.gpu_count) is int
                and env.runtime.gpu_count >= 1 and type(devices) is list and len(devices) == env.runtime.gpu_count)

    def expected_image_entrypoint(self, options):
        # GPU launches keep the image's own entrypoint.
        return None

    def generation_profile(self, options):
        return generation_readiness.profile(options)

    def version_request(self, base_url):
        return {'id': 'version', 'method': 'GET', 'url': base_url + '/version', 'want_body': True}

    def prepare_managed(self, env, cfg):
        base = deepcopy(cfg.get("vllm", {}))
        if env.engine.name != "vllm" or not env.engine.version:
            raise ValueError("managed update needs an explicit vllm engine version")
        image = env.engine.image_digest
        if image and re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            locator = base.get('image')
            if not isinstance(locator, str) or not locator.endswith('@' + image):
                raise ValueError('ENGINE_IMAGE_LOCATOR_REQUIRED_OR_MISMATCHED')
            image = locator
        if not image or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
            raise ValueError("managed update needs a repository@sha256 pinned image in each ENV")
        if not env.model.id or not env.model.revision or not env.model.inventory_sha256:
            raise ValueError("managed update needs pinned model id, revision and complete inventory")
        if env.model.components or env.model.tokenizer_revision not in (None, env.model.revision):
            raise ValueError('ENGINE_ARTIFACT_CONFIGURATION_UNSUPPORTED')
        if env.runtime.gpu_count is None:
            raise ValueError("managed update needs an explicit gpu_count")
        if env.runtime.gpu_count < 1:
            # 2026-11-12: CPU images are outside the product scope.
            raise ValueError("VLLM_GPU_REQUIRED: managed vLLM needs gpu_count>=1; the CPU image path was removed")
        devices = base.get('gpu_devices')
        if (type(devices) is not list or len(devices) != env.runtime.gpu_count or len(set(devices)) != len(devices)
                or any(type(v) is not str or re.fullmatch(r'(?:[0-9]+|GPU-[A-Za-z0-9-]+|MIG-[A-Za-z0-9/-]+)', v) is None for v in devices)):
            raise ValueError('GPU_ASSIGNMENT_REQUIRED_OR_COUNT_MISMATCHED')
        from guard.runtime.docker import ContainerLimits
        limits = ContainerLimits.from_mapping(base.get('resources', {}))
        if any(getattr(limits, k) is None for k in ('cpus', 'memory_bytes', 'pids_limit')):
            raise ValueError('GPU_CONTAINER_RESOURCE_LIMITS_REQUIRED')
        model_dir = Path(base.get("models_dir", "")).expanduser()
        layout = base.get("model_layout", "hf-cache")
        if type(layout) is not str or layout not in {"hf-cache", "snapshot"}:
            raise ValueError("model_layout must be hf-cache or snapshot")
        snapshot = model_dir if layout == "snapshot" else model_dir / "hub" / ("models--" + env.model.id.replace("/", "--")) / "snapshots" / env.model.revision
        if not snapshot.is_dir():
            raise ValueError("model snapshot is missing; prepare the offline HF cache first")
        actual = inventory(snapshot)
        if not actual["file_count"] or actual["inventory_sha256"] != env.model.inventory_sha256:
            raise ValueError("model inventory differs from ENV")
        base.update(image=image, model=env.model.id, revision=env.model.revision,
                    expected_version=env.engine.version, inventory_sha256=env.model.inventory_sha256,
                    gpu=env.runtime.gpu_count > 0)
        if env.runtime.cpu is not None:
            base["cpu"] = env.runtime.cpu.model_dump(mode="json")
        if env.runtime.platform:
            base["platform"] = env.runtime.platform
        generation_readiness.profile(base)  # Explicit startup generation is opt-in and bounded-GPU-only.
        launch = self.launch_plan(base)  # Pure option validation before any engine resources are created.
        if base.get("launch_profile") == BOUNDED_LAUNCH_PROFILE:
            if env.runtime.cpu is not None:
                raise ValueError("GPU_ENV_CPU_PROFILE_UNSUPPORTED: a GPU ENV must not declare a CPU comparison profile")
            allowed = {"models_dir", "model_layout", "launch_profile", "ready_timeout", "image", "model", "revision",
                       "expected_version", "inventory_sha256", "gpu", "gpu_devices", "gpu_model", "driver",
                       "platform", "expected_source_commit", "source_commit_observed", "source_commit_status",
                       "generation_readiness_timeout",
                       "files_read_by_engine_sha256", "files_read_by_engine_count", *BOUNDED_LAUNCH_KEYS}
            if set(base) - allowed:
                raise ValueError(f"unknown bounded GPU settings: {sorted(set(base) - allowed)}")
            bounded = GpuLaunchOptions.from_options(base)
            runtime = env.runtime
            if bounded.tensor_parallel_size != (runtime.tp_size or 1) or (runtime.pp_size or 1) != 1 or (runtime.dp_size or 1) != 1:
                raise ValueError("GPU_PARALLEL_LAYOUT_MISMATCH: gpu-bounded needs tensor_parallel_size equal to ENV tp_size and pp/dp of 1")
            if bounded.tensor_parallel_size != runtime.gpu_count:
                raise ValueError("GPU_PARALLEL_LAYOUT_MISMATCH: tensor_parallel_size must use every assigned GPU")
            if launch.limits.shm_bytes is None:
                raise ValueError("GPU_CONTAINER_RESOURCE_LIMITS_REQUIRED: gpu-bounded needs resources.shm_bytes")
            ready_timeout = base.get("ready_timeout", 600)
            if type(ready_timeout) not in (int, float) or not 0 < ready_timeout <= 3600:
                raise ValueError("bounded GPU ready_timeout must be positive and at most 3600 seconds")
            # Retain the host path only as launch input; its contents are identified
            # by inventory, while argv uses a stable container path.
            base["models_dir"] = str(model_dir.resolve())
        elif base.get("launch_profile", "legacy") != "legacy" or layout != "hf-cache":
            raise ValueError("snapshot models require launch_profile=gpu-bounded")
        return base

    def launch_plan(self, vllm: dict):
        if vllm.get('gpu') and not vllm.get('gpu_devices'):
            raise ValueError('GPU_ASSIGNMENT_REQUIRED')
        spec = LaunchSpec(
            model=vllm["model"], port=ENGINE_PORT, image=vllm["image"],
            options={"revision": vllm["revision"], "models_dir": Path(vllm["models_dir"]).expanduser(),
                     "device": "gpu" if vllm.get("gpu") else "cpu",
                     **{key: vllm[key] for key in ("launch_profile", "model_layout", *BOUNDED_LAUNCH_KEYS) if key in vllm and key != 'resources'}},
        )
        # Resource and device allocation is a launch concern, not model CLI.
        from dataclasses import replace
        plan = VllmAdapter().launch_plan(spec)
        if vllm.get('gpu_devices') is not None:
            from guard.runtime.docker import ContainerLimits
            plan = replace(plan, gpu_devices=tuple(vllm['gpu_devices']),
                limits=ContainerLimits.from_mapping(vllm['resources']))
        return plan

    def runtime_profile(self, options, profile_name, profile):
        profile = deepcopy(profile)
        if profile_name == 'hardened':
            profile['env'] = {**profile.get('env', {}), **CACHE_ENV}
        if options.get('launch_profile') == BOUNDED_LAUNCH_PROFILE and profile_name == 'hardened':
            profile['env'].update(TRITON_CACHE_DIR='/guard-cache/triton', TMPDIR='/guard-cache')
        return profile

    def launch_conditions(self, options, profile_name, credential_canary=False):
        conditions = super().launch_conditions(options, profile_name, credential_canary)
        if options.get('gpu_devices') is not None:
            conditions.update(gpu_count=len(options['gpu_devices']), resources=self.launch_plan(options).limits.to_dict())
        if options.get('launch_profile') == BOUNDED_LAUNCH_PROFILE:
            from guard.runtime.sandbox import ENGINE_PROFILES
            plan = self.launch_plan(options)
            profile = self.runtime_profile(options, profile_name, ENGINE_PROFILES[profile_name])
            conditions.update(profile='gpu-bounded-v1', model_layout=options.get('model_layout', 'hf-cache'),
                **GpuLaunchOptions.from_options(options).to_dict(),
                resources=plan.limits.to_dict(), entrypoint=plan.entrypoint,
                argv=plan.argv, environment={**plan.env, **profile.get('env', {}),
                    **({'GUARD_TEST_CREDENTIAL': 'EXAMPLE_NOT_A_SECRET'} if credential_canary else {})},
                engine_permissions={k: profile[k] for k in
                    ('cap_add', 'cap_drop', 'read_only', 'tmpfs', 'user', 'security_opt') if k in profile})
            if plan.chat_template is not None:
                conditions['chat_template'] = plan.chat_template
            if plan.log_profile is not None:
                conditions['log_profile'] = plan.log_profile
        return conditions


PROVIDERS = {
    'vllm': VllmProvider(),
    'sglang': EngineProvider('sglang', SGLangAdapter),
    'tensorrt-llm': EngineProvider('tensorrt-llm', TensorRTAdapter),
}
_PLUGINS_LOADED = False


def load_installed_providers():
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED: return
    _PLUGINS_LOADED = True
    from importlib.metadata import entry_points
    for entry in entry_points(group='guard.engine_providers'):
        contribution = entry.load()()
        if contribution is not None:
            register_provider(contribution, replace_unmanaged=True)


def get_provider(name):
    load_installed_providers()
    if name not in PROVIDERS:
        raise ValueError('ENGINE_PROVIDER_UNAVAILABLE:' + str(name))
    return PROVIDERS[name]


def register_provider(provider, *, replace_unmanaged=False):
    existing = PROVIDERS.get(provider.name) if isinstance(provider, EngineProvider) else None
    replaceable = replace_unmanaged and type(existing) is EngineProvider and not existing.executor_factories and not existing.managed_available
    if not isinstance(provider, EngineProvider) or existing is not None and not replaceable:
        raise ValueError('ENGINE_PROVIDER_DUPLICATE_OR_INVALID')
    PROVIDERS[provider.name] = provider


def evidence_contract(adapter, name, *args):
    if adapter is None:
        return None
    method = getattr(adapter, name, None)
    return method(*args) if callable(method) else None
