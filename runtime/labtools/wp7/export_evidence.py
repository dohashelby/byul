"""Make a sanitized copy; never alter server originals or decision fields."""
from __future__ import annotations
import argparse, hashlib, ipaddress, json, re, tarfile
from pathlib import Path
from common import digest, save

CLOUD = re.compile(r'\b(?:i|vol|snap|eipalloc|eipassoc|eni|ami|sg|subnet|vpc)-[0-9a-f]{8,17}\b')
ACCOUNT = re.compile(r'(?<![A-Za-z0-9])\d{12}(?![A-Za-z0-9])')
KEY = re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w.])')
HOST_IP = re.compile(r'\bip-(?:[0-9]{1,3}-){3}[0-9]{1,3}\b')
# Match IPv6 candidates, then validate. Timestamps and digest fragments are preserved.
IPV6 = re.compile(r'(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%[A-Za-z0-9_.-]+)?(?![\w:])')
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')
ASSIGNMENT = re.compile(r'(?im)((?:aws_secret_access_key|aws_session_token|RUNTIME_(?:VIEWER|INFERENCE)_TOKEN)\s*[=:]\s*)[^\s,;"\']+')
BEARER_PATTERN = re.compile(r'(?i)Bearer\s+[^\s"\']+')
SENSITIVE_FIELD = re.compile(r'(?i)^(?:authorization|password|secret_access_key|session_token|access_key_id|(?:inference|viewer|agent|api)_token|token)$')

def alias(kind, value):
    return '[' + kind + '-alias-' + hashlib.sha256(value.encode()).hexdigest()[:16] + ']'

def valid_ip(value):
    try:
        ipaddress.ip_interface(value.split('%')[0])
        return True
    except ValueError:
        return False

class Sanitizer:
    def __init__(self, secrets=()):
        self.secrets = tuple(sorted(set(x for x in secrets if x), key=len, reverse=True))
        self.replacements = 0
    def text(self, text):
        original = text
        for secret in self.secrets:
            text = text.replace(secret, '[REMOVED]')
            text = text.replace(hashlib.sha256(secret.encode()).hexdigest(), '[REMOVED]')
        text = KEY.sub('[REMOVED]', text)
        text = JWT.sub('[REMOVED]', text)
        text = ASSIGNMENT.sub(r'\1[REMOVED]', text)
        text = BEARER_PATTERN.sub('Bearer [REMOVED]', text)
        text = CLOUD.sub(lambda m: alias('resource', m[0]), text)
        text = ACCOUNT.sub(lambda m: alias('account', m[0]), text)
        text = HOST_IP.sub(lambda m: alias('host-address', m[0]) if valid_ip(m[0][3:].replace('-', '.')) else m[0], text)
        text = IPV4.sub(lambda m: alias('address', m[0]) if valid_ip(m[0]) else m[0], text)
        text = IPV6.sub(lambda m: alias('address', m[0]) if valid_ip(m[0]) else m[0], text)
        self.replacements += original != text
        return text
    def value(self, value):
        if isinstance(value, dict):
            return {self.text(k): '[REMOVED]' if SENSITIVE_FIELD.fullmatch(k) else self.value(v) for k,v in value.items()}
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value  # Numbers, booleans and null are never rewritten.

def leaks(text, secrets=()):
    found = []
    for name, pattern in [('key',KEY),('jwt',JWT),('resource',CLOUD),('account',ACCOUNT)]:
        if pattern.search(text): found.append(name)
    if any(valid_ip(m[0][3:].replace('-', '.')) for m in HOST_IP.finditer(text)):
        found.append('host_address')
    if any(valid_ip(m[0]) for pattern in (IPV4,IPV6) for m in pattern.finditer(text)):
        found.append('address')
    if any(x and (x in text or hashlib.sha256(x.encode()).hexdigest() in text) for x in secrets):
        found.append('known_credential')
    if any(m[0].lower() != 'bearer [removed]' for m in BEARER_PATTERN.finditer(text)):
        found.append('bearer')
    return sorted(set(found))

