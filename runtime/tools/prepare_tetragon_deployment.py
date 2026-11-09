"""Prepare the AWS lab's existing Tetragon renderer and sentinel without cluster access."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.tetragon import render_manifests
from tools.tetragon_trial import atomic_json

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = "app in (serving-engine,byul-tetragon-sentinel)"


def prepare(intent_path: Path, image: str, output: Path):
    if not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("sentinel image requires a reviewed sha256 digest")
    document = json.loads(intent_path.read_text())
    if document.get("namespace") != "serving-lab" or document.get("engine", {}).get("labels", {}).get("app") != "serving-engine":
        raise ValueError("AWS service templates require serving-lab and the app=serving-engine role label")
    document["tetragon_kernel_identity"] = True
    intent = parse_intent(document)
    policies = render_manifests(intent)
    sentinel_intent = copy.deepcopy(document)
    sentinel_intent["engine"]["labels"] = {"app": "byul-tetragon-sentinel"}
    sentinel_intent["protected_paths"]["secret_paths"] = ["/run/byul-runtime-sentinel/read.txt"]
    sentinel_intent["protected_paths"].pop("enforce_exact_files", None)
    sentinel_intent.pop("tetragon_rule_modes", None)
    sentinel_intent.pop("tetragon_enforce_method", None)
    sentinel_intent["tetragon_mode"] = "monitor"
    health = render_manifests(parse_intent(sentinel_intent))["tetragon-tg-secret-01.json"]
    health["metadata"]["name"] = "runtime-tg-health-01"
    health["metadata"]["annotations"]["serving-runtime.local/rule-id"] = "TG-HEALTH-01"
    policies["tetragon-tg-health-01.json"] = health
    sentinel = json.loads((ROOT / "deploy/lab/tetragon-sentinel.json").read_text())
    for container in sentinel["spec"]["template"]["spec"]["initContainers"] + sentinel["spec"]["template"]["spec"]["containers"]:
        container["image"] = image
    names = sorted(policy["metadata"]["name"] for policy in policies.values())
    stream_service = (ROOT / "deploy/gpu-lab/host/byul-tetragon-stream.service").read_text()
    stream_service = re.sub(r"(?: --policy [a-z0-9.-]+)+", "".join(" --policy " + name for name in names), stream_service)
    files = {"intent.json": document, "sentinel.json": sentinel, **policies}
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in [*files, "byul-tetragon-stream.service", "prepared.json"]):
        raise ValueError("prepare into a new output directory to preserve the prior reviewed artifacts")
    for name, value in files.items():
        atomic_json(output / name, value)
    (output / "byul-tetragon-stream.service").write_text(stream_service)
    record = {"state": "prepared-unapplied", "context": "byul-aws-lab", "namespace": intent.namespace,
              "identity_selector": SELECTOR, "policies": names,
              "sentinel_image": image,
              "sha256": {name: hashlib.sha256((output/name).read_bytes()).hexdigest() for name in [*files, "byul-tetragon-stream.service"]}}
    atomic_json(output / "prepared.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--sentinel-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = prepare(args.intent, args.sentinel_image, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
