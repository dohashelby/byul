"""Read scoped procfs identities and inode-verified argv0 paths, never arguments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from serving_runtime.enforcement.attribution import KERNEL_DATA, SCHEMA
from serving_runtime.enforcement.tetragon import comparable_spec
from tools.tetragon_trial import atomic_json, run

# The process list is restricted by Kubernetes container IDs before any exe
# metadata is read. Only argv0 and PATH are used locally to verify an alias;
# neither arguments nor environment values are returned in the snapshot.
# Re-read start time, cgroup, exe and file identity to fence exit/exec races.
PROC_SCAN = r'''
root=/procRoot
allowed="$*"
hz=$(getconf CLK_TCK) || exit 3
# Resolve symlinks inside the target's root, including absolute symlink targets.
# Never execute a binary from that root or evaluate argv0/PATH as shell code.
resolve_path() {
  pending=$1; resolved=; links=0; parts=0
  while [ -n "$pending" ]; do
    parts=$((parts+1)); [ "$parts" -le 256 ] || return 1
    part=${pending%%/*}
    case "$pending" in */*) pending=${pending#*/};; *) pending=;; esac
    case "$part" in ''|.) continue;; ..) resolved=${resolved%/*}; continue;; esac
    if target=$(readlink "$dir/root$resolved/$part" 2>/dev/null); then
      links=$((links+1)); [ "$links" -le 40 ] || return 1
      case "$target" in /*) resolved=;; esac
      pending=$target${pending:+/$pending}
    else
      resolved=$resolved/$part
    fi
  done
  printf '%s' "${resolved:-/}"
}
verify_alias() {
  case "$1" in /*) ;; *) return 1;; esac
  case "$1" in *[!a-zA-Z0-9_./+-]*) return 1;; esac
  resolved_path=$(resolve_path "$1") || return 1
  candidate_id=$(stat -L -c '%d:%i' "$dir/root$resolved_path" 2>/dev/null) || return 1
  [ "$candidate_id" = "$file_id" ] || return 1
  alias=$1
}
count=0
for dir in "$root"/[0-9]*; do
  [ -r "$dir/cgroup" ] && [ -r "$dir/stat" ] || continue
  cg=$(cat "$dir/cgroup" 2>/dev/null) || continue
  matched=
  for cid in $allowed; do
    case "$cg" in
      *"/$cid"|*"/$cid/"*|*"cri-containerd-$cid.scope"|*"cri-containerd-$cid.scope/"*) matched=$cid;;
    esac
  done
  [ -n "$matched" ] || continue
  IFS= read -r before < "$dir/stat" || continue
  rest=${before##*) }
  set -- $rest
  [ "$#" -ge 20 ] || continue
  shift 19
  ticks=$1
  ns=$(readlink "$dir/ns/pid" 2>/dev/null) || continue
  exe=$(readlink "$dir/exe" 2>/dev/null) || continue
  file_id=$(stat -L -c '%d:%i' "$dir/exe" 2>/dev/null) || continue
  alias=
  argv0=$(awk 'BEGIN { RS="\0" } NR==1 { printf "%s", $0; exit }' "$dir/cmdline" 2>/dev/null)
  case "$argv0" in
    /*) verify_alias "$argv0" || :;;
    */*|'') :;;
    *)
      search_path=$(awk 'BEGIN { RS="\0" } /^PATH=/ { printf "%s", substr($0,6); exit }' "$dir/environ" 2>/dev/null)
      while [ -n "$search_path" ]; do
        entry=${search_path%%:*}
        case "$search_path" in *:*) search_path=${search_path#*:};; *) search_path=;; esac
        case "$entry" in /*) verify_alias "$entry/$argv0" && break;; esac
      done;;
  esac
  npid=
  while IFS= read -r line; do
    case "$line" in NSpid:*) set -- $line; for item do npid=$item; done;; esac
  done < "$dir/status"
  [ -n "$npid" ] || continue
  IFS= read -r after < "$dir/stat" || continue
  rest=${after##*) }; set -- $rest; [ "$#" -ge 20 ] || continue; shift 19
  [ "$ticks" = "$1" ] || continue
  [ "$cg" = "$(cat "$dir/cgroup" 2>/dev/null)" ] || continue
  [ "$exe" = "$(readlink "$dir/exe" 2>/dev/null)" ] || continue
  [ "$file_id" = "$(stat -L -c '%d:%i' "$dir/exe" 2>/dev/null)" ] || continue
  if [ -n "$alias" ]; then
    sampled_alias=$alias; alias=
    verify_alias "$sampled_alias" || :
  fi
  printf '%s\000%s\000%s\000%s\000%s\000%s\000%s\000%s\000%s\000' "$matched" "${dir##*/}" "$ns" "$ticks" "$npid" "$exe" "$hz" "$file_id" "$alias"
  count=$((count+1)); [ "$count" -lt 10000 ] || exit 4
done
'''


class IdentityCollector:
    def __init__(self, context, namespace, selector, output, *, runner=run):
        self.context, self.namespace, self.selector = context, namespace, selector
        self.output, self.run = Path(output), runner

    def kube(self, *args, namespace=None):
        result = self.run(["kubectl", "--context", self.context, "-n", namespace or self.namespace, *args])
        if result.returncode:
            raise ValueError("identity source query failed")
        return result.stdout

    def collect(self):
        result = {"schema_version": SCHEMA, "context": self.context, "namespace": self.namespace,
                  "selector": self.selector, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "state": "ok", "error": None, "records": [], "policies": [], "agents": []}
        try:
            pods = json.loads(self.kube("get", "pods", "-l", self.selector, "-o", "json"))["items"]
            agents = json.loads(self.kube("get", "pods", "-l", "app.kubernetes.io/name=tetragon", "-o", "json", namespace="kube-system"))["items"]
            by_node = {}
            policies = json.loads(self.kube("get", "tracingpoliciesnamespaced", "-o", "json"))["items"]
            label_keys = {k for p in policies for k in p.get("spec", {}).get("podSelector", {}).get("matchLabels", {})}
            for agent in agents:
                state = next((s for s in agent.get("status", {}).get("containerStatuses", []) if s.get("name") == "tetragon"), {})
                node = agent["spec"]["nodeName"]
                if not state.get("ready") or node in by_node:
                    raise ValueError("identity agent is unavailable or ambiguous")
                by_node[node] = agent["metadata"]["name"]
                result["agents"].append({"node": node, "pod_uid": agent["metadata"]["uid"], "restart_count": state.get("restartCount")})
            containers = {}
            for pod in pods:
                if pod.get("status", {}).get("phase") != "Running" or pod["metadata"].get("deletionTimestamp"):
                    continue
                for c in pod.get("status", {}).get("containerStatuses", []):
                    cid = str(c.get("containerID", "")).removeprefix("containerd://")
                    if not re.fullmatch(r"[a-f0-9]{64}", cid) or not c.get("state", {}).get("running"):
                        continue
                    containers[cid] = {"node": pod["spec"]["nodeName"], "namespace": self.namespace,
                        "pod_uid": pod["metadata"]["uid"], "pod_name": pod["metadata"]["name"],
                        "labels": {k: v for k, v in pod["metadata"].get("labels", {}).items()
                                   if k in label_keys},
                        "container_id": cid, "container_name": c["name"]}
            for node in sorted({c["node"] for c in containers.values()}):
                if node not in by_node:
                    raise ValueError("scoped node has no ready Tetragon agent")
                cids = sorted(cid for cid, c in containers.items() if c["node"] == node)
                raw = self.kube("exec", by_node[node], "-c", "tetragon", "--", "sh", "-c", PROC_SCAN, "byul-proc-scan", *cids, namespace="kube-system")
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("identity process snapshot exceeds limit")
                fields = raw.split("\0")
                if fields[-1] != "" or (len(fields)-1) % 9:
                    raise ValueError("identity process snapshot is incomplete")
                for i in range(0, len(fields)-1, 9):
                    cid, pid, ns, ticks, npid, binary, hz, file_id, alias = fields[i:i+9]
                    if (cid not in containers or not re.fullmatch(r"pid:\[\d+\]", ns) or not binary.startswith("/")
                        or not re.fullmatch(r"\d+:\d+", file_id) or (alias and not alias.startswith("/"))):
                        raise ValueError("invalid identity process sample")
                    row = {**containers[cid], "node_pid": int(pid), "pidns": int(ns[5:-1]),
                           "start_ticks": int(ticks), "clock_ticks": int(hz), "ns_pid": int(npid),
                           "binary": binary, "binary_file_id": file_id,
                           "binary_aliases": [{"path": alias, "file_id": file_id}] if alias else [], "stable": True}
                    result["records"].append(row)
            for policy in policies:
                probes = comparable_spec(policy["spec"]).get("kprobes", [])
                result["policies"].append({"namespace": self.namespace, "name": policy["metadata"]["name"],
                    "selector": policy["spec"].get("podSelector", {}).get("matchLabels", {}),
                    "kernel_identity": bool(probes) and all(p.get("data") == KERNEL_DATA for p in probes)})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            result.update(state="error", error="scoped process identities could not be verified", records=[], policies=[])
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(self.output, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("interval must be at least one second")
    collector = IdentityCollector(args.context, args.namespace, args.selector, args.output)
    while True:
        result = collector.collect()
        print(json.dumps({"state": result["state"], "processes": len(result["records"])}), flush=True)
        if not args.watch:
            return 0 if result["state"] == "ok" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
