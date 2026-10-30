"""A1이 검토한 고정 REQ10 제품 snapshot. 런타임 해시 승인/경로 제외 없음."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys

# Reviewed native15/OBS02 profiler checkpoint; full common contracts retained, including
# A2 IPC/REQ12/native8~14 and all previous full-scope gates.
# Original B helper/default c9 pin remains unchanged.
REVIEWED_COMMON_COMMIT='a2dd6ea90202d4e36588c3c2e083059de7384353'
B_SOURCE_COMMIT='57a7537968c0c09a7aab8f741965d64a7eb0f0d5'
B_HELPER_SHA256='858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
PINNED_PATHS=('update/src/guard','update/packs','update/uv.lock',
    'update/pyproject.toml','update/reference/framework')
PACK31_REF={'kind':'PACK','id':'PACK-BASELINE','revision':31,
    'sha256':'54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}

def _verify_imports(repo):
    root=repo/'update/src/guard'
    for name,module in tuple(sys.modules.items()):
        if name=='guard' or name.startswith('guard.'):
            filename=getattr(module,'__file__',None)
            if not filename or not Path(filename).resolve().is_relative_to(root):
                raise ValueError('REQ10_COMMON_IMPORT_MISMATCH')
    for name in ('tests.builders',):
        if name in sys.modules and not Path(sys.modules[name].__file__).resolve().is_relative_to(repo/'update/tests'):
            raise ValueError('REQ10_FIXTURE_IMPORT_MISMATCH')

def _helper(repo):
    path=repo/'update/tools/b1_integration_cpu.py'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=B_HELPER_SHA256:
        raise ValueError('REQ10_FIXED_B_HELPER_MISMATCH')
    from tools import b1_integration_cpu as helper
    if Path(helper.__file__).resolve()!=path or tuple(helper.PINNED_PATHS)!=PINNED_PATHS:
        raise ValueError('REQ10_FIXED_B_HELPER_MISMATCH')
    return helper

def verify(repo):
    repo=Path(repo).resolve();helper=_helper(repo);_verify_imports(repo)
    # Explicit literal commit replaces the old consumer baseline only for this
    # A1 entry. The original B module/constants/selected overlay stay untouched.
    # Its same full ancestor/index/archive bytes/shadow algorithm is retained.
    helper.verify_snapshot(repo,REVIEWED_COMMON_COMMIT,PINNED_PATHS)
    from guard.models import load_pack
    if load_pack(repo/'update/packs/PACK-BASELINE').ref().model_dump(mode='json')!=PACK31_REF:
        raise ValueError('REQ10_PACK31_PIN_MISMATCH')
    framework=json.loads((repo/'update/reference/framework/UPSTREAM.json').read_text(encoding='utf-8'))
    if framework['commit']!='91e84c778f4fc8ee1108eaf0551bf51f2f6a2061':raise ValueError('REQ10_FRAMEWORK_PIN_MISMATCH')
    raw=subprocess.check_output(['git','ls-tree','-r','--name-only','-z',REVIEWED_COMMON_COMMIT,'--',*PINNED_PATHS],cwd=repo)
    names=[x.decode() for x in raw.split(b'\0') if x]
    hashes={n:hashlib.sha256((repo/n).read_bytes()).hexdigest() for n in names}
    canonical=json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()
    return {'reviewed_common_commit':REVIEWED_COMMON_COMMIT,'B_source_commit':B_SOURCE_COMMIT,
        'current_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip(),
        'fixed_B_helper_sha256':B_HELPER_SHA256,'pinned_paths':list(PINNED_PATHS),
        'files_verified':len(hashes),'verified_manifest_sha256':hashlib.sha256(canonical).hexdigest(),
        'pack_ref':PACK31_REF,'paths_excluded':[],'runtime_hash_approval':False,
        'original_B_default_pin_changed':False,'actual_CPU_execution':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.repo)
    if args.output:
        with args.output.open('x',encoding='utf-8',newline='\n') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
