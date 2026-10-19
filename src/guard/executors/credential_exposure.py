"""PLT-04: credential name/mount exposure. Never collect credential values."""
import re
from .container_config import ContainerConfigExecutor
from .http_common import functional_requests, functional_results

ENV_CMD = ('for d in /proc/[0-9]*; do echo "PID ${d#/proc/}"; '
           'if names=$(tr "\\000" "\\n" < "$d/environ" 2>/dev/null); then '
           'printf "%s\\n" "$names" | sed "s/=.*//"; echo GUARD_READ_OK; '
           'else echo GUARD_READ_ERROR; fi; done')


class CredentialExposureExecutor(ContainerConfigExecutor):
    name = "credential_exposure"
    version = "0.2.0"

    def validate_policy(self, check, values):
        from pathlib import PurePosixPath
        for name in values.get("forbidden_env_names", []):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("forbidden environment entries must be variable names, never values")
        for value in values.get("forbidden_mount_paths", []):
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("credential paths must be absolute container paths")

    def collect(self, ctx):
        container = self.cfg.get("container") or ctx.target["engine"].get("container")
        if not container or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
            raise ValueError("credential exposure needs a valid target container")
        docker = self._docker_obj()
        ins = docker.inspect_json(container, timeout=30)
        raw = docker.exec(container, ["sh", "-c", ENV_CMD], timeout=30)
        lines = raw.stdout.splitlines()
        pids = sum(s.startswith("PID ") for s in lines)
        complete = raw.returncode == 0 and pids > 0 and lines.count("GUARD_READ_OK") == pids and "GUARD_READ_ERROR" not in lines
        names = sorted({s for s in lines if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s) and not s.startswith("GUARD_READ_")})
        if any(s and not s.startswith(("PID ", "GUARD_READ_")) and s not in names for s in lines):
            complete = False
        # Values are not present even in the subprocess result or the artifact.
        destinations = sorted(m["Destination"] for m in ins.get("Mounts", []) if m.get("Type") in {"bind", "volume"})
        forbidden = ctx.policy_values.get("forbidden_mount_paths", [])
        exposed = sorted(p for p in forbidden if any(p == d or p.startswith(d.rstrip("/") + "/")
                                                      or d.startswith(p.rstrip("/") + "/") for d in destinations))
        requests, probes = functional_requests(ctx, via="engine")
        responses = ctx.cprobe(ctx.trusted_net, requests, ctx.timeout) if requests else []
        functional, _, normal = functional_results(probes, dict(zip(probes, responses)))
        obs = {
            "process_scan_complete": {"value": complete, "collected": True},
            "worker_env_names": {"value": names, "collected": bool(lines)},
            "exposed_credential_paths": {"value": exposed, "collected": "Mounts" in ins},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
        return obs, normal, [{"step": s, "attempted": True} for s in ctx.check.challenge_steps], {
            "credential_names": names, "mount_destinations": destinations, "scan_complete": complete,
            "fixture": {"env_command": ENV_CMD, "policy": ctx.policy_values}}
