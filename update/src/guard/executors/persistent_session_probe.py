"""Closed owned keep-alive observation; never changes issuer or credentials.

The Kubernetes wrapper supplies the unchanged bounded stream HTTPResponse class.
Only safe structural results leave this process; request/response bytes and keys
remain transient. A selected probe socket is not a serving session/worker receipt.
"""
from __future__ import annotations
import hashlib,http.client,ipaddress,json,math,os,re,socket,stat,time
from urllib.parse import urlsplit

SCHEMA='guard-owned-persistent-http-observation/1'
SLOTS=('control-before','retired-session-open','retired-session-reuse','retired-new','control-after')
MAX_BODY=131072


def _hash(value):return hashlib.sha256(value.encode()).hexdigest()


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError()
        result[key]=value
    return result


def _nonfinite(value):raise ValueError()


def _validate(spec):
    fields={'schema','url','api_model','request_token','keys','open_before_ns','reuse_after_ns','max_clock_skew_ms','max_wait_seconds','timeout_seconds'}
    if type(spec) is not dict or set(spec)!=fields or spec['schema']!=SCHEMA:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    endpoint=urlsplit(spec['url'])
    if (endpoint.scheme!='http' or endpoint.username is not None or endpoint.password is not None or endpoint.path or endpoint.query or endpoint.fragment
            or not endpoint.port or ipaddress.ip_address(endpoint.hostname).version!=4):raise ValueError('SESSION_PROBE_INPUT_INVALID')
    if type(spec['api_model']) is not str or re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}',spec['api_model']) is None:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    if type(spec['request_token']) is not str or re.fullmatch('[0-9a-f]{32}',spec['request_token']) is None:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    keys=spec['keys']
    if (type(keys) is not dict or set(keys)!={'retired','control'} or any(type(v) is not str or not 0<len(v)<=8192 or any(ord(c)<33 or ord(c)>126 for c in v) for v in keys.values())
            or len(set(keys.values()))!=2):raise ValueError('SESSION_PROBE_INPUT_INVALID')
    for field in ('open_before_ns','reuse_after_ns'):
        if type(spec[field]) is not int or not 0<spec[field]<2**63:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    if spec['open_before_ns']>=spec['reuse_after_ns']:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    if type(spec['max_clock_skew_ms']) is not int or not 0<=spec['max_clock_skew_ms']<=60000:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    for field,maximum in (('max_wait_seconds',10),('timeout_seconds',60)):
        if type(spec[field]) not in (int,float) or not math.isfinite(spec[field]) or not 0<spec[field]<=maximum:raise ValueError('SESSION_PROBE_INPUT_INVALID')
    return endpoint


def _identity(sock):
    try:
        fd=sock.fileno();info=os.fstat(fd)
        if type(sock) is not socket.socket or fd<0 or not stat.S_ISSOCK(info.st_mode) or info.st_ino<=0:return None
        return {'probe_pid':os.getpid(),'descriptor':fd,'device':info.st_dev,'inode':info.st_ino}
    except (OSError,ValueError,TypeError,AttributeError):return None