def protected(value, path=()):
    """Snapshot numeric/boolean/null and verdict/time fields, excluding secret fields."""
    rows=[]
    if isinstance(value, dict):
        for k,v in value.items():
            if not SENSITIVE_FIELD.fullmatch(k): rows += protected(v,path+(k,))
    elif isinstance(value,list):
        for i,v in enumerate(value): rows += protected(v,path+(i,))
    elif not isinstance(value,str) or (path and str(path[-1]) in {'result','state','status','phase','passed','started_at','ended_at','finished_at','reason_code','final_outcome','criteria'}):
        rows.append((path,value))
    return rows

def export(source, destination, secrets=()):
    src,dst=Path(source).resolve(),Path(destination).resolve()
    if dst.exists() or src == dst or src in dst.parents:
        raise ValueError('EXPORT_REQUIRES_NEW_SEPARATE_DIRECTORY')
    sanitizer=Sanitizer(secrets)
    prepared=[]
    for p in sorted(src.rglob('*')):
        if p.is_symlink(): raise ValueError('EXPORT_SYMLINK_REJECTED')
        if p.is_dir(): continue
        relative=Path(*[sanitizer.text(x) for x in p.relative_to(src).parts])
        if p.suffix not in {'.json','.jsonl','.txt','.md','.log','.yaml','.yml'}:
            raise ValueError('EXPORT_UNKNOWN_FILE_TYPE')
        text=p.read_text(encoding='utf-8')
        if p.suffix=='.json':
            before=json.loads(text);after=sanitizer.value(before)
            if protected(before)!=protected(after): raise ValueError('DECISION_FIELDS_CHANGED')
            text=json.dumps(after,ensure_ascii=False,indent=2)+'\n'
        elif p.suffix=='.jsonl':
            output=[]
            for line in text.splitlines():
                before=json.loads(line);after=sanitizer.value(before)
                if protected(before)!=protected(after): raise ValueError('DECISION_FIELDS_CHANGED')
                output.append(json.dumps(after,ensure_ascii=False))
            text='\n'.join(output)+'\n'
        else: text=sanitizer.text(text)
        if leaks(text,secrets): raise ValueError('EXPORT_LEAK_SCAN_FAILED')
        prepared.append((relative,text,digest(p)))
    names=[str(x[0]) for x in prepared]
    if len(set(names))!=len(names): raise ValueError('EXPORT_NAME_COLLISION')
    dst.mkdir(parents=True,mode=0o700)
    manifest=[]
    for rel,text,original_hash in prepared:
        target=dst/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        target.write_text(text);target.chmod(0o600)
        manifest.append({'path':str(rel),'original_sha256':original_hash,'export_sha256':digest(target)})
    save(dst/'export-manifest.json',{'files':manifest,'leak_hits':0,'decision_fields_preserved':True,
        'note':'Originals stay on server; addresses and resource identifiers are stable aliases; credentials removed.'})
    archive=dst.with_suffix('.tgz')
    if archive.exists(): raise ValueError('ARCHIVE_EXISTS')
    with tarfile.open(archive,'w:gz') as t: t.add(dst,arcname=dst.name,recursive=True)
    archive.chmod(0o600)
    return {'files':len(manifest),'leak_hits':0,'decision_fields_preserved':True,'archive_sha256':digest(archive)}

def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--secret-env-file',type=Path);a=p.parse_args()
    secrets=[]
    if a.secret_env_file:
        if a.secret_env_file.is_symlink() or a.secret_env_file.stat().st_mode & 0o077: raise ValueError('PRIVATE_ENV_REQUIRED')
        secrets=[line.split('=',1)[1] for line in a.secret_env_file.read_text().splitlines() if '=' in line and not line.startswith('#')]
    print(json.dumps(export(a.source,a.destination,secrets),ensure_ascii=False))
if __name__=='__main__':main()
