"""EXAMPLE collector boundaries; actual installed plugin execution runs separately."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors.plugin_loading import CASES, project
from tools import b1_plugin_cpu as collector


@pytest.fixture
def installed_fixture(tmp_path, monkeypatch):
    original = Path(collector.__file__).resolve().parents[1] / 'tests/fixtures/b_plugin_probe'
    root = tmp_path / 'repo'
    copied = root / collector.FIXTURE_SCOPE[0]
    shutil.copytree(original, copied, ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build'))
    distribution = SimpleNamespace(version='0.0.1', files=[Path(name) for name in collector.FIXTURE_SHA256],
                                   locate_file=lambda name: copied / str(name))
    monkeypatch.setattr(collector.metadata, 'distribution', lambda name: distribution)
    def entries(*, group):
        return [SimpleNamespace(name=name, value=value, dist=SimpleNamespace(metadata={'Name': collector.FIXTURE_DIST}))
                for name, value in collector.FIXTURE_ENTRYPOINTS[group].items()]
    monkeypatch.setattr(collector.metadata, 'entry_points', entries)
    return root, copied, distribution


def test_approved_fixture_manifest_needs_no_git_history(installed_fixture):
    root, _, _ = installed_fixture
    assert collector.verify_fixture(root) == collector.FIXTURE_SHA256
    assert not (root / '.git').exists()


def test_modified_fixture_cannot_regenerate_its_own_expected_hash(installed_fixture):
    root, copied, _ = installed_fixture
    (copied / 'byul_b_cpu_probe/events.py').write_text('PRIVATE_MODIFIED_FIXTURE')
    with pytest.raises(ValueError, match='PLUGIN_CPU_FIXTURE_SOURCE_MISMATCH'):
        collector.verify_fixture(root)


def test_unknown_installed_entrypoint_is_rejected(installed_fixture, monkeypatch):
    root, _, _ = installed_fixture
    original = collector.metadata.entry_points
    def extra(*, group):
        return original(group=group) + [SimpleNamespace(name='unknown', value='private:call',
            dist=SimpleNamespace(metadata={'Name': collector.FIXTURE_DIST}))]
    monkeypatch.setattr(collector.metadata, 'entry_points', extra)
    with pytest.raises(ValueError, match='PLUGIN_CPU_FIXTURE_ENTRYPOINT_MISMATCH'):
        collector.verify_fixture(root)


def test_profile_selects_exact_source_and_preserves_reference_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(collector, 'verify_checkout', lambda repo: None)
    monkeypatch.setattr(collector, '_verify_source', lambda root, **kw: seen.append(kw['expected_commit']))
    monkeypatch.setattr(collector, '_validate_target031_dependencies', lambda: seen.append('target-deps'))
    monkeypatch.setattr(collector, 'verify_fixture', lambda repo: {})
    collector.verify_profile(tmp_path, 'reference5d')
    collector.verify_profile(tmp_path, 'target031')
    assert seen == [collector.SOURCE_COMMIT, collector.TARGET031_COMMIT, 'target-deps']
    with pytest.raises(ValueError, match='PLUGIN_CPU_SOURCE_PROFILE_INVALID'):
        collector.verify_profile(tmp_path, 'arbitrary-head')


def test_parent_postcheck_error_preserves_direct_child_events_and_independent_violation(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, 'verify_profile', lambda *a: collector.FIXTURE_SHA256)
    monkeypatch.setattr(collector, 'verify_loaded_target', lambda root: None)
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(version=SimpleNamespace(cuda=None)))
    monkeypatch.setattr(collector, 'post_validation', lambda *a, **kw: {'status':'unavailable','error_type':'OSError'})
    def launch(argv, **kw):
        case = argv[argv.index('--case') + 1]
        work = Path(argv[argv.index('--workdir') + 1])
        row = {'case_id':case, 'status':'completed','target_started':True,
               'event_collection_status':'completed',
               'events':[{'plugin':'byul-unapproved','phase':'import'}] if case=='general-disabled' else [],
               'after':{'status':'completed','events':[{'plugin':'byul-approved','phase':'general_hook'}]}}
        (work / (case + '.json')).write_text(json.dumps(row))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(collector.subprocess,'run',launch)
    record = collector.capture(tmp_path/'source',tmp_path/'capture',source_profile='target031')
    assert record['capture_status']=='incomplete' and len(record['results'])==len(CASES)
    assert record['source_commit']==collector.TARGET031_COMMIT
    assert record['source_validation']['post']['status']=='unavailable'
    assert project(record['results'])['plugin_loading_confirmed_violations']['value']==['general-disabled']
    assert record['installed_target'] is None and not record['runner_hook_verified']


def test_collector_byte_drift_is_post_mismatch_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(collector,'verify_profile',lambda *a: {})
    monkeypatch.setattr(collector,'verify_loaded_target',lambda root: None)
    result = collector.post_validation(tmp_path,'reference5d',producer_sha256='0'*64)
    assert result=={'status':'mismatch','error_type':'ValueError'}


@pytest.mark.parametrize('optimization',['-O','-OO'])
def test_optimized_cli_stops_before_source_or_output(tmp_path,optimization):
    update=Path(collector.__file__).resolve().parents[1]
    output=tmp_path/'output'
    result=subprocess.run([sys.executable,optimization,str(Path(collector.__file__).resolve()),
        '--source-root',str(tmp_path/'missing'),'--workdir',str(output)],cwd=update,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(update/'src'),str(update)])},text=True,capture_output=True)
    assert result.returncode!=0 and 'PLUGIN_CPU_REQUIRES_ASSERTIONS' in result.stderr
    assert not output.exists()


def test_foreign_lif14_gate_cannot_replace_the_fixed_verifier(tmp_path):
    update=Path(collector.__file__).resolve().parents[1]
    foreign=tmp_path/'foreign'/'tools';foreign.mkdir(parents=True)
    (foreign/'__init__.py').write_text('')
    marker=tmp_path/'noop-called'
    (foreign/'lif14_profiler_cpu.py').write_text("def verify_component(*a):\n from pathlib import Path\n Path("+repr(str(marker))+").write_text('noop')\n")
    output=tmp_path/'output'
    result=subprocess.run([sys.executable,str(Path(collector.__file__).resolve()),
        '--source-root',str(tmp_path/'missing'),'--workdir',str(output)],cwd=update,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(foreign.parent),str(update/'src'),str(update)])},
        text=True,capture_output=True)
    assert result.returncode!=0 and 'PLUGIN_CPU_COMPONENT_GATE_IDENTITY_MISMATCH' in result.stderr
    assert not marker.exists() and not output.exists()
