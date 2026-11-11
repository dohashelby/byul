"""Build an owned, offline Phi-3 Vision SUP-08 fixture from a pinned model snapshot.

The Microsoft model files are read-only inputs. Large weights are hard-linked into a
derived local snapshot. Only the processor auto_map is changed. Two deterministic Git
commits contain the same processor plus a harmless import marker; the second commit
adds a variant comment. Their SHA-1s become the approved and unapproved code revisions.
Nothing is uploaded to Hugging Face or another service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SOURCE_MODEL = 'microsoft/Phi-3-vision-128k-instruct'
SOURCE_REVISION = 'ed2772fabe9dc9acd0caad54b62761d92520cc44'
CODE_REPOSITORY = 'local-byul/sup08-phi3v-processor'
PROCESSOR_SHA256 = '99d029093d9a99478fea4e6cea92b5ff47323903ca5e1af9cc441b0a64d126a8'
IMAGE_PROCESSOR_SHA256 = '8470db7ad9c2483f60ca14ed84e5eba99be0ff15b66ef9958754879b85d492bc'
WEIGHT_SHA256 = {
    'model-00001-of-00002.safetensors': '175b2fe918dd8bd2549e3441615ee0c6d7b1f6d638c0104a614546f55c273482',
    'model-00002-of-00002.safetensors': 'e61ece5a8f0c9663afa06cc22799056f5cc084fb993518bf036dc8e268fd4c94',
}
MARKER_CODE = '''\n# BYUL SUP-08: harmless import marker in an isolated, operator-owned lab cache.
import hashlib as _byul_hashlib
import json as _byul_json
import os as _byul_os
from pathlib import Path as _ByulPath
import re as _byul_re
import time as _byul_time
_byul_root = _byul_os.environ.get("BYUL_CODE_MARKER_ROOT")
_byul_revision = _ByulPath(__file__).parent.name
if _byul_root and _byul_re.fullmatch(r"[0-9a-f]{40}", _byul_revision):
    _byul_file = _ByulPath(_byul_root) / (_byul_revision + ".json")
    _byul_file.parent.mkdir(parents=True, exist_ok=True)
    _byul_data = {"schema": "guard-code-import-marker/1", "revision": _byul_revision,
                  "module_sha256": _byul_hashlib.sha256(_ByulPath(__file__).read_bytes()).hexdigest(),
                  "pid": _byul_os.getpid(), "monotonic_ns": _byul_time.monotonic_ns()}
    try:
        with _byul_file.open("x", encoding="utf-8") as _byul_stream:
            _byul_json.dump(_byul_data, _byul_stream, sort_keys=True)
    except FileExistsError:
        pass
'''


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True, env=env)
    return result.stdout.strip()


def _commit(repo: Path, message: str, date: str) -> str:
    env = {**os.environ, 'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date,
           'GIT_AUTHOR_NAME': 'BYUL SUP-08 fixture', 'GIT_AUTHOR_EMAIL': 'fixture@byul.invalid',
           'GIT_COMMITTER_NAME': 'BYUL SUP-08 fixture', 'GIT_COMMITTER_EMAIL': 'fixture@byul.invalid'}
    _git(repo, 'add', '.', env=env)
    _git(repo, 'commit', '-m', message, env=env)
    return _git(repo, 'rev-parse', 'HEAD')


def build(source: Path, output: Path, *, run_id: str) -> dict:
    source = source.resolve(strict=True)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', run_id) or output.exists():
        raise ValueError('SUP08_FIXTURE_OUTPUT_OR_RUN_ID_INVALID')
    required = ('config.json', 'preprocessor_config.json', 'processing_phi3_v.py',
                'image_processing_phi3_v.py', 'tokenizer.json', 'tokenizer_config.json',
                'model.safetensors.index.json', *WEIGHT_SHA256)
    if any(not (source / name).is_file() for name in required):
        raise ValueError('SUP08_FIXTURE_SOURCE_INCOMPLETE')
    for name, expected in {'processing_phi3_v.py': PROCESSOR_SHA256,
                           'image_processing_phi3_v.py': IMAGE_PROCESSOR_SHA256}.items():
        if _sha(source / name) != expected:
            raise ValueError(f'SUP08_FIXTURE_SOURCE_CHANGED:{name}')
    # The large files are verified as they are inventoried below; never trust only a filename.
    output.mkdir(parents=True)
    code_repo = output / 'code-repo'
    code_repo.mkdir()
    _git(code_repo, 'init', '-q')
    _git(code_repo, 'config', 'core.autocrlf', 'false')
    text = (source / 'processing_phi3_v.py').read_text(encoding='utf-8')
    anchor = 'import re\nfrom typing import List, Optional, Union\n'
    if text.count(anchor) != 1:
        raise ValueError('SUP08_FIXTURE_PROCESSOR_LAYOUT_CHANGED')
    (code_repo / 'processing_phi3_v.py').write_text(text.replace(anchor, MARKER_CODE + anchor), encoding='utf-8', newline='\n')
    shutil.copyfile(source / 'image_processing_phi3_v.py', code_repo / 'image_processing_phi3_v.py')
    approved = _commit(code_repo, 'SUP-08 approved processor with import marker', '2026-11-11T00:00:00+00:00')
    with (code_repo / 'processing_phi3_v.py').open('a', encoding='utf-8', newline='\n') as stream:
        stream.write('\n# BYUL SUP-08 unapproved harmless code revision\n')
    unapproved = _commit(code_repo, 'SUP-08 unapproved harmless processor variant', '2026-11-11T00:01:00+00:00')
    hf_home = output / 'hf'
    hub = hf_home / 'hub' / ('models--' + CODE_REPOSITORY.replace('/', '--')) / 'snapshots'
    code_hashes = {}
    for label, rev in (('approved', approved), ('unapproved', unapproved)):
        dest = hub / rev
        dest.mkdir(parents=True)
        for name in ('processing_phi3_v.py', 'image_processing_phi3_v.py'):
            raw = subprocess.run(['git', '-C', str(code_repo), 'show', f'{rev}:{name}'],
                                 check=True, capture_output=True).stdout
            (dest / name).write_bytes(raw)
        code_hashes[label] = _sha(dest / 'processing_phi3_v.py')
    # vLLM may omit code_revision during startup processor discovery. Its default
    # offline ref must resolve to the approved bytes before the normal control.
    refs = hub.parent / 'refs'
    refs.mkdir(parents=True)
    (refs / 'main').write_text(approved, encoding='ascii')
    model = output / 'model-snapshot'
    model.mkdir()
    for item in source.iterdir():
        if not item.is_file():
            continue
        dest = model / item.name
        if item.name in WEIGHT_SHA256:
            os.link(item.resolve(strict=True), dest)
        else:
            shutil.copyfile(item, dest)
    preprocessor = json.loads((model / 'preprocessor_config.json').read_text(encoding='utf-8'))
    preprocessor['auto_map']['AutoProcessor'] = CODE_REPOSITORY + '--processing_phi3_v.Phi3VProcessor'
    preprocessor['auto_map']['AutoImageProcessor'] = CODE_REPOSITORY + '--image_processing_phi3_v.Phi3VImageProcessor'
    (model / 'preprocessor_config.json').write_text(json.dumps(preprocessor, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    inventory = {item.name: {'bytes': item.stat().st_size, 'sha256': _sha(item)}
                 for item in sorted(model.iterdir()) if item.is_file()}
    for name, expected in WEIGHT_SHA256.items():
        if inventory[name]['sha256'] != expected:
            raise ValueError(f'SUP08_FIXTURE_WEIGHT_CHANGED:{name}')
    inventory_raw = json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()
    inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
    snapshot_id = hashlib.sha1(json.dumps({'source_revision': SOURCE_REVISION,
        'preprocessor_sha256': inventory['preprocessor_config.json']['sha256'], 'code_repository': CODE_REPOSITORY},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    marker_root = '/cache/hf/byul-code-markers/' + run_id
    (hf_home / 'byul-code-markers' / run_id).mkdir(parents=True)
    manifest = {'schema': 'byul-sup08-phi3v-fixture/1', 'source_model': SOURCE_MODEL,
        'source_revision': SOURCE_REVISION, 'source_license': 'MIT; processor files retain Apache-2.0 headers',
        'model_id': 'local-byul/sup08-phi3v-derived', 'model_snapshot_id': snapshot_id,
        'model_path': str(model), 'model_inventory_sha256': inventory_sha256, 'model_inventory': inventory,
        'code_repository': CODE_REPOSITORY, 'code_revisions': {'approved': approved, 'unapproved': unapproved},
        'code_processor_sha256': code_hashes, 'code_marker_root': marker_root,
        'hf_home': str(hf_home), 'offline_environment': {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='pinned Microsoft model snapshot with both safetensors shards')
    parser.add_argument('--output', type=Path, required=True, help='new output directory on the same filesystem')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    manifest = build(args.source, args.output, run_id=args.run_id)
    print(json.dumps({key: manifest[key] for key in ('model_snapshot_id', 'model_inventory_sha256', 'code_revisions',
                                                    'code_repository', 'code_marker_root')}))


if __name__ == '__main__':
    main()
