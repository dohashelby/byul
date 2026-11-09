"""Root-started read-only live Platform/static web with a listening receipt."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import re
import socket
import sys

IMPORTS = {
    'byul_platform.main':'platform/api/src/byul_platform/main.py',
    'byul_platform.config':'platform/api/src/byul_platform/config.py',
    'byul_platform.sources.runtime':'platform/api/src/byul_platform/sources/runtime.py',
    'serving_runtime.mvp.lab_report':'runtime/src/serving_runtime/mvp/lab_report.py',
    'serving_runtime.mvp.app':'runtime/src/serving_runtime/mvp/app.py',
    'serving_runtime.mvp.http_handler':'runtime/src/serving_runtime/mvp/http_handler.py',
    'serving_runtime.mvp.request_flow':'runtime/src/serving_runtime/mvp/request_flow.py',
}


def build(args):
    source=args.source.resolve()
    sys.path[:0]=[str(source/p) for p in ('runtime','runtime/src','update/src','platform/api/src')]
    from tools.selected_acceptance_run import source_pins,origin,digest,bounded,checked_document
    pins=source_pins(source)
    if pins['sha']!=args.source_sha:raise ValueError('fixed source mismatch')
    if 'index.html' not in pins['web'] or not any(p.endswith('.js') for p in pins['web']) or not any(p.endswith('.css') for p in pins['web']):raise ValueError('built static web required')
    origin(args.gateway_url)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',args.viewer_token_env):raise ValueError('viewer environment reference')
    viewer=os.environ.get(args.viewer_token_env)
    if not viewer:raise ValueError('viewer value unavailable')
    if not args.update_reports_dir.is_dir():raise ValueError('explicit report directory required')
    modules={name:importlib.import_module(name) for name in IMPORTS}
    for name,relative in IMPORTS.items():
        if Path(modules[name].__file__).resolve()!=source/relative:raise ValueError('foreign Platform/runtime import')
    from serving_runtime.mvp.lab_report import LabReportSource
    # Same validated public report is supplied to Gateway by root; the live API
    # still reads Gateway /api/lab-report and is never replaced by this input.
    if args.lab_report is not None:
        lab,_sha,_raw=checked_document(args.lab_report)
        LabReportSource._validate(lab)
    data=args.platform_data_dir.resolve()
    if data.exists():raise FileExistsError('new owned Platform store required')
    data.mkdir(mode=0o700,parents=False,exist_ok=False)
    empty=data/'empty-evidence-index.json'
    from tools.selected_runtime_check import now
    empty.write_text(__import__('json').dumps({'schema_version':'byul-evidence-index/0.1','producer':'runtime','created_at':now(),'items':[]}))
    settings=modules['byul_platform.config'].Settings(runtime_base_url=origin(args.gateway_url),runtime_viewer_token=viewer,
        contracts_dir=source/'contracts',web_dist=source/'platform/web/dist',platform_data_dir=data,
        update_reports_dir=args.update_reports_dir.resolve(),evidence_index_paths=(empty,))
    app=modules['byul_platform.main'].create_app(settings)
    if app.state.byul.example_mode:raise ValueError('live file sources required')
    from fastapi.responses import JSONResponse
    @app.middleware('http')
    async def read_only(request,call_next):
        if request.method not in ('GET','HEAD'):
            return JSONResponse(status_code=405,content={'detail':{'code':'READ_ONLY_ACCEPTANCE'}})
        return await call_next(request)
    receipt={'schema':'byul-selected-platform-process/1','source_sha':pins['sha'],'pid':os.getpid(),
        'imports':{name:{'file':module.__file__,'sha256':digest(bounded(module.__file__))} for name,module in modules.items()},
        'web_dist_sha256':pins['web'],'host':'127.0.0.1','runtime_base_url':settings.runtime_base_url,'read_only':True}
    return app,receipt,viewer


def serve(args):
    import uvicorn
    app,receipt,viewer=build(args)
    from tools.selected_acceptance_run import write_public
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try:
        listener.bind(('127.0.0.1',args.port))
        receipt['port']=listener.getsockname()[1]
        class ReceiptServer(uvicorn.Server):
            async def startup(self,sockets=None):
                await super().startup(sockets=sockets)
                if not self.started:raise RuntimeError('Platform did not start listening')
                write_public(args.receipt,{**receipt,'listening':True},[viewer])
        server=ReceiptServer(uvicorn.Config(app,host='127.0.0.1',port=receipt['port'],log_level='critical',access_log=False))
        server.run(sockets=[listener])
    finally:
        listener.close()
        app.state.byul.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--source-sha',required=True)
    parser.add_argument('--gateway-url',required=True);parser.add_argument('--viewer-token-env',required=True)
    parser.add_argument('--platform-data-dir',type=Path,required=True);parser.add_argument('--update-reports-dir',type=Path,required=True)
    parser.add_argument('--lab-report',type=Path);parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--port',type=int,default=18700)
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[0-9a-f]{40}',args.source_sha) or not 0<=args.port<=65535:parser.error('full source SHA and valid loopback port required')
    if args.receipt.exists():parser.error('new public receipt path required')
    try:serve(args)
    except Exception as error:
        # Values, source documents and exception messages remain private.
        print(__import__('json').dumps({'execution':'aborted','error_class':type(error).__name__}));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
