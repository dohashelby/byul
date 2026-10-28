"""PLT-04: explicit mount bans and separate credential candidate metadata."""
import hashlib
import json
from pathlib import PurePosixPath
import re
from guard.credential_policy import manual_pass_ceiling, validate_credential_policy
from guard.models import canonical_json
from .container_config import ContainerConfigExecutor
from .credential_candidates import (MAX_OUTPUT_BYTES, METADATA_PROBE, PROFILE, PROBE_TIMEOUT,
                                    candidate_paths, decode_metadata, shadowed_paths)
from .http_common import functional_requests, functional_results
from .owned_capture import engine_identity

ENV_CMD = ('for d in /proc/[0-9]*; do echo "PID ${d#/proc/}"; '
           'if names=$(tr "\\000" "\\n" < "$d/environ" 2>/dev/null); then '
           'printf "%s\\n" "$names" | sed "s/=.*//"; echo GUARD_READ_OK; '
           'else echo GUARD_READ_ERROR; fi; done')


class _SourceUnverified(ValueError):
    """A fixed internal signal; never retain transport exception text."""


def _inspection_matches(row, owner):
    if type(row) is not dict or type(row.get("State")) is not dict or type(row.get("Config")) is not dict:
        return False
    return {"container_id": row.get("Id"), "image_id": row.get("Image"),
            "started_at": row["State"].get("StartedAt"), "image": row["Config"].get("Image")} == owner


