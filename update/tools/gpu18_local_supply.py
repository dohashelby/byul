"""Actual bounded local components with synthetic inputs; never an engine/GPU test.

No installation, Docker, HTTP, AWS, model, dynamic plugin or test-double import.
Example: python -I -B gpu18_local_supply.py --source-root REPO --output NEW_DIR
         --checks SUP-03 SUP-06 SUP-11 LIF-11
Linux can additionally select PLT-02 for an owned, zero-payload Unix socket.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import traceback


CHECK_IDS = ("SUP-03", "SUP-06", "SUP-09", "SUP-10", "SUP-11", "PLT-02", "LIF-06", "LIF-11")
REMAINING = {
    "SUP-03": ["Actual pinned vLLM/HF tokenizer/template loader and worker consumption/identity.",
               "Complete approved auxiliary/secondary supply inventory and policy; not this synthetic allowlist."],
    "SUP-06": ["Actual checkpoint, required tensor/rank assignments, native load/worker/shard evidence.",
               "Actual GPU publisher/payload generation and native final tensor consumption if selected."],
    "SUP-09": ["Existing supported vLLM/transformers CPU installation for benign built-in config control.",
               "Actual loader trust/source/consumption binding; no custom-code execution performed."],
    "SUP-10": ["Existing supported vLLM CPU installation and reviewed allowlisted benign plugin distribution.",
               "Real selected entry-point/hook/consumer and lifecycle evidence, not metadata discovery alone."],
    "SUP-11": ["Actual vLLM/transformers built-in loader in the supported Python modes.",
               "Native worker code selection/consumption; interpreter flags alone do not prove it."],
    "PLT-02": ["Actual engine IPC inventory/namespace/owner and approved control request.",
               "Separately authorized nonapproved-subject/channel tests and selected TP scope."],
    "LIF-06": ["Supported actual torch/vLLM layerwise/storage/fallback API and pinned local model.",
               "Live requests and GPU worker weights/cache/replica generation consumption if selected."],
    "LIF-11": ["Real failed worker ownership and process/IPC/SHM/GPU inventory before/after recovery.",
               "Repaired launch and successful engine request; collector child cleanup is not worker recovery."],
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       default=lambda x: dict(x) if isinstance(x, Mapping) else _unsupported_json(x)) + "\n").encode()


def _unsupported_json(value):
    raise TypeError("Unsupported actual output type: " + type(value).__module__ + "." + type(value).__name__)


def source(path: Path, version: str) -> dict:
    path = path.resolve(strict=True)
    return {"path": str(path), "sha256": digest(path.read_bytes()), "version": version}


def runtime() -> dict:
    versions = {}
    for name in ("vllm", "torch", "transformers", "tokenizers", "safetensors", "jinja2", "byul-b-cpu-plugin-probe"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": sys.version, "executable": source(Path(sys.executable), platform.python_version()),
            "invoked_executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
            "argv": sys.argv, "profile": "actual_local_installed_cpu_profile; not pinned v0.31" if versions["vllm"] else "dependency_only_local_profile",
            "pid": os.getpid(), "platform": sys.platform,
            "uid": os.geteuid() if hasattr(os, "geteuid") else None,
            "flags": {key: getattr(sys.flags, key) for key in ("isolated", "optimize", "dont_write_bytecode", "no_site")},
            "packages": versions}


def load_module(path: Path, name: str):
    """Load the entire unchanged stdlib-only production module; no AST/stubs."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exception(error: Exception) -> dict:
    return {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}


def rejected(call) -> dict:
    try:
        result = call()
    except ValueError as error:
        return {"raised": True, "exception": exception(error)}
    return {"raised": False, "returned": result}


def assertion(name: str, passed: bool) -> dict:
    return {"name": name, "passed": bool(passed)}


