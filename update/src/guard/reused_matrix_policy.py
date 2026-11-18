"""Explicit reused-field scope; historical single-field policies are unchanged."""
import re
from .input_policy import InputContext

PROFILE='reused-token-input-matrix-v1'
MARKERS={'reused_fields_tested':'list[str]','reused_matrix_complete':'bool'}


def selected(check,values):
    if(check.id!='REQ-08'or getattr(check,'collection_profile',None)!=PROFILE
            or {o.key:o.type for o in check.required_observations if o.key in MARKERS}!=MARKERS
            or not all(any(p.key==key and p.required and p.type==kind for p in check.policy_params)
                for key,kind in [('reused_token_fields','list[str]'),('reused_field_endpoints','dict')])):
        raise ValueError('REUSED_MATRIX_DEFINITION_INCOMPLETE')
    fields=values.get('reused_token_fields');endpoints=values.get('reused_field_endpoints')
    if(type(fields)is not list or not 1<=len(fields)<=8
            or any(type(v)is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,3}',v)is None for v in fields)
            or len(set(fields))!=len(fields)or values.get('reused_token_field')not in fields
            or type(endpoints)is not dict or set(endpoints)!=set(fields)
            or any(type(v)is not str for v in endpoints.values())
            or set(endpoints.values())!={p.id for p in check.functional_probes}
            or any(p.method!='POST'or p.via!='engine'or p.expect!=[200]for p in check.functional_probes)):
        raise ValueError('REUSED_MATRIX_POLICY_INVALID')
    return fields,endpoints


def context(check,values,observations=None):
    errors=[];missing=[]
    try:
        fields,endpoints=selected(check,values)
        if observations is not None:
            for key in (*MARKERS,'reused_input_trials'):
                if key not in observations or not observations[key].collected:missing.append(key)
            if missing:raise ValueError('REUSED_MATRIX_OBSERVATIONS_MISSING')
            rows=observations['reused_input_trials'].value
            if(observations['reused_fields_tested'].value!=fields or observations['reused_matrix_complete'].value is not True
                    or type(rows)is not list or len(rows)!=len(fields)
                    or any(type(row)is not dict or row.get('field')!=field or row.get('endpoint')!=endpoints[field]
                        or row.get('qualified_frontend_response_identity')is not True
                        or row.get('state')not in {'ignored','rejected','consumed'}for field,row in zip(fields,rows))):
                raise ValueError('REUSED_MATRIX_SCOPE_INCOMPLETE')
    except(ValueError,KeyError,TypeError)as error:errors.append(str(error)if type(error)is ValueError else'REUSED_MATRIX_CONTRACT_INVALID')
    reasons=['POLICY_CONTEXT: REUSED_MATRIX; selected current-request frontend and controlled response identities; no GPU token-memory claim']
    reasons.extend('POLICY_CONTEXT: '+code+'; PASS withheld'for code in errors)
    return InputContext(bool(errors),tuple(reasons),tuple(missing))
