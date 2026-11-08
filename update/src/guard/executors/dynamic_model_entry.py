"""SUP-09·11 model-entry 지원 계약 `guard-model-entry/1` (개발 담당, B 의존 반환 1, 2026-11-08).

기존 `dynamic_code_cpu`(autoconfig·vllm-config 12사례의 import·constructor)와 독립 FAIL은 그대로 둔다. 이 계약은 그 위에
**실제 선택된 모델 클래스**의 단계를 따로 본다.

- 진입점: transformers `AutoConfig.from_pretrained` → `AutoModelForCausalLM.from_config`(vLLM이 remote code 모델에 쓰는 같은 경로)
  → 작은 입력의 forward. 사례는 trust(False/True) × Python 모드(normal/-O/-OO) 6개이며 사례마다 새 자식 프로세스다.
- 단계 표식(무해 사용자 모델 자체가 남김): `import`(configuration·modeling 모듈), `constructor`(모델), `forward`(모델).
- 선택 사실: 실제로 만들어진 모델 객체 클래스의 정의 파일 bytes SHA-256과 고정 fixture bytes의 일치, 클래스 이름 SHA-256.
- 정상 대조: 사례 앞뒤로 내장 모델(사용자 코드 없음)을 만들고 forward한다.

판정 재료:
- SUP-09 `model_entry_confirmed_violations`: trust=False인데 사용자 modeling 코드가 import·생성·실행된 사례.
- SUP-11 `model_entry_mode_violations`: 같은 진입점의 normal trust=False는 trust gate에서 거부됐는데 -O/-OO trust=False에서
  사용자 코드가 import된 사례.
- `complete`: 6사례가 모두 있고, 수집 완료·정상 대조 통과, trust=True 사례는 수용·선택 source 일치·import/constructor/forward 모두 관측.
실행되지 않은 단계를 다른 단계 이름으로 바꾸지 않는다. forward가 없으면 실행 소비를 주장하지 않는다.
"""
from __future__ import annotations

import hashlib
import json

CONTRACT = 'guard-model-entry/1'
PHASES = ('import', 'constructor', 'forward')
MODULES = ('configuration', 'modeling')
MODES = {'normal': 0, 'O': 1, 'OO': 2}


def fixtures():
    return [{'case_id': f'model-entry-{trust}-{mode}', 'entry': 'model-entry', 'trust': trust, 'mode': mode}
            for trust in (False, True) for mode in MODES]


