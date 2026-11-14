"""Reject non-finite durations before any CLI runtime starts."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
import io
from unittest.mock import patch

import pytest

from serving_runtime.cli import main
from serving_runtime.config.config import MVPConfig
from serving_runtime.config.node_config import ALIASES, load_node_defaults

OPTIONS = {
    "relay_timeout_s": "--relay-timeout",
    "stream_idle_timeout_s": "--stream-idle-timeout",
    "request_deadline_s": "--request-deadline",
    "request_header_timeout_s": "--request-header-timeout",
    "request_body_timeout_s": "--request-body-timeout",
    "collector_interval_s": "--collector-interval",
    "collector_stale_after_s": "--collector-stale-after",
    "asset_interval_s": "--asset-interval",
    "retention_max_age_days": "--retention-max-age-days",
    "retention_interval_s": "--retention-interval",
    "cilium_timeout_s": None,
}
BAD = ["nan", "inf", "-inf"]


def invoke(arguments):
    with (
        patch("serving_runtime.gateway.app.run") as run,
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        try:
            code = main(arguments)
        except SystemExit as error:
            code = error.code
    return code, run


def test_all_float_configuration_fields_are_covered():
    assert {f.name for f in fields(MVPConfig) if f.type is float} == set(OPTIONS)


@pytest.mark.parametrize("field", OPTIONS)
@pytest.mark.parametrize("literal", BAD)
def test_config_rejects_non_finite(field, literal):
    with pytest.raises(ValueError):
        MVPConfig(**{field: float(literal)})


@pytest.mark.parametrize("field", OPTIONS)
@pytest.mark.parametrize("literal", BAD)
def test_toml_rejects_non_finite_before_cli_start(tmp_path, field, literal):
    path = tmp_path / "node.toml"
    path.write_text(f"{field} = {literal}\n")
    with pytest.raises(ValueError):
        load_node_defaults(path)
    code, run = invoke(["mvp", "--config", str(path)])
    assert code == 2
    run.assert_not_called()


@pytest.mark.parametrize("field,option", [(f, o) for f, o in OPTIONS.items() if o])
@pytest.mark.parametrize("literal", BAD)
def test_cli_rejects_non_finite_before_runtime_start(field, option, literal):
    code, run = invoke(["mvp", f"{option}={literal}"])
    assert code == 1
    run.assert_not_called()


@pytest.mark.parametrize("field,option", [(f, o) for f, o in OPTIONS.items() if o])
def test_invalid_toml_is_rejected_even_when_cli_overrides_it(tmp_path, field, option):
    path = tmp_path / "node.toml"
    path.write_text(f"{field} = nan\n")
    code, run = invoke(["mvp", "--config", str(path), f"{option}=0.5"])
    assert code == 2
    run.assert_not_called()


@pytest.mark.parametrize("field", OPTIONS)
@pytest.mark.parametrize("value", [0, -1])
def test_toml_rejects_nonpositive_values_at_load(tmp_path, field, value):
    path = tmp_path / "node.toml"
    path.write_text(f"{field} = {value}\n")
    with pytest.raises(ValueError):
        load_node_defaults(path)


@pytest.mark.parametrize("field", OPTIONS)
def test_finite_positive_values_keep_toml_and_cli_precedence(tmp_path, field):
    path = tmp_path / "node.toml"
    path.write_text(f"{field} = 0.5\n")
    assert getattr(MVPConfig(**{field: 0.5}), field) == 0.5
    assert load_node_defaults(path)[ALIASES.get(field, field)] == 0.5
    args = ["mvp", "--config", str(path)]
    if OPTIONS[field]:
        args.append(f"{OPTIONS[field]}=0.25")
    code, run = invoke(args)
    assert code == 0
    assert getattr(run.call_args.args[0], field) == (0.25 if OPTIONS[field] else 0.5)
