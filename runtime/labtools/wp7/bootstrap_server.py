"""Approved server setup, with original command/output evidence."""
import argparse,shlex
from pathlib import Path
from common import BASELINE,approval,private_json,run,save,now
from verify_artifacts import main as verify_artifacts

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--approval-file',required=True)
    a=p.parse_args();approval(a.approval_file);c=private_json(a.config)
    root=Path(__file__).resolve().parent;checkout=Path(c['runtime_root']).parent
    evidence=root/'bootstrap';evidence.mkdir(mode=0o700,exist_ok=True)
    commands=[];outputs=[];started=now();status=1
    def command(argv,timeout=120):
        commands.append(shlex.join(map(str,argv)));code,text=run(argv,timeout=timeout,cwd=root)
        outputs.append(text)
        if code:raise ValueError('BOOTSTRAP_COMMAND_FAILED')
        return text
    try:
        verify_artifacts()
        if not checkout.exists():command(['git','clone',root/'byul-7741687d.bundle',checkout])
        command(['git','-C',checkout,'bundle','verify',root/'byul-7741687d.bundle'])
        if command(['git','-C',checkout,'rev-parse','HEAD']).strip()!=BASELINE:raise ValueError('BASELINE_MISMATCH')
        command(['python3','-m','venv','--system-site-packages',checkout/'runtime/.venv'])
        python=checkout/'runtime/.venv/bin/python'
        # Runtime has no mandatory dependencies; use the installer's offline .pth/metadata method.
        script="import pathlib,sys,tomllib; root=pathlib.Path(sys.argv[1]); site=pathlib.Path(sys.argv[2]); site.mkdir(parents=True,exist_ok=True); (site/'wp7-runtime.pth').write_text(str(root/'src')+'\\n'+str(root)+'\\n'); version=tomllib.loads((root/'pyproject.toml').read_text())['project']['version']; d=site/('serving_runtime-'+version+'.dist-info'); d.mkdir(exist_ok=True); (d/'METADATA').write_text('Metadata-Version: 2.1\\nName: serving-runtime\\nVersion: '+version+'\\n')"
        site=command([python,'-c','import sysconfig;print(sysconfig.get_path("purelib"))']).strip()
        command([python,'-c',script,checkout/'runtime',site])
        # LAB callback preflight imports guard from the matching update checkout.
        # update requires Python 3.12; do not silently substitute another package.
        command([python,'-c','import sys; assert sys.version_info[:2] == (3,12), "LABTOOLS_REQUIRE_PYTHON_312"'])
        command([python,'-m','pip','install','-e',checkout/'update'],timeout=300)
        command([python,'-c','from guard.adapters.vllm_engine_callback_sensor import TAG; assert TAG.fullmatch("byul-cb-" + "0"*32)'])
        command([python,'-B','-m','serving_runtime','--version'])
        command(['sudo','ctr','-n','k8s.io','images','import',root/'serving-gateway-rt-7741687d.tar'],timeout=300)
        if not c.get('customer_install_validation'):
            command(['sudo',python,root/'prepare_lab_bundle.py','--config',a.config,'--approval-file',a.approval_file],timeout=60)
        status=0
    finally:
        for name,text in [('command.txt','\n'.join(commands)),('output.txt','\n'.join(outputs))]:
            (evidence/name).write_text(text+'\n');(evidence/name).chmod(0o600)
        save(evidence/('result-'+str(__import__('time').time_ns())+'.json'),
             {'started_at':started,'ended_at':now(),'exit_code':status,'passed':status==0,
              'criteria':'Transferred artifact hashes and baseline match; local package installed; image imported'})
    if status:raise SystemExit(status)
if __name__=='__main__':main()
