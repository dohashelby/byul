"""Validate bounded rolling shard discovery and exact source predecessor links."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json


def inventory_reader(original):
    return (original.replace("seen>64", "seen>128")
        .replace("len(files)>=32", "len(files)>=96")
        .replace("\\.jsonl',entry.name)", "(?:-segment-([0-9]{1,10}))?\\.jsonl',entry.name)")
        .replace("bytes=value.st_size,uid", "segment=int(match[3]) if match[3] is not None else None,bytes=value.st_size,uid")
        .replace("bytes=None,uid", "segment=int(match[3]) if match[3] is not None else None,bytes=None,uid")
        .replace("guard-lifecycle-session-inventory/1", "guard-lifecycle-rolling-inventory/1")
        .replace("(v['pid'],v['start_ticks'])", "(v['pid'],v['start_ticks'],-1 if v['segment'] is None else v['segment'])"))


def parse_inventory(raw, session):
    from .owned_capture import strict_json
    from .lifecycle_session import parse_inventory as parse_original
    value = strict_json(raw, limit=131072)
    if type(value) is not dict or value.get('schema') != 'guard-lifecycle-rolling-inventory/1':
        raise ValueError('LIFECYCLE_ROLLING_INVENTORY_INVALID')
    files = value.get('files')
    if type(files) is not list or len(files) > 96:
        raise ValueError('LIFECYCLE_ROLLING_FILE_BOUND')
    seen = set()
    validation = deepcopy(value); validation['schema'] = 'guard-lifecycle-session-inventory/1'
    validation['files'] = []
    parse_original(json.dumps(validation).encode(), session)
    for row in files:
        if type(row) is not dict or 'segment' not in row:
            raise ValueError('LIFECYCLE_ROLLING_FILE_INVALID')
        segment = row['segment']
        if segment is not None and (type(segment) is not int or not 0 <= segment < 10**10):
            raise ValueError('LIFECYCLE_ROLLING_SEGMENT_INVALID')
        key = (row.get('pid'), row.get('start_ticks'), segment)
        if key in seen:raise ValueError('LIFECYCLE_ROLLING_DUPLICATE_FILE')
        seen.add(key)
        validation['files'] = [{k:v for k,v in row.items() if k!='segment'}]
        parse_original(json.dumps(validation).encode(), session)
    value['files'] = sorted(files,key=lambda r:(r['pid'],r['start_ticks'],-1 if r['segment'] is None else r['segment']))
    value['processes'] = sorted(value['processes'],key=lambda r:r['pid'])
    value['unreadable_pids'] = sorted(value['unreadable_pids'])
    return value


def segment_marker(row, session):
    p = row.get('payload') or {}
    return (row.get('event')=='SEGMENT' and type(p) is dict
        and set(p)=={'schema','session','segment','previous_segment','previous_sha256','previous_bytes'}
        and p['schema']=='guard-native-segment/1' and p['session']==session
        and type(p['segment']) is int and 1<=p['segment']<10**10
        and type(p['previous_segment']) is int and p['previous_segment']==p['segment']-1
        and type(p['previous_bytes']) is int and 0<p['previous_bytes']<=4*1024*1024
        and type(p['previous_sha256']) is str and len(p['previous_sha256'])==64
        and all(c in '0123456789abcdef' for c in p['previous_sha256']))


def active_for(item, packet):
    """Return the current shard only after checking each retained byte link."""
    from .lifecycle_checkpoint import canonical
    cap=item.get('capture') or {}; rows=cap.get('records') or []
    if not rows:return None
    context=rows[0]['payload']['context']
    group=[]
    for other in packet.get('recordings',[]):
        value=other.get('capture') or {}; current=value.get('records') or []
        if not current:return None
        if current[0]['payload']['context']!=context:continue
        markers=[r for r in current if r.get('event')=='SEGMENT']
        if len(markers)>1 or markers and (current[1] is not markers[0] or not segment_marker(markers[0],packet['session'])):
            return None
        generation=markers[0]['payload']['segment'] if markers else 0
        if other.get('segment') is not None and other['segment']!=generation:return None
        group.append((generation,other,current,markers[0]['payload'] if markers else None))
    group.sort(key=lambda r:r[0])
    active=[row for row in group if row[1].get('segment') is None]
    if len(active)!=1 or active[0] is not group[-1]:return None
    if len({r[0] for r in group})!=len(group):return None
    first_header=group[0][2][0]['payload']
    for index,(generation,other,current,link) in enumerate(group):
        if current[0]['payload']!=first_header:return None
        if any(r.get('event')=='SG_GAP' or r.get('payload',{}).get('phase')=='OBSERVER_GAP' for r in current):return None
        if other is not active[0][1]:
            if current[-1].get('event')!='END' or current[-1]['payload']!={'reason':'SEGMENT_ROLLOVER'}:
                return None
            summary=other['capture'].get('summary') or {}
            if summary.get('recording_closed') is not True or summary.get('recording_end')!='SEGMENT_ROLLOVER':return None
        if index:
            prevgen,previous,previous_rows,_=group[index-1]
            raw=b''.join(canonical(r) for r in previous_rows)
            if (generation!=prevgen+1 or link is None or link['previous_segment']!=prevgen
                or link['previous_bytes']!=len(raw) or link['previous_sha256']!=hashlib.sha256(raw).hexdigest()
                or previous['capture'].get('provenance',{}).get('capture_sha256')!=hashlib.sha256(raw).hexdigest()):return None
    return active[0][1]