def record(cid: str, component: str, api, target, input_data, observations, assertions, limitations) -> dict:
    return {"check_id": cid, "component": component, "execution_kind": "actual_local_component",
            "input_origin": "synthetic", "official": False, "formal_data_origin": "EXAMPLE",
            "status": "completed" if all(x["passed"] for x in assertions) else "assertion_failed",
            "command_or_api": api, "target_source": target, "input_sha256": digest(encode(input_data)),
            "observations": observations, "assertions": assertions, "scope_limitations": limitations,
            "remaining_real_integration": REMAINING[cid],
            "requires_gpu_for_remaining": cid in ("SUP-06", "LIF-06", "LIF-11")}


def template_policy(root: Path, out: Path) -> dict:
    import yaml
    from guard.models import CheckDef
    from guard.executors import template_supply_approval as actual
    checkfile = root / "update/packs/PACK-BASELINE/checks/SUP-03.yaml"
    check = CheckDef.model_validate(yaml.safe_load(checkfile.read_text(encoding="utf-8")))
    text = "Synthetic auxiliary supply normal control {{ message }}\n"
    path = out / "normal-template.jinja"
    path.write_text(text, encoding="utf-8")
    policy = {"allowed_template_sha256": [digest(path.read_bytes())]}
    normal_return = actual.validate_policy(check, policy, {})
    invalid = {"allowed_template_sha256": ["invalid-sha256-input"]}
    refusal = rejected(lambda: actual.validate_policy(check, invalid, {}))
    return record("SUP-03", "guard.template_supply_approval.validate_policy",
                  ["validate_policy(actual CheckDef, synthetic explicit allowlist, {})", "validate_policy(invalid digest input)"],
                  source(Path(inspect.getsourcefile(actual.validate_policy)), actual.PROFILE),
                  {"template_sha256": digest(path.read_bytes()), "policy": policy, "invalid_policy": invalid,
                   "checkdef_sha256": digest(checkfile.read_bytes())},
                  {"normal_return": normal_return, "invalid_policy": refusal},
                  [assertion("normal policy returned normally", normal_return is None),
                   assertion("standard invalid input refused", refusal["raised"])],
                  ["Actual repository policy validator only; no engine/API/worker.",
                   "Synthetic allowlist is not corporate approval; original CheckDef is read-only."])


def template_loader(root: Path, out: Path) -> dict:
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound
    text = "Synthetic auxiliary supply: {{ message }} / {{ generation }}\n"
    template = out / "local-normal.jinja"
    template.write_text(text, encoding="utf-8")
    os.chmod(template, 0o400)
    env = Environment(loader=FileSystemLoader(str(out)), autoescape=False)
    context = {"message": "normal-local-input-232", "generation": "synthetic-v1"}
    loaded = env.get_template(template.name)
    rendered = loaded.render(**context)
    refusal = None
    try:
        env.get_template("absent-normal-template.jinja")
    except TemplateNotFound as error:
        refusal = exception(error)
    finally:
        os.chmod(template, 0o600)  # This tool's newly-created file only.
    return record("SUP-03", "installed jinja2.FileSystemLoader/get_template/render",
                  ["Environment(FileSystemLoader(owned temp directory)).get_template(...).render(**context)",
                   "get_template(absent normal filename)"],
                  source(Path(inspect.getsourcefile(FileSystemLoader)), importlib.metadata.version("jinja2")),
                  {"template_sha256": digest(template.read_bytes()), "context": context},
                  {"rendered": rendered, "loaded_filename": loaded.filename, "missing_template_exception": refusal,
                   "input_template_sha256_after": digest(template.read_bytes())},
                  [assertion("new literal template loaded/rendered", rendered.strip() == "Synthetic auxiliary supply: normal-local-input-232 / synthetic-v1"),
                   assertion("ordinary absent template refused", refusal is not None)],
                  ["Actual installed dependency API, not vLLM/HF selected template consumption.",
                   "No sandbox attack or custom filter/plugin. Jinja version is measured, not approved against another profile.",
                   "Existing SUP13 pinned 3.1.6 gate is not invoked or relaxed."])


