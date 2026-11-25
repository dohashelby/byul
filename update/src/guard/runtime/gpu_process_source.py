"""Pinned stdlib-only host metadata program; no engine or GPU library import."""
from pathlib import Path
from guard.adapters import gpu_process_probe as probe,resource_recovery_probe as selected
from . import bounded
from .source_assets import read_pinned_source

PINS={'selected': '86f51b71a70b494562c04bb518afdab420a4f70d2577d6f7f205cec5a067240c', 'bounded': '25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290', 'probe': '1758f8a635463d146f090c42c2e91ed81898d0c60fae2529e36eafa39dbd5761'}
SELECTED_IMPORT='from . import resource_recovery_probe as selected'
BOUNDED_IMPORT='from guard.runtime.bounded import capture_pipes'


def source():
    values={}
    for name,module in(('selected',selected),('bounded',bounded),('probe',probe)):
        values[name] = read_pinned_source(Path(module.__file__), PINS.get(name),
                                          "GPU_PROCESS_SOURCE_UNREVIEWED", max_bytes=65536)
    text=values['probe']
    if text.count(SELECTED_IMPORT)!=1 or text.count(BOUNDED_IMPORT)!=1:raise ValueError('GPU_PROCESS_SOURCE_UNREVIEWED')
    text=text.replace(SELECTED_IMPORT,'selected=__byul_selected_gpu').replace(BOUNDED_IMPORT,'capture_pipes=__byul_bounded_gpu.capture_pipes')
    program=('import types,sys\n'
        '__byul_selected_gpu=types.ModuleType("_byul_selected_gpu")\n'
        'exec(compile('+repr(values['selected'])+',"<reviewed-selected-gpu>","exec"),vars(__byul_selected_gpu))\n'
        '__byul_bounded_gpu=types.ModuleType("_byul_bounded_gpu")\n'
        'exec(compile('+repr(values['bounded'])+',"<reviewed-bounded-gpu>","exec"),vars(__byul_bounded_gpu))\n'
        '__byul_probe_gpu=types.ModuleType("_byul_probe_gpu")\n'
        'vars(__byul_probe_gpu).update(__byul_selected_gpu=__byul_selected_gpu,__byul_bounded_gpu=__byul_bounded_gpu)\n'
        'exec(compile('+repr(text)+',"<reviewed-node-gpu-process>","exec"),vars(__byul_probe_gpu))\n'
        '__byul_request=__byul_bounded_gpu.strict_json(sys.stdin.buffer.read(65537),limit=65536)\n'
        'if type(__byul_request)is not dict or set(__byul_request)!={"selection","max_seconds"}:raise ValueError("GPU_PROCESS_INPUT_INVALID")\n'
        '__byul_result=__byul_probe_gpu.observe(__byul_request["selection"],max_seconds=__byul_request["max_seconds"])\n'
        'sys.stdout.buffer.write(__byul_probe_gpu.canonical(__byul_result)+b"\\n")\n')
    if len(program.encode('utf-8'))>131072:raise ValueError('GPU_PROCESS_SOURCE_BOUND')
    return program
