import sys
from importlib.machinery import ModuleSpec
from types import ModuleType, SimpleNamespace

from owned_startup_trace import MODEL_CLASS, MODEL_MODULE, digest, model_return


def test_model_origin_requires_the_actual_return_and_exact_installed_source(tmp_path, monkeypatch):
    file = tmp_path.resolve() / "qwen2_vl.py"
    raw = b"class Qwen2VLForConditionalGeneration:\n    def __init__(self):\n        pass\n"
    file.write_bytes(raw)
    module = ModuleType(MODEL_MODULE)
    module.__file__ = str(file)
    module.__spec__ = ModuleSpec(MODEL_MODULE, loader=None, origin=str(file))
    exec(compile(raw, str(file), "exec", dont_inherit=True), vars(module))
    monkeypatch.setitem(sys.modules, MODEL_MODULE, module)
    cls = vars(module)[MODEL_CLASS]
    config_cls = type("ModelConfig", (), {"__module__": "vllm.config.model"})
    config = config_cls()
    config.model = "/models"
    config.trust_remote_code = False
    frame = SimpleNamespace(f_locals={"model_config": config})
    origin = {"model": "/models", "file": str(file), "sha256": digest(raw)}
    observed = {"module": MODEL_MODULE, "qualname": MODEL_CLASS, "source_file": str(file),
                "source_sha256": digest(raw), "model": "/models", "trust_remote_code": False}

    assert model_return("model-loader-return", frame, cls(), origin) == observed
    assert model_return("model-registry-return", frame, (cls, MODEL_CLASS), origin) == observed
    assert model_return("model-loader-return", frame, cls, origin) is None
    assert model_return("model-loader-return", frame, object(), origin) is None
    assert model_return("model-registry-return", frame, (cls, "different"), origin) is None
    monkeypatch.setattr(config, "trust_remote_code", True)
    assert model_return("model-loader-return", frame, cls(), origin) is None
    monkeypatch.setattr(config, "trust_remote_code", False)
    monkeypatch.setattr(config, "model", "/different")
    assert model_return("model-loader-return", frame, cls(), origin) is None
    monkeypatch.setattr(config, "model", "/models")
    file.write_bytes(raw + b"\n")
    assert model_return("model-loader-return", frame, cls(), origin) is None
