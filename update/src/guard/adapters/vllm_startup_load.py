"""Source pins for the stock CUDA V1 model load log in the owned paired operation."""

from .vllm import REQUEST_BINDING_GPU_IMAGE


# The tagged final runtime stages install the vLLM wheel into system Python.
# The collector verifies these files inside the selected container at runtime.
_SOURCE_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
_SOURCE_PROFILES = {
    "0.30.0": {
        "image": "docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
        "commit": "ced6857afa0ea7b2e3f0846a62e1394e90f15607",
        "line": 5416,
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
        "files": {
            "v1/worker/gpu_model_runner.py": "bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a",
            "utils/mem_utils.py": "8b21755c8cae378b64d2d5c72c901135820072764218808191f266a65cf3aad5",
            "platforms/cuda.py": "27bd20e6fe42dd70b72fe58ce12cb615a2385776f0ac705349646c67ca68bb26",
            "logger.py": "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010",
            "logging_utils/formatter.py": "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5",
        },
    },
}


def source_profile(env):
    """Select the reviewed image and installed file identities for this startup reader."""
    engine = env.engine
    profile = _SOURCE_PROFILES.get(engine.version)
    if (engine.name != "vllm" or profile is None or engine.image_digest != profile["image"]
            or engine.source_commit not in {None, profile["commit"]}):
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