def shard_consumer(root: Path, out: Path) -> dict:
    from guard.executors import supply_boot_contract as actual
    from guard.runtime.bounded import strict_json
    env = {"kind": "ENV", "id": "ENV-SYNTHETIC-232", "revision": 1, "sha256": digest(b"synthetic-env-232")}
    target_sha = digest(b"synthetic-target-232")
    contract_source = source(Path(inspect.getsourcefile(actual.validate_record)), actual.SCHEMA)
    packet = {"schema": actual.SCHEMA, "profile": actual.CHECK_PROFILES["SUP-06"], "role": "normal",
              "environment_ref": env, "target_sha256": target_sha, "source_sha256": contract_source["sha256"],
              "change": {"kind": "synthetic-complete-shard-input", "applied": False, "denied": False,
                         "actor_sha256": digest(b"synthetic-record-writer-232"), "sensor_started_before_consumption": False},
              "rows": [{"rank": 0, "required_missing": False, "ready_accepted": True}]}
    path = out / "new-synthetic-shard-record.json"
    path.write_bytes(encode(packet))
    parsed = strict_json(path.read_bytes())
    kwargs = {"check_id": "SUP-06", "role": "normal", "environment_ref": env,
              "target_sha256": target_sha, "source_sha256": contract_source["sha256"]}
    validated = actual.validate_record(parsed, **kwargs)
    evaluation = actual.evaluate_record("SUP-06", validated, {})
    invalid = {**parsed, "unexpected_field": "ordinary-invalid-shape"}
    refusal = rejected(lambda: actual.validate_record(invalid, **kwargs))
    return record("SUP-06", "guard strict_json → supply_boot_contract.validate_record/evaluate_record",
                  ["strict_json(new temporary JSON file)", "validate_record(parsed, exact synthetic bindings)",
                   "evaluate_record(SUP-06, validated, {})", "validate_record(ordinary invalid extra field)"],
                  contract_source, {"packet": packet, "invalid_packet": invalid},
                  {"parsed_file_sha256": digest(path.read_bytes()), "validated_equal": validated == parsed,
                   "consumer_evaluation": evaluation, "invalid_shape": refusal,
                   "producer_authenticated": False, "actual_rank_or_tensor_loaded": False},
                  [assertion("new file reached actual consumer", validated == parsed),
                   assertion("normal record consumer returned no issues", evaluation == ([], [])),
                   assertion("invalid record shape refused", refusal["raised"])],
                  ["Actual checkout consumer with synthetic JSON only; no real producer/rank/worker.",
                   "Empty consumer issues are not a product PASS, native authentication, or real shard loading."])


def builtin_config(out: Path) -> Path:
    folder = out / "builtin-opt-config"
    folder.mkdir()
    (folder / "config.json").write_bytes(encode({
        "model_type": "opt", "architectures": ["OPTForCausalLM"], "vocab_size": 16,
        "hidden_size": 4, "word_embed_proj_dim": 4, "ffn_dim": 8, "num_attention_heads": 1,
        "num_hidden_layers": 1, "max_position_embeddings": 16, "torch_dtype": "float32",
    }))
    return folder


