"""Parse deployment examples and guard collector permissions/start ordering."""
import configparser
from pathlib import Path
import shlex
import unittest

from serving_runtime.operations.agent import Config

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "agent"


def unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(ROOT / f"serving-runtime-{name}.service")
    return parser


class AgentServicesTest(unittest.TestCase):
    def test_collectors_are_unprivileged_with_read_only_inputs(self):
        for name, module in (("tetragon-status", "tools.tetragon_status"),
                             ("monitor-window", "tools.tetragon_monitor_window")):
            with self.subTest(name=name):
                service = unit(name)["Service"]
                self.assertEqual(service["User"], "runtime-agent")
                self.assertEqual(service["SupplementaryGroups"].split(), ["10000", "10010"])
                self.assertEqual(service["UMask"], "0022")
                self.assertEqual(service["Environment"], "KUBECONFIG=/etc/serving-runtime/kubeconfig")
                self.assertEqual(service["WorkingDirectory"], "/opt/byul/runtime")
                self.assertEqual(service["ProtectSystem"], "strict")
                for flag in ("ProtectHome", "PrivateTmp", "NoNewPrivileges"):
                    self.assertEqual(service[flag], "true")
                self.assertEqual(service["ReadWritePaths"], "/var/lib/serving-runtime")
                self.assertEqual(service["Restart"], "on-failure")
                command = shlex.split(service["ExecStart"])
                self.assertEqual(command[1:4], ["-B", "-m", module])
                self.assertIn("--watch", command)
                self.assertNotIn("--capture-baseline", command)
        command = shlex.split(unit("monitor-window")["Service"]["ExecStart"])
        self.assertEqual(command[command.index("--trusted-owner-uid") + 1], "10000")

    def test_agent_ordering_does_not_weaken_production_defaults(self):
        agent = unit("agent")
        for collector in ("tetragon-status", "monitor-window"):
            name = f"serving-runtime-{collector}.service"
            for relation in ("After", "Wants"):
                self.assertIn(name, agent["Unit"][relation].split())
        monitor = unit("monitor-window")["Unit"]
        for relation in ("After", "Wants"):
            self.assertIn("serving-runtime-tetragon-status.service", monitor[relation].split())
        self.assertEqual(Config.monitor_seconds, 86400)
        self.assertFalse(Config.lab_self_approval)
        for text in (agent["Service"]["ExecStart"], (ROOT / "sudoers.example").read_text()):
            self.assertNotIn("--monitor-seconds", text)
            self.assertNotIn("--lab-self-approval", text)
