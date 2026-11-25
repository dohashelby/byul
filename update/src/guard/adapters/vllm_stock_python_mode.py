"""Installed source identities for the ordinary stock Python mode observer."""

import re


LOADER = "vllm.model_executor.model_loader.default_loader"
MODEL = "vllm.model_executor.models.qwen2_vl"
SERVICE = "vllm.entrypoints.launchers.api_server.entry"
INSTALLED_ROOT = "/usr/local/lib/python3.12/dist-packages"
STOCK_MODEL = ("Qwen/Qwen2-VL-2B-Instruct", "895c3a49bc3fa70a340399125c650a463535e71c",
               "ad73950ffd6022ad452aef105d66caf2c623a3eeb7dc9eafe983ee416168d432")
FILE_SIZES = {"chat_template.json": 1050, "config.json": 1196, "generation_config.json": 272,
              "merges.txt": 1671839, "model-00001-of-00002.safetensors": 3988609112,
              "model-00002-of-00002.safetensors": 429441656, "model.safetensors.index.json": 56411,
              "preprocessor_config.json": 347, "tokenizer.json": 7029741, "tokenizer_config.json": 4190,
              "vocab.json": 2776833}
CONFIG_SHA256 = {
    "config.json": "422adefa19e62dd175961cec85bc0400344fe5bf9b22bd1182e05aaae78556e0",
    "preprocessor_config.json": "b5eaad0c2815f07631535dcc58f3c462b0d73693638ad21d19f3c50820eae1cc",
    "tokenizer_config.json": "ff5c4fd898fe8c39591eb70e5d39d2782802d4204d6ae9ba1223252f354842a0",
}
_RELEASES = {
    "0.30.0": (
        "5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
        "db689f8486e5d2c9eb59d347e03a27ce837d014f8d3d7f84d55a0e382813ca84",
        "9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8",
        "24233e656920e3ae05c45d04993ddcaf4c87bf82d60b82b35305498b63356057",
        ("vllm", "serve"),
    ),
    "0.31.0": (
        "a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b",
        "4f60dee4693159b79016c6a84f016eb0ade024433807ff393ea782a31bb9f7e4",
        "d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9",
        "5021855de72a2b1b04befcb2bb766eab17afd0e7892dc6d4135c8eeef8c99978",
        ("/usr/local/bin/vllm-nonroot-entrypoint.sh",),
    ),
}
_SHA = re.compile("[a-f0-9]{64}")
GENERAL_PROFILE_SCHEMA = "guard-sup11-vllm-general-observer/1"
GENERAL_MODULE_FILE = "guard_sup11_lifecycle_observer.py"
GENERAL_DIST_INFO = "guard_sup11_lifecycle_observer-1.0.dist-info"
GENERAL_PLUGIN_NAMES = ["guard_sup11_lifecycle_observer", "lora_filesystem_resolver", "lora_hf_hub_resolver"]
GENERAL_METADATA = {
    "METADATA": b"Metadata-Version: 2.1\nName: guard-sup11-lifecycle-observer\nVersion: 1.0\n",
    "entry_points.txt": b"[vllm.general_plugins]\n"
                        b"guard_sup11_lifecycle_observer = guard_sup11_lifecycle_observer:activate\n",
}

# Only reviewed complete installed-OCI qualifications belong here. The public
# source tree's release tags are supporting material, not installation pins.
_GENERAL_QUALIFICATIONS = {}
_GENERAL_QUALIFICATION_ERROR = "SUP11_GENERAL_OBSERVER_OCI_QUALIFICATION_UNAVAILABLE"
_GENERAL_QUALIFICATION_KEYS = {
    "oci_source_qualification_sha256", "plugin_discovery_sha256", "site_roster_sha256",
    "plugin_loader_sha256", "service_activation_sha256", "worker_activation_sha256",
    "sitecustomize_origin", "sitecustomize_target", "sitecustomize_sha256",
}


def is_sha256(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _release(env):
    release = _RELEASES.get(env.engine.version)
    if env.engine.name != "vllm" or release is None:
        raise ValueError("SUP11_INSTALLED_SOURCE_PROFILE_INVALID")
    digest = release[0]
    image = f"vllm/vllm-openai@sha256:{digest}"
    if env.engine.image_digest not in {image, "docker.io/" + image}:
        raise ValueError("SUP11_INSTALLED_SOURCE_PROFILE_INVALID")
    return release


def observer_profile(env):
    _, service, loader, model, _ = _release(env)
    return {"schema": "guard-sup11-stock-observer/1", "engine_image": env.engine.image_digest,
            "service": {"module": SERVICE, "sha256": service}, "worker_loader_sha256": loader,
            "model_source_sha256": model, "installed_root": INSTALLED_ROOT}


def validate_observer_profile(profile, env):
    expected = (general_observer_profile(env)
                if type(profile) is dict and profile.get("schema") == GENERAL_PROFILE_SCHEMA
                else observer_profile(env))
    if profile != expected:
        raise ValueError("SUP11_INSTALLED_SOURCE_PROFILE_INVALID")


def general_observer_profile(env):
    profile = observer_profile(env)
    pins = _GENERAL_QUALIFICATIONS.get(env.engine.version)
    if (type(pins) is not dict or set(pins) != _GENERAL_QUALIFICATION_KEYS
            or any(not is_sha256(pins[key]) for key in pins if key.endswith("_sha256"))
            or pins["sitecustomize_origin"] != "/usr/lib/python3.12/sitecustomize.py"
            or pins["sitecustomize_target"] != "/etc/python3.12/sitecustomize.py"):
        raise ValueError(_GENERAL_QUALIFICATION_ERROR)
    return {**profile, "schema": GENERAL_PROFILE_SCHEMA, "qualification": dict(pins)}


def launch_command(env):
    return list(_release(env)[4])