class CredentialExposureExecutor(ContainerConfigExecutor):
    name = "credential_exposure"
    version = "0.5.2"

    def validate_policy(self, check, values):
        validate_credential_policy(check, values)

    def collect(self, ctx):
        container = self.cfg.get("container") or ctx.target["engine"].get("container")
        if not container or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
            raise ValueError("credential exposure needs a valid target container")
        docker = self._docker_obj()
        if any(obs.key == "forbidden_mount_violations" for obs in ctx.check.required_observations):
            return self._collect_separated(ctx, container, docker)
        # Old field names do not exempt a new live collection from source
        # binding. Reading saved EVD/Ref objects never invokes this collector.
        obs, normal, challenges, detail = self._collect_separated(ctx, container, docker, include_candidates=False)
        legacy = {key: obs[key] for key in ("process_scan_complete", "worker_env_names",
                  "functional_probes_planned", "functional_probe_results")}
        legacy["exposed_credential_paths"] = obs["forbidden_mount_violations"]
        summary = {key: detail[key] for key in ("credential_names", "mount_destinations", "scan_complete",
                   "source_binding", "collection_errors")}
        summary["source_binding"]["intervals_verified"].pop("candidates")
        summary["fixture"] = {"env_command": ENV_CMD, "policy": ctx.policy_values}
        return legacy, normal, challenges, summary

    def _collect_separated(self, ctx, container, docker, *, include_candidates=True):
        # Missing declarations and a stable wrong instance fail before any
        # process scan, candidate read or normal-control request is attempted.
        try:
            image = ctx.env.engine.image_digest
            anchor = engine_identity(docker, ctx.target, ctx.env, container, image, 30)
            if not anchor["started_at"]:
                raise ValueError("CREDENTIAL_SOURCE_UNVERIFIED")
        except Exception:
            raise ValueError("CREDENTIAL_TARGET_UNVERIFIED") from None
        source_container = anchor["container_id"]
        intervals = {name: False for name in ("mounts", "environment", "candidates", "normal_control")}

        def check_owner():
            try:
                current = engine_identity(docker, ctx.target, ctx.env, container, image, 5)
            except Exception:
                raise _SourceUnverified("CREDENTIAL_SOURCE_UNVERIFIED") from None
            if current != anchor:
                raise _SourceUnverified("CREDENTIAL_SOURCE_UNVERIFIED")

        errors, inspected = [], None
        try:
            inventory = docker.inspect_json(source_container, timeout=30)
            check_owner()
            if not _inspection_matches(inventory, anchor):
                raise _SourceUnverified("CREDENTIAL_SOURCE_UNVERIFIED")
            inspected = inventory
            intervals["mounts"] = True
        except _SourceUnverified:
            errors.append("MOUNT_SOURCE_UNVERIFIED")
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
            check_owner()
            raw = docker.exec(source_container, ["sh", "-c", ENV_CMD], timeout=30)
            check_owner()
            intervals["environment"] = True
            lines = raw.stdout.splitlines()
            pids = sum(line.startswith("PID ") for line in lines)
            complete = (raw.returncode == 0 and pids > 0 and lines.count("GUARD_READ_OK") == pids
                        and "GUARD_READ_ERROR" not in lines)
            names = sorted({line for line in lines if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line)
                            and line not in {"GUARD_READ_OK", "GUARD_READ_ERROR"}})
            if any(line and not line.startswith("PID ") and line not in {"GUARD_READ_OK", "GUARD_READ_ERROR"}
                   and line not in names for line in lines):
                complete = False
        except _SourceUnverified:
            errors.append("PROCESS_SOURCE_UNVERIFIED")
        except Exception:
            errors.append("PROCESS_ENVIRONMENT_UNAVAILABLE")

        requested = include_candidates and "credential_candidate_paths" in ctx.policy_values
        paths = candidate_paths(ctx.policy_values.get("credential_candidate_paths")) if requested else None
        candidates = {} if paths == [] else {path: "unknown" for path in paths or []}
        candidate_detail = {"profile": PROFILE, "code": "CANDIDATES_NOT_REQUESTED"}
        if paths:
            candidate_mounts = [mount["Destination"] for mount in mounts] if mounts_collected else []
            shadowed = shadowed_paths(paths, candidate_mounts) if mounts_collected else set()
            candidates.update({path: "shadowed" for path in shadowed})
            probed = [path for path in paths if path not in shadowed]
            call = getattr(docker, "exec_bounded", None)
            if probed and mounts_collected and callable(call):
                try:
                    check_owner()
                    result = call(source_container, ["python3", "-c", METADATA_PROBE,
                                  json.dumps(probed, ensure_ascii=False)],
                                  timeout=PROBE_TIMEOUT, max_bytes=MAX_OUTPUT_BYTES)
                    observed, candidate_detail = decode_metadata(result, probed)
                    after = docker.inspect_json(source_container, timeout=5)
                    check_owner()
                    if _inspection_matches(after, anchor) and after.get("Mounts") == mounts:
                        candidates.update(observed)
                        intervals["candidates"] = True
                    else:
                        candidate_detail = {"profile": PROFILE, "code": "CANDIDATE_SOURCE_CHANGED"}
                except _SourceUnverified:
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
            check_owner()
            requests, probes = functional_requests(ctx, via="engine")
            functional_attempted = bool(requests)
            responses = ctx.cprobe(ctx.trusted_net, requests, ctx.timeout) if requests else []
            check_owner()
            intervals["normal_control"] = True
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
            "source_binding": {"profile": "docker-engine-source-intervals/1",
                               "environment_ref": ctx.env.ref().model_dump(mode="json"),
                               "owner_sha256": hashlib.sha256(canonical_json(anchor)).hexdigest(),
                               "intervals_verified": intervals},
            "limitations": ["Mount policy overlap is distinct from credential presence or access.",
                            "Candidate identity is declared policy; no credential file contents or validity are collected.",
                            "Read permission requires the same observed worker/collector subject and stable metadata.",
                            "An explicit empty candidate list tests no file paths; dynamic providers and other credentials are unverified.",
                            "Owner checks bind sampled source intervals, not worker attribution or producer authenticity.",
                            "No owned canary content/access contract is available; canary_readable is not inferred."],
            "fixture": {"env_command": ENV_CMD, "policy": ctx.policy_values,
                        "candidate_profile": PROFILE, "candidate_probe_sha256": hashlib.sha256(METADATA_PROBE.encode()).hexdigest(),
                        "candidate_probe_timeout": PROBE_TIMEOUT, "candidate_output_limit": MAX_OUTPUT_BYTES},
        }
