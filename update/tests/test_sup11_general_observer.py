"""Drive the fixed SUP-11 producer with synthetic startup frames and owned files."""

import builtins
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_stock_python_mode as adapter
from guard.executors import kubernetes_normal_python_mode as consumer
from guard.models import canonical_json, sha256_hex


MOUNT = Path("/guard-sup11")
DESTINATION = Path("/cache/guard-sup11")
SITE_ORIGIN = Path("/usr/lib/python3.12/sitecustomize.py")
SITE_TARGET = Path("/etc/python3.12/sitecustomize.py")
ASSET = consumer._ASSETS / "sup11_general_observer.py.txt"
PLUGIN_LOADER = "vllm.plugins"
SERVICE_ACTIVATION = ("vllm.engine.arg_utils", "AsyncEngineArgs.add_cli_args")
WORKER_ACTIVATION = ("vllm.v1.worker.worker_base", "WorkerWrapperBase.init_worker")
CUTLASS = adapter.INSTALLED_ROOT + "/nvidia_cutlass_dsl"
PATH_ENTRIES = ["/usr/local/bin", CUTLASS + "/dsl_packages", str(MOUNT), adapter.INSTALLED_ROOT]
WORKER_ANCHOR_PATH_ENTRIES = [*PATH_ENTRIES, "/fixture/worker-loader-root"]
ROLE_PATHS = {
    role: {"activation": PATH_ENTRIES, "anchor": PATH_ENTRIES if role == "service" else WORKER_ANCHOR_PATH_ENTRIES}
    for role in adapter.GENERAL_PATH_ROLES
}
ROLE_PATH_PINS = {
    role: {stage: sha256_hex(canonical_json(paths)) for stage, paths in stages.items()}
    for role, stages in ROLE_PATHS.items()
}
INTERPRETER_CONTEXT = {
    "cwd": "/cache", "home": "/cache/home", "uid": 2000, "gid": 0, "executable": "/usr/bin/python3",
    "executable_realpath": "/usr/bin/python3.12", "prefix": "/usr", "exec_prefix": "/usr",
    "base_prefix": "/usr", "base_exec_prefix": "/usr", "user_site": "/cache/home/.local/lib/python3.12/site-packages",
    "user_site_enabled": True, "site_origin": "/usr/lib/python3.12/site.py", "vendor_hook_origin": str(SITE_ORIGIN),
    "cutlass_origin": None, "cutlass_package_path": [CUTLASS],
}


def module_file(name):
    relative = name.replace(".", "/") + ("/__init__.py" if name == PLUGIN_LOADER else ".py")
    return Path(adapter.INSTALLED_ROOT) / relative


class InstalledFixturePath(type(Path())):
    def resolve(self, *, strict=False):
        return {SITE_ORIGIN: SITE_TARGET, Path("/usr/bin/python3"): Path("/usr/bin/python3.12")}.get(self, self)


