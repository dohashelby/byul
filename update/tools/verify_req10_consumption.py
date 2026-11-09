"""A1이 검토한 고정 REQ10 제품 snapshot. 런타임 해시 승인/경로 제외 없음."""
from pathlib import Path
import argparse,hashlib,json,os,sys

# update/release/reviewed-source.json이 고정 범위 전체의 파일 해시를 담는다.
# git이 없어도 검사하고, squash·rebase로 커밋이 바뀌어도 결과가 같다.
# 제품 소스를 바꾸면 --write로 목록을 다시 만들고 그 diff를 검토한다.
# Original B helper/default c9 pin remains unchanged.
MANIFEST_PATH='update/release/reviewed-source.json'
MANIFEST_SCHEMA='byul-reviewed-source/1'
B_SOURCE_COMMIT='57a7537968c0c09a7aab8f741965d64a7eb0f0d5'
B_HELPER_SHA256='858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
PINNED_PATHS=('update/src/guard','update/packs','update/uv.lock',
    'update/pyproject.toml','update/reference/framework')
# 승인 조합을 담은 B 파일도 같은 목록으로 고정한다.
REVIEWED_TOOLS=('update/tools/b1_integration_cpu.py','update/tools/b_lif07_native_capture.py',
    'update/tools/b_lif07_readiness.py')
FRAMEWORK_COMMIT='91e84c778f4fc8ee1108eaf0551bf51f2f6a2061'

def manifest_id(repo):
    path=Path(repo)/MANIFEST_PATH
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

# 기존 기록 키를 유지한다. 값은 커밋이 아니라 파일 해시 목록의 SHA-256이다.
RECEIPTS_PATH = Path(__file__).with_name('reviewed_manifest_receipts.json')

def approved_manifest(repo, receipt_path=RECEIPTS_PATH):
    value=json.loads(Path(receipt_path).read_bytes())
    if value.get('schema')!='byul-source-review-receipts/1':raise ValueError('SOURCE_REVIEW_RECEIPTS_INVALID')
    digest=manifest_id(repo)
    matches=[r for r in value['receipts'] if r.get('manifest_sha256')==digest and r.get('reviewed_by') and r.get('reviewed_at')]
    return digest if len(matches)==1 else None

# Reading current bytes never approves them. No new receipt is shipped by an
# implementation change or by --write. Historical exact gates remain separate.
REVIEWED_COMMON_COMMIT=approved_manifest(Path(__file__).resolve().parents[2])

def scan(repo):
    """고정 범위의 실제 파일 전부. ignored 파일이 import를 가리는 경우도 잡는다."""
    repo=Path(repo);files={}
    for scope in (*PINNED_PATHS,*REVIEWED_TOOLS):
        root=repo/scope
        for path in sorted(root.rglob('*')) if root.is_dir() and not root.is_symlink() else [root]:
            if path.is_dir() and not path.is_symlink():continue
            if path.parent.name=='__pycache__' and path.suffix=='.pyc':continue
            name=path.relative_to(repo).as_posix()
            if path.is_symlink():files[name]='symlink:'+os.readlink(path)
            elif path.is_file():files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files

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

def _verify_manifest(repo):
    if REVIEWED_COMMON_COMMIT is None:
        raise ValueError('SOURCE_REVIEW_RECEIPT_REQUIRED')
    path=repo/MANIFEST_PATH
    if path.is_symlink() or not path.is_file() or manifest_id(repo)!=REVIEWED_COMMON_COMMIT:
        raise ValueError('REQ10_MANIFEST_MISMATCH')
    return verify_inventory(repo)

def verify_inventory(repo):
    """Verify bytes only. This operation confers no source/runtime approval."""
    repo=Path(repo);path=repo/MANIFEST_PATH
    if path.is_symlink() or not path.is_file():raise ValueError('REQ10_MANIFEST_MISMATCH')
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema')!=MANIFEST_SCHEMA or tuple(manifest.get('paths',()))!=PINNED_PATHS:
        raise ValueError('REQ10_MANIFEST_MISMATCH')
    expected,current=manifest['files'],scan(repo)
    for name in sorted(set(expected)|set(current)):
        if name not in current:raise ValueError('pinned source file missing: '+name)
        if name not in expected:raise ValueError('pinned source untracked/ignored file: '+name)
        if expected[name]!=current[name]:raise ValueError('pinned source bytes mismatch: '+name)
    return manifest

def verify(repo):
    repo=Path(repo).resolve();helper=_helper(repo);_verify_imports(repo)
    manifest=_verify_manifest(repo)
    from guard.models import load_pack
    if load_pack(repo/'update/packs/PACK-BASELINE').ref().model_dump(mode='json')!=manifest['pack_ref']:
        raise ValueError('REQ10_PACK_PIN_MISMATCH')
    framework=json.loads((repo/'update/reference/framework/UPSTREAM.json').read_text(encoding='utf-8'))
    if framework['commit']!=FRAMEWORK_COMMIT:raise ValueError('REQ10_FRAMEWORK_PIN_MISMATCH')
    canonical=json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()
    return {'reviewed_common_commit':REVIEWED_COMMON_COMMIT,'B_source_commit':B_SOURCE_COMMIT,
        'fixed_B_helper_sha256':B_HELPER_SHA256,'pinned_paths':list(PINNED_PATHS),
        'files_verified':len(manifest['files']),'verified_manifest_sha256':hashlib.sha256(canonical).hexdigest(),
        'pack_ref':manifest['pack_ref'],'paths_excluded':[],'runtime_hash_approval':False,
        'original_B_default_pin_changed':False,'actual_CPU_execution':False}

def write_manifest(repo):
    from guard.models import load_pack
    repo=Path(repo).resolve()
    manifest={'schema':MANIFEST_SCHEMA,'paths':list(PINNED_PATHS),
        'pack_ref':load_pack(repo/'update/packs/PACK-BASELINE').ref().model_dump(mode='json'),
        'files':scan(repo)}
    path=repo/MANIFEST_PATH;path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=1,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
    return manifest_id(repo)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true');parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.write:print(write_manifest(args.repo));return
    result=verify(args.repo)
    if args.output:
        with args.output.open('x',encoding='utf-8',newline='\n') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
