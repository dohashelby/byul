"""Supervise scoped per-node gRPC feeds and record agent changes as observation gaps."""
from __future__ import annotations

import argparse
import fcntl
from contextlib import ExitStack
import json
from pathlib import Path
import re
import signal
import threading
import time
from uuid import uuid4

from tools import k8s_observation_feed as feed
from tools.k8s_observation_feed import (
    _Child, _FeedGroup, _Supervisor, _StopRequested, _stop_children, _open_append,
    _install_stop_signal_handlers, _restore_signal_handlers, redact_tetragon_line,
    _append_gap, _utc_now, GAP_SCHEMA,
)
from tools.tetragon_trial import run, atomic_json


class StreamScopeError(ValueError):
    """An absent/ambiguous policy scope requires configuration, not retries."""


def discovery_pending(output, context, opened_at, gap_id, *, first):
    output=Path(output).absolute()
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    feed._share(output,directory=True)
    with _open_append(output/"tetragon.jsonl"): pass
    gap={"gap_id":gap_id,"opened_at":opened_at,"backfill_since":opened_at,
         "last_healthy_at":None,"cause":[{"name":"agent_discovery","error":"SourceUnavailable"}],"attempts":0}
    if first:
        _append_gap(output/"tetragon.gaps.jsonl",{"schema":GAP_SCHEMA,"event":"gap_opened","kind":"tetragon",**gap})
    atomic_json(output/"tetragon.heartbeat.json",{"schema":"runtime-observation-feed-heartbeat/1.0",
        "kind":"tetragon","kube_context":context,"feed_path":str(output/"tetragon.jsonl"),
        "started_at":opened_at,"updated_at":_utc_now(),"state":"reconnecting","open_gap":gap,
        "processes":[{"name":"agent_discovery","running":False,"returncode":None}]},reader_gid=feed._reader_gid)


