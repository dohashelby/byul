"""Resolve deployed server inputs without exporting credential values."""
import argparse,hashlib,json,os,pwd,re,shutil,stat,subprocess
from pathlib import Path

def pairs(path):
    return {k:v for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#') and '=' in line for k,v in [line.split('=',1)]}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    home=Path('/home/ubuntu');out=Path(a.output);out.parent.mkdir(mode=0o700,exist_ok=True)
    owner=pwd.getpwnam('ubuntu');notes=[]
    def private(path,text):
        path.write_text(text);path.chmod(0o600);os.chown(path,owner.pw_uid,owner.pw_gid)
    def copy_private(src,name):
        src=Path(src)
        if src.is_symlink() or not src.is_file():raise ValueError('DEPLOYED_INPUT_MISSING_'+name)
        dst=out.parent/name;private(dst,src.read_text());return str(dst)
    required={'CONTEXT','NAMESPACE','SERVER_ID','PLATFORM_URL','AGENT_TOKEN_FILE','VIEWER_ENV_FILE','KUBECONFIG_FILE'}
    candidates=list(home.glob('*install*.env'))+list(home.glob('*/install.env'))+list(Path('/tmp').glob('byul-*/install.env'))+list(home.glob('*install*.conf'))
    configs=[x for x in candidates if x.is_file() and not x.is_symlink() and x.stat().st_mode & 0o077==0 and required<=pairs(x).keys() and pairs(x).get('CONTEXT')=='byul-aws-lab']
    if len(configs)==1:env=configs[0]
    else:
        # Reconstruct from installed values, preserving identities, context, URL, groups and optional cache.
        etc=Path('/etc/serving-runtime')
        unit=Path('/etc/systemd/system/serving-runtime-agent.service').read_text()
        found=re.search(r'--server(?:=|\s+)([A-Za-z0-9_.-]+)',unit)
        if not found:raise ValueError('INSTALLED_SERVER_ID_MISSING')
        context=re.search(r'--(?:kube-)?context(?:=|\s+)([A-Za-z0-9_.-]+)',unit)
        if not context or context[1]!='byul-aws-lab':raise ValueError('INSTALLED_CONTEXT_MISMATCH')
        obs=Path('/srv/serving/observation').stat();data=Path('/srv/serving/runtime/data').stat()
        config={'CONTEXT':context[1],'NAMESPACE':'serving-lab','SERVER_ID':found[1],
                'PLATFORM_URL':pairs(etc/'agent.env')['PLATFORM_URL'],
                'AGENT_TOKEN_FILE':copy_private(etc/'agent.token','installed-agent.token'),
                'VIEWER_ENV_FILE':copy_private(etc/'viewer.env','installed-viewer.env'),
                'KUBECONFIG_FILE':copy_private(etc/'kubeconfig','installed-kubeconfig'),
                'GATEWAY_DATA_GID':str(data.st_gid),'OBSERVE_GID':str(obs.st_gid),
                'OBSERVATION_USER':pwd.getpwuid(obs.st_uid).pw_name}
        if (etc/'platform-ca.pem').is_file():config['PLATFORM_CA_FILE']=copy_private(etc/'platform-ca.pem','installed-platform-ca.pem')
        if (etc/'cache-observer.json').is_file():
            config['CACHE_SETTINGS_FILE']=copy_private(etc/'cache-observer.json','installed-cache-observer.json')
            config['CACHE_SETTINGS_SHA256']=hashlib.sha256(Path(config['CACHE_SETTINGS_FILE']).read_bytes()).hexdigest()
            config['CACHE_KUBECONFIG_FILE']=copy_private(etc/'cache-kubeconfig','installed-cache-kubeconfig')
            config['CACHE_KUBECTL']=pairs(etc/'cache-observer.env')['CACHE_KUBECTL']
        env=out.parent/'install-resolved.env';private(env,''.join(k+'='+v+'\n' for k,v in config.items()))
        notes.append('Original installer input not unique or absent; reconstructed only from deployed unit/config/ownership, preserving optional cache.')
    tokens=None
    choices=[home/'runtime-acceptance.env',home/'runtime-tokens.env',home/'.config/byul/runtime-acceptance.env',Path('/etc/serving-runtime/viewer.env')]
    choices+=list(home.glob('*acceptance*.env'))+list(home.glob('*runtime*.env'))
    for candidate in dict.fromkeys(choices):
        if candidate.is_file() and not candidate.is_symlink():
            d=pairs(candidate)
            if {'RUNTIME_VIEWER_TOKEN','RUNTIME_INFERENCE_TOKEN'}<=d.keys():
                tokens=out.parent/'acceptance-resolved.env';private(tokens,''.join(k+'='+d[k]+'\n' for k in ['RUNTIME_VIEWER_TOKEN','RUNTIME_INFERENCE_TOKEN']));break
    if tokens is None:
        # File names only are diagnostic; token values never leave the host.
        raise ValueError('EXISTING_ACCEPTANCE_TOKEN_FILE_MISSING')
    bundle=Path('/srv/serving/bundles/current').resolve(strict=True)
    target=json.loads((bundle/'target.json').read_text());digest=target['engine']['image_digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise ValueError('APPROVED_ENGINE_DIGEST_REQUIRED')
    intent=out.parent/'intent-resolved.json';private(intent,(bundle/'intent.json').read_text())
    raw=Path('/srv/serving/observation/tetragon.jsonl')
    if not raw.is_file():raise ValueError('DEPLOYED_RAW_TETRAGON_FILE_MISSING')
    config={'runtime_root':'/home/ubuntu/byul-wp7-7741687d/runtime','context':'byul-aws-lab',
            'gateway_url':'http://localhost:18080','kubeconfig':str(home/'.kube/config'),
            'token_env_file':str(tokens),'installer_env':str(env),'resolved_intent':str(intent),
            'bundle_root':'/srv/serving/bundles','engine_digest':digest,'raw_tetragon_file':str(raw)}
    private(out,json.dumps(config,indent=2)+'\n')
    private(out.parent/'input-resolution-notes.json',json.dumps({'assumptions':notes,'credential_values_exported':False},indent=2)+'\n')
    print(json.dumps(config))
if __name__=='__main__':main()
