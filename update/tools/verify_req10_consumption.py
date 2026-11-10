"""A1이 검토한 고정 REQ10 제품 snapshot. 런타임 해시 승인/경로 제외 없음."""
from datetime import datetime
from pathlib import Path
import argparse,hashlib,json,os,re,sys

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
    'update/tools/b_lif07_readiness.py','update/tools/b_lif08_fault_control.py')
FRAMEWORK_COMMIT='91e84c778f4fc8ee1108eaf0551bf51f2f6a2061'

def manifest_id(repo):
    path=Path(repo)/MANIFEST_PATH
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

# 기존 기록 키를 유지한다. 값은 커밋이 아니라 파일 해시 목록의 SHA-256이다.
RECEIPTS_PATH = Path(__file__).with_name('reviewed_manifest_receipts.json')
RECEIPTS_SCHEMA = 'byul-source-review-receipts/1'
RECEIPT_KEYS = frozenset({'manifest_sha256', 'reviewed_by', 'reviewed_at'})
MAX_RECEIPTS = 4096
MAX_REVIEWER_CHARS = 128
_SHA256_HEX = re.compile(r'[0-9a-f]{64}')
_INVALID = 'SOURCE_REVIEW_RECEIPTS_INVALID'

def _unique_keys(pairs):
    keys=[key for key,_ in pairs]
    if len(set(keys))!=len(keys):raise ValueError(_INVALID)
    return dict(pairs)

def _valid_reviewer(value):
    return (type(value) is str and 0<len(value)<=MAX_REVIEWER_CHARS and value==value.strip()
            and all(ch.isprintable() for ch in value))

def _valid_time(value):
    if type(value) is not str:return False
    try:parsed=datetime.fromisoformat(value)
    except ValueError:return False
    # 날짜만 있거나 시간대가 없는 시각은 검토 시점을 정하지 못한다.
    return 'T' in value and parsed.tzinfo is not None and parsed.utcoffset() is not None

def read_receipts(receipt_path=RECEIPTS_PATH):
    """검토 기록 문서 전체를 엄격히 읽는다. 잘못된 행이 하나라도 있으면 그 행만 건너뛰지 않고 문서 전체를 거부한다.

    검토자 문자열은 형식만 확인한다. 검토자 신원·독립성은 저장소의 검토·변경 관리 절차가 보장한다.
    """
    path=Path(receipt_path)
    if path.is_symlink() or not path.is_file():raise ValueError(_INVALID)
    try:value=json.loads(path.read_bytes(),object_pairs_hook=_unique_keys)
    except (ValueError,UnicodeDecodeError):raise ValueError(_INVALID) from None
    if (type(value) is not dict or set(value)!={'schema','receipts'} or value['schema']!=RECEIPTS_SCHEMA
            or type(value['receipts']) is not list or len(value['receipts'])>MAX_RECEIPTS):
        raise ValueError(_INVALID)
    for row in value['receipts']:
        if (type(row) is not dict or set(row)!=RECEIPT_KEYS or type(row['manifest_sha256']) is not str
                or not _SHA256_HEX.fullmatch(row['manifest_sha256'])
                or not _valid_reviewer(row['reviewed_by']) or not _valid_time(row['reviewed_at'])):
            raise ValueError(_INVALID)
    return value['receipts']

def approved_manifest(repo, receipt_path=RECEIPTS_PATH):
    """현재 목록 SHA에 맞는 검토 기록이 정확히 한 건일 때만 그 SHA를 돌려준다. 같은 SHA가 두 건 이상이면 미승인이다."""
    receipts=read_receipts(receipt_path)
    digest=manifest_id(repo)
    if digest is None:return None
    matches=[row for row in receipts if row['manifest_sha256']==digest]
    return digest if len(matches)==1 else None

def receipt_state(repo, receipt_path=RECEIPTS_PATH):
    """(승인된 목록 SHA 또는 None, 거부 코드 또는 None). 잘못된 기록 문서는 미승인과 거부 코드로 남긴다."""
    try:return approved_manifest(repo,receipt_path),None
    except ValueError as error:return None,str(error)

# Reading current bytes never approves them. No new receipt is shipped by an
# implementation change or by --write. Historical exact gates remain separate.
REVIEWED_COMMON_COMMIT,RECEIPTS_ERROR=receipt_state(Path(__file__).resolve().parents[2])

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
    if RECEIPTS_ERROR is not None:
        raise ValueError(RECEIPTS_ERROR)
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
