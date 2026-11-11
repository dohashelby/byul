"""Actual stdlib HTTPResponse framing, no socket/HTTP parser replacement."""
from contextlib import redirect_stdout
import hashlib,http.client,io,json,sys
from unittest.mock import patch
import pytest
from guard.executors import lab_activation as actor

BODY=b'{"model":"model","choices":[{"message":{"content":"ok"}}]}'


def execute_response(raw):
    class Socket:
        def makefile(self,*_args,**_kwargs):return io.BytesIO(raw)
    response=http.client.HTTPResponse(Socket());response.begin()
    class Connection:
        def __init__(self,*_args,**_kwargs):pass
        def connect(self):pass
        def request(self,*_args,**_kwargs):pass
        def getresponse(self):return response
        def close(self):response.close()
    spec={'url':'http://127.0.0.1:8000','requests':[{'method':'POST','path':'/v1/chat/completions',
        'body':{'model':'model','messages':[{'role':'user','content':'ping'}]}}]}
    out=io.StringIO()
    with patch('http.client.HTTPConnection',Connection),patch('sys.stdin',io.StringIO(json.dumps(spec))),redirect_stdout(out):
        exec(compile(actor.CLIENT,'actual-owned-client.py','exec'),{'__name__':'_actual_client'})
    row,=json.loads(out.getvalue());return row


def test_eof_before_declared_content_length_is_not_a_complete_normal_control():
    raw=b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(BODY)+10).encode()+b'\r\n\r\n'+BODY
    row=execute_response(raw)
    assert row['outcome']==200 and row['complete'] is False and row['normal_shape_verified'] is False
    assert row['error_stage']=='read' and row['error_kind']=='incomplete-body'
    assert row['sha256']==hashlib.sha256(BODY).hexdigest() and row['bytes']==len(BODY)


@pytest.mark.parametrize('kind',['exact-content-length','close-delimited','chunked'])
def test_supported_complete_framings_preserve_normal_shape(kind):
    if kind=='exact-content-length':raw=b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(BODY)).encode()+b'\r\n\r\n'+BODY
    elif kind=='close-delimited':raw=b'HTTP/1.0 200 OK\r\n\r\n'+BODY
    else:raw=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'+hex(len(BODY))[2:].encode()+b'\r\n'+BODY+b'\r\n0\r\n\r\n'
    row=execute_response(raw)
    assert row['complete'] is True and row['normal_shape_verified'] is True
    assert row['sha256']==hashlib.sha256(BODY).hexdigest() and row['bytes']==len(BODY)


def test_truncated_chunked_response_never_becomes_normal_control():
    raw=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'+hex(len(BODY))[2:].encode()+b'\r\n'+BODY+b'\r\n'
    row=execute_response(raw)
    assert row['complete'] is False and row['normal_shape_verified'] is False
    assert row['error_stage']=='read' and row['error_kind']=='transport-error'

    assert row['sha256']==hashlib.sha256(BODY).hexdigest() and row['bytes']==len(BODY)
