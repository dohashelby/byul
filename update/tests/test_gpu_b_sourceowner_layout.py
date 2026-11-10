"""Actual producer -> plan layout -> unchanged resolver/helper readers.

CI uses EXAMPLE base-plan paths and overrides only the producer's base frame
SHA to bind that fixture. Owner/capture/resolver bytes and their pins are exact.
The separate private receipt consumes S2's actual generated42 unchanged.
No full source approval, environment, prepared role or product result is made.
"""
import ast
import contextlib
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

FIX=Path(__file__).resolve().parent/'fixtures/layout'
OWNER='fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def functions(path,names,scope):
    tree=ast.parse(path.read_bytes())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),scope)
    return scope

def exercise_layout(plan_root,work):
    """Consume an actual producer output; only native fixture ASSETS are written."""
    plan=json.loads((plan_root/'file-install-plan.json').read_bytes())
    caprow,=[r for r in plan['files'] if PurePosixPath(r['guest']).name=='capture_normal.py']
    assets=PurePosixPath(caprow['guest']).parent
    layout=work/'assets';layout.mkdir(mode=0o700)
    for row in plan['files']:
        guest=PurePosixPath(row['guest'])
        if not guest.is_relative_to(assets):continue
        relative=guest.relative_to(assets)
        raw=plan_root.joinpath(*PurePosixPath(row['local_file']).parts).read_bytes()
        if len(raw)!=row['bytes'] or sha(raw)!=row['sha256'] or row['owner']!=1000 or row['mode']!='0600':
            raise ValueError('EXACT_PRODUCER_ASSET_REQUIRED')
        path=layout.joinpath(*relative.parts);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with path.open('xb') as stream:stream.write(raw)
        path.chmod(0o600)
    resolver=next(r for r in plan['files'] if PurePosixPath(r['guest']).name=='resolver.py')
    resolver_path=plan_root.joinpath(*PurePosixPath(resolver['local_file']).parts)
    tree=ast.parse(resolver_path.read_bytes())
    pins=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='PINS' for t in n.targets)))
    pointer=lambda name:{'path':str(work/name),'file_sha256':'e'*64}
    values={k:pointer(k+'.json') for k in ('environment','target','model_manifest','pod','namespace','static_preparation')}
    values.update(phase='normal-before',source_root=str(work/'source'),source_commit='EXAMPLE-not-product-source',
        source_manifest_sha256='e'*64,role_output=str(work/'role'),capture_output=str(work/'capture'),
        capture_helper={'path':str(layout/'capture_normal.py'),'file_sha256':caprow['sha256']},
        previous_roles=[],kubectl_binary='EXAMPLE-not-executed')
    a=SimpleNamespace(stage='capture',inputs=work/'inputs.json',output=work/'resolved',
        role_preparation=work/'role/role-preparation.json',role_preparation_sha256='e'*64,
        ready_controller=None,capture=None,capture_receipt=None,journal=None)
    scope=dict(Path=Path,os=os,stat=stat,re=re,sys=sys,hashlib=hashlib,__file__=str(resolver_path),PINS=pins,
        inputs=lambda _:(values,None,None,None,{},work/'source',work/'tool.py',work/'packet','normal',[]),
        role_result=lambda *args:({},work/'role',{},None,None),
        reserve=lambda out,protected:Path(out),write=lambda *args:None)
    functions(resolver_path,{'sha','read','command','bind_commands'},scope)
    result=scope['bind_commands'](a)  # Actual read() consumes both exact helper files.
    assert result['commands'][0]['argv'][3]==str(layout/'capture_normal.py')
    assert scope['read'](layout/'source_owner_verify.py',OWNER)==(layout/'static/assets/source_owner.py').read_bytes()
    # Actual capture helper verify_source loads and executes the pinned sibling.
    # Only the downstream whole-product source verifier is a rejection seam.
    repo=work/'owned-source';(repo/'update/release').mkdir(parents=True);(repo/'update/tools').mkdir()
    manifest=b'EXAMPLE core verifier intentionally not exercised\n'
    (repo/'update/release/reviewed-source.json').write_bytes(manifest)
    (repo/'update/tools/__init__.py').write_bytes(b'')
    (repo/'update/tools/verify_req10_consumption.py').write_text("def verify(repo):\n    raise RuntimeError('EXAMPLE_CORE_VERIFIER_BOUNDARY_REACHED')\n")
    def git(*argv):
        p=subprocess.run(['git','-C',str(repo),*argv],capture_output=True,check=True)
        return p.stdout.decode().strip()
    git('init','-q');git('add','.')
    git('-c','user.name=EXAMPLE','-c','user.email=fixture@example.invalid','commit','-qm','EXAMPLE fixture only')
    commit=git('rev-parse','HEAD')
    helper_scope=dict(Path=Path,hashlib=hashlib,__file__=str(layout/'capture_normal.py'))
    functions(layout/'capture_normal.py',{'verify_source'},helper_scope)
    old_path=list(sys.path);old_tools={k:v for k,v in sys.modules.items() if k=='tools' or k.startswith('tools.')}
    for key in old_tools:sys.modules.pop(key)
    stderr=io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            try:helper_scope['verify_source'](repo,commit,sha(manifest))
            except RuntimeError as error:
                if str(error)!='EXAMPLE_CORE_VERIFIER_BOUNDARY_REACHED':raise
            else:raise AssertionError('core rejection seam was not reached')
    finally:
        sys.path[:]=old_path
        for key in list(sys.modules):
            if key=='tools' or key.startswith('tools.'):sys.modules.pop(key)
        sys.modules.update(old_tools)
    return dict(plan_rows=len(plan['files']),sibling_bytes=(layout/'source_owner_verify.py').stat().st_size,
        sibling_sha256=OWNER,static_source_preserved=True,actual_resolver_read=True,
        actual_capture_helper_sibling_loaded=True,owned_fixture_Git_calls=2,
        downstream_core_verifier='explicit EXAMPLE rejection seam; no product/source proof',
        full_capture_dispatch_or_GPU=False,diagnostic_text=stderr.getvalue())