class AgentEpochGuard:
    def __init__(self, context, *, runner=run):
        self.context, self.run, self.epochs = context, runner, None

    def agents(self):
        result = self.run(["kubectl", "--context", self.context, "-n", "kube-system", "get", "pods", "-l",
                           "app.kubernetes.io/name=tetragon", "-o", "json"])
        if result.returncode:
            raise ValueError("agent discovery unavailable")
        by_node = {}
        for pod in json.loads(result.stdout).get("items", []):
            if pod["metadata"].get("deletionTimestamp"):
                continue
            agent = next((c for c in pod.get("status", {}).get("containerStatuses", []) if c.get("name") == "tetragon"), {})
            if not agent.get("ready"):
                raise ValueError("agent is not ready")
            node = pod["spec"]["nodeName"]
            if node in by_node:
                raise ValueError("agent node is ambiguous")
            by_node[node] = {"name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"], "restarts": agent.get("restartCount")}
        if not by_node:
            raise ValueError("agents are missing")
        return by_node

    def check(self):
        try:
            current = {node: (a["uid"], a["restarts"]) for node, a in self.agents().items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            return [{"name":"tetragon_agent","error":"AgentUnavailable"}]
        changed = self.epochs is not None and self.epochs != current
        self.epochs = current
        return [{"name":"tetragon_agent","error":"AgentEpochChanged"}] if changed else []


class TetragonStream:
    def __init__(self, context, namespace, policies, output, *, runner=run):
        if not policies or any(not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", p) for p in policies):
            raise ValueError("explicit policy names are required")
        self.context, self.namespace, self.policies = context, namespace, sorted(set(policies))
        self.output = Path(output).absolute()
        self.run = (lambda command: run(command, timeout=5)) if runner is run else runner
        self.guard = AgentEpochGuard(context, runner=self.run)
        self.tag = "byul-tetragon-stream-"+uuid4().hex
        self.stack, self.group, self.supervisor = ExitStack(), None, None
        self.stop_requested = threading.Event()

    def command(self, node, since=None):
        # gRPC has no historical replay here. Gaps remain explicitly unverified.
        agents = self.guard.agents()
        if node not in agents:
            raise RuntimeError("stream node disappeared")
        script = ('stream_pid=; cleanup() { if [ -n "$stream_pid" ]; then kill "$stream_pid" 2>/dev/null; '
                  'wait "$stream_pid" 2>/dev/null; stream_pid=; fi; }; trap cleanup TERM EXIT; '
                  'tetra getevents "$@" & stream_pid=$!; wait "$stream_pid"; code=$?; stream_pid=; exit "$code"')
        return ["kubectl", "--context", self.context, "-n", "kube-system", "exec", agents[node]["name"],
                "-c", "tetragon", "--", "sh", "-c", script, self.tag, "--policy-names", ",".join(self.policies), "-o", "json"]

    def start(self):
        specs = self.run(["kubectl", "--context", self.context, "get", "tracingpoliciesnamespaced", "-A", "-o", "json"])
        if specs.returncode:
            raise ValueError("stream policy scope unavailable")
        docs = json.loads(specs.stdout).get("items", [])
        for name in self.policies:
            matches = [p for p in docs if p["metadata"]["name"] == name]
            if len(matches) != 1 or matches[0]["metadata"]["namespace"] != self.namespace:
                raise StreamScopeError("stream policy scope is missing or ambiguous")
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        feed._share(self.output, directory=True)
        lock=self.stack.enter_context(_open_append(self.output/"stream.lock"))
        try:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise StreamScopeError("another collector owns this output directory") from error
        session_path=self.output/"stream-session.json"
        try:
            previous=json.loads(session_path.read_text())
            if previous.get("context")==self.context and previous.get("namespace")==self.namespace:
                self.cleanup_remote(previous.get("tag", ""))
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        atomic_json(session_path,{"context":self.context,"namespace":self.namespace,"policies":self.policies,"tag":self.tag})
        out = self.stack.enter_context(_open_append(self.output/"tetragon.jsonl"))
        error = self.stack.enter_context(_open_append(self.output/"tetragon.stderr.log"))
        self.out,self.error=out,error
        self.guard.check()
        nodes = sorted(self.guard.agents())
        self.group = _FeedGroup("tetragon", feed_path=self.output/"tetragon.jsonl",
            heartbeat_path=self.output/"tetragon.heartbeat.json", gap_path=self.output/"tetragon.gaps.jsonl",
            children=[_Child(node, lambda since, n=node: self.command(n, since), out, error, transform=redact_tetragon_line) for node in nodes],
            health_check=self.guard.check,supports_backfill=False)
        self.supervisor = _Supervisor([self.group], context=self.context, stop_requested=self.stop_requested)
        try:
            self.supervisor.start_group(self.group, since=None)
        except RuntimeError:
            self.supervisor._open_gap(self.group, [{"name":"initial_start","error":"StartFailed"}])
        self.supervisor.write()

    def tick(self):
        try:
            nodes=sorted(self.guard.agents())
            if nodes != sorted(child.name for child in self.group.children):
                _stop_children(self.group)
                if self.group.open_gap is None:
                    self.supervisor._open_gap(self.group,[{"name":"tetragon_agents","error":"AgentTopologyChanged"}])
                self.group.children=[_Child(node,lambda since,n=node:self.command(n,since),self.out,self.error,
                                            transform=redact_tetragon_line) for node in nodes]
        except (ValueError,KeyError,TypeError,AttributeError):
            pass
        self.supervisor.tick()
        self.supervisor.write()

    def cleanup_remote(self,tag):
        if not isinstance(tag,str) or not re.fullmatch(r"byul-tetragon-stream-[a-f0-9]{32}",tag):
            return
        try:
            for agent in self.guard.agents().values():
                prefix=["kubectl","--context",self.context,"-n","kube-system","exec",agent["name"],"-c","tetragon","--"]
                result=self.run([*prefix,"ps","-o","pid,ppid,args"])
                rows=[]
                for line in result.stdout.splitlines():
                    fields=line.split(maxsplit=2)
                    if len(fields)==3 and fields[0].isdigit() and fields[1].isdigit():rows.append(fields)
                for pid,_,command in rows:
                    if f" {tag} " not in command:continue
                    # A stopped gRPC reader has no persistent work to flush.
                    # End only the tetra children of the exact UUID wrapper,
                    # so its shell cannot wait forever during service stop.
                    for child,parent,args in rows:
                        if parent==pid and "tetra getevents" in args:
                            self.run([*prefix,"kill","-KILL",child])
                    self.run([*prefix,"kill","-TERM",pid])
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

    def close(self):
        if self.group:
            _stop_children(self.group)
            self.supervisor.write(final_state="stopped")
        self.cleanup_remote(self.tag)
        self.stack.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context",required=True)
    parser.add_argument("--namespace",required=True)
    parser.add_argument("--policy",action="append",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--reader-group",type=int)
    args=parser.parse_args()
    feed.check_reader_group(args.reader_group)
    feed._reader_gid=args.reader_group
    stop_requested=threading.Event()
    # Signal handlers must not acquire Event/Condition locks. systemd and uv
    # can deliver duplicate termination signals while the first is handled.
    shutdown=[False]
    def request_stop(_signum,_frame):
        shutdown[0]=True
    previous={sig:signal.signal(sig,request_stop) for sig in (signal.SIGTERM,signal.SIGHUP)}
    stream=None
    opened_at,gap_id=_utc_now(),uuid4().hex
    pending=False
    try:
        while not shutdown[0] and not stop_requested.is_set():
            stream=TetragonStream(args.context,args.namespace,args.policy,args.output)
            stream.stop_requested=stop_requested
            try:
                stream.start()
                break
            except StreamScopeError:
                raise
            except (OSError,RuntimeError,ValueError,KeyError,TypeError):
                stream.close()
                discovery_pending(args.output,args.context,opened_at,gap_id,first=not pending)
                pending=True
                stop_requested.wait(3)
        while stream is not None and not shutdown[0] and not stop_requested.wait(1):
            stream.tick()
    except _StopRequested:
        pass
    finally:
        stop_requested.set()
        if stream is not None:stream.close()
        _restore_signal_handlers(previous)
    return 0


if __name__=="__main__": raise SystemExit(main())
