"""Exact standalone census sources for a package-free, owned container exec."""
import hashlib
from pathlib import Path

from guard.adapters import resource_group_probe as group
from guard.adapters import resource_recovery_probe as selected

GROUP_SHA256='ca3b335e18030862e439f5264b94ac886cc19d8d6b9b0a463d6bb2c9dc271c82'
SELECTED_SHA256='86f51b71a70b494562c04bb518afdab420a4f70d2577d6f7f205cec5a067240c'
IMPORT='from guard.adapters import resource_recovery_probe as selected'


def source():
    def reviewed(module,digest):
        raw=Path(module.__file__).read_bytes()
        if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('RESOURCE_GROUP_SOURCE_UNREVIEWED')
        return raw.decode('utf-8')
    helper=reviewed(selected,SELECTED_SHA256);census=reviewed(group,GROUP_SHA256)
    if census.count(IMPORT)!=1:raise ValueError('RESOURCE_GROUP_SOURCE_UNREVIEWED')
    census=census.replace(IMPORT,'selected = __byul_selected_metadata')
    # Private dictionaries only: no guard dependency or public module injection.
    program=('import types\n'
        '__byul_helper=types.ModuleType("_byul_selected_metadata")\n'
        'exec(compile('+repr(helper)+',"<reviewed-selected-metadata>","exec"),vars(__byul_helper))\n'
        '__byul_scope={"__name__":"__main__","__byul_selected_metadata":__byul_helper}\n'
        'exec(compile('+repr(census)+',"<reviewed-group-metadata>","exec"),__byul_scope)\n')
    if len(program.encode())>131072:raise ValueError('RESOURCE_GROUP_SOURCE_BOUND')
    return program
