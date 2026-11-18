"""Qualify installed-source method bodies without importing or executing vLLM."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_sources as sources

ROOT=Path(__file__).parent/'fixtures/vllm-0.30.0'


@pytest.mark.parametrize('name',['vllm.v1.executor.multiproc_executor',
    'vllm.distributed.device_communicators.shm_broadcast'])
def test_reply_points_match_original_installed_file_and_body_pins_without_execution(name):
    data=json.loads(sources.PROFILES.read_bytes())
    profile,=[row for row in data['profiles'] if row.get('ipc_topology',{}).get('kind')=='broadcast-workers']
    row=profile['rpc_sources'][name]
    path=ROOT/(row['path']+'.txt');raw=path.read_bytes()
    assert sha256(raw).hexdigest()==row['sha256'] and len(raw)==row['bytes']
    module=types.ModuleType(name);module.__file__=str(path)
    codes=sources._codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
    classes={};cells={}
    for point in row['functions']:
        owner,member=point.split('.')
        assert codes[point].co_freevars in((),('__class__',))
        closure=None
        if codes[point].co_freevars:
            cells.setdefault(owner,types.CellType());closure=(cells[owner],)
        function=types.FunctionType(codes[point],vars(module),member,closure=closure)
        classes.setdefault(owner,{})[member]=function
    for owner,members in classes.items():setattr(module,owner,type(owner,(),members))
    for owner,cell in cells.items():cell.cell_contents=vars(module)[owner]
    runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    if runtime!=profile['runtime']:
        with pytest.raises(ValueError):sources._qualified(module,profile,'rpc_sources')
        return
    result=sources._qualified(module,profile,'rpc_sources')
    assert set(result['functions'])==set(row['functions'])
    assert result['observer_installed'] is result['engine_group_verified'] is result['producer_registered'] is False
    from guard.adapters.vllm_rank_rpc_hooks import RPCObserver
    for point,function in result['functions'].items():RPCObserver._class_cell(function,module,point)
    if name.endswith('shm_broadcast'):
        hook=row['hooks']['MessageQueue.enqueue']
        assert hook['fault_kinds']==['communication-stall'] and hook['parent']['point']=='WorkerProc.enqueue_output'
        assert hook['required_initial_endpoint']=='response' and 'requests' not in hook
