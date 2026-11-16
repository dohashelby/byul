"""Collect and sanitize preparation diagnostics before the laptop requests stop."""
import json,os,shutil,time,uuid
from pathlib import Path
from common import run
from export_evidence import export

def main():
    root=Path(__file__).resolve().parent;out=root/('failure-'+uuid.uuid4().hex);out.mkdir(mode=0o700)
    secrets=[]
    for env in [root/'acceptance-resolved.env',root/'installed-viewer.env',Path('/home/ubuntu/runtime-acceptance.env')]:
        if env.is_file() and not env.is_symlink() and env.stat().st_mode & 0o077==0:
            secrets.extend(v for line in env.read_text().splitlines() if '=' in line and not line.startswith('#') for k,v in [line.split('=',1)] if 'TOKEN' in k)
    commands=[['sudo','journalctl','--no-pager','-n','180','-u','serving-runtime-agent','-u','serving-runtime-tetragon-status','-u','serving-runtime-gpu-status','-u','serving-runtime-monitor-window'],
              ['systemctl','--user','status','byul-observation-feed','--no-pager'],
              ['kubectl','--context','byul-aws-lab','-n','serving-lab','get','pods','-o','json']]
    texts=[]
    for argv in commands:
        code,text=run(argv,timeout=12);texts.append(json.dumps({'argv':argv,'exit_code':code})+'\n'+text)
    (out/'server-logs.txt').write_text('\n'.join(texts));(out/'server-logs.txt').chmod(0o600)
    if (root/'bootstrap').is_dir():shutil.copytree(root/'bootstrap',out/'bootstrap')
    for log in root.glob('evidence-round*.worker.log'):shutil.copy2(log,out/log.name)
    # Never include private configs, approval inputs, tokens, SSH material, or raw bundle files.
    result=export(out,out.with_name(out.name+'-export'),secrets)
    result['archive']=str(out.with_name(out.name+'-export').with_suffix('.tgz'));print(json.dumps(result))
if __name__=='__main__':main()
