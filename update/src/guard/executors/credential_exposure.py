"""PLT-04: explicit mount bans and separate credential candidate metadata."""
import hashlib
import json
from pathlib import PurePosixPath
import re
from guard.credential_policy import manual_pass_ceiling, validate_credential_policy
from .container_config import ContainerConfigExecutor
from .credential_candidates import (MAX_OUTPUT_BYTES, METADATA_PROBE, PROFILE, PROBE_TIMEOUT,
                                    candidate_paths, decode_metadata, shadowed_paths)
from .http_common import functional_requests, functional_results

ENV_CMD = ('for d in /proc/[0-9]*; do echo "PID ${d#/proc/}"; '
           'if names=$(tr "\\000" "\\n" < "$d/environ" 2>/dev/null); then '
           'printf "%s\\n" "$names" | sed "s/=.*//"; echo GUARD_READ_OK; '
           'else echo GUARD_READ_ERROR; fi; done')


class CredentialExposureExecutor(ContainerConfigExecutor):
    name = "credential_exposure"
    version = "0.4.1"

    def validate_policy(self, check, values):
        validate_credential_policy(check, values)

    def collect(self, ctx):
        container = self.cfg.get("container") or ctx.target["engine"].get("container")
        if not container or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
            raise ValueError("credential exposure needs a valid target container")
        docker = self._docker_obj()
        if any(obs.key == "forbidden_mount_violations" for obs in ctx.check.required_observations):
            return self._collect_separated(ctx, container, docker)
        # Saved definitions keep their historical field names and semantics.
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
        functional, _, normal = functional_results(probes, dict(zip(probes, responses)), ctx.env.model.id)
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

    def _collect_separated(self, ctx, container, docker):
        errors, inspected = [], None
        try:
            inspected = docker.inspect_json(container, timeout=30)
        except Exception:
            errors.append("MOUNT_INVENTORY_UNAVAILABLE")
        mounts = inspected.get("Mounts") if type(inspected) is dict else None
        valid_mounts = [mount for mount in mounts if type(mount) is dict
            and type(mount.get("Type")) is str and type(mount.get("Destination")) is str
            and mount["Destination"].startswith("/") and ".." not in mount["Destination"].split("/")
            and "\x00" not in mount["Destination"]
            and str(PurePosixPath(mount["Destination"])) == mount["Destination"]] if type(mounts) is list else []
        mounts_collected = type(mounts) is list and len(valid_mounts) == len(mounts)
        if not mounts_collected:
            errors.append("MOUNT_INVENTORY_INCOMPLETE")
        destinations = sorted({mount["Destination"] for mount in valid_mounts
                               if mount["Type"] in {"bind", "volume"}})
        # This is an explicit mount policy violation, not proof of a credential
        # file's presence, contents, effective permissions or successful use.
        forbidden = ctx.policy_values.get("forbidden_mount_paths", [])
        violations = sorted(path for path in forbidden if any(
            path == destination or path.startswith(destination.rstrip("/") + "/")
            or destination.startswith(path.rstrip("/") + "/") for destination in destinations))

        names, lines, complete = [], [], False
        try:
            raw = docker.exec(container, ["sh", "-c", ENV_CMD], timeout=30)
            lines = raw.stdout.splitlines()
            pids = sum(line.startswith("PID ") for line in lines)
            complete = (raw.returncode == 0 and pids > 0 and lines.count("GUARD_READ_OK") == pids
                        and "GUARD_READ_ERROR" not in lines)
            names = sorted({line for line in lines if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line)
                            and line not in {"GUARD_READ_OK", "GUARD_READ_ERROR"}})
            if any(line and not line.startswith("PID ") and line not in {"GUARD_READ_OK", "GUARD_READ_ERROR"}
                   and line not in names for line in lines):
                complete = False
        except Exception:
            errors.append("PROCESS_ENVIRONMENT_UNAVAILABLE")

        requested = "credential_candidate_paths" in ctx.policy_values
        paths = candidate_paths(ctx.policy_values.get("credential_candidate_paths")) if requested else None
        candidates = {} if paths == [] else {path: "unknown" for path in paths or []}
        candidate_detail = {"profile": PROFILE, "code": "CANDIDATES_NOT_REQUESTED"}
        if paths:
            candidate_mounts = [mount["Destination"] for mount in mounts] if mounts_collected else []
            shadowed = shadowed_paths(paths, candidate_mounts) if mounts_collected else set()
            candidates.update({path: "shadowed" for path in shadowed})
            probed = [path for path in paths if path not in shadowed]
            call = getattr(docker, "exec_bounded", None)
            state = inspected.get("State") if type(inspected) is dict else None
            source_identified = (type(inspected) is dict and type(inspected.get("Id")) is str
                and bool(inspected["Id"]) and type(inspected.get("Image")) is str and bool(inspected["Image"])
                and type(state) is dict and state.get("Running") is True
                and type(state.get("StartedAt")) is str and bool(state["StartedAt"]))
            if probed and mounts_collected and source_identified and callable(call):
                try:
                    result = call(container, ["python3", "-c", METADATA_PROBE,
                                  json.dumps(probed, ensure_ascii=False)],
                                  timeout=PROBE_TIMEOUT, max_bytes=MAX_OUTPUT_BYTES)
                    observed, candidate_detail = decode_metadata(result, probed)
                    after = docker.inspect_json(container, timeout=5)
                    if (type(after) is dict and after.get("Id") == inspected["Id"]
                            and after.get("Image") == inspected.get("Image")
                            and after.get("State", {}).get("StartedAt") == state["StartedAt"]
                            and after.get("State", {}).get("Running") is True
                            and after.get("Mounts") == mounts):
                        candidates.update(observed)
                    else:
                        candidate_detail = {"profile": PROFILE, "code": "CANDIDATE_SOURCE_CHANGED"}
                except Exception:
                    candidate_detail = {"profile": PROFILE, "code": "CANDIDATE_METADATA_UNAVAILABLE"}
            elif probed:
                candidate_detail = {"profile": PROFILE, "code": "CANDIDATE_METADATA_UNAVAILABLE"}
            else:
                candidate_detail = {"profile": PROFILE, "code": "CANDIDATE_TREE_SHADOWED"}
        candidate_complete = requested and all(value in {"absent", "denied"} for value in candidates.values())
        if paths is None:
            candidate_complete = False

        # Preparation and response collection are downstream of independent
        # mount/environment facts. Neither failure may erase those facts.
        requests, probes = [], {probe.id: probe for probe in ctx.check.functional_probes}
        functional_attempted = False
        try:
            requests, probes = functional_requests(ctx, via="engine")
            functional_attempted = bool(requests)
            responses = ctx.cprobe(ctx.trusted_net, requests, ctx.timeout) if requests else []
            if (type(responses) is not list or len(responses) != len(probes)
                    or any(type(response) is not dict for response in responses)):
                responses = []
                errors.append("FUNCTIONAL_PROBE_UNAVAILABLE")
        except Exception:
            # A later functional reader failure cannot erase a measured policy
            # violation already collected from mounts or environment names.
            responses = []
            errors.append("FUNCTIONAL_PROBE_UNAVAILABLE")
        try:
            functional, _, normal = functional_results(probes, dict(zip(probes, responses)), ctx.env.model.id)
        except Exception:
            functional = {probe: "no_response" for probe in probes}
            normal = {"attempted": bool(requests), "succeeded": False,
                      "detail": "credential normal control response unavailable"}
            errors.append("FUNCTIONAL_PROBE_UNAVAILABLE")
        normal["attempted"] = functional_attempted
        mount_fact_collected = mounts_collected or bool(violations)
        obs = {
            "process_scan_complete": {"value": complete, "collected": True},
            "worker_env_names": {"value": names if lines else None, "collected": bool(lines)},
            # A valid forbidden row proves overlap even in a partial list.
            # An incomplete list with no known violation proves no absence.
            "forbidden_mount_violations": {"value": violations if mount_fact_collected else None,
                                            "collected": mount_fact_collected},
            # These are observed planning/collection states. Unknown paths and
            # missing configuration do not assert successful access collection.
            "credential_candidates_planned": {"value": paths or [], "collected": True},
            "credential_candidate_results": {"value": candidates, "collected": True},
            "credential_candidates_complete": {"value": candidate_complete, "collected": True},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
        return obs, normal, [{"step": step, "attempted": True} for step in ctx.check.challenge_steps], {
            "credential_names": names, "mount_destinations": destinations, "scan_complete": complete,
            "forbidden_mount_violations": violations if mount_fact_collected else None,
            "candidate_request": "missing" if paths is None else "explicit_empty" if not paths else "explicit_paths",
            "candidate_configured": requested,
            "candidate_metadata": candidate_detail, "collection_errors": errors,
            "limitations": ["Mount policy overlap is distinct from credential presence or access.",
                            "Candidate identity is declared policy; no credential file contents or validity are collected.",
                            "Read permission requires the same observed worker/collector subject and stable metadata.",
                            "An explicit empty candidate list tests no file paths; dynamic providers and other credentials are unverified.",
                            "No owned canary content/access contract is available; canary_readable is not inferred."],
            "fixture": {"env_command": ENV_CMD, "policy": ctx.policy_values,
                        "candidate_profile": PROFILE, "candidate_probe_sha256": hashlib.sha256(METADATA_PROBE.encode()).hexdigest(),
                        "candidate_probe_timeout": PROBE_TIMEOUT, "candidate_output_limit": MAX_OUTPUT_BYTES},
        }
