"""Immutable stdlib-only program for selected file-access metadata.

No engine import, file content read, permission mediation or target launch.
"""
from pathlib import Path
import hashlib

from guard.adapters import owned_file_access_probe as probe
from guard.adapters import resource_recovery_probe as selected
from . import bounded

PINS = {'selected': '86f51b71a70b494562c04bb518afdab420a4f70d2577d6f7f205cec5a067240c', 'bounded': '25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290', 'probe': '44e79c99719933deda94118f82fc4b3c4ff81f44080aa52adc643a3a68d51534'}
SELECTED_IMPORT = 'from . import resource_recovery_probe as selected'


def source():
    values = {}
    for name, module in (('selected', selected), ('bounded', bounded), ('probe', probe)):
        raw = Path(module.__file__).read_bytes()
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != PINS.get(name):
            raise ValueError('FILE_ACCESS_SOURCE_UNREVIEWED')
        values[name] = raw.decode('utf-8')
    text = values['probe']
    if text.count(SELECTED_IMPORT) != 1:
        raise ValueError('FILE_ACCESS_SOURCE_UNREVIEWED')
    text = text.replace(SELECTED_IMPORT, 'selected=__byul_selected_file_access')
    program = ('import types,sys\n'
        '__byul_selected_file_access=types.ModuleType("_byul_selected_file_access")\n'
        'exec(compile(' + repr(values['selected']) + ',"<reviewed-file-access-context>","exec"),vars(__byul_selected_file_access))\n'
        '__byul_bounded_file_access=types.ModuleType("_byul_bounded_file_access")\n'
        'exec(compile(' + repr(values['bounded']) + ',"<reviewed-file-access-json>","exec"),vars(__byul_bounded_file_access))\n'
        '__byul_probe_file_access=types.ModuleType("_byul_probe_file_access")\n'
        'vars(__byul_probe_file_access).update(__byul_selected_file_access=__byul_selected_file_access)\n'
        'exec(compile(' + repr(text) + ',"<reviewed-owned-file-access>","exec"),vars(__byul_probe_file_access))\n'
        '__byul_request=__byul_bounded_file_access.strict_json(sys.stdin.buffer.read(65537),limit=65536)\n'
        'if type(__byul_request)is not dict or set(__byul_request)!={"selection","max_seconds"}:raise ValueError("FILE_ACCESS_INPUT_INVALID")\n'
        '__byul_result=__byul_probe_file_access.observe(__byul_request["selection"],max_seconds=__byul_request["max_seconds"])\n'
        'sys.stdout.buffer.write(__byul_probe_file_access.canonical(__byul_result)+b"\\n")\n')
    if len(program.encode('utf-8')) > 131072:
        raise ValueError('FILE_ACCESS_SOURCE_BOUND')
    return program
