import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import selected_copy_checks as checks
from tools import selected_runtime_check as tool


def test_complete_selected_file_absence_and_findings_do_not_export_values(tmp_path):
    path=tmp_path/'journal.jsonl';path.write_text('safe synthetic fixture\n')
    secret='SYNTHETIC_PRIVATE_CREDENTIAL_001'
    result=checks.copies([path],{'credential':secret})
    assert result['cases'][0]['result']=='passed' and result['recursive_scan'] is False
    path.write_text(secret+'\n')
    result=checks.copies([path],{'credential':secret})
    assert result['cases'][0]['result']=='failed'
    assert result['cases'][0]['facts']['forbidden_occurrences']=={'credential':1}
    assert secret not in json.dumps(result)


def test_symlinks_directories_and_oversize_files_never_receive_absence_pass(tmp_path,monkeypatch):
    path=tmp_path/'raw';path.write_bytes(b'abcde')
    link=tmp_path/'link';link.symlink_to(path)
    monkeypatch.setattr(checks,'MAX_FILE_BYTES',4)
    for selected in [tmp_path,link,path]:
        result=checks.scan_file(selected,{'marker':'synthetic-marker'})
        assert result['result']=='unknown'


def test_reader_roles_require_actual_sentinel_and_never_treat_sudo_failure_as_denial(monkeypatch,tmp_path):
    monkeypatch.setattr(checks.pwd,'getpwnam',lambda name:object())
    monkeypatch.setattr(checks.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    assert checks.reader_access(tmp_path/'x','nobody',1)['state']=='unknown'
    monkeypatch.setattr(checks.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=81))
    result=checks.reader_access(tmp_path/'x','nobody',1)
    assert result['state']=='observed' and result['readable'] is False


def test_known_os_permission_failure_is_preserved_and_unknown_accounts_are_unknown(tmp_path,monkeypatch):
    path=tmp_path/'journal';path.write_text('safe')
    monkeypatch.setattr(checks,'reader_access',lambda p,u,t:{'state':'observed','readable':True})
    result=checks.copies([path],{'marker':'synthetic-marker'},allowed_reader='root',denied_reader='nobody')
    assert result['cases'][0]['result']=='failed'


def test_file_count_and_reader_pair_are_bounded(tmp_path):
    with pytest.raises(ValueError):checks.copies([tmp_path]*17,{'x':'synthetic'})
    with pytest.raises(ValueError):checks.copies([tmp_path],{'x':'synthetic'},allowed_reader='root')


def test_cli_uses_environment_values_without_saving_them(tmp_path,monkeypatch):
    path=tmp_path/'journal';path.write_text('safe synthetic fixture')
    value='SYNTHETIC_MARKER_NOT_TO_EXPORT';monkeypatch.setenv('BYUL_TEST_MARKER',value)
    output=tmp_path/'report.json'
    assert tool.main(['copies','--file',str(path),'--marker-env','BYUL_TEST_MARKER','--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['cases'][0]['result']=='passed' and value not in output.read_text()
    assert result['volume_settings_evidence']['state']=='not_run'
    assert result['all_active_copies_inventoried'] is False


def test_volume_settings_do_not_claim_file_device_binding(tmp_path):
    path=tmp_path/'journal';path.write_text('safe')
    public=tmp_path/'volumes.json';public.write_text(json.dumps({'Volumes':[{'VolumeId':'vol-0123456789abcdef0','Encrypted':True,'KmsKeyId':'public-key-ref'}]}))
    result=checks.copies([path],{'marker':'synthetic-marker'},volume_evidence=public)
    evidence=result['volume_settings_evidence']
    assert evidence['state']=='observed' and evidence['volumes'][0]['encrypted'] is True
    assert evidence['file_volume_binding_verified'] is False
    assert 'public-key-ref' not in json.dumps(result)


@pytest.mark.parametrize('change',['replace','chmod'])
def test_reader_generation_change_never_certifies_current_path(tmp_path,monkeypatch,change):
    path=tmp_path/'journal';path.write_text('safe snapshot')
    replacement=tmp_path/'replacement';replacement.write_text('FORBIDDEN-SYNTHETIC-MARKER')
    calls=[]
    def access(p,user,timeout):
        calls.append(user)
        if len(calls)==1:
            if change=='replace':replacement.replace(path)
            else:path.chmod(0o600 if path.stat().st_mode&0o077 else 0o644)
        return {'state':'observed','readable':user=='root'}
    monkeypatch.setattr(checks,'reader_access',access)
    result=checks.copies([path],{'marker':'FORBIDDEN-SYNTHETIC-MARKER'},allowed_reader='root',denied_reader='nobody')
    row=result['cases'][0]
    assert row['result']=='unknown'
    assert row['facts']['snapshot_complete_stable'] is True
    assert row['facts']['current_path_matches_snapshot'] is False
    assert row['facts']['reader_generation_bound'] is False
