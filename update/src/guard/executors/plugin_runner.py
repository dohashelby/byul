"""SUP-10 plugin runner 지원 계약 `guard-plugin-runner/1` (개발 담당, B 의존 반환 2, 2026-11-08).

기존 `plugin_loading`(고정 10사례: general·endpoint·logits·io의 등록·hook 단계, io는 `plugin_from_init`)과 독립 FAIL은 그대로 둔다.
이 계약은 IO processor 그룹을 **실제 엔진 설정 경로**로 본다.

- 선택: `EngineArgs(model).create_engine_config()`로 만든 VllmConfig와 `renderer_from_config`의 renderer로 `get_io_processor`를 부른다.
  플러그인은 모델 config.json의 `io_processor_plugin` 값으로 선택된다(vLLM 0.31.0 원 경로).
- 결합: 플러그인 생성자가 받은 ModelConfig(hf_config 정규 JSON의 SHA-256)와 renderer 형식, 요청별 pre/post 처리의 요청 ID SHA-256과
  실제 출력 SHA-256을 표식으로 남긴다. 생산자는 자기가 만든 설정·요청과 대조한다.
- 그룹 허용: `VLLM_PLUGINS` 허용 목록은 그룹의 등록 함수 호출을 거른다. 허용 목록이 있는데 비승인 플러그인 코드(등록·생성·처리)가
  실행되면 위반이다. 허용 목록이 없을 때 모든 등록 함수가 호출되는 것은 사실로 남기며 이 계약만으로 기업 정책 FAIL로 바꾸지 않는다.

사례(새 자식 프로세스): io-config-approved / io-config-unapproved(허용 밖 플러그인을 모델이 지목) / io-config-allowlist-unset / io-config-none.
"""
from __future__ import annotations

import hashlib
import json

CONTRACT = 'guard-plugin-runner/1'
NAMES = ('byul-approved', 'byul-unapproved')
PHASES = ('registration', 'constructor', 'io_preprocess', 'io_postprocess')
CASES = (
    # case_id, VLLM_PLUGINS(None=unset), 모델이 지목한 플러그인(None=지정 없음)
    ('io-config-approved', 'byul-approved', 'byul-approved'),
    ('io-config-unapproved', 'byul-approved', 'byul-unapproved'),
    ('io-config-allowlist-unset', None, 'byul-approved'),
    ('io-config-none', 'byul-approved', None),
)
_SHA = set('0123456789abcdef')


def fixtures():
    return [{'case_id': c, 'allowlist': a, 'model_plugin': m} for c, a, m in CASES]


