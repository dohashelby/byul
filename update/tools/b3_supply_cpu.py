"""B3의 새 C mapping/npcache/template 오류·복구만 직접 관측한다.

기존 정상 모델 추론·decoder suite를 반복하지 않는다. 실제 dependency/API를
대역으로 바꾸지 않으며, 이 도구의 EXAMPLE 관측은 등록 producer/제품 판정이 아니다.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
import importlib
from importlib import metadata
import inspect
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import traceback

SOURCE_COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
CHECKS = {"SUP-05", "SUP-07", "SUP-12", "SUP-13"}
NORMAL_TEMPLATE = "B3 {{ messages[0]['role'] }}: {{ messages[0]['content'] }}"
BROKEN_TEMPLATE = "B3 {% if %}"


class CollectorDeadline(BaseException):
    """관측기 자체의 고정 기한. 제품·옛 모델 기한을 변경하지 않는다."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_ref(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}


def error_record(error):
    return {"type": type(error).__name__, "module": type(error).__module__,
            "message": str(error), "traceback": traceback.format_exc()}


def invoke(name, call):
    before = time.monotonic()
    try:
        result = {"case": name, "outcome": "returned", "value": call()}
    except Exception as error:
        result = {"case": name, "outcome": "raised", "exception": error_record(error)}
    result["seconds"] = time.monotonic() - before
    return result


def stat_identity(value):
    return {"device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid,
            "gid": value.st_gid, "mode": value.st_mode & 0o7777, "bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns, "nlink": value.st_nlink}


def own_mapping(path):
    """자기 파일과 정확히 일치하는 proc 항목만 저장한다. 주소·타 파일은 제외."""
    path = str(path)
    fds, maps = [], []
    for item in Path("/proc/self/fd").iterdir():
        try:
            if os.readlink(item) == path:
                with item.open("rb") as stream:
                    fds.append({"fd": int(item.name), **stat_identity(os.fstat(stream.fileno()))})
        except (OSError, ValueError):
            continue
    for line in Path("/proc/self/maps").read_text().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) == 6 and parts[5] == path:
            begin, end = (int(x, 16) for x in parts[0].split("-"))
            maps.append({"permissions": parts[1], "offset": int(parts[2], 16),
                         "device": parts[3], "inode": int(parts[4]), "mapped_bytes": end - begin})
    return {"fds": fds, "maps": maps, "scope": "exact owned path in this process only"}


