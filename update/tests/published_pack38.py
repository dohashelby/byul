"""Replay the unchanged single-input definitions by their published full Ref.

Current matrix product tests load packs/PACK-BASELINE directly. Legacy native
runner fixtures here retain their original policy/probe/observation contract.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import atexit,shutil,tempfile
from guard.models import load_pack

ROOT=Path(__file__).parents[1]
EXPECTED='22dee30a0df2e9e79defd6d28010f3d16e202512d67269e831d3c5fa95113d80'


@lru_cache(maxsize=1)
def _original():
    directory=Path(tempfile.mkdtemp(prefix='byul-test-published-pack38-'))
    atexit.register(shutil.rmtree,directory)
    shutil.copytree(ROOT/'packs/PACK-BASELINE',directory/'pack')
    fixture=ROOT/'tests/fixtures/input-matrix-pack38'
    for file in fixture.rglob('*.yaml'):
        shutil.copyfile(file,directory/'pack'/file.relative_to(fixture))
    pack=load_pack(directory/'pack')
    assert pack.ref().revision==38 and pack.ref().sha256==EXPECTED
    return pack


def original():
    return deepcopy(_original())
