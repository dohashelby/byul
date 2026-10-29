"""PLT-04: explicit mount bans and separate credential candidate metadata."""
import hashlib
import json
from pathlib import PurePosixPath
import re
from guard.credential_policy import manual_pass_ceiling, validate_credential_policy
from guard.models import canonical_json
from .container_config import (ContainerConfigExecutor, kubernetes_process_rows,
                               read_kubernetes_process_observations, kubernetes_process_control)
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


def parse_environment_names(text, returncode):
    """Keep names from identified records; only a complete scan proves absence.

    A malformed/duplicate PID or misplaced marker cannot borrow another
    process's completion marker. Values and unrelated text are never returned.
    Names observed before a later collection failure remain positive facts.
    This parser does not establish worker attribution or PID/start stability.
    """
    if type(text) is not str:
        return [], False
    names, pids = set(), set()
    current, closed = False, False
    complete = type(returncode) is int and returncode == 0
    for line in text.splitlines():
        if line.startswith("PID") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line):
            if current and not closed:
                complete = False
            match = re.fullmatch(r"PID ([1-9][0-9]{0,9})", line)
            pid = int(match[1]) if match else None
            current = pid is not None and pid <= 2147483647 and pid not in pids
            closed = False
            if current:
                pids.add(pid)
            else:
                complete = False
            continue
        if not line:
            continue  # The fixed producer emits a blank line for an empty environment.
        if not current:
            complete = False
        elif line in {"GUARD_READ_OK", "GUARD_READ_ERROR"}:
            if closed or line == "GUARD_READ_ERROR":
                complete = False
            closed = True
        elif closed:
            complete = False
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line):
            names.add(line)
        else:
            complete = False
    complete = complete and bool(pids) and current and closed
    return sorted(names), complete


def collect_kubernetes_credential_exposure(ctx):
    """Keep observed container metadata separate from worker credential claims."""
    if ctx.check.id != "PLT-04":
        raise ValueError("KUBERNETES_CREDENTIAL_CHECK_UNSUPPORTED")
    selected = candidate_paths(ctx.policy_values.get("credential_candidate_paths"))
    record = read_kubernetes_process_observations(ctx, selected)
    rows = kubernetes_process_rows(record)
    names = sorted({name for row in rows for name in row["environment_names"]})
    # v1 explicitly reports no source-bound worker identity/access. Its names
    # and lstat metadata must not fill the existing worker/access observations.
    observations = {
        "process_scan_complete": {"value": False, "collected": True},
        "worker_env_names": {"value": None, "collected": False},
        "forbidden_mount_violations": {"value": None, "collected": False},
        "credential_candidates_planned": {"value": selected or [], "collected": True},
        "credential_candidate_results": {"value": {path: "unknown" for path in selected or []}, "collected": True},
        "credential_candidates_complete": {"value": selected == [], "collected": True},
    }
    functional, normal = kubernetes_process_control(ctx)
    observations.update(functional)
    summary = {"profile": "kubernetes-credential-metadata-consumer-v1", "process_observation": record,
        "container_environment_names_observed": names, "worker_subject_verified": False,
        "candidate_access_verified": False, "kubernetes": ctx.cprobe.report(),
        "candidate_request": "missing" if selected is None else "explicit_empty" if not selected else "explicit_paths",
        "fixture": {"process_profile": record["profile"], "program_sha256": record["program_sha256"],
                    "candidate_paths": selected,
                    "declared_control": [probe.model_dump(mode="json") for probe in ctx.check.functional_probes]},
        "limitations": ["Container-reported environment names are not source-bound worker environment names.",
            "Candidate lstat absence/mode does not establish a worker's namespace, effective access or credential use.",
            "Pod volumeMount declarations are not observed process mounts or actual credential access.",
            "An explicit empty candidate list tests no path; whole-worker credential coverage is unverified."]}
    packet = record.get("packet")
    challenges = [{"step": step, "attempted": type(packet) is dict and
                   (index == 0 or index == 1 and bool(packet.get("candidate_metadata")))}
                  for index, step in enumerate(ctx.check.challenge_steps)]
    return observations, normal, challenges, summary


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

        names, complete = [], False
        try:
            check_owner()
            raw = docker.exec(source_container, ["sh", "-c", ENV_CMD], timeout=30)
            check_owner()
            intervals["environment"] = True
            names, complete = parse_environment_names(raw.stdout, raw.returncode)
            if not complete:
                errors.append("PROCESS_ENVIRONMENT_INCOMPLETE")
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
                    or any(type(response) is not dict for response in responses)
                    or any("id" in response for response in responses)
                    and [response.get("id") for response in responses] != [request["id"] for request in requests]):
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
            "worker_env_names": {"value": names if complete or names else None,
                                 "collected": complete or bool(names)},
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