def collect(spec,response_class):
    """Five fixed GETs; reused connection has implicit reconnect disabled."""
    endpoint=_validate(spec);wall0=time.time_ns();mono0=time.monotonic_ns();deadline=mono0+int(spec['timeout_seconds']*10**9)
    latest=spec['reuse_after_ns']+spec['max_clock_skew_ms']*10**6
    # No connection is opened unless the bounded declared schedule is ready.
    if not wall0<spec['open_before_ns'] or not 0<latest-wall0<=spec['max_wait_seconds']*10**9:raise ValueError('SESSION_PROBE_SCHEDULE_UNAVAILABLE')
    if spec['max_wait_seconds']>=spec['timeout_seconds']:raise ValueError('SESSION_PROBE_SCHEDULE_UNAVAILABLE')
    rows=[];cleanup=[];selected=None;selected_identity=None;selected_socket=None
    def remaining():
        value=(deadline-time.monotonic_ns())/10**9
        if value<=0:raise TimeoutError()
        return min(10,value)
    def clock_ok(wall,mono):return abs((wall-wall0)-(mono-mono0))<=spec['max_clock_skew_ms']*10**6
    def close(connection):
        if connection is not None:
            try:connection.close()
            except Exception:cleanup.append('SESSION_PROBE_OWN_CONNECTION_CLOSE_UNVERIFIED')
    def new_connection():
        connection=http.client.HTTPConnection(endpoint.hostname,endpoint.port,timeout=remaining());connection.response_class=response_class
        try:
            connection.connect();connection.auto_open=0
            return connection
        except Exception:
            close(connection);raise
    try:
        for slot in SLOTS:
            role='retired' if slot.startswith('retired-') else 'control';connection=None
            rid='byul-plt11-'+spec['request_token']+'-'+slot
            row={'case_id':'plt11-'+slot,'request_id_sha256':_hash(rid),'credential_label':role,
                'attempted':False,'outcome':None,'normal_shape_verified':False,'denial_response_observed':False,'capture_complete':False,
                'started_wall_ns':None,'finished_wall_ns':None,'started_monotonic_ns':None,'finished_monotonic_ns':None,'clock_consistent':False,
                'probe_socket_before':None,'probe_socket_after':None,'selected_existing_socket_verified':False,'new_distinct_probe_connection_verified':False,'response_allows_keepalive':False,
                'diagnostic':None,'issuer_event_verified':False,'serving_session_receipt_verified':False}
            rows.append(row)
            try:
                if slot=='retired-session-reuse':
                    wait_start=time.monotonic_ns()
                    while time.time_ns()<=latest:
                        remaining()
                        if (time.monotonic_ns()-wait_start)/10**9>spec['max_wait_seconds']:raise TimeoutError()
                        time.sleep(min(0.02,remaining()))
                    if selected is None or selected.sock is not selected_socket or selected_identity is None or _identity(selected.sock)!=selected_identity:raise ValueError()
                    connection=selected
                else:connection=new_connection()
                before=connection.sock;before_identity=_identity(before)
                row['new_distinct_probe_connection_verified']=bool(slot=='retired-new' and before is not selected_socket and before_identity is not None
                    and selected_identity is not None and selected is not None and selected.sock is selected_socket
                    and _identity(selected_socket)==selected_identity and before_identity!=selected_identity)
                if slot=='retired-session-open':
                    if time.time_ns()>=spec['open_before_ns']:raise ValueError()
                    selected=connection;selected_socket=before;selected_identity=before_identity
                row.update(attempted=True,started_wall_ns=time.time_ns(),started_monotonic_ns=time.monotonic_ns(),probe_socket_before=before_identity)
                connection.timeout=remaining();before.settimeout(remaining())
                connection.request('GET','/v1/models',headers={'Authorization':'Bearer '+spec['keys'][role],'X-Request-Id':rid,'Connection':'keep-alive'})
                response=connection.getresponse();raw=bytearray();complete=False
                while len(raw)<=MAX_BODY:
                    remaining()
                    if connection.sock is not None:connection.sock.settimeout(remaining())
                    piece=response.read1(min(16384,MAX_BODY-len(raw)+1))
                    if not piece:complete=True;break
                    raw.extend(piece)
                status=response.status;shape=False
                if complete and status==200:
                    body=json.loads(bytes(raw).decode('utf-8'),object_pairs_hook=_unique,parse_constant=_nonfinite);models=body.get('data') if type(body) is dict else None
                    shape=bool(type(body) is dict and body.get('object')=='list' and type(models) is list and 1<=len(models)<=128
                        and all(type(v) is dict and type(v.get('id')) is str for v in models) and any(v['id']==spec['api_model'] for v in models))
                same=connection.sock is before and before_identity is not None and _identity(connection.sock)==before_identity
                row.update(outcome=status,normal_shape_verified=shape,capture_complete=complete,denial_response_observed=complete and status in {401,403},
                    probe_socket_after=_identity(connection.sock),response_allows_keepalive=not response.will_close,
                    selected_existing_socket_verified=bool(same and not response.will_close and slot in {'retired-session-open','retired-session-reuse'}
                        and before is selected_socket and before_identity==selected_identity))
                response.close()
            except Exception:
                row['diagnostic']='SESSION_PROBE_SELECTED_ACCESS_UNAVAILABLE'
            finally:
                row.update(finished_wall_ns=time.time_ns(),finished_monotonic_ns=time.monotonic_ns())
                row['clock_consistent']=bool(row['started_wall_ns'] and clock_ok(row['started_wall_ns'],row['started_monotonic_ns'])
                    and clock_ok(row['finished_wall_ns'],row['finished_monotonic_ns']))
                if connection is not selected:close(connection)
    finally:close(selected)
    return {'schema':SCHEMA,'rows':rows,'cleanup_errors':sorted(set(cleanup)),'issuer_mutated':False,'credential_bytes_or_hashes_recorded':False,
        'probe_process_only':True,'producer_authenticated':False,'serving_session_receipt_verified':False,'application_session_or_handle_verified':False}
