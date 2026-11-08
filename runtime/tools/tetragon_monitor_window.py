"""Build fail-closed monitor windows from Gateway's attributed observation JSONL.

Run with the agent's UID (or root). No Kubernetes mutation or human approval.
Continuity is intentionally not resumed across generator restarts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from serving_runtime.enforcement.attribution import timestamp
from serving_runtime.operations.safety import recent, SHA

SCHEMA = "runtime-tetragon-monitor-windows/1.0"
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
HEALTH_CHECKS = ("policies_verified", "writer_verified", "identities_verified", "events_verified")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_bytes(path, limit, *, trusted_owner_uids=()):
    # O_NOFOLLOW and fstat bind the checks to the opened file, including a rename race.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid(), *trusted_owner_uids}
                or info.st_mode & 0o022 or info.st_size > limit):
            raise ValueError("untrusted or oversized input")
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("oversized input")
        return raw, (info.st_dev, info.st_ino)


def read_object(path, *, trusted_owner_uids=()):
    value = json.loads(read_bytes(path, 2 * 1024 * 1024, trusted_owner_uids=trusted_owner_uids)[0])
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(canonical(value) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def human_reviews(path, *, trusted_owner_uids=()):
    """A missing/invalid review never supplies approvals, even a valid subset."""
    if path is None:
        return {}, None
    try:
        value = read_object(path, trusted_owner_uids=trusted_owner_uids)
        reviewer = value.get("reviewer_token_id")
        if not isinstance(reviewer, str) or not TOKEN.fullmatch(reviewer):
            raise ValueError("invalid reviewer")
        rows = value["events"]
        if not isinstance(rows, list):
            raise ValueError("invalid reviews")
        result = {}
        for row in rows:
            key, verdict = row["event_sha256"], row["verdict"]
            if (not isinstance(key, str) or not SHA.fullmatch(key) or key in result
                    or verdict not in {"not_false_positive", "false_positive"}):
                raise ValueError("invalid or duplicate review")
            result[key] = verdict
        return result, reviewer
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, None


class MonitorWindowGenerator:
    def __init__(self, *, context, namespace, policies, status, observations, output,
                 reviews=None, catalog=None, trusted_owner_uids=()):
        self.context, self.namespace, self.policies = context, namespace, set(policies)
        self.status, self.observations = Path(status), Path(observations)
        self.output, self.reviews = Path(output), reviews
        self.trusted_owner_uids = frozenset(trusted_owner_uids)
        self.catalog = Path(catalog) if catalog else self.output.with_name(self.output.name + ".events.json")
        self.windows = {}
        self.last_at = None
        self.source = None

    def collect(self, *, now=None):
        now = now or datetime.now(timezone.utc)
        result = {"schema_version": SCHEMA, "generated_at": now.isoformat(),
                  "context": self.context, "windows": [], "state": "unavailable"}
        catalog = []
        try:
            snapshot = read_object(self.status, trusted_owner_uids=self.trusted_owner_uids)
            monitoring = snapshot.get("monitoring") or {}
            if (snapshot.get("schema_version") != "runtime-tetragon-state/1.0"
                    or snapshot.get("context") != self.context or snapshot.get("state") != "ok"
                    or not recent(snapshot.get("generated_at"), 20, now)
                    or monitoring.get("state") != "healthy" or monitoring.get("open_gap")
                    or not all(monitoring.get(k) is True for k in HEALTH_CHECKS)):
                raise ValueError("status unavailable")
            end = timestamp(snapshot["generated_at"])
            raw, inode = read_bytes(self.observations, 64 * 1024 * 1024, trusted_owner_uids=self.trusted_owner_uids)
            # A retention rewrite, truncate or replacement cannot prove completeness.
            source_changed = self.source is not None and (
                inode != self.source[0] or len(raw) < self.source[1]
                or hashlib.sha256(raw[:self.source[1]]).hexdigest() != self.source[2])
            if (source_changed or (self.last_at is not None and
                    not 0 <= (end - self.last_at).total_seconds() <= 20)):
                self.windows.clear()
            self.source = inode, len(raw), hashlib.sha256(raw).hexdigest()
            if raw and not raw.endswith(b"\n"):
                raise ValueError("incomplete observation record")
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if any(not isinstance(event, dict) or event.get("schema") != "runtime-observation/1.0"
                   or timestamp(event.get("observed_at")) is None for event in events):
                raise ValueError("invalid observation record")
            reviews, reviewer = human_reviews(self.reviews, trusted_owner_uids=self.trusted_owner_uids)
            active = {}
            seen = set()
            for policy in snapshot["policies"]:
                if policy.get("name") not in self.policies or policy.get("namespace") != self.namespace:
                    continue
                node = policy.get("node")
                key = (policy["name"], node)
                if key in seen:
                    raise ValueError("ambiguous policy node")
                seen.add(key)
                caps = [c for c in snapshot["capabilities"] if c.get("node") == node]
                if (policy.get("mode") != "monitor" or policy.get("state") != "enabled"
                        or not node or not policy.get("id") or len(caps) != 1 or not caps[0].get("kernel")
                        or not SHA.fullmatch(policy.get("spec_sha256", ""))
                        or policy.get("method") not in {"lsm_file_open", "override_lsm", "sigkill_fd_install", "override_syscall"}):
                    continue
                binding = {"policy": policy["name"], "namespace": self.namespace, "node": node,
                           "policy_id": policy["id"], "spec_sha256": policy["spec_sha256"],
                           "method": policy["method"], "mode": "monitor", "kernel": caps[0]["kernel"]}
                epoch = digest([binding, monitoring.get("history", []), monitoring.get("verification_since"),
                                monitoring.get("epochs"), snapshot.get("agents", []),
                                [t for t in snapshot.get("transitions", []) if t.get("policy") == policy["name"]
                                 and t.get("namespace") == self.namespace]])
                old = self.windows.get(key, {})
                since = old["since"] if old.get("epoch") == epoch else end.isoformat()
                active[key] = {"epoch": epoch, "since": since}
                start = timestamp(since)
                selected = []
                for event in events:
                    identity = event.get("identity") or {}
                    if (event.get("source") != "tetragon" or event.get("data_origin") != "real"
                            or event.get("policy_name") != policy["name"]
                            or identity.get("state") != "confirmed" or identity.get("node") != node
                            or identity.get("context") != self.context
                            or (event.get("pod") or {}).get("namespace") != self.namespace
                            or event.get("observation_purpose") == "health_check"
                            or identity.get("container_name") == "sentinel"):
                        continue
                    at = timestamp(event.get("observed_at"))
                    if at is None:
                        raise ValueError("invalid event time")
                    if start <= at <= end:
                        selected.append(event)
                selected.sort(key=canonical)
                entries = [{"event_sha256": digest([self.context, binding, since, event]), "event": event}
                           for event in selected]
                verdicts = [reviews.get(entry["event_sha256"]) for entry in entries]
                window = {**binding, "since": since, "until": end.isoformat(), "continuous": True,
                          "event_count": len(entries), "events_sha256": digest(selected),
                          "reviewed_event_count": sum(v is not None for v in verdicts),
                          "false_positive_count": verdicts.count("false_positive"),
                          "reviewer_token_id": reviewer}
                result["windows"].append(window)
                catalog.append({**binding, "since": since, "until": end.isoformat(), "events": entries})
            self.windows, self.last_at = active, end
            result["state"] = "ok"
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            self.windows.clear()
            self.last_at, self.source = None, None
            result["windows"] = []
            catalog = []
        # Write the review catalog first; windows are independently usable by the agent.
        try:
            atomic_json(self.catalog, {"schema_version": SCHEMA, "generated_at": result["generated_at"],
                                       "windows": catalog})
        except OSError:
            self.windows.clear()
            self.last_at, self.source = None, None
            result.update(windows=[], state="unavailable")
        atomic_json(self.output, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("context", "namespace", "status", "observations", "output"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--policy", action="append", required=True)
    parser.add_argument("--reviews", help="Human-authored event verdicts; never generated automatically")
    parser.add_argument("--catalog", help="Review catalog (default: OUTPUT.events.json)")
    parser.add_argument("--trusted-owner-uid", type=int, action="append", default=[],
                        help="Additionally trust this input file owner UID (repeatable; no group/other writes)")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if any(uid < 0 for uid in args.trusted_owner_uid):
        parser.error("trusted owner UIDs must be nonnegative")
    if not 1 <= args.interval <= 20:
        parser.error("interval must be between 1 and 20 seconds")
    paths = [Path(p).resolve() for p in (args.status, args.observations, args.output,
             args.catalog or args.output + ".events.json", *([args.reviews] if args.reviews else []))]
    if len(paths) != len(set(paths)):
        parser.error("input and output paths must be distinct")
    generator = MonitorWindowGenerator(context=args.context, namespace=args.namespace, policies=args.policy,
        status=args.status, observations=args.observations, output=args.output, reviews=args.reviews, catalog=args.catalog,
        trusted_owner_uids=args.trusted_owner_uid)
    generator.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(generator.output) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            result = generator.collect()
            if not args.watch:
                return 0 if result["state"] == "ok" else 1
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
