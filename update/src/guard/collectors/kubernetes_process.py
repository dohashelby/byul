"""Common live input for A-2 permission/credential consumers, not a new judge."""
from pathlib import Path

from guard.transport_options import valid_kubectl_options
from guard.collectors.file import load_bounded_json, load_environment
from guard.executors.owned_capture import strict_json
from guard.executors.kubernetes_container_config import (
    normalize_process_capture_paths, validate_process_capture_target, collect_process_capture)


def validate_options(binary,duration):
    if not valid_kubectl_options(binary, duration):
        raise ValueError('KUBERNETES_PROCESS_OPTIONS_INVALID')


def load_inputs(env_path,target_path,paths_path=None):
    environment = load_environment(Path(env_path))
    target = load_bounded_json(target_path)
    selected = None
    if paths_path is not None:
        with Path(paths_path).open('rb') as stream:
            selected = strict_json(stream.read(8193),limit=8192)
        if selected is None:
            raise ValueError('KUBERNETES_PROCESS_PATHS_INVALID')
        selected = normalize_process_capture_paths(selected)
    try:
        validate_process_capture_target(target,environment)
    except Exception:
        raise ValueError('KUBERNETES_PROCESS_INPUT_INVALID') from None
    return environment,target,selected


def collect(environment,target,*,binary='kubectl',duration=300,candidate_paths=None,runner=None):
    validate_options(binary,duration)
    return collect_process_capture(environment,target,binary=binary,duration=duration,
                                   candidate_paths=candidate_paths,runner=runner)
