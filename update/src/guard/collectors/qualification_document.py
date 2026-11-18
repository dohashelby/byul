"""Bounded strict qualification JSON without runtime or target access."""
import json,math


def read(raw):
    if type(raw)is not bytes or not 0<len(raw)<=32768:raise ValueError('QUALIFICATION_DOCUMENT_BOUND')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('QUALIFICATION_DOCUMENT_DUPLICATE_KEY')
            result[key]=value
        return result
    def number(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('QUALIFICATION_DOCUMENT_NONFINITE')
        return result
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_float=number,
            parse_constant=lambda _:(_ for _ in()).throw(ValueError('QUALIFICATION_DOCUMENT_NONFINITE')))
    except(UnicodeError,RecursionError,json.JSONDecodeError):raise ValueError('QUALIFICATION_DOCUMENT_INVALID')from None
    pending=[(value,0)]
    while pending:
        item,depth=pending.pop()
        if depth>64:raise ValueError('QUALIFICATION_DOCUMENT_DEPTH_BOUND')
        if type(item)is dict:pending.extend((child,depth+1)for child in item.values())
        elif type(item)is list:pending.extend((child,depth+1)for child in item)
    return value
