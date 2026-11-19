"""Source pins for stock CUDA V1 load, reader and compile cache observations."""

from .vllm import REQUEST_BINDING_GPU_IMAGE


# The tagged final runtime stages install the vLLM wheel into system Python.
# The collector verifies these files inside the selected container at runtime.
_SOURCE_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
_SOURCE_PROFILES = {
    "0.30.0": {
        "image": "docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
        "commit": "ced6857afa0ea7b2e3f0846a62e1394e90f15607",
        "line": 5416,
        "compile_backend": ("fa4bff8920b363efbbde9456f135fa9d2c004a5bde1898877f177418b2d8b008", 1094),
        "safetensors_reader": {
            "file_sha256": {
                "model_executor/model_loader/weight_utils.py":
                    "7443e55184777dcc99f621b0d97d9149d52c2ea8b4304c6621eb4902e1c8ba5b",
                "model_executor/model_loader/default_loader.py":
                    "9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8",
            },
            "filesystem_call_linenumber": 895,
            "default_strategy_call_linenumber": 918,
            "loader_completion_call_linenumber": 430,
        },
        "files": {
            "v1/worker/gpu_model_runner.py": "87c29d08c0bbf66993d8b984811e7325e35e6436242a773feb55ec88f16b2c51",
            "utils/mem_utils.py": "d2b5c90f643d9a4ae17cb68fac19032161bc83329db3c8b30db589620f25b5a7",
            "platforms/cuda.py": "f0464617920723fd7a088b05aee84aa6b97ff8bf3ff4301fc474de67afb0ca31",
            "logger.py": "17a9293737985a6e5f68c5d7dd848b4c9ac666e55e9c867ae6a4db84a2c7645c",
            "logging_utils/formatter.py": "d8caa69bf25884f6d4f59ca1b3c27e1162f7ab775874b236afc32e0696d5fe7c",
        },
    },
    "0.31.0": {
        "image": "docker.io/" + REQUEST_BINDING_GPU_IMAGE,
        "commit": "db9527a46873454610df6dbedf79a36d6bf1a7f6",
        "line": 5354,
        "compile_backend": ("16af622b799f22f30eaa1c78f7ccca9d54c7c72785ad89e90c87e859bf8ef876", 1090),
        "safetensors_reader": {
            "file_sha256": {
                "model_executor/model_loader/weight_utils.py":
                    "7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd",
                "model_executor/model_loader/default_loader.py":
                    "d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9",
            },
            "filesystem_call_linenumber": 1063,
            "default_strategy_call_linenumber": 1086,
            "loader_completion_call_linenumber": 484,
        },
        "files": {
            "v1/worker/gpu_model_runner.py": "bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a",
            "utils/mem_utils.py": "8b21755c8cae378b64d2d5c72c901135820072764218808191f266a65cf3aad5",
            "platforms/cuda.py": "27bd20e6fe42dd70b72fe58ce12cb615a2385776f0ac705349646c67ca68bb26",
            "logger.py": "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010",
            "logging_utils/formatter.py": "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5",
        },
    },
}


def _reviewed_profile(env):
    engine = env.engine
    profile = _SOURCE_PROFILES.get(engine.version)
    if (engine.name != "vllm" or profile is None or engine.image_digest != profile["image"]
            or engine.source_commit not in {None, profile["commit"]}):
        return None
    return profile


def source_profile(env):
    """Select the reviewed image and installed file identities for this startup reader."""
    profile = _reviewed_profile(env)
    if profile is None:
        raise ValueError("LIF01_BOOT_SOURCE_UNQUALIFIED")
    return {
        "source_root": _SOURCE_ROOT,
        "file_sha256": dict(profile["files"]),
        "logger_call_linenumber": profile["line"],
        "entrypoint": ["/usr/local/bin/vllm-nonroot-entrypoint.sh"],
        "required_env": {"VLLM_USE_V2_MODEL_RUNNER": "0"},
        "optional_env": {
            "VLLM_CONFIGURE_LOGGING": "1",
            "VLLM_LOGGING_CONFIG_PATH": "",
            "VLLM_LOGGING_LEVEL": "INFO",
            "VLLM_LOGGING_PREFIX": "",
            "VLLM_LOGGING_COLOR": "0",
            "VLLM_LOGGING_STREAM": "ext://sys.stdout",
            "VLLM_PLUGINS": "",
        },
        "forbidden_args": [
            "--config", "--log-config-file", "--logging-config", "--load-format", "--trust-remote-code",
            "--worker-cls", "--worker-extension-cls", "--speculative-config",
        ],
    }


def compile_cache_profile(env):
    """Select the compile log call sites and known cache layout for the same GPU image."""
    profile = _reviewed_profile(env)
    if profile is None:
        raise ValueError("SUP07_COMPILE_CACHE_SOURCE_UNQUALIFIED")
    backend_sha256, directory_line = profile["compile_backend"]
    return {
        "file_sha256": {
            "compilation/backends.py": backend_sha256,
            "compilation/monitor.py": "43b24c5cb1bc4d32dc979f3222596905c7b3acc58415328a234402e6edc76abf",
        },
        "directory_call_linenumber": directory_line,
        "completion_call_linenumber": 53,
        "cache_env": "VLLM_CACHE_ROOT",
        "cache_root": "/cache/vllm",
        "relative_directory_pattern": "torch_compile_cache/[0-9a-f]{10}/rank_0_0/backbone",
        "artifact_name": "vllm_compile_cache.py",
    }


def safetensors_reader_profile(env):
    """Select the stock loader call sites and installed source pins for this image."""
    profile = _reviewed_profile(env)
    if profile is None:
        raise ValueError("SUP12_SAFETENSORS_READER_SOURCE_UNQUALIFIED")
    reader = profile["safetensors_reader"]
    return {**reader, "file_sha256": dict(reader["file_sha256"])}