class Process:
    def __init__(self, directory, rank=None):
        self.directory = directory
        self.rank = rank
        self.pid = 1 if rank is None else rank + 10
        self.now = 0
        self.profile_callback = None
        self.accesses = []
        self.paths = list(PATH_ENTRIES)
        self.modules = {}
        self.producer = ASSET.read_bytes()
        self.module_path = MOUNT / adapter.GENERAL_MODULE_FILE
        self.files = {
            self.module_path: self.producer,
            SITE_TARGET: b"fixture vendor hook bytes",
        }
        for name in (PLUGIN_LOADER, SERVICE_ACTIVATION[0], WORKER_ACTIVATION[0], adapter.SERVICE,
                     adapter.LOADER, adapter.MODEL):
            filename = module_file(name)
            self.files[filename] = name.encode()
            self.modules[name] = SimpleNamespace(__name__=name, __file__=str(filename))
        self.modules["sitecustomize"] = SimpleNamespace(
            __file__=str(SITE_ORIGIN), __spec__=SimpleNamespace(origin=str(SITE_ORIGIN)))
        self.modules["site"] = SimpleNamespace(USER_SITE=INTERPRETER_CONTEXT["user_site"], ENABLE_USER_SITE=True,
                                               __file__=INTERPRETER_CONTEXT["site_origin"],
                                               __spec__=SimpleNamespace(origin="frozen"))
        self.modules["nvidia_cutlass_dsl"] = SimpleNamespace(__spec__=SimpleNamespace(origin=None), __path__=[CUTLASS])
        self.modules["vllm.distributed.parallel_state"] = SimpleNamespace(_TP=SimpleNamespace(rank_in_group=rank))
        self.model_type = type("Qwen2VLForConditionalGeneration", (), {"__module__": adapter.MODEL})
        setattr(self.modules[adapter.MODEL], self.model_type.__name__, self.model_type)

        qualification = {
            "service_activation_sha256": self.module_digest(SERVICE_ACTIVATION[0]),
            "worker_activation_sha256": self.module_digest(WORKER_ACTIVATION[0]),
            "plugin_loader_sha256": self.module_digest(PLUGIN_LOADER),
            "sitecustomize_origin": str(SITE_ORIGIN), "sitecustomize_target": str(SITE_TARGET),
            "sitecustomize_sha256": sha256_hex(self.files[SITE_TARGET]),
            "ordinary_import_path_sha256": ROLE_PATH_PINS,
            "ordinary_interpreter_context_sha256": sha256_hex(canonical_json(INTERPRETER_CONTEXT)),
        }
        self.profile = {
            "schema": adapter.GENERAL_PROFILE_SCHEMA, "installed_root": adapter.INSTALLED_ROOT,
            "service": {"module": adapter.SERVICE, "sha256": self.module_digest(adapter.SERVICE)},
            "worker_loader_sha256": self.module_digest(adapter.LOADER),
            "model_source_sha256": self.module_digest(adapter.MODEL), "qualification": qualification,
        }
        self.files[MOUNT / "profile.json"] = canonical_json(self.profile)
        fake_sys = SimpleNamespace(
            modules=self.modules, path=self.paths,
            flags=SimpleNamespace(no_site=0, optimize=0, isolated=0, no_user_site=0, ignore_environment=0, safe_path=0),
            executable="/usr/bin/python3", prefix="/usr", exec_prefix="/usr", base_prefix="/usr", base_exec_prefix="/usr",
            gettrace=lambda: None,
            getprofile=lambda: self.profile_callback, setprofile=self.set_profile,
            _getframe=lambda depth: self.plugin_frame,
        )
        fake_os = SimpleNamespace(
            environ={"PYTHONPATH": str(MOUNT), "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "HOME": "/cache/home"},
            getcwd=lambda: "/cache", getuid=lambda: 2000, getgid=lambda: 0, geteuid=lambda: 2000, getegid=lambda: 0,
            statvfs=self.statvfs, ST_RDONLY=os.ST_RDONLY, getpid=lambda: self.pid, open=self.open_receipt,
            fdopen=os.fdopen, O_WRONLY=os.O_WRONLY, O_NOFOLLOW=os.O_NOFOLLOW,
            O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL,
        )
        imports = {
            "sys": fake_sys, "os": fake_os, "pathlib": SimpleNamespace(Path=InstalledFixturePath),
            "time": SimpleNamespace(monotonic=lambda: self.now),
        }

        def fixture_import(name, *args, **kwargs):
            if name in imports:
                return imports[name]
            assert name in {"hashlib", "json", "re", "stat"}
            return builtins.__import__(name, *args, **kwargs)

        self.observer = {"__file__": str(self.module_path),
                         "__builtins__": {**vars(builtins), "__import__": fixture_import}}
        exec(compile(self.producer, str(self.module_path), "exec"), self.observer)
        assert self.profile_callback is None and self.accesses == []
        self.observer.update(read_file=self.read_file, read_file_proc=self.read_process_stat)

        def unused_resolver():
            pytest.fail("The observer must not invoke an installed resolver")

        plugins = {Path(adapter.GENERAL_MODULE_FILE).stem: self.observer["activate"]}
        for plugin, (module, method) in self.observer["STOCK_PLUGINS"].items():
            plugins[plugin] = unused_resolver
            self.modules[module] = SimpleNamespace(**{method: unused_resolver})
        startup = SERVICE_ACTIVATION if rank is None else WORKER_ACTIVATION
        self.plugin_frame = self.frame(PLUGIN_LOADER, "load_general_plugins",
                                       parent=self.frame(*startup), plugins=plugins)
        self.anchor = (self.frame(adapter.SERVICE, "run_server") if rank is None
                       else self.frame(adapter.LOADER, "DefaultModelLoader.load_weights", model=self.model_type()))

    def module_digest(self, name):
        return sha256_hex(self.files[module_file(name)])

    def frame(self, module, function, *, parent=None, **locals_):
        code = SimpleNamespace(co_qualname=function, co_filename=str(module_file(module)))
        return SimpleNamespace(f_globals=vars(self.modules[module]), f_code=code, f_back=parent, f_locals=locals_)

    def set_profile(self, callback):
        self.profile_callback = callback

    def statvfs(self, path):
        self.accesses.append(("mount", Path(path)))
        return SimpleNamespace(f_flag=os.ST_RDONLY)

    def read_file(self, path, limit):
        self.accesses.append(("read", Path(path)))
        value = self.files[Path(path)]
        assert 0 < len(value) <= limit
        return value

    def read_process_stat(self, path):
        assert path == "/proc/self/stat"
        return b"1 (fixture owner) " + b" ".join([b"S", *([b"0"] * 18), str(100 + self.pid).encode()])

    @property
    def receipt(self):
        name = "service.json" if self.rank is None else f"worker-{self.rank}.json"
        return self.directory / name

    def open_receipt(self, path, flags, mode):
        assert Path(path) == DESTINATION / self.receipt.name
        assert self.profile_callback is None
        return os.open(self.receipt, flags, mode)


def test_fixed_observer_records_are_accepted_by_the_product(tmp_path):
    processes = [Process(tmp_path, rank) for rank in (None, 0, 1)]
    for process in processes:
        accepted = process.plugin_frame
        process.plugin_frame = process.frame(PLUGIN_LOADER, "load_general_plugins",
                                             parent=process.frame(PLUGIN_LOADER, "unknown_startup"))
        process.observer["activate"]()
        assert process.profile_callback is None and process.accesses == []
        process.plugin_frame = accepted
        process.observer["activate"]()
        assert process.profile_callback is not None and not process.receipt.exists()
        if process.rank is not None:
            process.paths[:] = WORKER_ANCHOR_PATH_ENTRIES
        process.profile_callback(process.anchor, "call", None)
        assert process.profile_callback is None

    rows = [json.loads(process.receipt.read_bytes()) for process in processes]
    profile = processes[0].profile
    profile_digest = sha256_hex(canonical_json(profile))
    readback = {"profile_sha256": profile_digest, "producer_sha256": sha256_hex(ASSET.read_bytes()),
                "plugin_metadata": {name: sha256_hex(raw) for name, raw in adapter.GENERAL_METADATA.items()},
                "processes": rows}
    assert consumer._processes(readback, profile, profile_digest) == rows
    paths = rows[1]["ordinary_import_path"]
    paths["activation"], paths["anchor"] = paths["anchor"], paths["activation"]
    with pytest.raises(ValueError, match="SUP11_STOCK_MODE_PROOF_UNAVAILABLE"):
        consumer._processes(readback, profile, profile_digest)


@pytest.mark.parametrize("failure", ["digest", "expiry", "path"])
def test_fixed_observer_stops_after_unverified_or_expired_startup(tmp_path, failure):
    process = Process(tmp_path, rank=0)
    process.observer["activate"]()
    assert process.profile_callback is not None
    if failure == "digest":
        process.files[module_file(adapter.LOADER)] = b"changed loader bytes"
        frame = process.anchor
    elif failure == "path":
        process.paths.append("/fixture/unreviewed")
        frame = process.anchor
    else:
        process.now = process.observer["PROFILE_SECONDS"]
        frame = process.frame(PLUGIN_LOADER, "unrelated")
    process.profile_callback(frame, "call", None)
    assert process.profile_callback is None and not process.receipt.exists()