def safe_mapping(work):
    import numpy as np
    import safetensors
    from safetensors.numpy import save_file
    native = importlib.import_module("safetensors._safetensors_rust")
    path = work / "owned-normal.safetensors"
    values = np.array([[1.5, 2.5], [3.5, 4.5]], dtype=np.float32)
    save_file({"b3_owned_numeric": values}, str(path))
    original = file_ref(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    result = {"check_ids": ["SUP-05"], "new_input": "new owned 2x2 float32 C safe_open/get_slice path",
              "new_purpose": "Python open sensor와 구분한 실제 C mmap 파일 신원·수명 관측",
              "native_source": file_ref(native.__file__), "dependency": metadata.version("safetensors"),
              "input": original, "before": own_mapping(path)}
    try:
        result["anchor_fd"] = {"fd": fd, **stat_identity(os.fstat(fd))}
        with safetensors.safe_open(str(path), framework="np", device="cpu") as reader:
            result["keys"] = list(reader.keys())
            array = reader.get_slice("b3_owned_numeric")[:]
            result["slice"] = {"shape": list(array.shape), "dtype": str(array.dtype), "values": array.tolist()}
            result["during"] = own_mapping(path)
            result["normal_values_match"] = bool(np.array_equal(array, values))
            del array
        gc.collect()
        result["after_context"] = own_mapping(path)
        result["input_unchanged"] = file_ref(path)["sha256"] == original["sha256"]
        result["mapping_matches_owned_inode"] = bool(result["during"]["maps"]) and all(
            row["inode"] == result["anchor_fd"]["inode"] for row in result["during"]["maps"])
    finally:
        os.close(fd)
    result["after_anchor_close"] = own_mapping(path)
    result["assertions"] = {"actual_C_slice_matches": result["normal_values_match"],
        "observed_mapping_matches_owned_inode": result["mapping_matches_owned_inode"],
        "normal_file_unchanged": result["input_unchanged"],
        "owned_mapping_released": not result["after_anchor_close"]["maps"],
        "owned_FDs_closed": not result["after_anchor_close"]["fds"]}
    result["remaining"] = ["실 worker/rank·승인 inventory·pre-read 인과 root·최종 tensor 소비",
        "타 계정·symlink/mount 교체 거부 및 권한 집행; 이 정상 파일 관측은 해당 보안 시험이 아님"]
    return result


@contextmanager
def call_trace(functions):
    """함수 대체 없이 선정된 원 Python call만 제한적으로 관측."""
    counts = dict.fromkeys(functions.values(), 0)
    codes = {function.__code__: name for function, name in functions.items()}
    previous = sys.getprofile()
    def profile(frame, event, arg):
        if event == "call" and frame.f_code in codes:
            counts[codes[frame.f_code]] += 1
    sys.setprofile(profile)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


def npcache(work, source_root):
    """원 vLLM 전체 모듈의 실제 writer/reader, 무해 numeric 손상·복구만."""
    import numpy as np
    import torch
    from vllm.model_executor.model_loader import weight_utils as actual
    expected = source_root / "vllm/model_executor/model_loader/weight_utils.py"
    if Path(inspect.getsourcefile(actual.np_cache_weights_iterator)).resolve() != expected.resolve():
        raise ValueError("B3_NPCACHE_ORIGIN_MISMATCH")
    if torch.version.cuda is not None or torch.cuda.is_initialized():
        raise ValueError("B3_CPU_TORCH_REQUIRED")
    torch.set_num_threads(2)
    folder = work / "numeric-checkpoint"; folder.mkdir()
    locks = work / "private-locks"; locks.mkdir()
    checkpoint = folder / "normal.bin"
    torch.save({"b3_numeric": torch.tensor([[2., 4.], [6., 8.]])}, checkpoint)
    tracked = {actual.np_cache_weights_iterator: "vllm.np_cache_weights_iterator",
               np.load: "numpy.load", torch.load: "torch.load"}
    def read(name):
        with call_trace(tracked) as calls:
            row = invoke(name, lambda: [{"name": key, "shape": list(value.shape), "values": value.tolist()}
                for key, value in actual.np_cache_weights_iterator("b3-s5-numeric", str(locks), str(folder), [str(checkpoint)], False)])
        row["actual_call_counts"] = dict(calls)
        return row
    rows = [read("new-numeric-cache-create"), read("same-generated-cache-read")]
    cache = folder / "np/b3_numeric"
    normal_bytes = cache.read_bytes(); normal_sha = sha(normal_bytes)
    rows[-1]["generated_cache"] = file_ref(cache)
    # Ordinary truncated numeric header: no object arrays, custom reducer,
    # pickle payload, untrusted writer or executable generated code.
    cache.write_bytes(normal_bytes[:8])
    damaged = file_ref(cache)
    rows.append(read("truncated-numeric-header"))
    cache.write_bytes(normal_bytes)
    rows.append(read("normal-after-owned-byte-restore"))
    normal = [{"name": "b3_numeric", "shape": [2, 2], "values": [[2., 4.], [6., 8.]]}]
    result = {"check_ids": ["SUP-07", "SUP-12"], "new_input": {"checkpoint": file_ref(checkpoint),
        "generated_cache_sha256": normal_sha, "truncated_cache": damaged},
        "new_purpose": "실제 npcache 생성/재사용 reader·손상 예외 후 동일 numeric reader 복구",
        "actual_source": file_ref(expected), "source_commit": SOURCE_COMMIT,
        "dependencies": {name: metadata.version(name) for name in ("torch", "numpy")}, "calls": rows,
        "restored_bytes_sha256": sha(cache.read_bytes()),
        "assertions": {"normal_create_matches": rows[0].get("value") == normal,
            "generated_cache_reader_matches": rows[1].get("value") == normal,
            "actual_numpy_reader_reached": all(r["actual_call_counts"]["numpy.load"] >= 1 for r in rows),
            "reuse_did_not_reload_checkpoint": all(r["actual_call_counts"]["torch.load"] == 0 for r in rows[1:]),
            "truncation_raised": rows[2]["outcome"] == "raised",
            "normal_after_error_matches": rows[3].get("value") == normal,
            "normal_cache_bytes_restored": sha(cache.read_bytes()) == normal_sha},
        "remaining": ["비허용 writer의 실제 권한·변경·검증 전 집행은 관측하지 않음",
            "DefaultModelLoader 선택/fallback 전체·공유 daemon·GPU/worker 최종 소비 미관측",
            "Python 함수 호출만 관측; OS 전체 side effects 부재·기업 허용 reader를 채우지 않음"],
        "legacy_B_row_not_fabricated": {"SUP-07": ["writer_allowed", "verified_before_use"],
            "SUP-12": ["기업 승인 reader 정책", "전체 side_effects/fallback 관측"]}}
    return result


def template_recovery(work, guard_root):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import AutoTokenizer, PreTrainedTokenizerFast
    from transformers.utils import chat_template_utils
    from jinja2 import TemplateSyntaxError
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=Tokenizer(WordLevel({"[UNK]": 0, "B3": 1}, unk_token="[UNK]")), unk_token="[UNK]")
    folder = work / "owned-tokenizer"; tokenizer.save_pretrained(folder)
    selected = AutoTokenizer.from_pretrained(str(folder), local_files_only=True, trust_remote_code=False)
    messages = [{"role": "user", "content": "syntax recovery control"}]
    rows = []
    for name, text in [("normal-before", NORMAL_TEMPLATE), ("ordinary-template-syntax-error", BROKEN_TEMPLATE), ("normal-after", NORMAL_TEMPLATE)]:
        row = invoke(name, lambda text=text: selected.apply_chat_template(messages, chat_template=text, tokenize=False))
        row["template_sha256"] = sha(text.encode()); rows.append(row)
    from guard.models import CheckDef
    from guard.executors import template_sandbox as consumer
    import yaml
    check_path = guard_root / "update/packs/PACK-BASELINE/checks/SUP-13.yaml"
    check = CheckDef.model_validate(yaml.safe_load(check_path.read_bytes()))
    policies = [{"forbidden_template_attributes": ["__class__"]}, {}, {"forbidden_template_attributes": ["unselected_attribute"]}]
    policy_calls = [invoke(name, lambda values=values: consumer.validate_policy(check, values, {}))
        for name, values in zip(("explicit-example-policy", "missing-policy", "unsupported-policy"), policies)]
    return {"check_ids": ["SUP-13"], "new_input": {"normal_template_sha256": sha(NORMAL_TEMPLATE.encode()),
        "broken_template_sha256": sha(BROKEN_TEMPLATE.encode()), "messages": messages},
        "new_purpose": "선택된 실제 HF/Jinja renderer의 구문 오류·후속 정상 처리와 원 B 정책 진입 경계",
        "source": file_ref(inspect.getsourcefile(selected.apply_chat_template)),
        "renderer_dependency_source": file_ref(chat_template_utils.__file__),
        "B_consumer_source": file_ref(consumer.__file__), "check_source": file_ref(check_path),
        "dependencies": {name: metadata.version(name) for name in ("transformers", "jinja2", "tokenizers")},
        "calls": rows, "B_policy_calls": policy_calls, "policy_origin": "EXAMPLE, predefined before calls",
        "assertions": {"normal_before_matches": rows[0].get("value") == "B3 user: syntax recovery control",
            "syntax_error_retained": rows[1]["outcome"] == "raised" and rows[1]["exception"]["type"] == TemplateSyntaxError.__name__,
            "same_renderer_normal_after_matches": rows[2].get("value") == rows[0].get("value") and rows[2]["outcome"] == "returned",
            "original_B_accepts_selected_policy": policy_calls[0]["outcome"] == "returned",
            "original_B_rejects_missing_or_unsupported_policy": all(r["outcome"] == "raised" for r in policy_calls[1:])},
        "remaining": ["직접 installed HF dependency 호출이며 vLLM endpoint/worker의 공급 template 귀속 미확인",
            "금지 속성 접근·임의 코드/파일/통신 payload를 실행하지 않음; sandbox 집행/PASS 근거가 아님",
            "원 pinned image/Jinja·readonly mount·실 generation·요청 projection과 전체 비용 한도 별도"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard-root", required=True, type=Path)
    parser.add_argument("--vllm-source", required=True, type=Path)
    parser.add_argument("--source-freeze", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checks", nargs="+", choices=sorted(CHECKS), required=True)
    args = parser.parse_args()
    root = args.guard_root.resolve(strict=True); source_root = args.vllm_source.resolve(strict=True)
    freeze = json.loads(args.source_freeze.read_bytes())
    if freeze["vllm_commit"] != SOURCE_COMMIT:
        raise ValueError("B3_VLLM_COMMIT_PIN_MISMATCH")
    for item in freeze["files"]:
        if sha(Path(item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("B3_SOURCE_BYTES_CHANGED:" + item["path"])
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    selected = set(args.checks)
    receipt = {"schema": "guard-b3-local-observations/1", "input_origin": "synthetic", "data_origin": "EXAMPLE",
        "official": False, "execution_kind": "actual_local_component", "GPU_READY": False,
        "product_verdict_produced": False, "registered_producer_record_created": False,
        "started_at": datetime.now(timezone.utc).isoformat(), "tool": file_ref(__file__),
        "freeze": file_ref(args.source_freeze), "argv": sys.orig_argv, "records": [], "cleanup_errors": [],
        "runtime": {"python": sys.executable, "version": sys.version, "uid": os.getuid(), "gid": os.getgid(),
            "pid": os.getpid(), "pgrp": os.getpgrp(), "session": os.getsid(0),
            "proc_stat": Path("/proc/self/stat").read_text(), "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "flags": {key: getattr(sys.flags, key) for key in ("isolated", "no_site", "dont_write_bytecode")}},
        "forbidden_operations_attempted": []}
    receipt["runtime"]["distributions"] = {}
    for name in ("numpy", "torch", "transformers", "jinja2", "safetensors", "tokenizers", "vllm"):
        try:
            dist = metadata.distribution(name)
            receipt["runtime"]["distributions"][name] = {"version": dist.version, "root": str(dist.locate_file(""))}
        except metadata.PackageNotFoundError:
            receipt["runtime"]["distributions"][name] = {"installed": False}
    sys.path[:0] = [str(root / "update/src"), str(source_root)]
    started = time.monotonic()
    def partial():
        (out / "receipt.partial.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def interrupt(signum, frame):
        raise CollectorDeadline("B3_COLLECTOR_SIGNAL:" + str(signum))
    signal.signal(signal.SIGALRM, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    signal.alarm(120)
    receipt["collector_deadline_seconds"] = 120
    partial()
    work = None
    try:
        with tempfile.TemporaryDirectory(prefix="byul-b3-s5-", dir="/tmp") as temp:
            work = Path(temp)
            receipt["owned_temporary_root"] = str(work)
            for name in ("HF_HOME", "XDG_CACHE_HOME", "TMPDIR", "VLLM_CACHE_ROOT", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR"):
                folder = work / name; folder.mkdir(); os.environ[name] = str(folder)
            os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", VLLM_TARGET_DEVICE="cpu", CUDA_VISIBLE_DEVICES="",
                              VLLM_PLUGINS="", VLLM_LOGGING_LEVEL="ERROR", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false")
            def audit(event, values):
                if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.fork", "os.posix_spawn", "os.system"}:
                    receipt["forbidden_operations_attempted"].append(event)
                    raise PermissionError("B3_NETWORK_OR_CHILD_FORBIDDEN:" + event)
            sys.addaudithook(audit)
            for name, checks, call in [("C-safe-open-mapping", {"SUP-05"}, lambda: safe_mapping(work)),
                    ("numeric-npcache-reader", {"SUP-07", "SUP-12"}, lambda: npcache(work, source_root)),
                    ("template-syntax-recovery", {"SUP-13"}, lambda: template_recovery(work, root))]:
                if checks & selected:
                    receipt["active_component"] = name
                    partial()
                    value = invoke(name, call)
                    receipt["records"].append(value)
                    receipt["active_component"] = None
                    partial()
    except BaseException as error:
        receipt["collector_terminal_exception"] = error_record(error)
    finally:
        signal.alarm(0)
    receipt["owned_temporary_root_removed"] = work is None or not work.exists()
    receipt["seconds"] = time.monotonic() - started
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["assertions_passed"] = bool(receipt["records"]) and not receipt.get("collector_terminal_exception") and all(
        r["outcome"] == "returned" and all(r["value"]["assertions"].values()) for r in receipt["records"])
    receipt["existing_model_engine_suite_archive_reexecutions"] = 0
    destination = out / "receipt.json"
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(destination), "sha256": sha(destination.read_bytes()),
        "assertions_passed": receipt["assertions_passed"], "seconds": receipt["seconds"]}, ensure_ascii=False))
    return 0 if receipt["assertions_passed"] and receipt["owned_temporary_root_removed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
