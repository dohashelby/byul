"""The owned offline fixture pins both code revisions and writes only harmless markers."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from tools import prepare_sup08_phi3v_fixture as fixture


def test_fixture_builds_two_git_revisions_and_import_marker(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    processor = '# test processor\nimport re\nfrom typing import List, Optional, Union\nclass Phi3VProcessor: pass\n'
    image = 'class Phi3VImageProcessor: pass\n'
    files = {'processing_phi3_v.py': processor, 'image_processing_phi3_v.py': image,
             'config.json': '{}', 'tokenizer.json': '{}', 'tokenizer_config.json': '{}',
             'model.safetensors.index.json': '{}',
             'preprocessor_config.json': json.dumps({'auto_map': {'AutoProcessor': 'processing_phi3_v.Phi3VProcessor',
                 'AutoImageProcessor': 'image_processing_phi3_v.Phi3VImageProcessor'}}),
             'model-00001-of-00002.safetensors': 'fixture-weight-a',
             'model-00002-of-00002.safetensors': 'fixture-weight-b'}
    for name, content in files.items():
        (source / name).write_bytes(content.encode())
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    monkeypatch.setattr(fixture, 'PROCESSOR_SHA256', digest(processor))
    monkeypatch.setattr(fixture, 'IMAGE_PROCESSOR_SHA256', digest(image))
    monkeypatch.setattr(fixture, 'WEIGHT_SHA256', {name: digest(files[name]) for name in files if name.endswith('.safetensors')})
    output = tmp_path / 'owned'
    manifest = fixture.build(source, output, run_id='sup08-1')
    approved, unapproved = (manifest['code_revisions'][key] for key in ('approved', 'unapproved'))
    assert len(approved) == len(unapproved) == 40 and approved != unapproved
    assert (output / 'hf/hub/models--local-byul--sup08-phi3v-processor/refs/main').read_text() == approved
    assert manifest['model_inventory_sha256'] == hashlib.sha256(json.dumps(
        manifest['model_inventory'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert manifest['code_repository'] in json.dumps(json.loads((output / 'model-snapshot/preprocessor_config.json').read_text()))
    code = output / 'hf/hub/models--local-byul--sup08-phi3v-processor/snapshots' / unapproved / 'processing_phi3_v.py'
    assert code.is_file() and 'unapproved harmless' in code.read_text()
    marker_root = output / 'hf/byul-code-markers/sup08-1'
    subprocess.run([sys.executable, '-I', '-S', '-c', 'import runpy,sys;runpy.run_path(sys.argv[1])', str(code)],
                   check=True, env={**os.environ, 'BYUL_CODE_MARKER_ROOT': str(marker_root)})
    marker = json.loads((marker_root / f'{unapproved}.json').read_text())
    assert marker['revision'] == unapproved and marker['module_sha256'] == hashlib.sha256(code.read_bytes()).hexdigest()
