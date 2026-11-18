"""Closed submitted-pair policy; historical token-only definitions stay strict."""
from .input_policy import InputContext

PROFILE='input-matrix-v1'
PAIR_KEY='allowed_input_pairs'
MARKERS={'input_matrix_planned':'list[str]','input_matrix_cases':'list','input_matrix_complete':'bool'}
FORMATS=frozenset({'text','token_ids'})


def pairs(check,values):
    if(check.id!='REQ-09'or getattr(check,'collection_profile',None)!=PROFILE
            or {o.key:o.type for o in check.required_observations if o.key in MARKERS}!=MARKERS
            or not any(p.key==PAIR_KEY and p.type=='list[str]'and p.required for p in check.policy_params)):
        raise ValueError('INPUT_MATRIX_DEFINITION_INCOMPLETE')
    endpoints=values.get('allowed_endpoints');formats=values.get('allowed_input_formats');selected=values.get(PAIR_KEY)
    probes=[p.id for p in check.functional_probes if p.method=='POST'and p.via=='engine'and p.expect==[200]]
    if(any(type(v)is not list or not v or len(v)>8 or any(type(x)is not str or not x for x in v)
            or len(set(v))!=len(v)for v in(endpoints,formats,selected))
            or set(endpoints)!=set(probes)or not set(formats)<=FORMATS):raise ValueError('INPUT_MATRIX_POLICY_INVALID')
    result=[]
    for pair in selected:
        endpoint,separator,format_name=pair.rpartition('|')
        if not separator or endpoint not in endpoints or format_name not in formats:raise ValueError('INPUT_MATRIX_PAIR_INVALID')
        result.append((endpoint,format_name))
    if {p[0]for p in result}!=set(endpoints)or {p[1]for p in result}!=set(formats):raise ValueError('INPUT_MATRIX_POLICY_COVERAGE_INCOMPLETE')
    return result


def planned(check,values):
    return [endpoint+'|'+format_name+'|'+case for endpoint,format_name in pairs(check,values)
        for case in(('token_id','length')if format_name=='token_ids'else('length',))]


def context(check,values,observations=None):
    reasons=['POLICY_CONTEXT: INPUT_MATRIX; explicit submitted endpoint/format pairs, not all parser or consumer paths']
    missing=[];errors=[]
    try:
        expected=planned(check,values)
        if observations is not None:
            for key in('input_endpoints_tested','input_formats_tested',*MARKERS):
                if key not in observations or not observations[key].collected:missing.append(key)
            if missing:raise ValueError('INPUT_MATRIX_OBSERVATIONS_MISSING')
            if(observations['input_endpoints_tested'].value!=values['allowed_endpoints']
                    or observations['input_formats_tested'].value!=values['allowed_input_formats']
                    or observations['input_matrix_planned'].value!=expected
                    or observations['input_matrix_complete'].value is not True):raise ValueError('INPUT_MATRIX_SCOPE_INCOMPLETE')
            rows=observations['input_matrix_cases'].value
            if(type(rows)is not list or len(rows)!=len(expected)
                    or [r.get('case_id')for r in rows]!=expected
                    or any(r.get('rejection_state')!='verified'or r.get('normal_controls_pass')is not True for r in rows)):
                raise ValueError('INPUT_MATRIX_CASES_INCOMPLETE')
    except(ValueError,TypeError,KeyError)as error:errors.append(str(error)if type(error)is ValueError else'INPUT_MATRIX_CONTRACT_INVALID')
    reasons.extend('POLICY_CONTEXT: '+code+'; PASS withheld'for code in errors)
    return InputContext(bool(errors),tuple(reasons),tuple(missing))
