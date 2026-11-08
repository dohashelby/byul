"""Installer boundaries with temporary files and fake host state; no host mutation."""
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/install/install-runtime-node.sh"
spec = importlib.util.spec_from_file_location("install_runtime_node", SCRIPT.with_name("install_runtime_node.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallRuntimeNodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = {}
        for name in ("token", "viewer", "kubeconfig"):
            path = self.root / name
            path.write_text("private-value-never-print\n")
            path.chmod(0o600)
            self.inputs[name] = path
        self.config = self.root / "install.env"
        self.text = ("CONTEXT=cluster.test\nNAMESPACE=serving-test\nSERVER_ID=node-test\n"
                     "PLATFORM_URL=https://private-platform.invalid\n"
                     f"AGENT_TOKEN_FILE={self.inputs['token']}\n"
                     f"VIEWER_ENV_FILE={self.inputs['viewer']}\n"
                     f"KUBECONFIG_FILE={self.inputs['kubeconfig']}\n")
        self.write_config(self.text)

    def write_config(self, text):
        self.config.write_text(text)
        self.config.chmod(0o600)

    def invoke(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = installer.main(["--config", str(self.config), *args])
        for value in ("private-value-never-print", "private-platform.invalid", "cluster.test", "node-test"):
            self.assertNotIn(value, output.getvalue())
        return code, output.getvalue()

    def test_wrapper_dry_run_rejects_shell_config_without_executing_it(self):
        marker = self.root / "executed"
        self.write_config(self.text.replace("cluster.test", f"$(touch {marker})"))
        result = subprocess.run(["bash", str(SCRIPT), "--config", str(self.config), "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(marker.exists())
        self.assertNotIn(str(marker), result.stdout + result.stderr)
        self.assertNotIn("private-value", result.stdout + result.stderr)

    def test_config_rejects_duplicate_unknown_injection_and_remote_http(self):
        for text in (self.text + "SERVER_ID=duplicate\n", self.text + "UNEXPECTED=bad\n",
                     self.text.replace("cluster.test", "x;id"),
                     self.text.replace("https://", "http://"),
                     self.text.replace("cluster.test", "%h")):
            with self.subTest(text=text):
                self.write_config(text)
                self.assertEqual(self.invoke("--dry-run")[0], 1)

    def test_private_permissions_and_symlink_input_rejected(self):
        self.inputs["token"].chmod(0o640)
        self.assertEqual(self.invoke("--dry-run")[0], 1)
        self.inputs["token"].chmod(0o600)
        link = self.root / "linked"
        link.symlink_to(self.inputs["token"])
        self.write_config(self.text.replace(str(self.inputs["token"]), str(link)))
        self.assertEqual(self.invoke("--dry-run")[0], 1)

    def test_sudo_accepts_operator_owned_inputs_but_not_another_owner(self):
        operator = os.getuid()
        with patch.object(installer.os, "getuid", return_value=0), \
                patch.object(installer.os, "geteuid", return_value=0), \
                patch.dict(installer.os.environ, {"SUDO_UID": str(operator)}):
            self.assertEqual(installer.load_config(self.config)["SERVER_ID"], "node-test")
            if operator != 0:  # root-owned inputs are always trusted
                with patch.dict(installer.os.environ, {"SUDO_UID": str(operator + 1)}):
                    with self.assertRaises(installer.InstallError):
                        installer.load_config(self.config)

    def test_render_preserves_sudo_scope_and_safe_agent_defaults(self):
        config = installer.load_config(self.config)
        config.update(GATEWAY_DATA_GID="12000", OBSERVE_GID="12010")
        result = installer.render(config)
        for name in installer.UNITS:
            unit = result[name].decode()
            self.assertIn("SupplementaryGroups=12000 12010", unit)
            self.assertIn("--context cluster.test", unit)
            self.assertNotIn("lab-context", unit)
            self.assertNotIn("--namespace serving ", unit)
        agent = result["serving-runtime-agent.service"].decode()
        self.assertIn("--server node-test", agent)
        self.assertIn("--namespace serving-test", agent)
        self.assertIn("--dry-run", agent)
        self.assertNotIn("--execute", agent)
        command = next(line for line in agent.splitlines() if line.startswith("ExecStart="))
        self.assertNotIn("--ca-file /etc", command)
        self.assertNotIn("--lab-self-approval", agent)
        sudoers = result["sudoers"].decode()
        self.assertIn(r"--context cluster\\.test --namespace serving-test", sudoers)
        self.assertIn("--operation-result$", sudoers)
        self.assertIn("--trusted-owner-uid 10000", result["serving-runtime-monitor-window.service"].decode())
        config["PLATFORM_CA_FILE"] = "/private/ca.pem"
        execute = installer.render(config, execute=True)["serving-runtime-agent.service"].decode()
        self.assertIn("--execute", execute)
        self.assertNotIn(" --dry-run", execute)
        self.assertIn("--ca-file /etc/serving-runtime/platform-ca.pem", execute)

    @unittest.skipUnless(shutil.which("visudo", path=installer.PATH) and
                         shutil.which("systemd-analyze", path=installer.PATH), "local validators unavailable")
    def test_rendered_sudoers_and_units_pass_local_syntax_validation(self):
        config = installer.load_config(self.config)
        config["PLATFORM_CA_FILE"] = "/private/ca.pem"
        artifacts = installer.render(config, execute=True)
        sudoers = self.root / "sudoers"
        sudoers.write_bytes(artifacts["sudoers"])
        result = subprocess.run(["visudo", "-cf", str(sudoers)], capture_output=True, env={"PATH": installer.PATH})
        self.assertEqual(result.returncode, 0, "rendered sudoers validation failed")
        for name in installer.UNITS:
            (self.root / name).write_bytes(artifacts[name].replace(
                b"ExecStart=/opt/byul/runtime/.venv/bin/python", b"ExecStart=/usr/bin/python3"))
        result = subprocess.run(["systemd-analyze", "verify", *[str(self.root / n) for n in installer.UNITS]],
                                capture_output=True, env={"PATH": installer.PATH})
        self.assertEqual(result.returncode, 0, "rendered unit validation failed")

    def test_dry_run_never_calls_install_or_external_commands(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(installer, "preflight", return_value=None) as preflight, \
                patch.object(installer, "install") as install, \
                patch.object(installer.subprocess, "run") as external:
            code, output = self.invoke("--dry-run", "--execute", "--capture-baseline")
        self.assertEqual(code, 0)
        self.assertIn("무변경", output)
        self.assertIn("agent 모드: execute", output)
        preflight.assert_called_once()
        install.assert_not_called()
        external.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_missing_crictl_stops_before_mutation(self):
        with patch.object(installer.shutil, "which", side_effect=lambda name, **kw: None if name == "crictl" else "/tool"), \
                patch.object(installer, "install") as install:
            code, output = self.invoke("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("crictl", output)
        self.assertIn("수동 설치", output)
        install.assert_not_called()

    def test_complete_dry_run_preflight_has_no_commands_or_writes(self):
        # Exercise the real preflight while supplying a hypothetical prepared host.
        account = SimpleNamespace(pw_uid=999, pw_gid=999)
        with patch.object(installer.shutil, "which", return_value="/tool"), \
                patch.object(installer, "group_check"), \
                patch.object(installer.pwd, "getpwnam", return_value=account), \
                patch.object(installer.grp, "getgrgid", return_value=SimpleNamespace(gr_name="runtime-agent")), \
                patch.object(installer, "secure_target"), \
                patch.object(installer, "BASELINE", self.root / "missing-baseline"), \
                patch.object(installer, "bpf_plan", return_value=None), \
                patch.object(installer.subprocess, "run") as external, \
                patch.object(installer, "write_file") as write:
            self.assertEqual(self.invoke("--dry-run", "--capture-baseline")[0], 0)
        external.assert_not_called()
        write.assert_not_called()

    def bpf_files(self, lsms="lockdown,apparmor"):
        lsm = self.root / "lsm"
        lsm.write_text(lsms)
        kernel = self.root / "kernel"
        kernel.write_text("CONFIG_BPF_LSM=y\n")
        grub = self.root / "grub.d/99-byul-bpf-lsm.cfg"
        grub.parent.mkdir()
        defaults = self.root / "grub"
        defaults.write_text('GRUB_CMDLINE_LINUX=""\n')
        for key, value in (("LSM_FILE", lsm), ("KERNEL_CONFIG", kernel),
                           ("GRUB", grub), ("GRUB_DEFAULT", defaults)):
            patcher = patch.object(installer, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return grub, defaults

    def test_bpf_requires_explicit_opt_in_and_preserves_order(self):
        grub, defaults = self.bpf_files()
        with self.assertRaises(installer.InstallError):
            installer.bpf_plan(False)
        with patch.object(installer.shutil, "which", return_value="/tool"):
            content = installer.bpf_plan(True)
            self.assertIn(b"lsm=lockdown,apparmor,bpf", content)
            self.assertFalse(grub.exists())
            grub.write_bytes(content)
            self.assertEqual(installer.bpf_plan(True), content)
            defaults.write_text('GRUB_CMDLINE_LINUX="lsm=apparmor"\n')
            with self.assertRaises(installer.InstallError):
                installer.bpf_plan(True)

    def test_already_active_bpf_does_not_rewrite_grub(self):
        grub, _ = self.bpf_files("lockdown,bpf,apparmor")
        self.assertIsNone(installer.bpf_plan(False))
        self.assertFalse(grub.exists())

    def test_group_collision_rejected(self):
        with patch.object(installer.grp, "getgrgid", return_value=SimpleNamespace(gr_name="other")):
            with self.assertRaises(installer.InstallError):
                installer.group_check("serving-observe", 10010)

    def baseline(self, mode="monitor"):
        path = self.root / "baseline.json"
        path.write_text(json.dumps({"schema_version": "runtime-tetragon-baseline/1.0",
                                   "context": "cluster.test",
                                   "policies": [{"node": "test-node", "namespace": "serving-test",
                                                 "name": "test-policy", "mode": mode, "spec_sha256": "a" * 64}]}))
        path.chmod(0o644)
        return path

    def test_existing_baseline_preserved_and_wrong_mode_rejected(self):
        path = self.baseline()
        config = installer.load_config(self.config)
        with patch.object(installer, "BASELINE", path), \
                patch.object(installer.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid())), \
                patch.object(installer, "run") as run, redirect_stdout(io.StringIO()):
            before = path.stat().st_mtime_ns
            installer.capture_baseline(config, True)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(run.call_count, 1)  # only account readability check
            self.baseline("enforce")
            with self.assertRaises(installer.InstallError):
                installer.capture_baseline(config, True)

    def test_capture_uses_agent_umask_and_accepts_only_verified_candidate(self):
        config = installer.load_config(self.config)
        baseline = self.root / "new-baseline.json"
        calls = []
        def run(command):
            calls.append(command)
            if "--capture-baseline" in command:
                candidate = command[-1]
                candidate.write_bytes(self.baseline("monitor_only").read_bytes())
                candidate.chmod(0o644)
        with patch.object(installer, "STATE", self.root), patch.object(installer, "BASELINE", baseline), \
                patch.object(installer.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid())), \
                patch.object(installer, "run", side_effect=run):
            installer.capture_baseline(config, True)
        self.assertTrue(baseline.exists())
        self.assertIn("runtime-agent", calls[0])
        self.assertTrue(any("umask 022" in str(v) for v in calls[0]))
        self.assertFalse((self.root / "baseline.capture.json").exists())

    def test_failed_capture_never_installs_a_baseline(self):
        config = installer.load_config(self.config)
        baseline = self.root / "new-baseline.json"
        def run(command):
            command[-1].write_bytes(self.baseline("enforce").read_bytes())
        with patch.object(installer, "STATE", self.root), patch.object(installer, "BASELINE", baseline), \
                patch.object(installer.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid())), \
                patch.object(installer, "run", side_effect=run):
            with self.assertRaises(installer.InstallError):
                installer.capture_baseline(config, True)
        self.assertFalse(baseline.exists())

    def test_command_failure_suppresses_output_and_stops(self):
        with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as external:
            with self.assertRaisesRegex(installer.InstallError, "출력 생략"):
                installer.run(["visudo", "-cf", "/private-input"])
        self.assertEqual(external.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(external.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_atomic_writes_are_idempotent_and_reject_links(self):
        path = self.root / "installed"
        kwargs = dict(uid=os.getuid(), gid=os.getgid(), mode=0o640)
        installer.write_file(path, b"protected", **kwargs)
        before = path.stat().st_mtime_ns
        path.chmod(0o664)
        installer.write_file(path, b"protected", **kwargs)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(path.stat().st_mtime_ns, before)
        link = self.root / "output-link"
        link.symlink_to(path)
        with self.assertRaises(installer.InstallError):
            installer.write_file(link, b"changed", **kwargs)
        self.assertEqual(path.read_bytes(), b"protected")

    def test_visudo_failure_does_not_install_sudoers_or_units(self):
        config = installer.load_config(self.config)
        account = SimpleNamespace(pw_uid=999, pw_gid=999)
        with patch.object(installer, "directory"), patch.object(installer, "write_file") as write, \
                patch.object(installer, "run", side_effect=installer.InstallError("검증 실패")) as run:
            with self.assertRaises(installer.InstallError):
                installer.install_files(config, account, installer.render(config))
        self.assertEqual(run.call_args.args[0][0:2], ["visudo", "-cf"])
        targets = [call.args[0] for call in write.call_args_list]
        self.assertNotIn(Path("/etc/sudoers.d/serving-runtime-agent"), targets)
        self.assertFalse(any(p.parent == Path("/etc/systemd/system") for p in targets))

    def install_mocks(self, stack):
        mocks = {}
        for name in ("install_accounts", "install_code", "install_files", "directory", "run",
                     "write_file", "capture_baseline", "summary"):
            mocks[name] = stack.enter_context(patch.object(installer, name))
        mocks["install_accounts"].return_value = SimpleNamespace(pw_uid=999, pw_gid=999)
        stack.enter_context(patch.object(installer, "install_lock", return_value=nullcontext()))
        stack.enter_context(patch.object(installer, "active", return_value=False))
        stack.enter_context(patch.object(installer.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return mocks

    def test_pending_bpf_reboot_never_captures_or_enables_services(self):
        config = installer.load_config(self.config)
        args = SimpleNamespace(capture_baseline=True)
        with ExitStack() as stack:
            mocks = self.install_mocks(stack)
            result = installer.install(config, args, installer.render(config), b"managed grub")
        self.assertEqual(result, 2)
        mocks["capture_baseline"].assert_not_called()
        mocks["summary"].assert_not_called()
        commands = [call.args[0] for call in mocks["run"].call_args_list]
        self.assertIn(["update-grub"], commands)
        self.assertFalse(any(c[0] == "systemctl" and c[1] in {"enable", "start", "reboot"} for c in commands))

    def test_failed_code_install_stops_all_following_stages(self):
        config = installer.load_config(self.config)
        with ExitStack() as stack:
            mocks = self.install_mocks(stack)
            mocks["install_code"].side_effect = installer.InstallError("파일 검증 실패")
            with self.assertRaises(installer.InstallError):
                installer.install(config, SimpleNamespace(capture_baseline=True), installer.render(config), None)
        mocks["install_files"].assert_not_called()
        mocks["capture_baseline"].assert_not_called()
        mocks["run"].assert_not_called()

    def test_baseline_validation_precedes_ordered_service_start(self):
        config = installer.load_config(self.config)
        events = []
        with ExitStack() as stack:
            mocks = self.install_mocks(stack)
            stack.enter_context(patch.object(installer.time, "sleep"))
            stack.enter_context(patch.object(installer, "active", side_effect=[False] * 6 + [True] * 3))
            mocks["capture_baseline"].side_effect = lambda *args: events.append("baseline-verified")
            mocks["run"].side_effect = lambda cmd: events.append(cmd)
            result = installer.install(config, SimpleNamespace(capture_baseline=True), installer.render(config), None)
        self.assertEqual(result, 0)
        expected = ["baseline-verified"]
        expected += [command for unit in installer.UNITS for command in
                     (["systemctl", "enable", unit], ["systemctl", "is-enabled", "--quiet", unit],
                      ["systemctl", "start", unit])]
        self.assertEqual(events[events.index("baseline-verified"):], expected)
        mocks["summary"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
