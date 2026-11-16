"""Group collision diagnostics before installer mutation."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "wp7_installer", Path(__file__).resolve().parents[1] / "deploy/install/install_runtime_node.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.mark.parametrize("group,setting", [("serving-bundles", "GATEWAY_DATA_GID"), ("serving-observe", "OBSERVE_GID")])
def test_collision_reports_alternative_setting_before_host_mutation(group, setting):
    config = {"GATEWAY_DATA_GID": "10000", "OBSERVE_GID": "10010"}
    with (
        patch.object(installer, "check_sudoers_conflicts"),
        patch.object(installer.shutil, "which", return_value="/tool"),
        patch.object(installer.grp, "getgrgid", return_value=SimpleNamespace(gr_name="other")),
        patch.object(installer, "run") as run,
    ):
        # Reach either configured group through the real preflight entry point.
        if group == "serving-observe":
            installer.grp.getgrgid.side_effect = [
                SimpleNamespace(gr_name="serving-bundles"),
                SimpleNamespace(gr_name="other"),
            ]
        with (
            patch.object(installer.grp, "getgrnam", side_effect=KeyError),
            pytest.raises(installer.InstallError, match=setting),
        ):
            installer.preflight(config, SimpleNamespace())
        run.assert_not_called()


def test_unused_replacement_and_matching_existing_groups_are_accepted():
    with (
        patch.object(installer.grp, "getgrgid", side_effect=KeyError),
        patch.object(installer.grp, "getgrnam", side_effect=KeyError),
    ):
        installer.group_check("serving-bundles", 12000)
    with (
        patch.object(installer.grp, "getgrgid", return_value=SimpleNamespace(gr_name="serving-bundles")),
        patch.object(installer.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=12000)),
    ):
        installer.group_check("serving-bundles", 12000)


def test_existing_group_name_requires_its_actual_gid():
    with (
        patch.object(installer.grp, "getgrgid", side_effect=KeyError),
        patch.object(installer.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=12000)),
        pytest.raises(installer.InstallError, match="GATEWAY_DATA_GID"),
    ):
        installer.group_check("serving-bundles", 10000)
