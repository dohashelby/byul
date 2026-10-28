"""SUP-09/11 shared local loader witness, no new approval or product policy."""
from __future__ import annotations
import hashlib
import json


def fixtures():
    return [{'case_id':f'{entry}-{trust}-{mode}','entry':entry,'trust':trust,'mode':mode}
            for entry in ('autoconfig','vllm-config') for trust in (False,True) for mode in ('normal','O','OO')]


def identity(case):
    return hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def clean_phases(events):
    if type(events) is not list or any(type(e) is not dict or set(e)!={'phase'} or e['phase'] not in ('import','constructor') for e in events):
        raise ValueError('invalid dynamic module marker')
    return [dict(e) for e in events]


def _control_ok(value):
    return isinstance(value,dict) and value.get("outcome")=="accepted"


def project(rows):
    specs={r['case_id']:r for r in fixtures()};valid={};violations=[];mode_violations=[];errors=[]
    for row in rows:
        key=row.get('case_id') if isinstance(row,dict) else None
        if type(key) is not str or key not in specs or key in valid:
            errors.append('unknown_or_duplicate_case');continue
        case=specs[key]
        if row.get('case_identity')!=identity(case):errors.append(key);continue
        try:events=clean_phases(row.get('events',[]))
        except ValueError:errors.append(key);continue
        valid[key]=row
        if (row.get('event_collection_status')!='completed' or row.get('outcome') not in ('accepted','rejected_at_trust_gate')
                or not _control_ok(row.get('before')) or not _control_ok(row.get('after'))
                or (case['trust'] and (row.get('outcome')!='accepted'
                    or row.get('selected_fixture_source_matches') is not True
                    or not {'import','constructor'}.issubset({e['phase'] for e in events})))):errors.append(key)
        if not case['trust'] and any(e['phase']=='import' for e in events):violations.append(key)
    for case in fixtures():
        if case['trust'] or case['mode']=='normal':continue
        row=valid.get(case['case_id']);baseline=valid.get(f"{case['entry']}-False-normal")
        if (row is not None and baseline is not None and baseline.get('outcome')=='rejected_at_trust_gate'
                and baseline.get('event_collection_status')=='completed' and not baseline.get('events')
                and any(e['phase']=='import' for e in row.get('events',[]))):
            mode_violations.append(case['case_id'])
    return {
        'SUP-09':{'dynamic_code_confirmed_violations':{'value':violations,'collected':bool(valid)}},
        'SUP-11':{'dynamic_mode_confirmed_violations':{'value':mode_violations,'collected':bool(valid)}},
        'complete':set(valid)==set(specs) and not errors,
    }


def judge_params(check_id):
    key={'SUP-09':'dynamic_code_confirmed_violations','SUP-11':'dynamic_mode_confirmed_violations'}[check_id]
    return {'fail_when_any':[{'obs':key,'op':'nonempty'}]}
