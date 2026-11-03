"""Offline SGLang overlay checks; no Secret value is part of the repository."""

from pathlib import Path
import json
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class SGLangDeploymentTests(unittest.TestCase):
    def test_rendered_engine_starts_without_shell_or_key_argument(self) -> None:
        if shutil.which("kubectl") is None:
            self.skipTest("kubectl kustomize unavailable")
        result = subprocess.run(["kubectl", "kustomize", str(ROOT / "deploy/gpu-lab-sglang")],
                                capture_output=True, text=True, check=True)
        documents = list(yaml.safe_load_all(result.stdout))
        engine = next(document for document in documents if document.get("kind") == "Deployment"
                      and document.get("metadata", {}).get("name") == "serving-engine")
        spec = next(container for container in engine["spec"]["template"]["spec"]["containers"]
                    if container["name"] == "engine")
        self.assertEqual(spec["command"], ["python3", "-m", "sglang.launch_server"])
        self.assertEqual(spec["args"][:2], ["--config", "/run/sglang-admin/config.yaml"])
        self.assertEqual(spec["args"][spec["args"].index("--context-length") + 1], "2112")
        self.assertNotIn("/bin/sh", str(spec["command"] + spec["args"]))
        self.assertNotIn("admin-api-key", str(spec["command"] + spec["args"]))
        self.assertNotIn("/bin/sh", yaml.safe_dump(engine))
        self.assertNotIn("admin-api-key", yaml.safe_dump(engine))
        self.assertEqual(next(volume for volume in engine["spec"]["template"]["spec"]["volumes"]
                              if volume["name"] == "admin-key")["secret"]["secretName"],
                         "serving-sglang-admin")
        gateway = next(document for document in documents if document.get("kind") == "Deployment"
                       and document.get("metadata", {}).get("name") == "serving-gateway")
        self.assertIn("--observation-timeline", gateway["spec"]["template"]["spec"]["containers"][0]["args"])

    def test_sglang_intent_uses_observed_python_binary(self) -> None:
        intent = json.loads((ROOT / "deploy/gpu-lab-sglang/intent.aws-lab.json").read_text(encoding="utf-8"))
        binaries = intent["allowed_engine_binaries"] + [
            binary for phase in intent["phase_binaries"].values() for binary in phase
        ]
        self.assertIn("/opt/sglang/bin/python3", binaries)
        self.assertNotIn("/usr/bin/python3", binaries)
        self.assertNotIn("/usr/bin/python3.12", binaries)
