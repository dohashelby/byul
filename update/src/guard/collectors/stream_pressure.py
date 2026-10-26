"""LIF-04 입력 준비. 기본 경로는 실행 클라이언트·엔진·저장소를 만들지 않는다."""
from pathlib import Path

from guard.collectors.file import load_environment
from guard.collectors.route_observations import validate_output
from guard.executors.owned_capture import strict_json
from guard.executors.stream_pressure import normalize_settings, validate_inputs


def load_inputs(env_path, target_path, settings_path):
    env = load_environment(Path(env_path))
    def read(path):
        with Path(path).open("rb") as stream:
            return strict_json(stream.read(65537))
    target, settings = read(target_path), normalize_settings(read(settings_path))
    validate_inputs(env, target, settings)
    return env, target, settings