def config_loader(root: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        from vllm.transformers_utils.config import get_config
    folder = builtin_config(out)
    config = get_config(folder, trust_remote_code=False, local_files_only=True)
    invalid = out / "invalid-config"
    invalid.mkdir()
    (invalid / "config.json").write_bytes(encode({"model_type": "not-a-builtin-model-type-232"}))
    refusal = rejected(lambda: get_config(invalid, trust_remote_code=False, local_files_only=True))
    return record("SUP-09", "installed CPU vLLM get_config built-in control",
                  ["get_config(new local OPT config, trust_remote_code=False, local_files_only=True)",
                   "get_config(local unsupported model_type, trust_remote_code=False)"],
                  source(Path(inspect.getsourcefile(get_config)), importlib.metadata.version("vllm")),
                  {"normal_config_sha256": digest((folder / "config.json").read_bytes()),
                   "invalid_config_sha256": digest((invalid / "config.json").read_bytes())},
                  {"returned_class": type(config).__module__ + "." + type(config).__name__,
                   "model_type": config.model_type, "hidden_size": config.hidden_size,
                   "standard_input_refusal": refusal},
                  [assertion("installed vLLM built-in config returned", config.model_type == "opt" and config.hidden_size == 4),
                   assertion("ordinary unsupported model_type refused", refusal["raised"])],
                  ["Actual installed alternate CPU profile, not approved v0.31 source gate or GPU.",
                   "No dynamic/custom code, weights, engine, worker or trust=True invocation."])


def tokenizer_loader(root: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import AutoTokenizer, PreTrainedTokenizerFast
    raw = Tokenizer(models.WordLevel({"[UNK]": 0, "local232": 1, "supply232": 2}, unk_token="[UNK]"))
    raw.pre_tokenizer = pre_tokenizers.Whitespace()
    producer = PreTrainedTokenizerFast(tokenizer_object=raw, unk_token="[UNK]")
    producer.chat_template = "{{ messages[0]['content'] }}"
    folder = out / "new-local-tokenizer"
    producer.save_pretrained(folder)
    loaded = AutoTokenizer.from_pretrained(folder, trust_remote_code=False, local_files_only=True)
    messages = [{"role": "user", "content": "local232 supply232"}]
    rendered = loaded.apply_chat_template(messages, tokenize=False)
    token_output = loaded.apply_chat_template(messages, tokenize=True)
    token_ids = token_output.get("input_ids") if isinstance(token_output, Mapping) else token_output
    flat_ids = token_ids[0] if isinstance(token_ids, list) and len(token_ids) == 1 and isinstance(token_ids[0], list) else token_ids
    files = {p.name: digest(p.read_bytes()) for p in folder.iterdir() if p.is_file()}
    return record("SUP-03", "installed HF AutoTokenizer and selected benign chat template",
                  ["PreTrainedTokenizerFast.save_pretrained(new owned directory)",
                   "AutoTokenizer.from_pretrained(local path, local_files_only=True, trust_remote_code=False)",
                   "loaded.apply_chat_template(messages, tokenize=False/True)"],
                  source(Path(inspect.getsourcefile(AutoTokenizer)), importlib.metadata.version("transformers")),
                  {"files": files, "messages": messages},
                  {"returned_class": type(loaded).__module__ + "." + type(loaded).__name__,
                   "rendered": rendered, "token_output_type": type(token_output).__module__ + "." + type(token_output).__name__,
                   "token_output": dict(token_output) if isinstance(token_output, Mapping) else token_output,
                   "token_ids": token_ids, "files_after": files},
                  [assertion("new local template consumed by real tokenizer", rendered == "local232 supply232"),
                   assertion("real tokenizer produced input IDs", flat_ids == [1, 2])],
                  ["Actual installed tokenizer dependency; no vLLM engine/API or worker final consumption.",
                   "Synthetic built-in WordLevel assets only; no remote/custom code or past archive reuse."])


def native_shard_loader(root: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from safetensors.torch import save_file
        from vllm.model_executor.model_loader.weight_utils import safetensors_weights_iterator, default_weight_loader
    if torch.version.cuda is not None or torch.get_default_device().type != "cpu":
        raise ValueError("CPU-only torch runtime required")
    supplied = torch.tensor([[2.0, 3.0, 5.0], [7.0, 11.0, 13.0]], device="cpu")
    path = out / "new-normal-shard.safetensors"
    save_file({"linear.weight": supplied}, str(path))
    rows = list(safetensors_weights_iterator([str(path)], False, safetensors_load_strategy="eager"))
    parameter = torch.nn.Parameter(torch.zeros_like(supplied))
    default_weight_loader(parameter, rows[0][1])
    missing = out / "absent-normal-shard.safetensors"
    try:
        list(safetensors_weights_iterator([str(missing)], False, safetensors_load_strategy="eager"))
    except (OSError, ValueError) as error:
        refusal = exception(error)
    else:
        refusal = None
    return record("SUP-06", "installed CPU vLLM safetensors iterator and default_weight_loader",
                  ["safetensors_weights_iterator([new local shard], False, safetensors_load_strategy='eager')",
                   "default_weight_loader(CPU Parameter, actual returned tensor)", "iterator(absent file)"],
                  source(Path(inspect.getsourcefile(safetensors_weights_iterator)), importlib.metadata.version("vllm")),
                  {"file_sha256": digest(path.read_bytes()), "tensor_values": supplied.tolist()},
                  {"keys": [name for name, _ in rows], "actual_tensor_values": parameter.detach().tolist(),
                   "missing_file_exception": refusal, "device": str(parameter.device)},
                  [assertion("real iterator and weight loader copied supplied values", len(rows) == 1 and torch.equal(parameter, supplied)),
                   assertion("ordinary missing shard refused", refusal is not None)],
                  ["Actual installed vLLM CPU loader component, not an admitted worker/rank/full model.",
                   "No GPU/TP, native production producer authentication or full required tensor inventory."])


def disabled_plugin_selection(root: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        from vllm.plugins import load_plugins_by_group
    group = "vllm.general_plugins"
    entries = [{"name": ep.name, "value": ep.value} for ep in importlib.metadata.entry_points(group=group)]
    selected = load_plugins_by_group(group)
    return record("SUP-10", "installed vLLM plugin selector with explicit empty allowlist",
                  "load_plugins_by_group('vllm.general_plugins') with VLLM_PLUGINS=''",
                  source(Path(inspect.getsourcefile(load_plugins_by_group)), importlib.metadata.version("vllm")),
                  {"group": group, "VLLM_PLUGINS": ""},
                  {"discovered_entry_points": entries, "selected_names": list(selected), "plugin_hooks_executed": 0},
                  [assertion("actual selector kept explicit disable", selected == {})],
                  ["Actual installed selection API only. No selected plugin hook executed or unapproved module imported.",
                   "Does not complete allowlisted plugin normal/hook/consumer or autoload branches."])


def layerwise_normal(root: Path, out: Path) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from vllm.config import ModelConfig
        from vllm.model_executor.model_loader.reload import layerwise
    if torch.version.cuda is not None or torch.get_default_device().type != "cpu":
        raise ValueError("CPU-only torch required")
    folder = builtin_config(out)
    config = ModelConfig(model=str(folder), tokenizer=str(folder), skip_tokenizer_init=True,
                         dtype="float32", max_model_len=16, trust_remote_code=False)
    layer = torch.nn.Linear(3, 2, bias=False, device="cpu")
    with torch.no_grad():
        layer.weight.zero_()
    held = layer.weight
    before = held.detach().tolist()
    payload = torch.tensor([[1., 3., 5.], [2., 4., 6.]], device="cpu")
    layerwise.record_metadata_for_reloading(layer)
    layerwise.initialize_layerwise_reload(layer)
    layer.weight.weight_loader(layer.weight, payload)
    layerwise.finalize_layerwise_reload(layer, config)
    value = torch.tensor([[2., 1., 3.]], device="cpu")
    with torch.no_grad():
        result = layer(value)
    return record("LIF-06", "installed vLLM native layerwise reload normal CPU component",
                  ["real ModelConfig(local built-in config, skip_tokenizer_init=True)",
                   "record_metadata_for_reloading(torch.nn.Linear)", "initialize_layerwise_reload",
                   "original wrapped weight_loader(normal tensor)", "finalize_layerwise_reload"],
                  source(Path(inspect.getsourcefile(layerwise.record_metadata_for_reloading)), importlib.metadata.version("vllm")),
                  {"config_sha256": digest((folder / "config.json").read_bytes()), "weights": payload.tolist(), "input": value.tolist()},
                  {"before": before, "after": layer.weight.detach().tolist(), "original_parameter_restored": layer.weight is held,
                   "loading_closed": not layerwise.get_layerwise_info(layer).can_load(), "output": result.tolist()},
                  [assertion("actual native reloader retained normal supplied values", torch.equal(layer.weight, payload)),
                   assertion("actual normal linear operation used values", torch.equal(result, value @ payload.T)),
                   assertion("native loading state closed", not layerwise.get_layerwise_info(layer).can_load())],
                  ["Real installed native helper with a standard torch layer; not a vLLM model/worker.",
                   "No live request, partial-update fault, rollback/fallback, KV generation or GPU concurrency tested."])


def child(input_path: Path, config_root: Path | None = None) -> int:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    config_info = None
    if config_root is not None:
        with contextlib.redirect_stdout(sys.stderr):
            from vllm.transformers_utils.config import get_config
            config = get_config(config_root, trust_remote_code=False, local_files_only=True)
        config_info = {"class": type(config).__module__ + "." + type(config).__name__,
                       "model_type": config.model_type, "hidden_size": config.hidden_size,
                       "source": source(Path(inspect.getsourcefile(get_config)), importlib.metadata.version("vllm"))}
    print(json.dumps({"input_sha256": digest(input_path.read_bytes()), "echo": data,
                      "sum": sum(data["numbers"]), "pid": os.getpid(),
                      "python": sys.version, "executable": sys.executable,
                      "flags": {k: getattr(sys.flags, k) for k in ("isolated", "optimize", "dont_write_bytecode")},
                      "json_source": source(Path(json.__file__), platform.python_version()), "vllm_config": config_info}))
    return 0


def python_modes(root: Path, out: Path) -> dict:
    data = {"purpose": "new-normal-interpreter-input-232", "numbers": [3, 7, 11]}
    path = out / "interpreter-input.json"
    path.write_bytes(encode(data))
    calls = []
    try:
        importlib.metadata.version("vllm")
    except importlib.metadata.PackageNotFoundError:
        config_root = None
    else:
        config_root = builtin_config(out)
    for flags in (["-I", "-B"], ["-I", "-O", "-B"]):
        argv = [sys.executable, *flags, str(Path(__file__).resolve()), "--child-input", str(path)]
        if config_root is not None:
            argv += ["--child-config-root", str(config_root)]
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = subprocess.run(argv, capture_output=True, timeout=60, check=False)
            raw_out, raw_err, returncode, timed_out = result.stdout, result.stderr, result.returncode, False
        except subprocess.TimeoutExpired as error:
            raw_out, raw_err, returncode, timed_out = error.stdout or b"", error.stderr or b"", None, True
        tag = "mode-" + str(len(calls))
        (out / (tag + ".stdout")).write_bytes(raw_out)
        (out / (tag + ".stderr")).write_bytes(raw_err)
        call = {"argv": argv, "started_at_UTC": started,
                "finished_at_UTC": datetime.now(timezone.utc).isoformat(),
                "returncode": returncode, "timed_out": timed_out,
                "stdout": raw_out.decode("utf-8", "replace"), "stdout_sha256": digest(raw_out),
                "stderr": raw_err.decode("utf-8", "replace"), "stderr_sha256": digest(raw_err), "parsed": None}
        (out / (tag + ".process.json")).write_bytes(encode(call))
        if returncode == 0:
            try:
                call["parsed"] = json.loads(raw_out.splitlines()[-1])
            except (ValueError, IndexError) as error:
                call["parse_exception"] = exception(error)
        calls.append(call)
    checks = [assertion("two fresh normal children completed", all(x["returncode"] == 0 for x in calls)),
              assertion("actual isolated/optimized modes observed", all(x["parsed"] for x in calls) and [x["parsed"]["flags"]["optimize"] for x in calls] == [0, 1]
                        and all(x["parsed"]["flags"]["isolated"] == 1 for x in calls)),
              assertion("new input consumed under both modes", all(x["parsed"] and x["parsed"]["sum"] == 21 and x["parsed"]["input_sha256"] == digest(path.read_bytes()) for x in calls))]
    if config_root is not None:
        checks.append(assertion("installed built-in vLLM loader returned in both actual modes",
                                all(x["parsed"] and x["parsed"]["vllm_config"] and x["parsed"]["vllm_config"]["model_type"] == "opt" for x in calls)))
    return record("SUP-11", "actual Python startup modes and built-in loader" if config_root else "actual Python/stdlib only", [x["argv"] for x in calls],
                  source(Path(sys.executable), platform.python_version()), data, {"child_processes": calls}, checks,
                  ["Real interpreter and, where recorded, installed built-in config loader; never a vLLM worker.",
                   "No custom module, unapproved code, optimization bypass or Python mode gate alteration."])


def pipe_cleanup(root: Path, out: Path) -> dict:
    path = root / "update/src/guard/runtime/bounded.py"
    actual = load_module(path, "gpu18_actual_bounded")
    data = {"purpose": "new-normal-pipe-cleanup-input-232", "numbers": [5, 6]}
    input_path = out / "pipe-input.json"
    input_path.write_bytes(encode(data))
    argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--child-input", str(input_path)]
    result = actual.capture_pipes(argv, timeout=10, env_extra={}, max_stdout_bytes=65536, max_stderr_bytes=8192)
    (out / "child.stdout").write_bytes(result["stdout_bytes"])
    (out / "child.stderr").write_bytes(result["stderr_bytes"])
    (out / "capture.json").write_bytes(encode({"returncode": result["returncode"], "capture": result["capture"]}))
    parsed = actual.strict_json(result["stdout_bytes"])
    observations = {"returncode": result["returncode"], "capture": result["capture"], "parsed_child_output": parsed,
                    "stdout": result["stdout_bytes"].decode("utf-8", "replace"),
                    "stderr": result["stderr_bytes"].decode("utf-8", "replace")}
    return record("LIF-11", "guard.runtime.bounded.capture_pipes normal child/pipe cleanup", argv,
                  source(path, "guard-pipe-capture/1"), data, observations,
                  [assertion("normal owned child exited", result["returncode"] == 0),
                   assertion("actual pipes closed and capture complete", result["capture"]["complete"] and result["capture"]["pipes_closed"]),
                   assertion("new input consumed", parsed["sum"] == 11)],
                  ["Actual collector helper only; no vLLM worker or resource recovery tested.",
                   "No fault, shared resource, external PID termination, timeout extension or IPC cleanup claim."])


def ipc_normal(root: Path, out: Path) -> dict:
    if sys.platform != "linux":
        raise RuntimeError("Linux SO_PEERCRED/proc runtime required; no fallback")
    path = root / "update/src/guard/executors/ipc_probe.py"
    actual = load_module(path, "gpu18_actual_ipc_probe")
    with tempfile.TemporaryDirectory(prefix="gpu18-ipc-") as temp:
        endpoint = Path(temp) / "normal.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            inspected = actual.inspect_paths([str(endpoint)])
            connected = actual.connect_paths([str(endpoint)], 0.5)
        endpoint.unlink()
        removed = not endpoint.exists()
    row = connected["rows"][0]
    return record("PLT-02", "actual guard ipc_probe on owned zero-payload Unix socket",
                  ["inspect_paths([owned socket])", "connect_paths([owned socket], 0.5)"],
                  source(path, connected["schema"]), {"endpoint_kind": "owned-temp-UDS", "payload_bytes": 0},
                  {"metadata": inspected, "connection": connected, "owned_path_removed": removed},
                  [assertion("own path metadata collected", inspected["rows"][0]["complete"]),
                   assertion("actual zero-payload connect", row["outcome"] == "connected" and row.get("path_stable") is True),
                   assertion("peer is current allowed subject", row.get("peer", {}).get("uid") == os.geteuid()
                             and row.get("peer", {}).get("pid") == os.getpid()),
                   assertion("only owned socket cleaned", removed)],
                  ["Own process listener is not an engine/worker; no real IPC inventory or command consumption.",
                   "Only same-subject normal control; no UID switch, permission bypass, SHM, TCP or unapproved subject."])


def unavailable(cid: str, reason: str, runtime_info: dict, error=None) -> dict:
    return {"check_id": cid, "component": "supported real component unavailable", "execution_kind": "not_executed",
            "input_origin": "synthetic", "official": False, "formal_data_origin": "EXAMPLE", "status": "unavailable",
            "command_or_api": None, "target_source": {"path": None, "sha256": None, "version": None},
            "input_sha256": None, "observations": {"reason": reason, "runtime_packages": runtime_info["packages"], "exception": error},
            "assertions": [], "scope_limitations": ["Not counted as an actual execution; no installation/download or substitution."],
            "remaining_real_integration": REMAINING[cid], "requires_gpu_for_remaining": cid in ("SUP-06", "LIF-06", "LIF-11")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checks", nargs="+", choices=CHECK_IDS, default=list(CHECK_IDS))
    parser.add_argument("--child-input", type=Path)
    parser.add_argument("--child-config-root", type=Path)
    args = parser.parse_args(argv)
    if args.child_input is not None:
        return child(args.child_input, args.child_config_root)
    if args.source_root is None or args.output is None:
        parser.error("--source-root and --output are required")
    root = args.source_root.resolve(strict=True)
    out = args.output.absolute()
    if out.is_relative_to(root / "sources") or out.is_relative_to(root / "update/src"):
        raise ValueError("output must not be a source directory")
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root / "update/src"))
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                      VLLM_PLUGINS="", VLLM_USE_MODELSCOPE="0", CUDA_VISIBLE_DEVICES="",
                      TOKENIZERS_PARALLELISM="false", HF_HOME=str(out / "hf-cache"),
                      XDG_CACHE_HOME=str(out / "cache"), TRITON_CACHE_DIR=str(out / "triton-cache"))
    identity = runtime()
    records = []
    for cid in dict.fromkeys(args.checks):
        installed = identity["packages"]["vllm"] is not None
        if cid in ("SUP-09", "SUP-10", "LIF-06") and not installed:
            records.append(unavailable(cid, "No supported installed vLLM/transformers/torch/plugin runtime selected; old Docker path is forbidden in this task.", identity))
            continue
        checks = {"SUP-03": [template_policy, template_loader] + ([tokenizer_loader] if installed else []),
                  "SUP-06": [shard_consumer] + ([native_shard_loader] if installed else []),
                  "SUP-09": [config_loader], "SUP-10": [disabled_plugin_selection], "LIF-06": [layerwise_normal],
                  "SUP-11": [python_modes], "PLT-02": [ipc_normal], "LIF-11": [pipe_cleanup]}[cid]
        for index, call in enumerate(checks):
            folder = out / (cid + "-" + str(index))
            folder.mkdir()
            try:
                current = call(root, folder)
                raw_record = encode(current)
            except Exception as error:
                current = unavailable(cid, "Component attempt did not complete; original exception retained; not counted as completed.", identity, exception(error))
                current["attempted_component"] = call.__name__
                raw_record = encode(current)
            (folder / "component-record.json").write_bytes(raw_record)
            records.append(current)
    receipt = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
               "official": False, "formal_data_origin": "EXAMPLE", "policy_status": "DRAFT",
               "started_or_recorded_UTC": datetime.now(timezone.utc).isoformat(),
               "tool": source(Path(__file__), "gpu18-local-supply/1"), "source_root": str(root),
               "runtime": identity, "records": records,
               "completed_record_count": sum(r["status"] == "completed" for r in records),
               "not_executed_record_count": sum(r["execution_kind"] == "not_executed" for r in records),
               "actual_engine_or_worker_consumption_verified": False,
               "AWS_GPU_Docker_download_real_network_operations": 0,
               "test_doubles_or_AST_extraction": False,
               "prior_component_results": "Not rerun. Prior Docker/CPU results remain separate with their original source/Refs."}
    (out / "receipt.json").write_bytes(encode(receipt))
    print(json.dumps({"receipt": str(out / "receipt.json"), "sha256": digest((out / "receipt.json").read_bytes()),
                      "completed_records": receipt["completed_record_count"],
                      "not_executed_records": receipt["not_executed_record_count"]}))
    return 1 if any(r["status"] == "assertion_failed" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
