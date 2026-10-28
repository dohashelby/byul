"""Prepare a new privacy-reviewed copy of the exact 2026-10-26 AWS handoff.

Does not run kubectl, install software, access AWS or change source inputs.
The source manifest and smoke script must match the reviewed bytes exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

MANIFEST_SHA = "7f5d2b8770c746502867565064600562e605cad02edfe11db33e178640f496ab"
SMOKE_SHA = "9aa40665d33b1e4cb5caeb495bcb6e2fd004abc06cba6ebe3455d4377a1d556b"


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError("UNREVIEWED_SMOKE_ANCHOR")
    return source.replace(before, after, 1)


def replace_between(source, begin, end, replacement):
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError("UNREVIEWED_SMOKE_BLOCK")
    start = source.index(begin)
    stop = source.index(end, start) + len(end)
    return source[:start] + replacement + source[stop:]


def patch_smoke(source):
    h = "python3 aws_smoke_privacy.py"
    source = replace_once(source, "source ./profile.sh\n", "source ./profile.sh\nexec 2>/dev/null\nkubectl() { command kubectl --context=byul-aws-lab \"$@\"; }\nRUN_ID=guard-$(date -u +%Y%m%d%H%M%S)-$$\nexport RUN_ID\nACCESS_STARTED=false\n")
    source = replace_once(source, 'mkdir -p "$EVIDENCE"', 'mkdir -p evidence\nmkdir "$EVIDENCE"')
    source = replace_once(source, 'mkdir "$EVIDENCE"\n', '''mkdir "$EVIDENCE"
trap 'code=$?; printf "{\\"schema\\":\\"byul-bootstrap-failure/1\\",\\"script_line\\":%s,\\"exit_code\\":%s}\\n" "$LINENO" "$code" > "$EVIDENCE/first-failure.json"' ERR
''')
    source = replace_between(source, "kubectl -n serving-lab get deployments,networkpolicies,", "python3 check_baseline.py \"$EVIDENCE/baseline-namespace.json\" \"$EVIDENCE/baseline-cluster-policies.json\"",
        f'{h} capture --kind baseline --output "$EVIDENCE/baseline.json"')
    source = replace_between(source, 'kubectl get pods -A -o json > "$EVIDENCE/preflight-pods.json"', "\nGUARD",
        f'{h} capture --kind preflight --output "$EVIDENCE/preflight-pods.json"')
    source = replace_between(source, 'python3 - "$EVIDENCE/environment-profile.json"', "\nPROFILE",
        f'{h} capture --kind profile --output "$EVIDENCE/environment-profile.json"')
    source = replace_between(source, "collect_failure() {", "trap collect_failure EXIT", f'''collect_failure() {{
  status=$?
  trap - EXIT
  {h} capture --kind final-pods --output "$EVIDENCE/final-pods.json" || true
  {h} capture --kind events --output "$EVIDENCE/events.json" || true
  {h} logs --target deployment/serving-engine --kind opaque --output "$EVIDENCE/engine-final-summary.json" || true
  if $ACCESS_STARTED; then
    if ! {h} revoke --run-id "$RUN_ID" --output "$EVIDENCE/access-cleanup-final.json"; then
      echo 'OWNED_TEMPORARY_ACCESS_REVOKE_UNVERIFIED' >&2
      status=1
    fi
  fi
  exit "$status"
}}
trap collect_failure EXIT''')
    source = replace_once(source, "kubectl delete job -n serving-lab gpu-smoke engine-smoke engine-denied --ignore-not-found --wait=true\n", "")
    source = replace_once(source, "    digest_image docker.io/curlimages/curl:latest; echo\n", "    digest_image docker.io/curlimages/curl:latest; echo\n    digest_image docker.io/library/python:3.12-slim; echo\n")
    source = replace_once(source, "[[ ${#locked_images[@]} -eq 3 ]]", "[[ ${#locked_images[@]} -eq 4 ]]")
    source = replace_once(source, "export ENGINE_IMAGE CUDA_IMAGE CURL_IMAGE", "PYTHON_IMAGE=${locked_images[3]}\nexport ENGINE_IMAGE CUDA_IMAGE CURL_IMAGE PYTHON_IMAGE")
    source = replace_once(source, '"$ENGINE_IMAGE" "$CUDA_IMAGE" "$CURL_IMAGE" >', '"$ENGINE_IMAGE" "$CUDA_IMAGE" "$CURL_IMAGE" "$PYTHON_IMAGE" >')
    source = replace_once(source, "metadata: {name: gpu-smoke, namespace: serving-lab}", "metadata: {name: gpu-smoke, namespace: serving-lab, labels: {guard.run: $RUN_ID}}")
    source = replace_once(source, "  backoffLimit: 0\n  template:\n    spec:", "  backoffLimit: 0\n  ttlSecondsAfterFinished: 300\n  activeDeadlineSeconds: 300\n  template:\n    metadata:\n      labels: {guard.run: $RUN_ID}\n    spec:")
    source = replace_once(source, 'kubectl logs -n serving-lab job/gpu-smoke | tee "$EVIDENCE"/gpu-compute.txt', f'{h} logs --target job/gpu-smoke --kind gpu --output "$EVIDENCE/gpu-compute.json"')
    source = replace_once(source, 'kubectl get pods -n serving-lab -l job-name=gpu-smoke -o json > "$EVIDENCE/gpu-pods.json"', f'{h} capture --kind gpu-pods --output "$EVIDENCE/gpu-pods.json"')
    source = replace_once(source, "grep -q 'Test PASSED' \"$EVIDENCE\"/gpu-compute.txt\n", "")
    source = replace_once(source, "kubectl delete -n serving-lab job/gpu-smoke --wait=true", f'{h} finish-job --target gpu-smoke --run-id "$RUN_ID" --output "$EVIDENCE/gpu-cleanup.json"')
    source = replace_once(source, "'ENGINE_MEMORY_REQUEST','ENGINE_MEMORY_LIMIT']:", "'ENGINE_MEMORY_REQUEST','ENGINE_MEMORY_LIMIT','RUN_ID']:")
    source = replace_between(source, 'kubectl get pods -n serving-lab -l app=serving-engine -o json >', "\nPODCHECK", f'{h} capture --kind engine-ready --output "$EVIDENCE/engine-ready-pods.json"')
    source = replace_once(source, "kubectl apply -f engine-test-access.yaml", f'{h} render-policy --run-id "$RUN_ID" --output engine-test-access.generated.json\nACCESS_STARTED=true\nkubectl apply -f engine-test-access.generated.json')
    source = replace_between(source, "cat > engine-smoke.yaml <<EOF", "\nEOF\nkubectl apply -f engine-smoke.yaml",
        f'{h} render-client --image "$PYTHON_IMAGE" --engine-ip "$ENGINE_IP" --run-id "$RUN_ID" --output engine-smoke.yaml\nkubectl apply -f engine-smoke.yaml')
    source = replace_once(source, 'kubectl logs -n serving-lab job/engine-smoke | tee "$EVIDENCE"/engine-responses.txt', f'{h} logs --target job/engine-smoke --kind normal --output "$EVIDENCE/engine-response-validation.json"')
    source = replace_once(source, 'kubectl get pods -n serving-lab -o json > "$EVIDENCE"/workloads.json', f'{h} capture --kind workloads --output "$EVIDENCE/workloads.json"')
    source = replace_once(source, 'kubectl get deployments,services,networkpolicies -n serving-lab -o yaml > "$EVIDENCE"/workload-config.yaml', f'{h} capture --kind workload-config --output "$EVIDENCE/workload-config.json"')
    source = replace_once(source, 'nvidia-smi > "$EVIDENCE"/nvidia-smi.txt', f'{h} capture --kind gpu-info --output "$EVIDENCE/gpu-info.json"')
    source = replace_once(source, 'kubectl logs -n serving-lab deployment/serving-engine > "$EVIDENCE"/engine.log', f'{h} logs --target deployment/serving-engine --kind opaque --output "$EVIDENCE/engine-log-summary.json"')
    source = replace_once(source, 'python3 validate_responses.py "$EVIDENCE/engine-responses.txt"\n', "")
    source = replace_once(source, "kubectl delete -f engine-test-access.yaml --ignore-not-found", f'{h} revoke --run-id "$RUN_ID" --output "$EVIDENCE/access-revoked.json"')
    source = replace_between(source, "python3 - <<'DENY'", "\nDENY", f'{h} render-denied --image "$CURL_IMAGE" --engine-ip "$ENGINE_IP" --run-id "$RUN_ID" --output engine-denied.yaml')
    source = replace_once(source, 'kubectl logs -n serving-lab job/engine-denied | tee "$EVIDENCE/direct-denied.txt"', f'{h} logs --target job/engine-denied --kind denied --output "$EVIDENCE/direct-denied.json"')
    source = replace_once(source, 'kubectl get networkpolicies -n serving-lab -o yaml > "$EVIDENCE/final-networkpolicies.yaml"', f'{h} capture --kind networkpolicies --output "$EVIDENCE/final-networkpolicies.json"')
    source = replace_once(source, 'kubectl get pods -n serving-lab -l job-name=engine-denied -o json > "$EVIDENCE/denied-pods.json"', f'{h} capture --kind denied-pods --output "$EVIDENCE/denied-pods.json"')
    source = replace_once(source, 'cp engine.yaml gpu-smoke.yaml engine-smoke.yaml engine-denied.yaml "$EVIDENCE/"', f'{h} manifest-hashes --output "$EVIDENCE/rendered-file-hashes.json" -- engine.yaml gpu-smoke.yaml engine-smoke.yaml engine-denied.yaml engine-test-access.generated.json')
    for job in ("engine-denied", "engine-smoke"):
        source = replace_once(source, f"kubectl delete -n serving-lab job/{job} --wait=true", f'{h} finish-job --target {job} --run-id "$RUN_ID" --output "$EVIDENCE/{job}-cleanup.json"')
    if "kubectl delete" in source or "engine-responses.txt" in source or " | tee " in source:
        raise ValueError("RAW_SINK_OR_UNBOUND_CLEANUP_REMAINS")
    # The first-bootstrap guard requires absent resources. Create is atomic and
    # cannot overwrite a resource created after that guard by another owner.
    source = source.replace("kubectl apply -f", "kubectl create -f")
    return source


def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    repo = Path(__file__).resolve().parents[2]
    if destination.exists() or source == destination or source in destination.parents or destination in source.parents or repo in destination.parents:
        raise ValueError("NEW_EXTERNAL_DESTINATION_REQUIRED")
    manifest = source / "SHA256SUMS"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA:
        raise ValueError("UNREVIEWED_HANDOFF_MANIFEST")
    files = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        sha, name = line.split(maxsplit=1)
        if Path(name).name != name or (source / name).is_symlink() or hashlib.sha256((source / name).read_bytes()).hexdigest() != sha:
            raise ValueError("HANDOFF_FILE_MISMATCH")
        files[name] = sha
    if files.get("03-engine-smoke.sh") != SMOKE_SHA:
        raise ValueError("UNREVIEWED_HANDOFF_SMOKE")
    patched = patch_smoke((source / "03-engine-smoke.sh").read_text(encoding="utf-8"))
    template = replace_once((source / "engine.yaml.template").read_text(encoding="utf-8"), "        lab-profile: __LAB_PROFILE__", "        lab-profile: __LAB_PROFILE__\n        guard.run: __RUN_ID__")
    destination.mkdir(parents=True)
    for name in [n for n in [*files, "SHA256SUMS"] if n not in {"03-engine-smoke.sh", "engine.yaml.template"}]:
        shutil.copy2(source / name, destination / name)
    (destination / "03-engine-smoke.sh").write_text(patched, encoding="utf-8", newline="\n")
    (destination / "engine.yaml.template").write_text(template, encoding="utf-8", newline="\n")
    for name in ("aws_smoke_client.py", "aws_smoke_privacy.py"):
        shutil.copy2(Path(__file__).with_name(name), destination / name)
    shutil.copy2(Path(__file__).resolve().parents[1] / "src/guard/runtime/bounded.py", destination / "bounded_capture.py")
    record = {"schema": "byul-reviewed-handoff/1", "original_manifest_sha256": MANIFEST_SHA, "original_files": files,
        "prepared_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(destination.iterdir()) if p.is_file()},
        "source_read_only": True, "actual_installation": False, "actual_GPU_trial": False,
        "remaining_launch_gates": ["applied GPU quota", "AMI and fixed install release availability", "all-in cost/budget", "owned dedicated fresh host"],
        "cleanup": "UID/resourceVersion/guard.run JSON Patch tests; completed Jobs via TTL controller; temporary policy retained with empty ingress"}
    with (destination / "REVIEWED-CHANGES.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2); stream.write("\n")
    with (destination / "REVIEWED-SHA256SUMS").open("x", encoding="utf-8", newline="\n") as stream:
        for path in sorted(destination.iterdir()):
            if path.is_file() and path.name != "REVIEWED-SHA256SUMS":
                stream.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.output)
        print(json.dumps({"prepared_files": len(result["prepared_files"]), "actual_installation": False}))
        return 0
    except Exception:
        print("HANDOFF_PREPARATION_UNAVAILABLE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
