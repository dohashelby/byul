"""ASGI/static/receipt preparation tests; no AWS or live server started."""
from argparse import Namespace
import asyncio
import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tools import selected_platform_server as server
from tools import selected_acceptance_run as runner


@pytest.fixture
def args(tmp_path,monkeypatch):
    # Commit metadata is a double; imported module files and built assets are real.
    monkeypatch.setattr(runner,'source_revision',lambda path:'a'*40)
    monkeypatch.setenv('BYUL_TEST_PLATFORM_VIEWER','SYNTHETIC_PRIVATE_VIEWER_"quoted\\line')
    reports=tmp_path/'reports';reports.mkdir()
    lab=tmp_path/'lab.json';lab.write_text(json.dumps({'schema':'serving-runtime-k8s-lab-report/1.0','run_id':'synthetic-run',
        'started_at':'2026-11-07T00:00:00Z','finished_at':'2026-11-07T00:00:01Z','context':'synthetic','namespace':'synthetic','overall':'PASSED',
        'cases':[{'id':f'L-{i:02d}','title':'synthetic','status':'PASSED','summary':'synthetic','evidence':[]} for i in range(1,7)]}))
    return Namespace(source=Path(__file__).resolve().parents[2],source_sha='a'*40,gateway_url='http://127.0.0.1:18080',
        viewer_token_env='BYUL_TEST_PLATFORM_VIEWER',platform_data_dir=tmp_path/'NEW-platform',update_reports_dir=reports,
        lab_report=lab,receipt=tmp_path/'public-process.json',port=0)


def test_live_sources_static_web_and_read_only_routes(args):
    app,receipt,viewer=server.build(args)
    with TestClient(app) as client:
        html=client.get('/runtime/observe/collectors?period=all')
        assert html.status_code==200 and '<script' in html.text and viewer not in html.text
        assert not app.state.byul.example_mode
        for method in ('post','put','patch','delete'):
            reply=getattr(client,method)('/api/environments')
            assert reply.status_code==405 and reply.json()['detail']['code']=='READ_ONLY_ACCEPTANCE'
    assert receipt['source_sha']=='a'*40 and receipt['read_only'] is True
    assert all(Path(row['file']).is_relative_to(args.source) for row in receipt['imports'].values())
    assert set(receipt['web_dist_sha256'])==set(runner.source_pins(args.source)['web'])
    assert not runner.contains_secret(receipt,[viewer])
    assert list(args.platform_data_dir.rglob('*.json'))==[args.platform_data_dir/'empty-evidence-index.json']


@pytest.mark.parametrize('problem',['foreign-gateway','wrong-source','missing-viewer','invalid-lab','existing-store'])
def test_preconditions_do_not_start_or_replace_existing_data(args,monkeypatch,problem):
    if problem=='foreign-gateway':args.gateway_url='http://example.com:18080'
    if problem=='wrong-source':args.source_sha='b'*40
    if problem=='missing-viewer':monkeypatch.delenv('BYUL_TEST_PLATFORM_VIEWER')
    if problem=='invalid-lab':args.lab_report.write_text('{}')
    if problem=='existing-store':args.platform_data_dir.mkdir();(args.platform_data_dir/'keep').write_text('keep')
    with pytest.raises((ValueError,FileExistsError)):server.build(args)
    assert not args.receipt.exists()
    if problem=='existing-store':assert (args.platform_data_dir/'keep').read_text()=='keep'
    else:assert not args.platform_data_dir.exists()


def test_receipt_only_after_successful_listening_startup(args,monkeypatch):
    import uvicorn
    class Listener:
        def setsockopt(self,*args):pass
        def bind(self,address):assert address==('127.0.0.1',0)
        def getsockname(self):return ('127.0.0.1',18700)
        def close(self):pass
    monkeypatch.setattr(server,'socket',types.SimpleNamespace(socket=lambda:Listener(),SOL_SOCKET=1,SO_REUSEADDR=2))
    async def startup(self,sockets=None):self.started=True
    def run(self,sockets=None):
        assert not args.receipt.exists()
        asyncio.run(self.startup(sockets=sockets))
    monkeypatch.setattr(uvicorn.Server,'startup',startup);monkeypatch.setattr(uvicorn.Server,'run',run)
    server.serve(args)
    receipt=json.loads(args.receipt.read_text())
    assert receipt['listening'] is True and receipt['host']=='127.0.0.1' and receipt['port']==18700
    assert 'viewer_token_env' not in receipt and 'viewer_token' not in receipt


def test_failed_startup_has_no_listening_receipt(args,monkeypatch):
    import uvicorn
    class Listener:
        def setsockopt(self,*args):pass
        def bind(self,address):pass
        def getsockname(self):return ('127.0.0.1',18700)
        def close(self):pass
    monkeypatch.setattr(server,'socket',types.SimpleNamespace(socket=lambda:Listener(),SOL_SOCKET=1,SO_REUSEADDR=2))
    async def startup(self,sockets=None):self.started=False
    monkeypatch.setattr(uvicorn.Server,'startup',startup)
    monkeypatch.setattr(uvicorn.Server,'run',lambda self,sockets=None:asyncio.run(self.startup(sockets=sockets)))
    with pytest.raises(RuntimeError):server.serve(args)
    assert not args.receipt.exists()


def test_absent_lab_report_keeps_live_gateway_lab_route_and_rpt_source(args):
    args.lab_report=None
    app,receipt,viewer=server.build(args)
    try:
        assert not app.state.byul.example_mode
        assert app.state.byul.runtime.mode=='live'
        assert app.state.byul.update.mode=='live'
        assert app.state.byul.settings.update_reports_dir==args.update_reports_dir.resolve()
        assert receipt['runtime_base_url']==args.gateway_url
    finally:app.state.byul.close()