def identity(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _sha(value):
    return value is None or (type(value) is str and len(value) == 64 and set(value) <= _SHA)


def clean_events(events):
    keys = {'plugin', 'phase', 'model_sha256', 'request_sha256', 'effect_sha256'}
    if (type(events) is not list or len(events) > 64
            or any(type(e) is not dict or set(e) != keys or e['plugin'] not in NAMES or e['phase'] not in PHASES
                   or not all(_sha(e[k]) for k in ('model_sha256', 'request_sha256', 'effect_sha256')) for e in events)):
        raise ValueError('PLUGIN_RUNNER_EVENTS_INVALID')
    return [dict(e) for e in events]


def project(rows):
    """실제 생산자 행 → SUP-10 관측. 형식이 틀린 행은 오류로만 센다."""
    specs = {c['case_id']: c for c in fixtures()}
    valid, errors, violations, facts = {}, [], [], {}
    for row in rows:
        key = row.get('case_id') if isinstance(row, dict) else None
        if type(key) is not str or key not in specs or key in valid or row.get('contract') != CONTRACT:
            errors.append('unknown_or_duplicate_case'); continue
        case = specs[key]
        if row.get('case_identity') != identity(case):
            errors.append(key); continue
        try:
            events = clean_events(row.get('events', []))
        except ValueError:
            errors.append(key); continue
        valid[key] = row
        model, request = row.get('selected_model_sha256'), row.get('request_sha256')
        if row.get('event_collection_status') != 'completed' or row.get('renderer_built') is not True or not _sha(model) or model is None:
            errors.append(key)
        if case['allowlist'] is not None:
            allowed = set(case['allowlist'].split(','))
            if any(e['plugin'] not in allowed for e in events):
                violations.append({'case': key, 'reason': 'unapproved_plugin_code_executed'})
        for e in events:
            if e['phase'] == 'constructor' and e['model_sha256'] != model:
                violations.append({'case': key, 'reason': 'plugin_bound_to_other_model_config'})
            if e['phase'] in ('io_preprocess', 'io_postprocess') and e['request_sha256'] != request:
                violations.append({'case': key, 'reason': 'io_effect_request_mismatch'})
        phases = {(e['plugin'], e['phase']) for e in events}
        selected = row.get('selected_plugin')
        if key == 'io-config-approved':
            need = {('byul-approved', p) for p in PHASES}
            if (row.get('outcome') != 'selected' or selected != 'byul-approved' or not need <= phases
                    or not any(e['phase'] == 'io_preprocess' and e['effect_sha256'] == row.get('preprocess_output_sha256') for e in events)):
                errors.append(key)
        elif key == 'io-config-unapproved':
            if row.get('outcome') not in ('rejected_not_loadable', 'execution_error') or selected is not None:
                errors.append(key)
            if selected == 'byul-unapproved':
                violations.append({'case': key, 'reason': 'unapproved_plugin_selected'})
        elif key == 'io-config-allowlist-unset':
            facts['allowlist_unset_registrations'] = sorted(p for p, ph in phases if ph == 'registration')
            if row.get('outcome') != 'selected' or selected != 'byul-approved':
                errors.append(key)
        elif key == 'io-config-none':
            if row.get('outcome') != 'none_requested' or selected is not None:
                errors.append(key)
            if any(ph != 'registration' for _, ph in phases):
                violations.append({'case': key, 'reason': 'plugin_constructed_without_model_request'})
    seen = set()
    unique = [v for v in violations if (v['case'], v['reason']) not in seen and not seen.add((v['case'], v['reason']))]
    return {'plugin_runner_confirmed_violations': {'value': unique, 'collected': bool(valid)},
            'plugin_runner_facts': facts, 'complete': set(valid) == set(specs) and not errors, 'errors': sorted(set(errors)),
            'scope': 'IO processor group via real VllmConfig/renderer selection; general/endpoint/logits remain plugin_loading v1'}


def evaluate(rows):
    """`supply_boot_contract._plugin`과 같은 (위반, 공백) 모양. 소비자 연결은 B가 정한다."""
    projected = project(rows)
    return projected['plugin_runner_confirmed_violations']['value'], ([] if projected['complete'] else ['plugin_runner_cases_incomplete'])


# --- 무해 플러그인 배포물과 실제 생산자(엔진 컨테이너 안에서 실행) ---------------------------------------

PACKAGE_PY = '''import hashlib, json, os


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _mark(plugin, phase, model=None, request=None, effect=None):
    path = os.environ.get('BYUL_PLUGIN_RUNNER_EVENTS')
    if path:
        with open(path, 'a') as out:
            out.write(json.dumps({'plugin': plugin, 'phase': phase, 'model_sha256': model, 'request_sha256': request,
                                  'effect_sha256': effect}) + '\\n')


def _model(config):
    return _sha(json.dumps(config.model_config.hf_config.to_dict(), sort_keys=True, default=str))


def register_approved():
    _mark('byul-approved', 'registration')
    return 'byul_plugin_runner_probe.ApprovedProcessor'


def register_unapproved():
    _mark('byul-unapproved', 'registration')
    return 'byul_plugin_runner_probe.UnapprovedProcessor'


from vllm.plugins.io_processors.interface import IOProcessor


class _Probe(IOProcessor):
    NAME = None

    def __init__(self, vllm_config, renderer):
        super().__init__(vllm_config, renderer)
        self.model = _model(vllm_config)
        _mark(self.NAME, 'constructor', model=self.model)

    def parse_data(self, data):
        return data

    def pre_process(self, prompt, request_id=None, **kwargs):
        out = 'byul-pre:' + str(prompt)
        _mark(self.NAME, 'io_preprocess', model=self.model, request=_sha(str(request_id)), effect=_sha(out))
        return out

    def post_process(self, model_output, request_id=None, **kwargs):
        out = 'byul-post:' + str(len(model_output))
        _mark(self.NAME, 'io_postprocess', model=self.model, request=_sha(str(request_id)), effect=_sha(out))
        return out


class ApprovedProcessor(_Probe):
    NAME = 'byul-approved'


class UnapprovedProcessor(_Probe):
    NAME = 'byul-unapproved'
'''
ENTRY_POINTS = ('[vllm.io_processor_plugins]\nbyul-approved = byul_plugin_runner_probe:register_approved\n'
                'byul-unapproved = byul_plugin_runner_probe:register_unapproved\n')
METADATA = 'Metadata-Version: 2.1\nName: byul-plugin-runner-probe\nVersion: 1.0\n'
FIXTURE_FILES = {'byul_plugin_runner_probe/__init__.py': PACKAGE_PY,
                 'byul_plugin_runner_probe-1.0.dist-info/METADATA': METADATA,
                 'byul_plugin_runner_probe-1.0.dist-info/entry_points.txt': ENTRY_POINTS}

RUNNER = r'''
import hashlib,json,os,subprocess,sys,tempfile
FILES=json.loads(sys.argv[1]);CASES=json.loads(sys.argv[2]);CONTRACT=sys.argv[3];MODEL=sys.argv[4]
CHILD=r"""
import hashlib,json,os,sys,traceback
case=json.loads(sys.argv[1]);model_dir=sys.argv[2];contract=sys.argv[3]
sha=lambda v:hashlib.sha256(v.encode()).hexdigest()
row={**case,'contract':contract,'case_identity':sys.argv[4],'outcome':'execution_error','selected_plugin':None,
     'selected_model_sha256':None,'renderer_built':False,'request_sha256':None,'preprocess_output_sha256':None}
try:
    from vllm.engine.arg_utils import EngineArgs
    from vllm.renderers import renderer_from_config
    from vllm.plugins.io_processors import get_io_processor
    config=EngineArgs(model=model_dir,max_model_len=512,dtype='float32',enforce_eager=True).create_engine_config()
    row['selected_model_sha256']=sha(json.dumps(config.model_config.hf_config.to_dict(),sort_keys=True,default=str))
    renderer=renderer_from_config(config);row['renderer_built']=True;row['renderer_type_sha256']=sha(type(renderer).__qualname__)
    try:
        proc=get_io_processor(config,renderer)
    except ValueError as error:
        row['outcome']='rejected_not_loadable' if 'not installed' in str(error) or 'No IOProcessor' in str(error) else 'execution_error'
        row['error_type']='ValueError';proc=False
    if proc is None:row['outcome']='none_requested'
    elif proc is not False:
        row['selected_plugin']=getattr(proc,'NAME',None);request='byul-req-'+case['case_id']
        row['request_sha256']=sha(request)
        out=proc.pre_process('probe input',request_id=request);row['preprocess_output_sha256']=sha(str(out))
        proc.post_process([],request_id=request);row['outcome']='selected'
except Exception as error:
    row['error_type']=type(error).__name__
events=[];status='completed'
try:
    for line in open(os.environ['BYUL_PLUGIN_RUNNER_EVENTS']).read().splitlines():events.append(json.loads(line))
except FileNotFoundError:pass
except Exception:status='partial'
row['events']=events;row['event_collection_status']=status
try:
    import vllm;row['vllm_version']=vllm.__version__
except Exception:row['vllm_version']=None
print(json.dumps(row))
"""
rows=[]
with tempfile.TemporaryDirectory() as base:
    site=os.path.join(base,'site')
    for name,text in FILES.items():
        path=os.path.join(site,name);os.makedirs(os.path.dirname(path),exist_ok=True);open(path,'w').write(text)
    for case,ident in CASES:
        model_dir=os.path.join(base,case['case_id']+'-model');os.makedirs(model_dir)
        for name in os.listdir(MODEL):
            if name!='config.json':os.symlink(os.path.join(MODEL,name),os.path.join(model_dir,name))
        cfg=json.load(open(os.path.join(MODEL,'config.json')))
        if case['model_plugin'] is not None:cfg['io_processor_plugin']=case['model_plugin']
        json.dump(cfg,open(os.path.join(model_dir,'config.json'),'w'))
        env=dict(os.environ,PYTHONPATH=site+((':'+os.environ['PYTHONPATH']) if os.environ.get('PYTHONPATH') else ''),
                 BYUL_PLUGIN_RUNNER_EVENTS=os.path.join(base,case['case_id']+'.events.jsonl'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        env.pop('VLLM_PLUGINS',None)
        if case['allowlist'] is not None:env['VLLM_PLUGINS']=case['allowlist']
        done=subprocess.run([sys.executable,'-c',CHILD,json.dumps(case),model_dir,CONTRACT,ident],env=env,capture_output=True,timeout=600)
        try:row=json.loads(done.stdout.decode().strip().splitlines()[-1])
        except Exception:row={**case,'contract':CONTRACT,'case_identity':ident,'outcome':'execution_error','events':[],'event_collection_status':'not_collected','stderr_tail_sha256':hashlib.sha256(done.stderr[-4000:]).hexdigest()}
        row['exit_code']=done.returncode;rows.append(row)
print(json.dumps({'contract':CONTRACT,'fixture_sha256':{n:hashlib.sha256(t.encode()).hexdigest() for n,t in FILES.items()},'rows':rows}))
'''


def runner_args(model_dir):
    """RUNNER 인자: 배포물 파일·사례(신원 포함)·계약 이름·엔진 컨테이너 안 모델 폴더."""
    if type(model_dir) is not str or not model_dir.startswith('/') or '..' in model_dir.split('/'):
        raise ValueError('PLUGIN_RUNNER_MODEL_DIR_INVALID')
    return [json.dumps(FIXTURE_FILES), json.dumps([[c, identity(c)] for c in fixtures()]), CONTRACT, model_dir]