def identity(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def clean_events(events):
    if (type(events) is not list or len(events) > 64
            or any(type(e) is not dict or set(e) != {'phase', 'module'} or e['phase'] not in PHASES or e['module'] not in MODULES
                   or (e['phase'] in ('constructor', 'forward') and e['module'] != 'modeling') for e in events)):
        raise ValueError('MODEL_ENTRY_EVENTS_INVALID')
    return [dict(e) for e in events]


def _control_ok(value):
    return isinstance(value, dict) and value.get('outcome') == 'accepted' and value.get('forward_completed') is True


def _modeling(events):
    return {e['phase'] for e in events if e['module'] == 'modeling'}


def project(rows):
    """실제 생산자 행 → SUP-09·11 관측. 형식이 틀린 행은 오류로만 세고 위반을 만들지 않는다."""
    specs = {c['case_id']: c for c in fixtures()}
    valid, errors, violations, mode_violations = {}, [], [], []
    for row in rows:
        key = row.get('case_id') if isinstance(row, dict) else None
        if type(key) is not str or key not in specs or key in valid or row.get('contract') != CONTRACT:
            errors.append('unknown_or_duplicate_case'); continue
        case = specs[key]
        if row.get('case_identity') != identity(case) or row.get('python_optimize') != MODES[case['mode']]:
            errors.append(key); continue
        try:
            events = clean_events(row.get('events', []))
        except ValueError:
            errors.append(key); continue
        valid[key] = (row, events)
        phases = _modeling(events)
        if (row.get('event_collection_status') != 'completed' or row.get('outcome') not in ('accepted', 'rejected_at_trust_gate')
                or not _control_ok(row.get('before')) or not _control_ok(row.get('after'))):
            errors.append(key)
        elif case['trust'] and (row.get('outcome') != 'accepted' or row.get('selected_source_matches') is not True
                                or row.get('forward_completed') is not True or not set(PHASES) <= phases):
            errors.append(key)
        if not case['trust'] and phases:
            violations.append(key)
    for case in fixtures():
        if case['trust'] or case['mode'] == 'normal':
            continue
        item, base = valid.get(case['case_id']), valid.get('model-entry-False-normal')
        if (item and base and base[0].get('outcome') == 'rejected_at_trust_gate' and base[0].get('event_collection_status') == 'completed'
                and not base[1] and any(e['phase'] == 'import' for e in item[1])):
            mode_violations.append(case['case_id'])
    complete = set(valid) == set(specs) and not errors
    return {'SUP-09': {'model_entry_confirmed_violations': {'value': violations, 'collected': bool(valid)}},
            'SUP-11': {'model_entry_mode_violations': {'value': mode_violations, 'collected': bool(valid)}},
            'complete': complete, 'errors': sorted(set(errors)),
            'scope': 'transformers model-entry loader in fresh Python children; not a vLLM engine boot or worker forward'}


def evaluate(rows, check_id):
    """`supply_boot_contract._dynamic`과 같은 (위반, 공백) 모양. 소비자 연결은 B가 정한다."""
    projected = project(rows)
    key = {'SUP-09': 'model_entry_confirmed_violations', 'SUP-11': 'model_entry_mode_violations'}[check_id]
    return ([{'reason': 'forbidden_model_code_executed' if check_id == 'SUP-09' else 'forbidden_model_code_imported_in_mode',
              'case': case} for case in projected[check_id][key]['value']],
            [] if projected['complete'] else ['model_entry_cases_incomplete'])


# --- 무해 사용자 모델 fixture와 실제 생산자(컨테이너 안에서 실행) --------------------------------------

CONFIG_JSON = json.dumps({'architectures': ['B2ProbeForCausalLM'], 'model_type': 'b2probe', 'vocab_size': 16, 'hidden_size': 8,
                          'auto_map': {'AutoConfig': 'configuration_b2probe.B2ProbeConfig',
                                       'AutoModelForCausalLM': 'modeling_b2probe.B2ProbeForCausalLM'}}, sort_keys=True)
CONFIGURATION_PY = '''import json, os
from transformers import PretrainedConfig


def _mark(phase, module):
    path = os.environ.get('BYUL_MODEL_ENTRY_EVENTS')
    if path:
        with open(path, 'a') as out:
            out.write(json.dumps({'phase': phase, 'module': module}) + '\\n')


_mark('import', 'configuration')


class B2ProbeConfig(PretrainedConfig):
    model_type = 'b2probe'

    def __init__(self, vocab_size=16, hidden_size=8, **kwargs):
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        super().__init__(**kwargs)
'''
MODELING_PY = '''import json, os
import torch
from transformers import PreTrainedModel
from .configuration_b2probe import B2ProbeConfig


def _mark(phase, module):
    path = os.environ.get('BYUL_MODEL_ENTRY_EVENTS')
    if path:
        with open(path, 'a') as out:
            out.write(json.dumps({'phase': phase, 'module': module}) + '\\n')


_mark('import', 'modeling')


class B2ProbeForCausalLM(PreTrainedModel):
    config_class = B2ProbeConfig

    def __init__(self, config):
        super().__init__(config)
        _mark('constructor', 'modeling')
        self.embed = torch.nn.Embedding(config.vocab_size, config.hidden_size)
        self.head = torch.nn.Linear(config.hidden_size, config.vocab_size)

    def forward(self, input_ids, **kwargs):
        _mark('forward', 'modeling')
        return self.head(self.embed(input_ids))
'''
FIXTURE_FILES = {'config.json': CONFIG_JSON, 'configuration_b2probe.py': CONFIGURATION_PY, 'modeling_b2probe.py': MODELING_PY}

# 컨테이너 안 `python3 -c RUNNER`. 표준 입력 없음. 임시 폴더에 fixture를 쓰고 사례마다 새 자식(-O/-OO)으로 실행해 행 목록을 낸다.
RUNNER = r'''
import hashlib,inspect,json,os,subprocess,sys,tempfile,traceback
FILES=json.loads(sys.argv[1]);CASES=json.loads(sys.argv[2]);CONTRACT=sys.argv[3]
CHILD=r"""
import hashlib,inspect,json,os,sys,traceback
case=json.loads(sys.argv[1]);folder=sys.argv[2];contract=sys.argv[3]
row={**case,'contract':contract,'case_identity':sys.argv[4],'outcome':'execution_error','python_optimize':sys.flags.optimize,
     'selected_source_sha256':None,'selected_source_matches':False,'selected_class_sha256':None,'forward_completed':False}
def control():
    try:
        import torch
        from transformers import AutoModelForCausalLM,GPT2Config
        m=AutoModelForCausalLM.from_config(GPT2Config(n_layer=1,n_embd=8,n_head=2,vocab_size=16,n_positions=8))
        m(torch.tensor([[1,2]]));return {'outcome':'accepted','forward_completed':True}
    except Exception as e:return {'outcome':'execution_error','forward_completed':False,'error_type':type(e).__name__}
def gate(error):
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error));tb=error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name=='resolve_trust_remote_code':return True
            tb=tb.tb_next
        error=error.__cause__ or error.__context__
    return False
row['before']=control()
try:
    import torch
    from transformers import AutoConfig,AutoModelForCausalLM
    cfg=AutoConfig.from_pretrained(os.path.join(folder,'model'),trust_remote_code=case['trust'],local_files_only=True)
    model=AutoModelForCausalLM.from_config(cfg,trust_remote_code=case['trust'])
    path=inspect.getfile(type(model));raw=open(path,'rb').read()
    row['selected_source_sha256']=hashlib.sha256(raw).hexdigest()
    row['selected_source_matches']=row['selected_source_sha256']==hashlib.sha256(open(os.path.join(folder,'model','modeling_b2probe.py'),'rb').read()).hexdigest()
    row['selected_class_sha256']=hashlib.sha256(type(model).__qualname__.encode()).hexdigest()
    model(torch.tensor([[1,2,3]]));row['forward_completed']=True;row['outcome']='accepted'
except Exception as e:
    row['outcome']='rejected_at_trust_gate' if not case['trust'] and gate(e) else 'execution_error';row['error_type']=type(e).__name__
row['after']=control()
events=[];status='completed'
try:
    for line in open(os.environ['BYUL_MODEL_ENTRY_EVENTS']).read().splitlines():events.append(json.loads(line))
except FileNotFoundError:pass
except Exception:status='partial'
row['events']=events;row['event_collection_status']=status
try:
    import transformers,torch;row['versions']={'transformers':transformers.__version__,'torch':torch.__version__}
except Exception:row['versions']=None
print(json.dumps(row))
"""
rows=[]
with tempfile.TemporaryDirectory() as base:
    for case,ident in CASES:
        folder=os.path.join(base,case['case_id']);os.makedirs(os.path.join(folder,'model'))
        for name,text in FILES.items():open(os.path.join(folder,'model',name),'w').write(text)
        env=dict(os.environ,HF_MODULES_CACHE=os.path.join(folder,'hf-modules'),BYUL_MODEL_ENTRY_EVENTS=os.path.join(folder,'events.jsonl'),
                 HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',VLLM_PLUGINS='')
        flags={'normal':[],'O':['-O'],'OO':['-OO']}[case['mode']]
        done=subprocess.run([sys.executable,*flags,'-c',CHILD,json.dumps(case),folder,CONTRACT,ident],env=env,capture_output=True,timeout=300)
        try:row=json.loads(done.stdout.decode().strip().splitlines()[-1])
        except Exception:row={**case,'contract':CONTRACT,'case_identity':ident,'outcome':'execution_error','events':[],'event_collection_status':'not_collected'}
        row['exit_code']=done.returncode;rows.append(row)
print(json.dumps({'contract':CONTRACT,'fixture_sha256':{n:hashlib.sha256(t.encode()).hexdigest() for n,t in FILES.items()},'rows':rows}))
'''


def runner_args():
    """RUNNER의 인자: fixture 파일·사례(신원 포함)·계약 이름."""
    return [json.dumps(FIXTURE_FILES), json.dumps([[c, identity(c)] for c in fixtures()]), CONTRACT]
