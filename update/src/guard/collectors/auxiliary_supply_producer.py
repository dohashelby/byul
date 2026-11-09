"""SUP-03 Python auxiliary parser support, distinct from native worker consumption."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from guard.adapters import vllm_auxiliary_read as observer

CONTRACT = 'guard-auxiliary-parser-input/1'
ROLES = ('config', 'tokenizer', 'template')


def producer():
    digest = hashlib.sha256()
    for path in (Path(observer.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': 'guard-auxiliary-parser', 'version': '1', 'source_sha256': digest.hexdigest()}


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def project(value, *, allowed_sha256=None):
    """Keep observed reads/independent exclusions; absent policy is never an empty allowlist.

    Complete applies to these three Python parser components, not SUP-03 native
    scope. No caller-supplied admission flag can change the worker limitation.
    """
    if allowed_sha256 is not None and (type(allowed_sha256) is not list or not all(_sha(v) for v in allowed_sha256)):
        raise ValueError('AUXILIARY_ALLOWLIST_INVALID')
    keys = {'contract', 'producer', 'session', 'pid', 'start_ticks', 'events', 'results', 'source_ended', 'diagnostic_codes', 'data_origin'}
    if (type(value) is not dict or set(value) != keys or value.get('contract') != CONTRACT or value.get('producer') != producer()
            or value.get('data_origin') != 'EXAMPLE'
            or type(value.get('session')) is not str or len(value['session']) != 32
            or any(c not in '0123456789abcdef' for c in value['session'])
            or type(value.get('pid')) is not int or value['pid'] < 1
            or type(value.get('start_ticks')) is not int or value['start_ticks'] < 0
            or type(value.get('events')) is not list or len(value['events']) > observer.MAX_EVENTS
            or type(value.get('results')) is not list or len(value['results']) > len(ROLES)
            or type(value.get('source_ended')) is not bool
            or type(value.get('diagnostic_codes')) is not list):
        raise ValueError('AUXILIARY_ENVELOPE_INVALID')
    if any(type(e) is not str or re.fullmatch(r'AUXILIARY_[A-Z0-9_]{1,116}', e) is None for e in value['diagnostic_codes']):
        raise ValueError('AUXILIARY_DIAGNOSTIC_INVALID')
    errors = set(value['diagnostic_codes'])
    reads = {}
    common = {'schema', 'session', 'pid', 'start_ticks', 'call', 'root', 'path_sha256', 'role', 'point', 'source_sha256', 'entry_sha256', 'phase'}
    read_fields = {'stable', 'qualified', 'complete', 'file_bytes', 'file_sha256', 'parser_input_bytes', 'parser_input_sha256'}
    for row in value['events']:
        if (type(row) is not dict or row.get('schema') != observer.SCHEMA
                or (row.get('session'), row.get('pid'), row.get('start_ticks')) != (value['session'], value['pid'], value['start_ticks'])
                or row.get('role') not in ROLES or type(row.get('call')) is not str
                or type(row.get('point')) is not str
                or len(row['call']) != 32 or any(c not in '0123456789abcdef' for c in row['call'])
                or type(row.get('root')) is not int or not 0 <= row['root'] < 8
                or not all(_sha(row.get(k)) for k in ('path_sha256', 'source_sha256', 'entry_sha256'))):
            errors.add('AUXILIARY_ROW_INVALID'); continue
        matching = [name for name, points in observer.POINTS.items() if points.get(row.get('point')) == row['role']]
        if len(matching) != 1 or row['source_sha256'] != observer.SOURCES[matching[0]]:
            errors.add('AUXILIARY_SOURCE_MISMATCH'); continue
        if row.get('phase') != 'READ_RETURN':
            if row.get('phase') not in ('READ_RAISE', 'OPEN_RAISE') or set(row) != common:
                errors.add('AUXILIARY_ROW_INVALID')
            else:
                errors.add('AUXILIARY_LOADER_READ_FAILED')
            continue
        if (set(row) != common | read_fields or not all(type(row.get(k)) is bool for k in ('stable', 'qualified', 'complete'))
                or not all(type(row.get(k)) is int and 0 <= row[k] <= observer.MAX_READ_BYTES for k in ('file_bytes', 'parser_input_bytes'))
                or not all(_sha(row.get(k)) for k in ('file_sha256', 'parser_input_sha256'))):
            errors.add('AUXILIARY_ROW_INVALID'); continue
        if row['call'] in reads:
            errors.add('AUXILIARY_DUPLICATE_READ'); continue
        reads[row['call']] = row
        if not row['qualified'] or not row['stable'] or not row['complete']:
            errors.add('AUXILIARY_READ_INCOMPLETE')
    consumed, roles = [], set()
    for result in value['results']:
        if (type(result) is not dict or set(result) != {'role', 'accepted', 'output_sha256', 'read_calls'}
                or result.get('role') not in ROLES or result['role'] in roles or type(result.get('accepted')) is not bool
                or not _sha(result.get('output_sha256')) or type(result.get('read_calls')) is not list
                or not result['read_calls'] or any(type(k) is not str or k not in reads for k in result['read_calls'])):
            errors.add('AUXILIARY_RESULT_UNBOUND'); continue
        if len(set(result['read_calls'])) != len(result['read_calls']):
            errors.add('AUXILIARY_RESULT_UNBOUND'); continue
        roles.add(result['role'])
        linked = [reads[k] for k in result['read_calls']]
        if any(r['role'] != result['role'] for r in linked):
            errors.add('AUXILIARY_RESULT_UNBOUND'); continue
        if not result['accepted']:
            errors.add('AUXILIARY_LOADER_FAILED'); continue
        consumed.extend(r for r in linked if r['stable'] and r['qualified'] and r['complete'])
    violations = [] if allowed_sha256 is None else [
        {'reason': 'auxiliary_parser_read_outside_allowlist', 'role': r['role'], 'path_sha256': r['path_sha256'],
         'file_sha256': r['file_sha256'], 'call': r['call']} for r in consumed if r['file_sha256'] not in allowed_sha256]
    if not value['source_ended']:
        errors.add('AUXILIARY_SOURCE_INCOMPLETE')
    if roles != set(ROLES):
        errors.add('AUXILIARY_PARSER_ROLE_MISSING')
    return {'parser_reads': consumed, 'violation_candidates': violations, 'errors': sorted(errors),
            'parser_scope_complete': not errors, 'policy_supplied': allowed_sha256 is not None,
            'native_worker_consumption_verified': False, 'producer_authenticated': False,
            'missing': ['AUXILIARY_NATIVE_TOKENIZER_READS_UNOBSERVED', 'AUXILIARY_WORKER_CONSUMPTION_UNOBSERVED',
                        'AUXILIARY_CACHE_CONSUMPTION_UNOBSERVED']}


CAPTURE = r'''
import hashlib,json,os,sys,types,uuid
source,producer,model,template=sys.argv[1:]
module=types.ModuleType('guard_auxiliary_observer')
exec(compile(source,'guard_auxiliary_observer.py','exec'),module.__dict__)
from transformers import AutoConfig,AutoTokenizer,configuration_utils,tokenization_utils_base
from vllm.entrypoints import chat_utils
observer=module.AuxiliaryReadObserver([model,template],session=uuid.uuid4().hex)
for target in (configuration_utils,tokenization_utils_base,chat_utils): observer.install(target)
results=[]
def run(role,loader):
    start=len(observer.events)
    try:
        value=loader(); accepted=True
    except Exception:
        value=None; accepted=False
    calls=[r['call'] for r in observer.events[start:] if r.get('phase')=='READ_RETURN' and r.get('role')==role]
    results.append({'role':role,'accepted':accepted,'output_sha256':module.sha(json.dumps(value,sort_keys=True,default=str).encode()),'read_calls':calls})
run('config',lambda:AutoConfig.from_pretrained(model,local_files_only=True,trust_remote_code=False).to_dict())
run('tokenizer',lambda:AutoTokenizer.from_pretrained(model,local_files_only=True,trust_remote_code=False).encode('guard control'))
run('template',lambda:chat_utils.load_chat_template(template))
observer.restore()
print(json.dumps({'contract':'guard-auxiliary-parser-input/1','producer':json.loads(producer),'session':observer.session,
 'pid':observer.pid,'start_ticks':observer.start_ticks,'events':observer.events,'results':results,
 'source_ended':True,'diagnostic_codes':sorted(observer.gaps),'data_origin':'EXAMPLE'}))
'''


def capture_args(model, template):
    for path in (model, template):
        if type(path) is not str or not path.startswith('/') or '..' in Path(path).parts:
            raise ValueError('AUXILIARY_PATH_INVALID')
    return [Path(observer.__file__).read_text(), json.dumps(producer()), model, template]