class Layout(unittest.TestCase):
    def test_actual_producer_output_to_both_actual_sibling_readers(self):
        with tempfile.TemporaryDirectory(prefix='byul-layout-') as tmp:
            work=Path(tmp);base=work/'base';(base/'files').mkdir(parents=True)
            rows=[]
            for name,guest in [('capture_normal.py','/EXAMPLE/ASSETS/capture_normal.py'),
                               ('resolver.py','/EXAMPLE/ASSETS/resolver.py'),
                               ('source_owner.py','/EXAMPLE/ASSETS/static/assets/source_owner.py')]:
                raw=(FIX/name).read_bytes();local='files/%03d-%s'%(len(rows),name)
                base.joinpath(*PurePosixPath(local).parts).write_bytes(raw)
                rows.append(dict(guest=guest,local_file=local,bytes=len(raw),sha256=sha(raw),owner=1000,mode='0600'))
            for i in range(38):
                raw=('EXAMPLE unused asset %d'%i).encode();local='files/unused-%02d'%i
                (base/local).write_bytes(raw)
                rows.append(dict(guest='/EXAMPLE/CODE/unused-%02d'%i,local_file=local,bytes=len(raw),sha256=sha(raw),owner=0,mode='0600'))
            raw=json.dumps({'files':rows}).encode();(base/'file-install-plan.json').write_bytes(raw)
            producer=load(Path(__file__).resolve().parents[1]/'tools/gpu_b_sup16_source_owner_layout.py','exact_layout_producer_fixture')
            producer.BASE_PLAN_SHA=sha(raw)  # EXAMPLE input-frame seam only; runtime candidate is unchanged.
            generated=work/'generated';receipt=producer.build(base,generated)
            self.assertTrue(receipt['original_rows_unchanged'])
            old=work/'old';old.mkdir()
            with self.assertRaises(FileNotFoundError) as failure:exercise_layout(base,old)
            self.assertTrue(str(failure.exception.filename).endswith('/source_owner_verify.py'))
            new=work/'new';new.mkdir()
            observation=exercise_layout(generated,new)
            self.assertEqual((observation['plan_rows'],observation['sibling_bytes']),(42,3540))
            self.assertFalse(observation['full_capture_dispatch_or_GPU'])

if __name__=='__main__':unittest.main(verbosity=2)
