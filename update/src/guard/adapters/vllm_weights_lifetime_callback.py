"""LIF-13: weights daemon의 공급·release·release 뒤 취득·fallback·release 주체.

vLLM 0.31.0 `weight_cache.daemon`(f4fc4254…)의 `_handle_get_state`·`_handle_release`와
`weight_cache.ipc_loader`(45c3a633…)의 `load_model`·`_request_state`·`_fallback_load`를
원 소스 해시로 고정해 감싼다. daemon 쪽은 UNIX socket 상대(SO_PEERCRED)의 pid·uid를
keyed 요약으로, 가중치 세대는 객체 token으로 남긴다. tensor·IPC handle·주소는 남기지 않는다.

release 주체의 허용 여부(기업 정책)는 판단하지 않는다. 주체가 누구였는지만 남긴다.
"""
from __future__ import annotations

import socket
import struct

from . import vllm_cache_callback as cache

DAEMON_MODULE = 'vllm.model_executor.model_loader.weight_cache.daemon'
LOADER_MODULE = 'vllm.model_executor.model_loader.weight_cache.ipc_loader'
TARGETS = {DAEMON_MODULE: ('WeightCacheDaemon._handle_get_state', 'WeightCacheDaemon._handle_release'),
           LOADER_MODULE: ('IpcModelLoader.load_model', 'IpcModelLoader._request_state', 'IpcModelLoader._fallback_load')}
SOURCES = {DAEMON_MODULE: 'f4fc42549b0cce117cc006506d80b0d493f095f58825f89494a8d6ce43849e63',
           LOADER_MODULE: '45c3a633a1556deab41404b5f5a4fb049f5f9a4dd1a21fb3a61bea16eea9f2da'}
SCHEMA = 'guard-vllm-weights-lifetime-callback/1'
_CRED = struct.Struct('3i')


def peer(conn):
    """UNIX socket 상대 프로세스의 (pid, uid, gid). Linux가 아니거나 읽을 수 없으면 None."""
    try:
        option = getattr(socket, 'SO_PEERCRED')
        return tuple(_CRED.unpack(conn.getsockopt(socket.SOL_SOCKET, option, _CRED.size)))
    except Exception:
        return None


class WeightsLifetimeObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-weights-lifetime-callback-candidate-v1'
    peer_reader = staticmethod(peer)

    def _peer(self, conn):
        found = self.peer_reader(conn)
        if found is None:
            self.gaps.add('WEIGHTS_PEER_CREDENTIAL_UNAVAILABLE')
            return {'peer_pid_sha256': None, 'peer_uid_sha256': None}
        pid, uid, _ = found
        return {'peer_pid_sha256': self.fingerprint(pid), 'peer_uid_sha256': self.fingerprint(uid)}

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        fields = cache.base.attrs(receiver)
        if point.startswith('WeightCacheDaemon.'):
            conn = args[1] if len(args) > 1 else kwargs.get('conn')
            model = fields.get('model')
            row = {**self._peer(conn), 'daemon_token': self.token(receiver), 'weights_generation_token': self.token(model),
                   'weights_present': model is not None}
            if point == 'WeightCacheDaemon._handle_get_state' and not returned:
                request = args[2] if len(args) > 2 else kwargs.get('request')
                client = request.get('cache_config') if type(request) is dict else None
                try:
                    mismatch = fields.get('cache_config').mismatched_fields(client) if client is not None else None
                except Exception:
                    mismatch = None
                row['config_matches'] = None if mismatch is None else not mismatch
            return row
        if point == 'IpcModelLoader.load_model':
            row = {'mode': fields.get('mode') if fields.get('mode') in ('zero_copy', 'copy') else None,
                   'fallback_enabled': fields.get('fallback') if type(fields.get('fallback')) is bool else None}
            if returned:
                row['model_token'] = self.token(result)
            return row
        if point == 'IpcModelLoader._request_state':
            if not returned:
                return {}
            entries = cache.base.attrs(result).get('entries')
            return {'entries': len(entries) if type(entries) in (list, dict) else None, 'served': True}
        # IpcModelLoader._fallback_load
        row = {'fallback_enabled': fields.get('fallback') if type(fields.get('fallback')) is bool else None}
        if returned:
            row['model_token'] = self.token(result)
        return row


def instrument(module, observer):
    name = vars(module).get('__name__')
    changes = [(cache._function(module, point), observer.wrap(module, point)) for point in TARGETS[name]]
    for (owner, method, _), wrapped in changes:
        setattr(owner, method, wrapped)
    return True
