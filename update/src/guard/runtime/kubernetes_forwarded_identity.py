"""REQ04 declared header boundary; supplied claims never authenticate a principal.

A supplied identity claim is never an authenticated authorization principal.
The actual normalized/consumed field requires an independently bound source.
"""
import re

PROFILE='kubernetes-owned-forwarded-identity-v1'
PRODUCT_REGISTERED=True
KINDS=frozenset({'identity_claim','operating_selector'})
RESERVED=frozenset({'authorization','proxy-authorization','cookie','set-cookie','host','content-length',
    'transfer-encoding','connection','x-request-id','content-type','accept','accept-encoding','expect','upgrade','trailer','te'})


def normalize_header_contract(value):
    """Names only; do not read credentials or infer an issuer/enterprise allowance."""
    try:
        if (type(value) is not dict or set(value)!={'header_name','field_kind','normal_value_env','alternate_value_env'}
                or type(value['header_name']) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',value['header_name']) is None
                or value['header_name'].lower() in RESERVED or type(value['field_kind']) is not str
                or value['field_kind'] not in KINDS):raise ValueError
        for key in ('normal_value_env','alternate_value_env'):
            if type(value[key]) is not str or re.fullmatch(r'[A-Z_][A-Z0-9_]{0,63}',value[key]) is None:raise ValueError
        if value['normal_value_env']==value['alternate_value_env']:raise ValueError
        return dict(value)
    except (KeyError,TypeError,ValueError):
        raise ValueError('FORWARDED_IDENTITY_HEADER_CONTRACT_INVALID') from None
